"""The ``search`` step of ``signals.scout``: weekly scouting with ``web_search``.

One QUICK_SEARCH (``gpt-6.2-sol``, public, ``web_search``) per pillar, with the pillar's research
allowlist as ``allowed_domains``, and one per active ``scout_query`` source (its pillar's
allowlist, or the open web when it has no pillar). The candidates are the answer's citations
(URL and title): the answer text itself is discarded.

Blocklist, before anything leaves the app: the pillar names, topics and allowlists that make up
the per-pillar searches are checked as a whole (a hit fails the job ``blocklist_hit``); each
scout query is checked on its own (a hit skips that search). After each search the queries the
model actually issued are audited: a hit drops that search's citations and raises a
``quarantined`` alert, because a confidential term may have reached the search provider.

Signals dedupe citations across weeks (partition: the source id, or ``scout-<pillarId>``), then
the ``triage`` step files ideas with origin ``scout``. Checkpoints hold the finished search keys
and the candidates (title, URL, publisher and ids only).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, cast
from urllib.parse import urlsplit

from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Citation, Message, Request, Sensitivity, Task, WebSearchTool
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import check_https_url, to_iso
from blogwriter.domain.confidentiality import ConfidentialText, blocklist_hit, check_texts
from blogwriter.domain.docstore import find_docs, read_singleton, update_doc
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.ports import ModelRunner, Services
from blogwriter.domain.settings import Blocklist, PillarSettings
from blogwriter.domain.signals import Source
from blogwriter.domain.states import SourceStatus
from blogwriter.notify import emit_alert
from blogwriter.signals._text import clean_text, prompt_json
from blogwriter.signals.candidates import (
    Candidate,
    candidates_from_json,
    candidates_to_json,
    record_signals,
    scout_partition,
)
from blogwriter.signals.feeds import MAX_PUBLISHER_CHARS, MAX_TITLE_CHARS, FeedItem
from blogwriter.signals.prompts import SCOUT_PROMPT, load_prompt
from blogwriter.signals.triage import check_prompt_settings

__all__ = [
    "MAX_CITATIONS_PER_SEARCH",
    "SCOUT_MAX_TOOL_CALLS",
    "SCOUT_WINDOW_DAYS",
    "ScoutProgress",
    "ScoutSearch",
    "citation_items",
    "plan_searches",
    "scout_request",
    "scout_searches",
]

SCOUT_MAX_TOOL_CALLS: Final = 4
SCOUT_WINDOW_DAYS: Final = 8
"""Weekly scouting looks back a little more than a week."""
MAX_CITATIONS_PER_SEARCH: Final = 12

_logger: Final = logging.getLogger("blogwriter.signals.scout")
register_log_fields("search", "citations", "new_items")


@dataclass(frozen=True, slots=True)
class ScoutSearch:
    """One search: its checkpoint key, the topic text, the allowlist and where signals go."""

    key: str
    topic: str
    allowed_domains: tuple[str, ...]
    partition: str
    pillar: str | None
    source_id: str | None = None


@dataclass
class ScoutProgress:
    """The search step's checkpoint and output."""

    done: list[str] = field(default_factory=list[str])
    items: list[Candidate] = field(default_factory=list[Candidate])
    searches: int = 0
    blocked: int = 0
    refused: int = 0
    new: int = 0
    requeued: int = 0

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "done": list(self.done),
            "items": candidates_to_json(self.items),
            "searches": self.searches,
            "blocked": self.blocked,
            "refused": self.refused,
            "newItems": self.new,
            "requeued": self.requeued,
        }

    @classmethod
    def from_json(cls, value: JsonValue) -> ScoutProgress:
        if not isinstance(value, dict):
            return cls()
        body = cast(Mapping[str, object], value)
        done = body.get("done")
        try:
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
            searches=count("searches"),
            blocked=count("blocked"),
            refused=count("refused"),
            new=count("newItems"),
            requeued=count("requeued"),
        )


def _pillar_topic(name: str, topics: str) -> str:
    topics = " ".join(topics.split())
    return f"{name}: {topics}" if topics else name


async def plan_searches(
    store: Store, *, blocklist: Blocklist, now: datetime
) -> tuple[list[ScoutSearch], int]:
    """The searches of this week and how many scout queries the blocklist refused.

    The pillar texts are checked as a whole (``JobFailed`` ``blocklist_hit`` or
    ``blocklist_not_configured``); a scout query that matches the blocklist is skipped.
    """
    pillars, _ = await read_singleton(store, PillarSettings, now=to_iso(now))
    texts: dict[str, str] = {}
    for index, pillar in enumerate(pillars.pillars):
        texts[f"pillars[{index}].id"] = pillar.id
        texts[f"pillars[{index}].topic"] = _pillar_topic(pillar.name, pillar.topics)
        for position, domain in enumerate(pillar.research_domains):
            texts[f"pillars[{index}].researchDomains[{position}]"] = domain
    check_prompt_settings(texts, blocklist, what="A pillar setting")
    by_id = {pillar.id: pillar for pillar in pillars.pillars}
    searches = [
        ScoutSearch(
            key=f"pillar:{pillar.id}",
            topic=_pillar_topic(pillar.name, pillar.topics),
            allowed_domains=tuple(pillar.research_domains),
            partition=scout_partition(pillar.id),
            pillar=pillar.id,
        )
        for pillar in pillars.pillars
    ]
    sources = await find_docs(
        store,
        Source,
        [Filter("type", "==", "scout_query"), Filter("status", "==", SourceStatus.ACTIVE.value)],
    )
    blocked = 0
    for stored in sorted(sources, key=lambda item: item.doc.id):
        source = stored.doc
        if source.query is None:
            continue
        try:
            check_texts({"query": source.query}, blocklist)
        except ConfidentialText:
            blocked += 1
            continue
        pillar = by_id.get(source.pillar) if source.pillar is not None else None
        searches.append(
            ScoutSearch(
                key=f"source:{source.id}",
                topic=source.query,
                allowed_domains=tuple(pillar.research_domains) if pillar is not None else (),
                partition=source.id,
                pillar=pillar.id if pillar is not None else None,
                source_id=source.id,
            )
        )
    return searches, blocked


def scout_request(search: ScoutSearch, *, now: datetime) -> Request:
    """The QUICK_SEARCH request of one search: public, ``web_search`` with the allowlist."""
    since = now - timedelta(days=SCOUT_WINDOW_DAYS)
    message = (
        f"Today is {now:%Y-%m-%d} (UTC). Window: the last {SCOUT_WINDOW_DAYS} days, "
        f"since {since:%Y-%m-%d}.\n\n<topic>\n{prompt_json(search.topic)}\n</topic>"
    )
    return Request(
        system=load_prompt(SCOUT_PROMPT),
        messages=(Message.user(message),),
        sensitivity=Sensitivity.PUBLIC,
        tools=(WebSearchTool(allowed_domains=search.allowed_domains),),
        max_tool_calls=SCOUT_MAX_TOOL_CALLS,
    )


def citation_items(citations: tuple[Citation, ...]) -> list[FeedItem]:
    """Feed items from search citations: absolute ``https`` URLs with a title, publisher = host."""
    items: list[FeedItem] = []
    for citation in citations:
        try:
            url = check_https_url(citation.url)
        except ValueError:
            continue
        title = clean_text(citation.title, max_chars=MAX_TITLE_CHARS)
        if title is None:
            continue
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        publisher = clean_text(host, max_chars=MAX_PUBLISHER_CHARS)
        items.append(FeedItem(url=url, title=title, published_at=None, publisher=publisher))
        if len(items) >= MAX_CITATIONS_PER_SEARCH:
            break
    return items


async def _touch_source(
    store: Store, search: ScoutSearch, *, new_items: int, now: datetime
) -> None:
    if search.source_id is None:
        return
    stamp = to_iso(now)

    def mutate(source: Source) -> Source | None:
        if source.query != search.topic:
            return None  # the owner changed it meanwhile
        return source.model_copy(
            update={
                "last_crawled_at": stamp,
                "last_success_at": stamp,
                "consecutive_failures": 0,
                "last_error": None,
                "items_seen": source.items_seen + new_items,
                "updated_at": stamp,
            }
        )

    try:
        await update_doc(store, Source, search.source_id, mutate)
    except NotFound:
        return


async def scout_searches(
    services: Services,
    ai: ModelRunner,
    *,
    blocklist: Blocklist,
    job_origin: Origin,
    job_id: str | None,
    progress: ScoutProgress,
    save: Callable[[ScoutProgress], Awaitable[None]],
) -> ScoutProgress:
    """Run every search not yet in ``progress.done`` (see the module docs)."""
    store = services.store
    searches, blocked = await plan_searches(store, blocklist=blocklist, now=services.clock.now())
    if not progress.done:
        progress.blocked += blocked
    for search in searches:
        if search.key in progress.done:
            continue
        now = services.clock.now()
        with defer_on_brake(now):
            result = await ai.run(
                Task.QUICK_SEARCH, scout_request(search, now=now), origin=job_origin, job_id=job_id
            )
        progress.searches += 1
        items: list[FeedItem] = []
        if result.refused:
            progress.refused += 1
        elif result.completion.search_queries and blocklist_hit(
            list(result.completion.search_queries), blocklist
        ):
            progress.blocked += 1
            emit_alert("quarantined", job_id)
            _logger.warning("signals_scout_query_blocked", extra={"search": search.key})
        else:
            items = citation_items(result.completion.citations)
        record = await record_signals(
            store,
            items,
            partition=search.partition,
            now=now,
            source_id=search.source_id,
            pillar=search.pillar,
            blocklist=blocklist,
        )
        progress.blocked += record.blocked
        progress.items.extend(record.candidates)
        progress.new += record.new
        progress.requeued += record.requeued
        progress.done.append(search.key)
        await save(progress)
        await _touch_source(store, search, new_items=record.new, now=now)
        _logger.info(
            "signals_scout_search",
            extra={"search": search.key, "citations": len(items), "new_items": record.new},
        )
    if not searches:
        _logger.info("signals_scout_nothing_to_search")
    return progress
