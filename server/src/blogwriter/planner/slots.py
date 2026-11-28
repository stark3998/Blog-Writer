"""Planner slot helpers: weeks in the owner's zone, slot inputs, taken slots and re-slotting.

The slot math every package shares lives in :mod:`blogwriter.domain.slots` (week slots, spacing,
conflicts, the next free slot, the DST rule of :mod:`blogwriter.domain.weeks`); this module adds
what only the planner needs:

- weeks as the owner sees them (:func:`current_week_start`, :func:`next_week_start`,
  :func:`week_bounds`) and short local labels for titles;
- :func:`taken_slots`: the days already spoken for by scheduled, natively scheduled, publishing
  and posted posts (read with ``strict=True``: a post the check cannot read fails it);
- :func:`free_week_slots`: the week's template slots on free, future days (primary first, then
  optional ones, like ``domain.slots.week_slots``);
- :func:`next_offer`: the missed-slot offer (the next free slot, never for a time-sensitive post
  that expires first);
- :func:`move_post`: re-slotting (``POST /api/posts/{id}/reslot`` and drag and drop): the
  planner writes only ``slot``, ``weekPlanId`` and ``staged|missed -> approved``, then
  re-enqueues ``posts.stage`` and ``planner.slot-check`` for the new slot.

Posts are never stacked: a new slot on a local day that another scheduled post holds is 409
``slot_conflict``.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import PreconditionFailed, Store
from blogwriter.domain.base import WEEKDAYS, parse_iso, to_iso
from blogwriter.domain.deps import etag_mismatch
from blogwriter.domain.docstore import Stored, read_doc, replace_doc, update_doc
from blogwriter.domain.golive import (
    TakenSlot,
    cancel_slot_jobs,
    enqueue_slot_jobs,
    taken_slots,
)
from blogwriter.domain.ports import JobQueue
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.settings import CadenceSettings, WeeklyTime
from blogwriter.domain.slots import (
    conflicting_slots,
    next_free_slot,
    slot_at,
    slot_from_utc,
    template_kind,
)
from blogwriter.domain.states import POST_MACHINE, PostStatus
from blogwriter.domain.weeks import add_days, local_date_time, slot_utc, week_start, weekday_of

__all__ = [
    "MOVABLE_STATUSES",
    "SCHEDULED_STATUSES",
    "SNOOZE",
    "Moved",
    "TakenSlot",
    "cancel_slot_jobs",
    "check_free",
    "check_move",
    "current_week_start",
    "enqueue_slot_jobs",
    "free_week_slots",
    "local_day",
    "local_label",
    "move_post",
    "next_offer",
    "next_week_start",
    "shift_weekly",
    "slot_at",
    "slot_from_utc",
    "snooze_slot",
    "taken_slots",
    "template_kind",
    "week_bounds",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("job_type", "post_id", "week_start", "plan_id")

SNOOZE: Final = timedelta(minutes=30)
""""Snooze 30 min" on the go-live page."""

SCHEDULED_STATUSES: Final = frozenset({PostStatus.APPROVED, PostStatus.STAGED, PostStatus.MISSED})
"""Statuses in which a post holds (or held) a go-live slot: moving it re-enqueues its jobs."""
MOVABLE_STATUSES: Final = SCHEDULED_STATUSES | {PostStatus.GENERATING, PostStatus.DRAFT}
"""Statuses in which the planner may change a post's slot. Unapproved posts (generating,
draft) only get the new slot; approval enqueues their jobs later."""


# --------------------------------------------------------------------------------------------
# Weeks and labels
# --------------------------------------------------------------------------------------------


def current_week_start(now: datetime, zone: str) -> str:
    """The Monday (``YYYY-MM-DD``) of the week containing ``now`` in ``zone``."""
    return week_start(local_date_time(to_iso(now), zone)[0])


def next_week_start(now: datetime, zone: str) -> str:
    """The Monday after the current week in ``zone`` (the week a Sunday plan is for)."""
    return add_days(current_week_start(now, zone), 7)


def week_bounds(week_start_date: str, zone: str) -> tuple[str, str]:
    """UTC timestamps of local Monday 00:00 and of the next Monday 00:00 (end exclusive)."""
    return slot_utc(week_start_date, "00:00", zone), slot_utc(
        add_days(week_start_date, 7), "00:00", zone
    )


def local_day(utc: str, zone: str) -> str:
    """The local ``YYYY-MM-DD`` of a stored UTC timestamp."""
    return local_date_time(utc, zone)[0]


def local_label(utc: str, zone: str) -> str:
    """``Tue 10:00``: the local weekday and time of a UTC timestamp (no content)."""
    day, clock = local_date_time(utc, zone)
    return f"{weekday_of(day).capitalize()} {clock}"


def shift_weekly(weekly: WeeklyTime, minutes: int) -> WeeklyTime:
    """``weekly`` moved by ``minutes`` (negative: earlier); the day shifts across midnight.

    ``shift_weekly(sun 18:00, -60)`` is ``sun 17:00``; ``shift_weekly(mon 00:30, -60)`` is
    ``sun 23:30``.
    """
    hours, _, mins = weekly.time.partition(":")
    total = WEEKDAYS.index(weekly.day) * 1440 + int(hours) * 60 + int(mins) + minutes
    total %= 7 * 1440
    day, rest = divmod(total, 1440)
    return WeeklyTime(day=WEEKDAYS[day], time=f"{rest // 60:02d}:{rest % 60:02d}")


# --------------------------------------------------------------------------------------------
# Slots
# --------------------------------------------------------------------------------------------


def snooze_slot(current: Slot | None, cadence: CadenceSettings, *, now: datetime) -> Slot:
    """``max(now, slot) + 30 min``, rounded up to the next whole minute, in the cadence zone."""
    base = now if current is None else max(now, parse_iso(current.utc))
    moment = base + SNOOZE
    if moment.second or moment.microsecond:
        moment = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return slot_from_utc(to_iso(moment), cadence)


def free_week_slots(
    cadence: CadenceSettings,
    week_start_date: str,
    count: int,
    *,
    taken_days: Collection[str] = (),
    after: datetime | None = None,
) -> list[Slot]:
    """Up to ``count`` template slots of the week on free local days, in time order.

    Primary templates first (in week order), optional ones only when more are needed, the
    same choice as :func:`blogwriter.domain.slots.week_slots`; a day in ``taken_days`` (or one
    this choice already used) is skipped, and so is a slot at or before ``after``.
    """
    if week_start(week_start_date) != week_start_date:
        raise ValueError("week_start must be a Monday (YYYY-MM-DD)")
    wanted = max(0, min(count, cadence.posts_per_week.max))

    def key(index: int) -> tuple[int, int, str]:
        template = cadence.post_slots[index]
        return (0 if template.kind == "primary" else 1, WEEKDAYS.index(template.day), template.time)

    used = set(taken_days)
    chosen: list[Slot] = []
    for index in sorted(range(len(cadence.post_slots)), key=key):
        if len(chosen) >= wanted:
            break
        template = cadence.post_slots[index]
        day = add_days(week_start_date, WEEKDAYS.index(template.day))
        candidate = Slot.at(day, template.time, cadence.timezone, template.kind)
        if after is not None and parse_iso(candidate.utc) <= after:
            continue
        if day in used:
            continue
        used.add(day)
        chosen.append(candidate)
    return sorted(chosen, key=lambda slot: slot.utc)


def next_offer(
    post: Post, taken: Sequence[Slot], cadence: CadenceSettings, *, after: datetime
) -> Slot | None:
    """The slot the missed-slot policy offers: :func:`~blogwriter.domain.slots.next_free_slot`
    after ``after`` (never on a taken day), or ``None`` when there is none or a time-sensitive
    post expires before it."""
    offer = next_free_slot(taken, cadence, after=after)
    if offer is None:
        return None
    if post.time_sensitive and post.expires_at is not None and offer.utc >= post.expires_at:
        return None
    return offer


# --------------------------------------------------------------------------------------------
# Slot jobs (posts.stage at slot - lead, planner.slot-check at slot + grace)
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Re-slotting
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Moved:
    stored: Stored[Post]
    job_ids: tuple[str, ...]


def _problem(code: str, detail: str, *, conflicts: Sequence[str] = ()) -> ProblemError:
    extensions: dict[str, JsonValue] | None = (
        {"conflicts": list[JsonValue](conflicts)} if conflicts else None
    )
    return ProblemError(409, code=code, detail=detail, extensions=extensions)


def _moved(post: Post, slot: Slot, *, stamp: str, week_plan_id: str | None) -> Post | None:
    """The post with its new slot (``staged|missed -> approved``), or ``None`` if unchanged."""
    if post.status not in MOVABLE_STATUSES:
        raise _problem(
            "post_not_movable", "This post cannot move now (it is live, in the kit or archived)."
        )
    status = post.status
    if status in (PostStatus.STAGED, PostStatus.MISSED):
        status = POST_MACHINE.transition(status, PostStatus.APPROVED)
    same_slot = post.slot == slot
    plan = week_plan_id if week_plan_id is not None else post.week_plan_id
    if same_slot and status is post.status and plan == post.week_plan_id:
        return None
    changes: dict[str, object] = {"slot": slot, "week_plan_id": plan, "updated_at": stamp}
    if status is not post.status:
        changes.update({"status": status, "status_changed_at": stamp})
    return post.model_copy(update=changes)


def check_move(post: Post, slot: Slot, *, now: datetime) -> None:
    """Refuse a move that can never be right (409): ``post_not_movable`` (live, publishing,
    kit, archived...), ``slot_in_past`` and ``expires_first`` (a time-sensitive post would go
    out after it expires)."""
    if post.status not in MOVABLE_STATUSES:
        raise _problem(
            "post_not_movable", "This post cannot move now (it is live, in the kit or archived)."
        )
    if parse_iso(slot.utc) <= now:
        raise _problem("slot_in_past", "Pick a time that has not passed yet.")
    if post.time_sensitive and post.expires_at is not None and slot.utc >= post.expires_at:
        raise _problem("expires_first", "This time-sensitive post expires before that slot.")


async def check_free(
    store: Store,
    slot: Slot,
    *,
    cadence: CadenceSettings,
    now: datetime,
    exclude: Collection[str],
) -> None:
    """409 ``slot_conflict`` (with the holders' ids) when another post holds ``slot``'s local
    day: posts are never stacked. Posts in ``exclude`` do not count (they move themselves)."""
    midnight = parse_iso(
        slot_utc(local_day(to_iso(now), cadence.timezone), "00:00", cadence.timezone)
    )
    taken = await taken_slots(store, cadence, since=midnight, exclude=exclude)
    clashes = conflicting_slots(slot, [item.slot for item in taken], cadence=cadence)
    if clashes:
        holders = sorted({item.post_id for item in taken if item.slot in clashes})
        raise _problem(
            "slot_conflict",
            "Another post already goes out that day. Posts are never stacked.",
            conflicts=holders,
        )


async def move_post(
    store: Store,
    jobs: JobQueue,
    post_id: str,
    slot: Slot,
    *,
    cadence: CadenceSettings,
    now: datetime,
    if_match: str | None = None,
    week_plan_id: str | None = None,
    check_conflicts: bool = True,
) -> Moved:
    """Give a post a new slot (the planner's write: ``slot``, ``weekPlanId``,
    ``staged|missed -> approved``) and re-enqueue its slot jobs.

    Refused with 409 by :func:`check_move`, and with ``slot_conflict`` (:func:`check_free`)
    when a scheduled post would share a local day with another post: checked for scheduled
    posts only (unapproved ones are checked at approval), and skipped with
    ``check_conflicts=False`` by a caller that validated a whole batch of moves first. With
    ``if_match`` (owner actions) a stale etag is 412 with ``current``; without it the write
    is a CAS loop.
    """
    current = await read_doc(store, Post, post_id)
    if if_match is not None and current.etag != if_match:
        raise etag_mismatch(current)
    post = current.doc
    stamp = to_iso(now)
    check_move(post, slot, now=now)
    if check_conflicts and post.status in SCHEDULED_STATUSES:
        await check_free(store, slot, cadence=cadence, now=now, exclude={post_id})
    old_slot = post.slot.utc if post.slot is not None else None
    was_scheduled = post.status in SCHEDULED_STATUSES
    if if_match is not None:
        changed = _moved(post, slot, stamp=stamp, week_plan_id=week_plan_id)
        if changed is None:
            return Moved(current, ())
        try:
            stored = await replace_doc(store, changed, if_match=if_match)
        except PreconditionFailed:
            raise etag_mismatch(await read_doc(store, Post, post_id)) from None
    else:

        def mutate(doc: Post) -> Post | None:
            return _moved(doc, slot, stamp=stamp, week_plan_id=week_plan_id)

        stored = await update_doc(store, Post, post_id, mutate)
    job_ids: list[str] = []
    if was_scheduled and old_slot is not None and old_slot != slot.utc:
        await cancel_slot_jobs(jobs, post_id, old_slot)
    if stored.doc.status is PostStatus.APPROVED:
        job_ids = list((await enqueue_slot_jobs(jobs, stored.doc, cadence=cadence)).values())
    _logger.info("planner_post_moved", extra={"post_id": post_id})
    return Moved(stored, tuple(job_ids))
