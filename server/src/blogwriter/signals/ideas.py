"""The ideas inbox: ids, owner capture and edits, transitions, use, expiry and snooze wake-ups.

Signals owns ``idea`` documents. Articles, planner and linkedin posts may set ``status = used``
(with ``articleId``/``postId``) themselves through ``IDEA_MACHINE``; :func:`mark_idea_used` is the
convenience. Every status change sets ``statusChangedAt``.

Time-sensitive ideas (news for the quick-take lane) carry ``expiresAt``: the crawl job's
housekeeping (:func:`expire_ideas`) moves ``new`` and ``snoozed`` ones past it to ``expired``.
Snoozed ideas whose ``snoozedUntil`` passed come back as ``new`` (:func:`wake_snoozed_ideas`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Final

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import Stored, find_docs, read_singleton, update_doc
from blogwriter.domain.settings import FormatPacks, LaneSettings, PillarSettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import IDEA_MACHINE, IdeaStatus

__all__ = [
    "IDEA_ID_PREFIX",
    "POST_FORMAT_SUGGESTIONS",
    "expire_ideas",
    "expires_for",
    "idea_id_for_url",
    "known_formats",
    "known_pillars",
    "mark_idea_used",
    "wake_snoozed_ideas",
]

IDEA_ID_PREFIX: Final = "idea-"
POST_FORMAT_SUGGESTIONS: Final = ("carousel", "text", "image_card", "poll")
"""Post formats an idea may suggest (``Idea.suggestedFormat`` takes ``image_card``'s
underscore: ``domain.signals.FORMAT_ID_PATTERN``)."""

_EXPIRABLE: Final = (IdeaStatus.NEW.value, IdeaStatus.SNOOZED.value)

_logger: Final = logging.getLogger("blogwriter.signals.ideas")
register_log_fields("ideas_expired", "ideas_woken")


def idea_id_for_url(url_sha256: str) -> str:
    """The id of the idea filed for a URL: ``idea-<urlSha256[:24]>`` (a re-triage is a
    ``Conflict``, never a second idea)."""
    if len(url_sha256) != 64:
        raise ValueError("url_sha256 must be a SHA-256 hex digest")
    return f"{IDEA_ID_PREFIX}{url_sha256[:24]}"


def expires_for(*, published_at: str | None, now: datetime, max_age_hours: int) -> str | None:
    """When a time-sensitive idea expires: ``max_age_hours`` (the quick-take window) after the
    news was published (or after ``now`` when the date is unknown). ``None`` when that moment
    has already passed: the quick-take window is over."""
    start = parse_iso(published_at) if published_at is not None else now
    start = min(start, now)
    expires = start + timedelta(hours=max_age_hours)
    if expires <= now:
        return None
    return to_iso(expires)


async def known_pillars(store: Store, *, now: datetime) -> PillarSettings:
    pillars, _ = await read_singleton(store, PillarSettings, now=to_iso(now))
    return pillars


async def known_formats(store: Store, *, now: datetime) -> set[str]:
    """Format ids an idea may suggest: the article format packs plus the post formats."""
    packs, _ = await read_singleton(store, FormatPacks, now=to_iso(now))
    return {pack.id for pack in packs.packs} | set(POST_FORMAT_SUGGESTIONS)


async def quick_take_hours(store: Store, *, now: datetime) -> int:
    lanes, _ = await read_singleton(store, LaneSettings, now=to_iso(now))
    return lanes.quick_take.max_age_hours


async def mark_idea_used(
    store: Store,
    idea_id: str,
    *,
    now: datetime,
    article_id: str | None = None,
    post_id: str | None = None,
) -> Stored[Idea]:
    """Move an idea to ``used`` and record what used it (CAS, retried).

    ``new`` and ``accepted`` ideas become ``used``. An idea that is already ``used`` keeps its
    status; the given ``article_id``/``post_id`` fill fields that are still empty (a replay is a
    no-op). Any other status raises :class:`~blogwriter.domain.states.TransitionError` (409
    ``invalid_transition`` through ``problems()``); ``NotFound`` when there is no such idea.
    """
    stamp = to_iso(now)

    def mutate(idea: Idea) -> Idea | None:
        updates: dict[str, object] = {}
        if article_id is not None and idea.article_id is None:
            updates["article_id"] = article_id
        if post_id is not None and idea.post_id is None:
            updates["post_id"] = post_id
        if idea.status is not IdeaStatus.USED:
            IDEA_MACHINE.transition(idea.status, IdeaStatus.USED)
            updates.update(
                {"status": IdeaStatus.USED, "status_changed_at": stamp, "snoozed_until": None}
            )
        if not updates:
            return None
        return idea.model_copy(update={**updates, "updated_at": stamp})

    return await update_doc(store, Idea, idea_id, mutate)


async def _move(
    store: Store, idea_id: str, *, now: datetime, wanted: IdeaStatus, due_field: str
) -> bool:
    """Move one idea to ``wanted`` if it is still due (re-checked on the fresh copy)."""
    stamp = to_iso(now)
    changed = False

    def mutate(idea: Idea) -> Idea | None:
        nonlocal changed
        changed = False
        due: object = getattr(idea, due_field)
        if not isinstance(due, str) or parse_iso(due) > now:
            return None
        if not IDEA_MACHINE.can(idea.status, wanted):
            return None
        if wanted is IdeaStatus.EXPIRED and idea.status.value not in _EXPIRABLE:
            return None
        changed = True
        return idea.model_copy(
            update={
                "status": wanted,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "snoozed_until": None,
            }
        )

    try:
        await update_doc(store, Idea, idea_id, mutate)
    except NotFound:
        return False
    return changed


async def expire_ideas(store: Store, *, now: datetime) -> int:
    """Expire time-sensitive ideas past ``expiresAt`` (``new``/``snoozed`` -> ``expired``).

    Returns how many changed. Each change is a CAS re-check, so an idea the owner accepted in
    the meantime is left alone.
    """
    stamp = to_iso(now)
    due = await find_docs(
        store,
        Idea,
        [Filter("status", "in", list(_EXPIRABLE)), Filter("expiresAt", "<=", stamp)],
    )
    count = 0
    for stored in due:
        if await _move(
            store, stored.doc.id, now=now, wanted=IdeaStatus.EXPIRED, due_field="expires_at"
        ):
            count += 1
    if count:
        _logger.info("signals_ideas_expired", extra={"ideas_expired": count})
    return count


async def wake_snoozed_ideas(store: Store, *, now: datetime) -> int:
    """Snoozed ideas whose ``snoozedUntil`` passed come back as ``new``. Returns the count."""
    stamp = to_iso(now)
    due = await find_docs(
        store,
        Idea,
        [Filter("status", "==", IdeaStatus.SNOOZED.value), Filter("snoozedUntil", "<=", stamp)],
    )
    count = 0
    for stored in due:
        if await _move(
            store, stored.doc.id, now=now, wanted=IdeaStatus.NEW, due_field="snoozed_until"
        ):
            count += 1
    if count:
        _logger.info("signals_ideas_woken", extra={"ideas_woken": count})
    return count
