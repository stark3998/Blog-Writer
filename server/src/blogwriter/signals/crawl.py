"""The ``fetch`` step of ``signals.crawl``: conditional GETs of every due feed source.

For each feed source, least recently crawled first:

1. A conditional ``GET`` through the egress helper (``services.egress``: https only, SSRF-safe,
   5 MiB and 15 s caps) with ``If-None-Match``/``If-Modified-Since`` from the last success.
   ``304`` means nothing changed.
2. Parse the bytes in a thread (:func:`blogwriter.signals.feeds.parse_feed_document`), drop items
   older than ``MAX_AGE_DAYS`` (:func:`~blogwriter.signals.feeds.within_age`).
3. Record signals (:func:`~blogwriter.signals.candidates.record_signals`): new items and
   untriaged ones become candidates; an item matching the blocklist is decided on the spot and
   never checkpointed.
4. Checkpoint the candidates (title, URL, publisher, date and ids only), then update the source's
   health: ``failing`` after ``FAILING_AFTER`` consecutive errors, back to ``active`` on the next
   success. Failing sources are retried once a day, so a site that comes back recovers without
   the owner.

A run stops taking new sources once :data:`MAX_CANDIDATES_PER_RUN` candidates are waiting (the
job document stays small); untouched sources keep their validators and are first in line next
time. Logs carry source ids, counts and reason codes, never URLs or titles.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, cast

from pydantic import JsonValue

from blogwriter.core.egress import EgressError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import check_https_url, parse_iso, to_iso
from blogwriter.domain.docstore import find_docs, update_doc
from blogwriter.domain.ports import HttpGetter
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.signals import Source
from blogwriter.domain.states import SourceStatus
from blogwriter.signals.candidates import (
    Candidate,
    candidates_from_json,
    candidates_to_json,
    record_signals,
)
from blogwriter.signals.feeds import FeedItem, FeedParseError, parse_feed_document, within_age
from blogwriter.signals.settings import SignalsSettings

__all__ = [
    "FAILING_RETRY",
    "FEED_ACCEPT",
    "MAX_CANDIDATES_PER_RUN",
    "CrawlProgress",
    "FetchOutcome",
    "conditional_headers",
    "crawl_feeds",
    "fetch_source",
    "sources_due",
    "update_source_health",
]

FEED_ACCEPT: Final = (
    "application/rss+xml, application/atom+xml, application/feed+json, "
    "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.1"
)
FAILING_RETRY: Final = timedelta(hours=24)
"""A failing source is tried again once this long has passed since its last attempt."""
MAX_CANDIDATES_PER_RUN: Final = 400
_HEADER_VALUE: Final = re.compile(r"[\x20-\x7e]{1,300}")
_MAX_ETAG: Final = 300
_MAX_LAST_MODIFIED: Final = 100

_logger: Final = logging.getLogger("blogwriter.signals.crawl")
register_log_fields("source_id", "new_items", "requeued_items", "sources", "candidates")


@dataclass
class CrawlProgress:
    """The fetch step's checkpoint and output: sources finished and candidates so far."""

    done: list[str] = field(default_factory=list[str])
    items: list[Candidate] = field(default_factory=list[Candidate])
    fetched: int = 0
    not_modified: int = 0
    failed: int = 0
    new: int = 0
    requeued: int = 0
    blocked: int = 0
    left_for_next_run: int = 0

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "done": list(self.done),
            "items": candidates_to_json(self.items),
            "fetched": self.fetched,
            "notModified": self.not_modified,
            "failed": self.failed,
            "newItems": self.new,
            "requeued": self.requeued,
            "blocked": self.blocked,
            "leftForNextRun": self.left_for_next_run,
        }

    @classmethod
    def from_json(cls, value: JsonValue) -> CrawlProgress:
        """A checkpoint read back (anything malformed starts over)."""
        if not isinstance(value, dict):
            return cls()
        body = cast(Mapping[str, object], value)
        try:
            done = body.get("done")
            items = candidates_from_json(body.get("items"))
        except ValueError:
            return cls()
        if not isinstance(done, list):
            return cls()

        def count(key: str) -> int:
            item = body.get(key)
            return item if isinstance(item, int) and not isinstance(item, bool) else 0

        return cls(
            done=[str(item) for item in cast(list[object], done)],
            items=items,
            fetched=count("fetched"),
            not_modified=count("notModified"),
            failed=count("failed"),
            new=count("newItems"),
            requeued=count("requeued"),
            blocked=count("blocked"),
        )


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """One source's fetch: ``ok`` with items (or ``not_modified``), or an ``error`` code."""

    ok: bool
    not_modified: bool = False
    items: tuple[FeedItem, ...] = ()
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None

    @classmethod
    def failure(cls, code: str) -> FetchOutcome:
        return cls(ok=False, error=code)


def _validator(value: str | None, limit: int) -> str | None:
    """A cache validator worth storing and sending back: printable ASCII, not too long."""
    if value is None or len(value) > limit or not _HEADER_VALUE.fullmatch(value):
        return None
    return value


def conditional_headers(source: Source) -> dict[str, str]:
    """``Accept`` plus ``If-None-Match``/``If-Modified-Since`` from the source's last success."""
    headers = {"Accept": FEED_ACCEPT}
    etag = _validator(source.etag, _MAX_ETAG)
    if etag is not None:
        headers["If-None-Match"] = etag
    modified = _validator(source.last_modified, _MAX_LAST_MODIFIED)
    if modified is not None:
        headers["If-Modified-Since"] = modified
    return headers


async def fetch_source(
    egress: HttpGetter, source: Source, *, settings: SignalsSettings, now: datetime
) -> FetchOutcome:
    """Download and parse one feed source. Never raises for a bad feed: returns its code
    (egress reasons such as ``timeout`` or ``scheme_not_allowed``, ``http_<status>``,
    ``not_a_feed``, ``insecure_url``)."""
    if source.url is None:
        return FetchOutcome.failure("no_url")
    try:
        url = check_https_url(source.url)
    except ValueError:
        return FetchOutcome.failure("insecure_url")
    try:
        response = await egress.get(url, headers=conditional_headers(source))
    except EgressError as exc:
        return FetchOutcome.failure(exc.reason)
    if response.status == 304:
        return FetchOutcome(ok=True, not_modified=True)
    if not 200 <= response.status < 300:
        return FetchOutcome.failure(f"http_{response.status}")
    try:
        document = await asyncio.to_thread(
            parse_feed_document,
            response.content,
            base_url=response.url,
            max_items=settings.max_items_per_feed,
            content_type=response.content_type,
        )
    except (FeedParseError, ValueError):
        return FetchOutcome.failure("not_a_feed")
    items = tuple(
        recent
        for item in document.items
        if (recent := within_age(item, now=now, max_age_days=settings.max_age_days)) is not None
    )
    return FetchOutcome(
        ok=True,
        items=items,
        etag=_validator(response.etag, _MAX_ETAG),
        last_modified=_validator(response.last_modified, _MAX_LAST_MODIFIED),
    )


async def update_source_health(
    store: Store,
    source_id: str,
    *,
    fetched_url: str,
    outcome: FetchOutcome,
    new_items: int,
    settings: SignalsSettings,
    now: datetime,
) -> None:
    """Record a crawl on the source (CAS, retried). Left alone when the owner changed its URL or
    deleted it meanwhile; a paused source keeps its status."""
    stamp = to_iso(now)

    def mutate(source: Source) -> Source | None:
        if source.url != fetched_url:
            return None
        updates: dict[str, object] = {"last_crawled_at": stamp, "updated_at": stamp}
        if outcome.ok:
            updates.update(
                last_success_at=stamp,
                consecutive_failures=0,
                last_error=None,
                items_seen=source.items_seen + new_items,
            )
            if not outcome.not_modified:
                updates.update(etag=outcome.etag, last_modified=outcome.last_modified)
            if source.status is SourceStatus.FAILING:
                updates.update(status=SourceStatus.ACTIVE, status_changed_at=stamp)
        else:
            failures = source.consecutive_failures + 1
            updates.update(consecutive_failures=failures, last_error=outcome.error)
            if source.status is SourceStatus.ACTIVE and failures >= settings.failing_after:
                updates.update(status=SourceStatus.FAILING, status_changed_at=stamp)
        return source.model_copy(update=updates)

    try:
        await update_doc(store, Source, source_id, mutate)
    except NotFound:
        return


def sources_due(sources: list[Source], *, now: datetime) -> list[Source]:
    """Feed sources to crawl now, least recently crawled first: every ``active`` one, and
    ``failing`` ones whose last attempt is :data:`FAILING_RETRY` old."""
    due: list[Source] = []
    for source in sources:
        if source.type != "feed" or source.status is SourceStatus.PAUSED:
            continue
        if (
            source.status is SourceStatus.FAILING
            and source.last_crawled_at is not None
            and now - parse_iso(source.last_crawled_at) < FAILING_RETRY
        ):
            continue
        due.append(source)
    return sorted(due, key=lambda source: (source.last_crawled_at or "", source.id))


async def crawl_feeds(
    store: Store,
    egress: HttpGetter,
    *,
    settings: SignalsSettings,
    now: datetime,
    progress: CrawlProgress,
    save: Callable[[CrawlProgress], Awaitable[None]],
    blocklist: Blocklist | None = None,
) -> CrawlProgress:
    """Crawl every due feed source not yet in ``progress.done`` (see the module docs)."""
    stored = await find_docs(
        store,
        Source,
        [
            Filter("type", "==", "feed"),
            Filter("status", "in", [SourceStatus.ACTIVE.value, SourceStatus.FAILING.value]),
        ],
    )
    due = [
        source
        for source in sources_due([item.doc for item in stored], now=now)
        if source.id not in progress.done
    ]
    for index, source in enumerate(due):
        if len(progress.items) >= MAX_CANDIDATES_PER_RUN:
            progress.left_for_next_run = len(due) - index
            break
        outcome = await fetch_source(egress, source, settings=settings, now=now)
        new_items = 0
        if outcome.ok:
            if outcome.not_modified:
                progress.not_modified += 1
            else:
                progress.fetched += 1
            record = await record_signals(
                store,
                outcome.items,
                partition=source.id,
                now=now,
                source_id=source.id,
                pillar=source.pillar,
                blocklist=blocklist,
            )
            new_items = record.new
            progress.items.extend(record.candidates)
            progress.new += record.new
            progress.requeued += record.requeued
            progress.blocked += record.blocked
        else:
            progress.failed += 1
        progress.done.append(source.id)
        await save(progress)
        await update_source_health(
            store,
            source.id,
            fetched_url=cast(str, source.url),
            outcome=outcome,
            new_items=new_items,
            settings=settings,
            now=now,
        )
        _logger.info(
            "signals_source_crawled",
            extra={
                "source_id": source.id,
                "reason": outcome.error or ("not_modified" if outcome.not_modified else "ok"),
                "new_items": new_items,
            },
        )
    _logger.info(
        "signals_crawl_fetched",
        extra={"sources": len(progress.done), "candidates": len(progress.items)},
    )
    return progress
