"""Reminders: reply nudges and snapshot prompts after go-live, the kit confirmation.

Each reminder is a document keyed by what it is about (``Reminder.id_for(key)``), so creating
it twice is a ``Conflict`` and a resumed step creates none twice:

- ``reply:<postId>:<minutes>`` at go-live + ``cadence.reminders.reply_minutes`` (15, 60, 180),
  each valid for 2 hours (a reply nudge hours late is noise);
- ``snapshot:<postId>:<hours>`` at go-live + ``cadence.reminders.snapshot_hours`` (48, 168),
  valid for 3 days; skipped when the snapshot is already logged;
- ``kit_confirm:<postId>:<T>`` at the native schedule time T + 30 minutes ("Did it go out? Add
  the final text"), valid for 2 days; earlier ones of the post are cancelled when T changes.

A delayed ``planner.remind`` job (``remind_job_id``, ``run_after = dueAt``) sends it: a
reminder found past its ``expiresAt`` (after downtime) expires instead of firing. Titles and
links carry no content (they pass the push payload checks). The owner can dismiss a reminder;
a sent one cannot be cancelled any more (``REMINDER_MACHINE``), so dismissal is recorded as
``dismissedAt`` (an extra field until the domain model has one) and hides it from Today.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from blogwriter.ai.ledger import Origin
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import Finding
from blogwriter.domain.docstore import Stored, create_doc, find_docs, read_doc, update_doc
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import PLANNER_REMIND, RemindInput, remind_job_id, slot_stamp
from blogwriter.domain.planner import Reminder, ReminderKind
from blogwriter.domain.ports import JobQueue, Notifier
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.slots import spacing_findings
from blogwriter.domain.states import REMINDER_MACHINE, KitState, PostStatus, ReminderStatus
from blogwriter.jobs.queue import JobStateError
from blogwriter.notify.payload import NotificationPayload
from blogwriter.planner.links import engage_href, post_href, snapshot_href
from blogwriter.planner.slots import slot_from_utc, taken_slots

__all__ = [
    "DISMISSED_KEY",
    "KIT_CONFIRM_DELAY",
    "KIT_CONFIRM_WINDOW",
    "REPLY_WINDOW",
    "SNAPSHOT_WINDOW",
    "KitConfirm",
    "cancel_stale_kit_reminders",
    "dismiss_reminder",
    "dismissed_at",
    "ensure_reminders",
    "kit_confirm_reminder",
    "natively_scheduled_at",
    "reminders_for_go_live",
    "reply_reminders",
    "schedule_kit_confirm",
    "send_reminder",
    "snapshot_logged",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("reminder_id", "post_id", "status", "reason")

REPLY_WINDOW: Final = timedelta(hours=2)
SNAPSHOT_WINDOW: Final = timedelta(days=3)
KIT_CONFIRM_DELAY: Final = timedelta(minutes=30)
KIT_CONFIRM_WINDOW: Final = timedelta(days=2)
KEEP_AFTER_EXPIRY: Final = timedelta(days=30)
"""The Cosmos TTL keeps a reminder this long after it expires (garbage collection only)."""
SNAPSHOT_TOLERANCE: Final = timedelta(hours=12)
DISMISSED_KEY: Final = "dismissedAt"
KIT_CONFIRM_TITLE: Final = "Did it go out? Add the final text"


def _offset_label(minutes: int) -> str:
    if minutes < 60 or minutes % 60:
        return f"{minutes} min"
    return f"{minutes // 60} h"


def _snapshot_label(hours: int) -> str:
    if hours % 24 == 0 and hours >= 7 * 24 and hours % (7 * 24) == 0:
        weeks = hours // (7 * 24)
        return "7-day" if weeks == 1 else f"{weeks * 7}-day"
    return f"{hours}-hour"


def _reminder(
    kind: ReminderKind,
    key: str,
    *,
    post_id: str,
    due: datetime,
    window: timedelta,
    title: str,
    url: str,
    now: datetime,
) -> Reminder:
    stamp = to_iso(now)
    expires = due + window
    ttl = max(1, int((expires + KEEP_AFTER_EXPIRY - now).total_seconds()))
    return Reminder(
        id=Reminder.id_for(key),
        created_at=stamp,
        updated_at=stamp,
        expires_at=to_iso(expires),
        ttl=ttl,
        reminder_kind=kind,
        status=ReminderStatus.PENDING,
        status_changed_at=stamp,
        due_at=to_iso(due),
        target_kind="post",
        target_id=post_id,
        title=title,
        url=url,
        key=key,
    )


def reply_reminders(
    post: Post, cadence: CadenceSettings, go_live_at: str, *, now: datetime, native: bool = False
) -> list[Reminder]:
    """The reply reminders of a post going live at ``go_live_at`` (+15/+60/+180 min,
    ``expiresAt = dueAt + 2 h``). Key ``reply:<postId>:<minutes>``; for a kit post the owner
    scheduled natively (``native``) the key also names T (``reply:<postId>:<T>:<minutes>``), so
    a new T gets its own reminders while the old ones are cancelled."""
    base = parse_iso(go_live_at)
    prefix = f"reply:{post.id}:{slot_stamp(go_live_at)}" if native else f"reply:{post.id}"
    return [
        _reminder(
            "reply",
            f"{prefix}:{minutes}",
            post_id=post.id,
            due=base + timedelta(minutes=minutes),
            window=REPLY_WINDOW,
            title=f"Live for {_offset_label(minutes)}: reply to comments on your post",
            url=engage_href(post.id),
            now=now,
        )
        for minutes in cadence.reminders.reply_minutes
    ]


def natively_scheduled_at(post: Post) -> str | None:
    """T, when the post is (or was) a kit post the owner scheduled natively."""
    kit = post.kit
    if kit is None or kit.scheduled_at is None:
        return None
    if kit.state not in (KitState.NATIVELY_SCHEDULED, KitState.POSTED):
        return None
    return kit.scheduled_at


def reminders_for_go_live(
    post: Post, cadence: CadenceSettings, go_live_at: str, *, now: datetime
) -> list[Reminder]:
    """The reply and snapshot reminders of a post that went live at ``go_live_at``.

    Reply at +15/+60/+180 min (``expiresAt = dueAt + 2 h``), snapshot at +48 h/+7 d
    (``expiresAt = dueAt + 3 d``); key ``<kind>:<postId>:<offset>`` (minutes for replies,
    hours for snapshots), id ``Reminder.id_for(key)`` (a second create is a ``Conflict``). A
    kit post that went live at its native time T got its reply reminders when it was scheduled
    (:func:`schedule_kit_confirm`, keyed on T): only the snapshots are added here.
    """
    base = parse_iso(go_live_at)
    out: list[Reminder] = []
    if natively_scheduled_at(post) != go_live_at:
        out.extend(reply_reminders(post, cadence, go_live_at, now=now))
    for hours in cadence.reminders.snapshot_hours:
        out.append(
            _reminder(
                "snapshot",
                f"snapshot:{post.id}:{hours}",
                post_id=post.id,
                due=base + timedelta(hours=hours),
                window=SNAPSHOT_WINDOW,
                title=f"Log the {_snapshot_label(hours)} numbers for your post",
                url=snapshot_href(post.id),
                now=now,
            )
        )
    return out


def kit_confirm_reminder(post: Post, scheduled_at: str, *, now: datetime) -> Reminder:
    """ "Did it go out?" at the native time T + 30 minutes (key ``kit_confirm:<postId>:<T>``)."""
    return _reminder(
        "kit_confirm",
        f"kit_confirm:{post.id}:{slot_stamp(scheduled_at)}",
        post_id=post.id,
        due=parse_iso(scheduled_at) + KIT_CONFIRM_DELAY,
        window=KIT_CONFIRM_WINDOW,
        title=KIT_CONFIRM_TITLE,
        url=post_href(post),
        now=now,
    )


def dismissed_at(reminder: Reminder) -> str | None:
    """When the owner dismissed it (``dismissedAt``), if they did."""
    value = reminder.model_dump(mode="json", by_alias=True).get(DISMISSED_KEY)
    return value if isinstance(value, str) else None


def _with_dismissed(reminder: Reminder, stamp: str) -> Reminder:
    body = reminder.model_dump(mode="json", by_alias=True)
    body[DISMISSED_KEY] = stamp
    return Reminder.model_validate(body)


async def _enqueue_remind(jobs: JobQueue, reminder: Reminder, *, origin: Origin) -> str | None:
    payload = RemindInput(reminder_id=reminder.id, origin=origin).to_json()
    try:
        job = await enqueue_once(
            jobs,
            PLANNER_REMIND,
            payload,
            job_id=remind_job_id(reminder.id),
            run_after=parse_iso(reminder.due_at),
        )
    except ValueError:
        _logger.warning("job_hook_unregistered", extra={"job_type": PLANNER_REMIND})
        return None
    return job.id


async def ensure_reminders(
    store: Store, jobs: JobQueue, reminders: Sequence[Reminder], *, origin: Origin
) -> tuple[int, int]:
    """Create each reminder (``Conflict``: it exists) and the delayed ``planner.remind`` job of
    each pending one. Returns ``(created, existing)``; running it again creates nothing."""
    created = existing = 0
    for reminder in reminders:
        try:
            stored = await create_doc(store, reminder)
            created += 1
        except Conflict:
            stored = await read_doc(store, Reminder, reminder.id)
            existing += 1
        if stored.doc.status is ReminderStatus.PENDING:
            await _enqueue_remind(jobs, stored.doc, origin=origin)
    return created, existing


def snapshot_logged(post: Post, reminder: Reminder) -> bool:
    """Whether the snapshot a reminder asks for is already logged: ``h48``/``d7`` for the
    48-hour and 7-day reminders, else any snapshot taken from 12 h before it was due."""
    label = {"48": "h48", "168": "d7"}.get(reminder.key.rsplit(":", 1)[-1])
    if label is not None and any(snapshot.offset == label for snapshot in post.snapshots):
        return True
    earliest = to_iso(parse_iso(reminder.due_at) - SNAPSHOT_TOLERANCE)
    return any(snapshot.taken_at >= earliest for snapshot in post.snapshots)


def _done_reason(post: Post | None, reminder: Reminder) -> str | None:
    """Why a reminder no longer needs sending (``None``: send it)."""
    if post is None or post.status in (PostStatus.ARCHIVED, PostStatus.DELETED):
        return "post_gone"
    match reminder.reminder_kind:
        case "snapshot":
            return "snapshot_logged" if snapshot_logged(post, reminder) else None
        case "kit_confirm":
            if post.status is PostStatus.POSTED or (
                post.kit is not None and post.kit.state is KitState.POSTED
            ):
                return "already_posted"
            if post.status is not PostStatus.KIT:
                return "not_scheduled"
            return None
        case "reply":
            return None


def _settle(
    status: ReminderStatus, stamp: str, **fields: object
) -> Callable[[Reminder], Reminder | None]:
    def mutate(reminder: Reminder) -> Reminder | None:
        if reminder.status is not ReminderStatus.PENDING:
            return None
        REMINDER_MACHINE.transition(reminder.status, status)
        return reminder.model_copy(
            update={"status": status, "status_changed_at": stamp, "updated_at": stamp, **fields}
        )

    return mutate


async def send_reminder(
    store: Store,
    notifier: Notifier,
    reminder_id: str,
    *,
    now: datetime,
    resumed_after_push: bool = False,
    before_push: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, str | bool | None]:
    """The ``send`` step of ``planner.remind``: expire it when past ``expiresAt``, cancel it
    when there is nothing left to do (snapshot logged, kit post confirmed, post gone), else
    push it and mark it ``sent`` (``delivered`` = push receipt). ``before_push`` runs right
    before the push (the job checkpoints there); ``resumed_after_push``: an earlier attempt may
    already have pushed it, so only the status is settled (at most one push)."""
    try:
        reminder = (await read_doc(store, Reminder, reminder_id)).doc
    except NotFound:
        return {"status": "missing"}
    if reminder.status is not ReminderStatus.PENDING:
        return {"status": reminder.status.value}
    stamp = to_iso(now)
    if reminder.expired(now):
        await update_doc(store, Reminder, reminder_id, _settle(ReminderStatus.EXPIRED, stamp))
        _logger.info("planner_reminder_expired", extra={"reminder_id": reminder_id})
        return {"status": ReminderStatus.EXPIRED.value}
    post: Post | None = None
    if reminder.target_kind == "post" and reminder.target_id is not None:
        with contextlib.suppress(NotFound):
            post = (await read_doc(store, Post, reminder.target_id)).doc
    reason = _done_reason(post, reminder)
    if reason is not None:
        await update_doc(store, Reminder, reminder_id, _settle(ReminderStatus.CANCELLED, stamp))
        return {"status": ReminderStatus.CANCELLED.value, "reason": reason}
    delivered: bool | None = None
    if not resumed_after_push:
        if before_push is not None:
            await before_push()
        payload = NotificationPayload(
            title=reminder.title, url=reminder.url, tag=f"reminder:{reminder.id}"
        )
        try:
            result = await notifier.notify(payload)
            delivered = result.any_delivered
        except Exception as exc:  # a push failure must not repeat the nudge
            _logger.warning(
                "planner_reminder_push_failed",
                extra={"reminder_id": reminder_id, "reason": type(exc).__name__},
            )
            delivered = False
    await update_doc(
        store,
        Reminder,
        reminder_id,
        _settle(ReminderStatus.SENT, stamp, sent_at=stamp, delivered=delivered),
    )
    return {"status": ReminderStatus.SENT.value, "delivered": delivered}


async def dismiss_reminder(
    store: Store, jobs: JobQueue, reminder_id: str, *, now: datetime
) -> Stored[Reminder]:
    """The owner dismisses a reminder (idempotent, no If-Match): a pending one is cancelled
    (its job turns into a no-op and is cancelled), a sent one keeps its status; both get
    ``dismissedAt`` and leave Today."""
    stamp = to_iso(now)

    def mutate(reminder: Reminder) -> Reminder | None:
        if dismissed_at(reminder) is not None:
            return None
        if reminder.status in (ReminderStatus.CANCELLED, ReminderStatus.EXPIRED):
            return None
        changed = reminder.model_copy(update={"updated_at": stamp})
        if reminder.status is ReminderStatus.PENDING:
            REMINDER_MACHINE.transition(reminder.status, ReminderStatus.CANCELLED)
            changed = changed.model_copy(
                update={"status": ReminderStatus.CANCELLED, "status_changed_at": stamp}
            )
        return _with_dismissed(changed, stamp)

    stored = await update_doc(store, Reminder, reminder_id, mutate)
    if stored.doc.status is ReminderStatus.CANCELLED:
        with contextlib.suppress(NotFound, JobStateError):
            await jobs.cancel(remind_job_id(reminder_id))
    return stored


# --------------------------------------------------------------------------------------------
# planner.post-scheduled
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KitConfirm:
    reminder_id: str | None
    cancelled: int
    spacing: tuple[str, ...]
    skipped: Literal["missing", "rescheduled"] | None = None


async def cancel_stale_kit_reminders(
    store: Store, jobs: JobQueue, post_id: str, keep: Collection[str], *, now: datetime
) -> int:
    """Cancel the post's pending kit confirmations and reply reminders of an earlier native
    time T (``keep``: those of the current T)."""
    stamp = to_iso(now)
    found = await find_docs(
        store,
        Reminder,
        [
            Filter("targetId", "==", post_id),
            Filter("reminderKind", "in", ["kit_confirm", "reply"]),
            Filter("status", "==", ReminderStatus.PENDING.value),
        ],
    )
    cancelled = 0
    for stored in found:
        if stored.doc.id in keep:
            continue
        await update_doc(store, Reminder, stored.doc.id, _settle(ReminderStatus.CANCELLED, stamp))
        with contextlib.suppress(NotFound, JobStateError):
            await jobs.cancel(remind_job_id(stored.doc.id))
        cancelled += 1
    return cancelled


async def _spacing_codes(
    store: Store, post: Post, scheduled_at: str, cadence: CadenceSettings
) -> list[Finding]:
    """Spacing findings between the native time T and the go-live times of other posts within
    two days of it."""
    mine = slot_from_utc(scheduled_at, cadence)
    moment = parse_iso(mine.utc)
    taken = await taken_slots(store, cadence, since=moment - timedelta(days=2), exclude={post.id})
    findings: list[Finding] = []
    for other in taken:
        if abs(parse_iso(other.slot.utc) - moment) <= timedelta(days=2):
            findings.extend(spacing_findings([mine, other.slot], cadence=cadence))
    return findings


async def schedule_kit_confirm(
    store: Store,
    jobs: JobQueue,
    notifier: Notifier,
    post_id: str,
    scheduled_at: str,
    *,
    cadence: CadenceSettings,
    origin: Origin,
    now: datetime,
) -> KitConfirm:
    """The ``reminders`` step of ``planner.post-scheduled``: keyed on the native time T, it
    cancels the post's earlier kit confirmations and reply reminders, creates the T + 30 min
    confirmation and the reply reminders at T + 15/60/180 min (LinkedIn posts it at T whether
    or not the owner confirms), and warns (push) when T is on the same day as, or less than 24
    hours from, another post. A job for a T the owner has since changed does nothing."""
    try:
        post = (await read_doc(store, Post, post_id)).doc
    except NotFound:
        return KitConfirm(None, 0, (), skipped="missing")
    if post.kit is None or post.kit.scheduled_at != scheduled_at:
        return KitConfirm(None, 0, (), skipped="rescheduled")
    reminder = kit_confirm_reminder(post, scheduled_at, now=now)
    replies = reply_reminders(post, cadence, scheduled_at, now=now, native=True)
    keep = {reminder.id, *(item.id for item in replies)}
    cancelled = await cancel_stale_kit_reminders(store, jobs, post_id, keep, now=now)
    await ensure_reminders(store, jobs, [reminder, *replies], origin=origin)
    findings = await _spacing_codes(store, post, scheduled_at, cadence)
    codes = tuple(sorted({finding.code for finding in findings}))
    if findings:
        same_day = any(finding.severity == "block" for finding in findings)
        title = (
            "Your scheduled post shares a day with another post"
            if same_day
            else "Your scheduled post is less than 24 h from another post"
        )
        payload = NotificationPayload(title=title, url=post_href(post), tag=f"spacing:{post.id}")
        try:
            await notifier.notify(payload)
        except Exception as exc:  # a warning that could not be pushed is still in the output
            _logger.warning(
                "planner_spacing_push_failed",
                extra={"post_id": post_id, "reason": type(exc).__name__},
            )
    return KitConfirm(reminder.id, cancelled, codes)
