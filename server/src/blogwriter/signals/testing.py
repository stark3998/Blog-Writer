"""Test helpers for code that uses the signals package (no network, no Azure, no real time).

- :class:`RecordingHttp`: an ``HttpGetter`` with canned replies per URL that records the
  request headers (conditional GETs), the egress fake ``domain.testing.StaticHttp`` does not.
- :func:`rss_bytes`: a small RSS 2.0 document.
- :class:`SignalsSeed`: seeds the blocklist, autonomy and sources.
- :func:`verdict` / :func:`triage_output`: scripted TRIAGE replies.
- :func:`run_signals_job`: runs a signals job on a real ``JobRunner`` until it settles.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Final
from xml.sax.saxutils import escape

from pydantic import JsonValue

from blogwriter.core.egress import EgressError, EgressResponse
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, upsert_doc
from blogwriter.domain.ports import services_installed
from blogwriter.domain.settings import AutonomySettings, Blocklist, BlocklistTerm
from blogwriter.domain.signals import Source
from blogwriter.domain.states import SourceStatus
from blogwriter.domain.testing import FakeServices
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.signals.jobs import register_jobs
from blogwriter.signals.settings import SignalsSettings
from blogwriter.signals.triage import TriageOutput, TriageVerdict

__all__ = [
    "T0",
    "FeedEntry",
    "RecordingHttp",
    "Reply",
    "SignalsSeed",
    "rss_bytes",
    "run_signals_job",
    "settings_for_tests",
    "triage_output",
    "verdict",
]

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
"""The time of ``domain.testing.FakeClock`` by default."""


def settings_for_tests(**overrides: object) -> SignalsSettings:
    """Settings with the contract defaults (``failing_after`` 3 to keep tests short)."""
    values: dict[str, object] = {
        "max_items_per_feed": 50,
        "max_age_days": 14,
        "triage_batch": 20,
        "min_relevance": 0.5,
        "failing_after": 3,
        **overrides,
    }
    return SignalsSettings.model_validate(values)


# --------------------------------------------------------------------------------------------
# Egress
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    content: bytes = b""
    status: int = 200
    content_type: str | None = "application/rss+xml"
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    error: EgressError | None = None


@dataclass
class RecordingHttp:
    """An ``HttpGetter`` with queued replies per URL (the last one repeats); unknown URLs 404."""

    replies: dict[str, list[Reply]] = field(default_factory=dict[str, list[Reply]])
    requests: list[tuple[str, dict[str, str]]] = field(
        default_factory=list[tuple[str, dict[str, str]]]
    )

    def add(
        self,
        url: str,
        content: bytes = b"",
        *,
        status: int = 200,
        content_type: str | None = "application/rss+xml",
        etag: str | None = None,
        last_modified: str | None = None,
        final_url: str | None = None,
        error: EgressError | None = None,
    ) -> None:
        self.replies.setdefault(url, []).append(
            Reply(
                content=content,
                status=status,
                content_type=content_type,
                etag=etag,
                last_modified=last_modified,
                final_url=final_url,
                error=error,
            )
        )

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        await asyncio.sleep(0)
        if allow_http:
            raise AssertionError("signals never allows plain http")
        self.requests.append((url, dict(headers or {})))
        queue = self.replies.get(url)
        if not queue:
            return EgressResponse(url=url, status=404, content_type="text/plain", content=b"")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if reply.error is not None:
            raise reply.error
        return EgressResponse(
            url=reply.final_url or url,
            status=reply.status,
            content_type=reply.content_type,
            content=reply.content,
            etag=reply.etag,
            last_modified=reply.last_modified,
        )

    def headers_for(self, url: str) -> list[dict[str, str]]:
        return [headers for requested, headers in self.requests if requested == url]


# --------------------------------------------------------------------------------------------
# Feeds and seeding
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedEntry:
    title: str
    link: str
    published: datetime | None = None
    summary: str | None = None


def rss_bytes(entries: Sequence[FeedEntry], *, title: str = "Example feed") -> bytes:
    """An RSS 2.0 document with ``entries`` (titles and links XML-escaped)."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{escape(title)}</title>",
    ]
    for entry in entries:
        parts.append("<item>")
        parts.append(f"<title>{escape(entry.title)}</title>")
        parts.append(f"<link>{escape(entry.link)}</link>")
        if entry.published is not None:
            parts.append(f"<pubDate>{format_datetime(entry.published, usegmt=True)}</pubDate>")
        if entry.summary is not None:
            parts.append(f"<description>{escape(entry.summary)}</description>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "\n".join(parts).encode("utf-8")


class SignalsSeed:
    """Seeds settings and sources into a fake services bundle (timestamps at :data:`T0`)."""

    def __init__(self, services: FakeServices) -> None:
        self.services = services

    async def blocklist(self, *terms: str, configured: bool = True) -> None:
        stamp = to_iso(T0)
        doc = Blocklist.model_validate(
            {
                "id": Blocklist.SINGLETON_ID,
                "created_at": stamp,
                "updated_at": stamp,
                "configured": configured,
                "terms": [
                    BlocklistTerm(id=f"t-{index}", term=term, category="client")
                    for index, term in enumerate(terms, start=1)
                ],
            }
        )
        await upsert_doc(self.services.store, doc)

    async def paused(self) -> None:
        stamp = to_iso(T0)
        doc = AutonomySettings.default(stamp).model_copy(
            update={"paused": True, "paused_at": stamp}
        )
        await upsert_doc(self.services.store, doc)

    async def source(
        self,
        url: str = "https://news.example.org/feed.xml",
        *,
        title: str = "A feed",
        pillar: str | None = "agents",
        status: SourceStatus = SourceStatus.ACTIVE,
        **fields: object,
    ) -> Stored[Source]:
        stamp = to_iso(T0)
        doc = Source.model_validate(
            {
                "id": Source.new_id(now=T0),
                "type": "feed",
                "title": title,
                "url": url,
                "pillar": pillar,
                "status": status,
                "status_changed_at": stamp,
                "created_at": stamp,
                "updated_at": stamp,
                **fields,
            }
        )
        return await create_doc(self.services.store, doc)

    async def scout(
        self, query: str, *, pillar: str | None = "agents", **fields: object
    ) -> Stored[Source]:
        stamp = to_iso(T0)
        doc = Source.model_validate(
            {
                "id": Source.new_id(now=T0),
                "type": "scout_query",
                "title": "A scouting query",
                "query": query,
                "pillar": pillar,
                "status_changed_at": stamp,
                "created_at": stamp,
                "updated_at": stamp,
                **fields,
            }
        )
        return await create_doc(self.services.store, doc)


# --------------------------------------------------------------------------------------------
# Triage replies and jobs
# --------------------------------------------------------------------------------------------


def verdict(item: int, relevance: float = 0.8, **fields: object) -> TriageVerdict:
    """A TRIAGE verdict for item ``item`` (an on-pillar flagship by default)."""
    values: dict[str, object] = {
        "item": item,
        "pillar": "agents",
        "relevance": relevance,
        "novelty": 0.6,
        "lane": "flagship",
        "format": "news-analysis",
        "angle": "Per-call token scopes stop a compromised MCP server from replaying them.",
        "time_sensitive": False,
        "reasons": "A specification change with concrete fields.",
        **fields,
    }
    return TriageVerdict.model_validate(values)


def triage_output(*verdicts: TriageVerdict) -> TriageOutput:
    return TriageOutput(verdicts=list(verdicts))


async def run_signals_job(
    services: FakeServices,
    job_type: str,
    input: JsonValue = None,
    *,
    settings: SignalsSettings | None = None,
    job_id: str | None = None,
    within: float = 10.0,
) -> Job:
    """Enqueue ``job_type`` and run it on a real ``JobRunner`` over ``services.store`` until it
    finishes or goes back to the queue after an error; return the job."""
    registry = JobRegistry()
    register_jobs(registry, settings=settings or settings_for_tests())
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        job = await services.jobs.enqueue(job_type, input, job_id=job_id)

        async def settled() -> bool:
            current = await services.jobs.get(job.id)
            return current.is_terminal or (current.status == "queued" and current.error is not None)

        await runner.start()
        try:
            await eventually(settled, within=within)
        finally:
            await runner.stop()
    return await services.jobs.get(job.id)
