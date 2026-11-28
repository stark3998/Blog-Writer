"""The missed-slot policy (``planner.slot-check``, plan: "Weekly production calendar").

A one-click post has until its slot plus ``cadence.missed_slot_grace_minutes`` (2 hours) to go
out: the check runs then (``run_after = slot + grace``) and whenever it is claimed later, since
lateness is exactly what it handles (after downtime it runs late and still does the right
thing). It does nothing when the post moved (re-slotted), went out or is going out (posted,
publishing), moved to the kit, or is no longer scheduled; otherwise the post becomes
``missed`` and the owner is offered the next free slot (Today and a push), never one on a day
another post holds (posts are never stacked). A time-sensitive post is offered nothing when it
expires before the next free slot; ``posts.expire`` retires it at ``expiresAt``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from blogwriter.core.logging import register_log_fields
from blogwriter.db import NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import read_doc, update_doc
from blogwriter.domain.ports import Notifier
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import POST_MACHINE, PostStatus
from blogwriter.domain.weeks import slot_utc
from blogwriter.notify.payload import NotificationPayload
from blogwriter.planner.links import post_href
from blogwriter.planner.slots import local_day, local_label, next_offer, taken_slots

__all__ = ["CHECKED_STATUSES", "SlotCheck", "check_slot", "missed_offer"]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("post_id", "status")

CHECKED_STATUSES: Final = frozenset({PostStatus.APPROVED, PostStatus.STAGED})
"""Only a post still waiting at its slot can miss it."""


@dataclass(frozen=True, slots=True)
class SlotCheck:
    post_id: str
    status: str
    offer: Slot | None = None
    skipped: str | None = None


async def missed_offer(
    store: Store, post: Post, cadence: CadenceSettings, *, now: datetime
) -> Slot | None:
    """The next free slot to offer a missed post (``None``: none, or it expires first)."""
    midnight = slot_utc(local_day(to_iso(now), cadence.timezone), "00:00", cadence.timezone)
    taken = await taken_slots(store, cadence, since=parse_iso(midnight), exclude={post.id})
    return next_offer(post, [item.slot for item in taken], cadence, after=now)


async def check_slot(
    store: Store,
    notifier: Notifier,
    post_id: str,
    slot_utc_value: str,
    *,
    cadence: CadenceSettings,
    now: datetime,
) -> SlotCheck:
    """The ``check`` step of ``planner.slot-check`` (module docs)."""
    try:
        post = (await read_doc(store, Post, post_id)).doc
    except NotFound:
        return SlotCheck(post_id, "missing", skipped="missing")
    if post.slot is None or post.slot.utc != slot_utc_value:
        return SlotCheck(post_id, post.status.value, skipped="reslotted")
    if post.status not in CHECKED_STATUSES:
        return SlotCheck(post_id, post.status.value, skipped=post.status.value)
    stamp = to_iso(now)

    def mutate(doc: Post) -> Post | None:
        if doc.slot is None or doc.slot.utc != slot_utc_value or doc.status not in CHECKED_STATUSES:
            return None
        status = POST_MACHINE.transition(doc.status, PostStatus.MISSED)
        return doc.model_copy(
            update={"status": status, "status_changed_at": stamp, "updated_at": stamp}
        )

    stored = await update_doc(store, Post, post_id, mutate)
    if stored.doc.status is not PostStatus.MISSED:
        return SlotCheck(post_id, stored.doc.status.value, skipped=stored.doc.status.value)
    missed = stored.doc
    offer = await missed_offer(store, missed, cadence, now=now)
    when = local_label(slot_utc_value, cadence.timezone)
    if offer is not None:
        title = (
            f"Missed your {when} post. Next free slot: {local_label(offer.utc, cadence.timezone)}"
        )
    elif missed.time_sensitive:
        title = f"Missed your {when} post. It expires before the next free slot"
    else:
        title = f"Missed your {when} post. Pick a new time"
    payload = NotificationPayload(title=title[:80], url=post_href(missed), tag=f"missed:{post_id}")
    try:
        await notifier.notify(payload)
    except Exception as exc:  # the post is missed either way; Today shows the offer
        _logger.warning(
            "planner_missed_push_failed", extra={"post_id": post_id, "status": type(exc).__name__}
        )
    _logger.info("planner_slot_missed", extra={"post_id": post_id})
    return SlotCheck(post_id, PostStatus.MISSED.value, offer=offer)
