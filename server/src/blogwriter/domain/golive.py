"""Where and how posts go live: one rule for the posts routes, the planner and the transport.

linkedin posts (approve), the planner (plans, re-slots, missed slots, kit spacing) and the
linkedin transport (switching a post between one-click and the kit) are built in parallel and
may not import each other, so the store-backed rules they share live here (the pure slot math is
:mod:`blogwriter.domain.slots`):

- :func:`taken_slots`: the go-live times other posts hold. Approved, staged, publishing,
  needs-attention and kit posts (the kit's native time T when set, else the slot) and posts that
  went out since ``since``. Read with ``strict=True``: a post the check cannot read fails it.
  **Two posts on one local day is a hard block**, so every route that gives a post a time checks
  against all of them.
- :func:`default_go_live_mode`: one-click when LinkedIn is connected, else the ready-to-post kit
  (it needs no API); every way a post is created uses it.
- :func:`enqueue_slot_jobs` / :func:`cancel_slot_jobs`: ``posts.stage`` at the slot minus the
  stage lead and ``planner.slot-check`` at the slot plus the grace, for one-click posts.
- :func:`plan_slot_for` / :func:`link_plan_slot`: a post created for a slot of a week plan (a
  quick take written for its "What's your take?" slot). The post carries the link
  (``weekPlanId``, ``planSlotId``) and the plan's slot names the post (``postId``); linking
  changes nothing else in the plan, so an approved plan stays approved. This is the one plan
  field the posts routes write (the planner owns the rest).
- :func:`slot_post_gone`: a slot whose post is gone (archived, expired or deleted:
  :data:`GONE_POSTS`) is open again. Nothing clears ``PlannedSlot.postId`` and quick takes
  expire by design, so a new post may fill the slot (it replaces the gone post's id) and Today
  asks for the take again, without a plan edit (which would send an approved plan back to
  proposed). A post that cannot be found keeps its slot (fail closed).
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import Stored, find_docs, read_doc, read_singleton, update_doc
from blogwriter.domain.jobflow import MAX_RERUNS, enqueue_once, rerun_job_id
from blogwriter.domain.jobtypes import (
    PLANNER_SLOT_CHECK,
    POSTS_STAGE,
    SlotJobInput,
    slot_check_job_id,
    stage_job_id,
)
from blogwriter.domain.planner import PlannedSlot, WeekPlan
from blogwriter.domain.ports import JobQueue
from blogwriter.domain.posts import Lane, Post, Slot
from blogwriter.domain.settings import CadenceSettings, LinkedInConnection
from blogwriter.domain.slots import slot_from_utc
from blogwriter.domain.states import ConnectionStatus, PlanStatus, PostStatus
from blogwriter.domain.weeks import plan_id
from blogwriter.jobs.queue import JobStateError

__all__ = [
    "GONE_POSTS",
    "LINKABLE_PLANS",
    "TAKEN_ACTIVE",
    "PlanSlotRefused",
    "TakenSlot",
    "cancel_slot_jobs",
    "default_go_live_mode",
    "enqueue_slot_jobs",
    "go_live_time",
    "link_plan_slot",
    "plan_slot_for",
    "slot_post_gone",
    "taken_slots",
]

TAKEN_ACTIVE: Final = (
    PostStatus.APPROVED,
    PostStatus.STAGED,
    PostStatus.PUBLISHING,
    PostStatus.NEEDS_ATTENTION,
    PostStatus.KIT,
)
"""Posts that will (or may already) be live at their go-live time."""

GONE_POSTS: Final = frozenset({PostStatus.ARCHIVED, PostStatus.EXPIRED, PostStatus.DELETED})
"""Posts that hold nothing any more: a planned slot that names one is open again."""

LINKABLE_PLANS: Final = frozenset({PlanStatus.PROPOSED, PlanStatus.APPROVED})
"""Plans whose slots a new post may fill (a generating plan's slots are about to be replaced;
a closed week is over)."""

_logger: Final = logging.getLogger("blogwriter.domain.golive")
register_log_fields("post_id", "plan_id")


class PlanSlotRefused(ValueError):
    """A post cannot fill the planned slot it names. ``code``: ``plan_slot_unknown`` (no such
    plan or slot, 422), ``plan_closed`` (409), ``plan_generating`` (409), ``plan_slot_lane``
    (the slot is for another lane, 409) or ``plan_slot_taken`` (another live post fills it, 409).
    ``detail`` is owner-facing text without content."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TakenSlot:
    """A post holding a go-live time (its slot, its native time T, or when it went out)."""

    post_id: str
    slot: Slot
    status: PostStatus


def go_live_time(post: Post) -> str | None:
    """The UTC time ``post`` holds: when it went out (posted), the kit's native T, else its
    slot."""
    if post.status is PostStatus.POSTED:
        return post.posted_at or (post.slot.utc if post.slot is not None else None)
    if post.status is PostStatus.KIT and post.kit is not None and post.kit.scheduled_at:
        return post.kit.scheduled_at
    return post.slot.utc if post.slot is not None else None


async def taken_slots(
    store: Store,
    cadence: CadenceSettings,
    *,
    since: datetime,
    exclude: Collection[str] = (),
) -> list[TakenSlot]:
    """Go-live times other posts hold (module docs), sorted by time."""
    active = await find_docs(
        store,
        Post,
        [Filter("status", "in", [status.value for status in TAKEN_ACTIVE])],
        strict=True,
    )
    posted = await find_docs(
        store,
        Post,
        [Filter("status", "==", PostStatus.POSTED.value), Filter("postedAt", ">=", to_iso(since))],
        strict=True,
    )
    out: list[TakenSlot] = []
    for stored in [*active, *posted]:
        post = stored.doc
        if post.id in exclude:
            continue
        moment = go_live_time(post)
        if moment is None:
            continue
        out.append(TakenSlot(post.id, slot_from_utc(moment, cadence), post.status))
    return sorted(out, key=lambda taken: taken.slot.utc)


async def default_go_live_mode(store: Store, *, now: datetime) -> Literal["one_click", "kit"]:
    """One-click when LinkedIn is connected, else the ready-to-post kit (needs no API)."""
    connection, _ = await read_singleton(store, LinkedInConnection, now=to_iso(now))
    return "one_click" if connection.status is ConnectionStatus.CONNECTED else "kit"


async def enqueue_slot_jobs(
    jobs: JobQueue, post: Post, *, cadence: CadenceSettings
) -> dict[str, str]:
    """For a one-click post with a slot: ``posts.stage`` at the slot minus the stage lead and
    ``planner.slot-check`` at the slot plus the grace, both ``enqueue_once(...,
    on_conflict="rerun")`` (a slot used again after an unapprove gets ``-r<n>``). An
    unregistered type is logged (``job_hook_unregistered``) and skipped. Returns the job id per
    job type."""
    slot = post.slot
    if slot is None or post.go_live_mode != "one_click":
        return {}
    moment = parse_iso(slot.utc)
    payload = SlotJobInput(post_id=post.id, slot_utc=slot.utc, origin="owner").to_json()
    planned = (
        (
            POSTS_STAGE,
            stage_job_id(post.id, slot.utc),
            timedelta(minutes=-cadence.stage_lead_minutes),
        ),
        (
            PLANNER_SLOT_CHECK,
            slot_check_job_id(post.id, slot.utc),
            timedelta(minutes=cadence.missed_slot_grace_minutes),
        ),
    )
    ids: dict[str, str] = {}
    for job_type, job_id, offset in planned:
        try:
            job = await enqueue_once(
                jobs,
                job_type,
                payload,
                job_id=job_id,
                run_after=moment + offset,
                on_conflict="rerun",
            )
        except ValueError:
            _logger.warning("job_hook_unregistered", extra={"job_type": job_type})
            continue
        ids[job_type] = job.id
    return ids


async def cancel_slot_jobs(jobs: JobQueue, post_id: str, slot_utc: str) -> int:
    """Cancel the active ``posts.stage`` and ``planner.slot-check`` jobs of one slot (re-runs
    included; finished ones are left alone). Returns how many were cancelled."""
    cancelled = 0
    for base in (stage_job_id(post_id, slot_utc), slot_check_job_id(post_id, slot_utc)):
        for index in range(MAX_RERUNS + 1):
            candidate = base if index == 0 else rerun_job_id(base, index)
            try:
                job = await jobs.get(candidate)
            except NotFound:
                break
            if not job.is_active:
                continue
            try:
                await jobs.cancel(candidate)
            except (JobStateError, NotFound):
                continue
            cancelled += 1
    return cancelled


# --------------------------------------------------------------------------------------------
# Posts written for a planned slot
# --------------------------------------------------------------------------------------------


async def slot_post_gone(store: Store, post_id: str) -> bool:
    """True when the post a planned slot names is archived, expired or deleted
    (:data:`GONE_POSTS`): the slot is open again. A post that cannot be found keeps its slot
    (fail closed: only a post seen gone frees it)."""
    try:
        post = (await read_doc(store, Post, post_id)).doc
    except NotFound:
        return False
    return post.status in GONE_POSTS


async def plan_slot_for(
    store: Store, week_start: str, slot_id: str, *, lane: Lane
) -> tuple[WeekPlan, PlannedSlot]:
    """The plan of ``week_start`` and its slot ``slot_id``, checked for a new ``lane`` post:
    the plan is proposed or approved, the slot is for ``lane`` (or has no lane) and no live
    post fills it (a post archived, expired or deleted since leaves it open:
    :func:`slot_post_gone`; the slot returned still names that post, for
    :func:`link_plan_slot`'s ``replaces``). :class:`PlanSlotRefused` otherwise."""
    try:
        plan = (await read_doc(store, WeekPlan, plan_id(week_start))).doc
    except NotFound:
        raise PlanSlotRefused(
            "plan_slot_unknown", 422, "That week has no plan. Reload the calendar."
        ) from None
    if plan.status is PlanStatus.CLOSED:
        raise PlanSlotRefused("plan_closed", 409, "This week is closed.")
    if plan.status not in LINKABLE_PLANS:
        raise PlanSlotRefused(
            "plan_generating", 409, "The plan is being generated. Try again soon."
        )
    slot = next((item for item in plan.slots if item.id == slot_id), None)
    if slot is None:
        raise PlanSlotRefused(
            "plan_slot_unknown", 422, "That slot is no longer in the plan. Reload the calendar."
        )
    if slot.lane is not None and slot.lane != lane:
        raise PlanSlotRefused("plan_slot_lane", 409, "That slot is planned for another lane.")
    if slot.post_id is not None and not await slot_post_gone(store, slot.post_id):
        raise PlanSlotRefused("plan_slot_taken", 409, "Another post already fills that slot.")
    return plan, slot


async def link_plan_slot(
    store: Store,
    week_plan_id: str,
    slot_id: str,
    post_id: str,
    *,
    now: datetime,
    replaces: str | None = None,
) -> Stored[WeekPlan] | None:
    """Name ``post_id`` on the plan's slot ``slot_id`` (a CAS update of that one field and
    ``updatedAt``: the plan's status, approval and other slots stay as they are). The slot must
    be empty, or name ``replaces``: the gone post :func:`plan_slot_for` found there (archived,
    expired or deleted), whose id the new post takes over. ``None`` when the plan or the slot is
    gone, or another post filled the slot meanwhile (logged; the post keeps its own link)."""
    stamp = to_iso(now)
    raced = False

    def mutate(plan: WeekPlan) -> WeekPlan | None:
        nonlocal raced
        if plan.status not in LINKABLE_PLANS:
            raced = True
            return None
        index = next((i for i, item in enumerate(plan.slots) if item.id == slot_id), None)
        if index is None:
            raced = True
            return None
        slot = plan.slots[index]
        if slot.post_id == post_id:
            return None
        if slot.post_id is not None and slot.post_id != replaces:
            raced = True
            return None
        slots = list(plan.slots)
        slots[index] = slot.model_copy(update={"post_id": post_id})
        return plan.model_copy(update={"slots": slots, "updated_at": stamp})

    try:
        stored = await update_doc(store, WeekPlan, week_plan_id, mutate)
    except NotFound:
        raced = True
        stored = None
    if raced:
        _logger.warning("plan_slot_not_linked", extra={"post_id": post_id, "plan_id": week_plan_id})
        return None
    return stored
