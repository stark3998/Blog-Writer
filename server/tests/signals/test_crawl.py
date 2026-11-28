"""``signals.crawl`` on a real job runner: conditional GETs, dedupe, health, pause, fail closed."""

from __future__ import annotations

import ipaddress
import json
from datetime import timedelta

import httpx
import pytest

from blogwriter.ai.types import Task
from blogwriter.core.egress import EgressTimeoutError, IPAddress, PinnedTarget, SafeHttpClient
from blogwriter.db import Filter
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import create_doc, find_docs, read_doc
from blogwriter.domain.hashing import url_sha256
from blogwriter.domain.jobtypes import ENTITY_CHANGED, SIGNALS_CRAWL
from blogwriter.domain.signals import Idea, Signal, Source
from blogwriter.domain.states import IdeaStatus, SourceStatus
from blogwriter.domain.testing import FakeServices, ScriptedReply, sample_document
from blogwriter.signals import crawl as crawl_module
from blogwriter.signals.crawl import CrawlProgress, crawl_feeds
from blogwriter.signals.settings import SignalsSettings
from blogwriter.signals.testing import (
    T0,
    FeedEntry,
    RecordingHttp,
    SignalsSeed,
    rss_bytes,
    run_signals_job,
    triage_output,
    verdict,
)
from blogwriter.signals.triage import TriageOutput

FEED = "https://news.example.org/feed.xml"
OTHER = "https://other.example.net/rss"
ENTRIES = [
    FeedEntry(
        "MCP spec adds per-tool token scopes", "https://news.example.org/a", T0 - timedelta(hours=1)
    ),
    FeedEntry(
        "Vendor raises a funding round", "https://news.example.org/b", T0 - timedelta(hours=2)
    ),
    FeedEntry(
        "CVE in an OAuth proxy, exploited", "https://news.example.org/c", T0 - timedelta(hours=3)
    ),
]


def items_section(text: str) -> str:
    """The ``<items>`` section of a TRIAGE request (the inbox section lists earlier ideas)."""
    return text.split("<items>", 1)[1]


async def signals_of(services: FakeServices, partition: str) -> list[Signal]:
    return [s.doc for s in await find_docs(services.store, Signal, partition_key=partition)]


async def ideas(services: FakeServices) -> list[Idea]:
    return [s.doc for s in await find_docs(services.store, Idea)]


async def source_doc(services: FakeServices, source_id: str) -> Source:
    return (await read_doc(services.store, Source, source_id)).doc


async def test_crawl_files_ideas_and_keeps_only_hashes(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    http.add(FEED, rss_bytes(ENTRIES), etag='"v1"', last_modified="Wed, 25 Nov 2026 10:00:00 GMT")
    assert services.ai is not None
    services.ai.add(
        Task.TRIAGE,
        triage_output(
            verdict(1, 0.9),
            verdict(2, 0.1, pillar=None, lane=None, format=None, angle=None),
            verdict(3, 0.7, time_sensitive=True, lane="quick_take", format="text"),
        ),
    )
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded", job.error
    assert job.result is not None
    assert isinstance(job.result, dict)
    assert job.result["ideas"] == 2
    assert job.result["belowThreshold"] == 1

    filed = {idea.url: idea for idea in await ideas(services)}
    assert set(filed) == {"https://news.example.org/a", "https://news.example.org/c"}
    first = filed["https://news.example.org/a"]
    digest = url_sha256(first.url or "")
    assert first.id == f"idea-{digest[:24]}"
    assert first.origin == "feed"
    assert first.status is IdeaStatus.NEW
    assert first.source_id == source.id
    assert first.signal_id == Signal.id_for(source.id, digest)
    assert first.publisher == "Example feed"
    assert first.triage is not None
    assert first.triage.served_by is not None
    assert first.triage.served_by.prompt_version == "triage.v1"
    news = filed["https://news.example.org/c"]
    assert news.time_sensitive
    assert news.expires_at == to_iso(T0 - timedelta(hours=3) + timedelta(hours=72))
    assert news.suggested_lane == "quick_take"
    assert news.suggested_format == "text"
    # One batch, so the ideas carry increasing stamps from one distinct_stamps run.
    assert first.created_at < news.created_at

    stored = await signals_of(services, source.id)
    assert len(stored) == 3
    assert all(signal.triaged for signal in stored)
    assert sorted(signal.idea_id or "" for signal in stored) == sorted(["", first.id, news.id])
    raw = json.dumps([s.to_document() for s in stored])
    for entry in ENTRIES:
        assert entry.title not in raw
        assert entry.link not in raw
        assert "news.example.org" not in raw

    after = await source_doc(services, source.id)
    assert after.etag == '"v1"'
    assert after.last_modified == "Wed, 25 Nov 2026 10:00:00 GMT"
    assert after.last_success_at == to_iso(T0)
    assert after.items_seen == 3
    assert after.consecutive_failures == 0
    changed = [event for event in job.events if event.type == ENTITY_CHANGED]
    assert [event.data for event in changed] == [
        {"entity": "source", "id": source.id},
        {"entity": "idea", "id": news.id},
    ]

    (call,) = services.ai.calls_for(Task.TRIAGE)
    assert call.origin == "background"
    assert call.job_id == job.id
    assert call.request.tools == ()
    assert call.request.output_schema is TriageOutput
    assert call.request.sensitivity.value == "public"


async def test_conditional_get_and_304(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    http.add(
        FEED, rss_bytes(ENTRIES[:1]), etag='W/"abc"', last_modified="Wed, 25 Nov 2026 10:00:00 GMT"
    )
    http.add(FEED, b"", status=304)
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    services.clock.advance(timedelta(hours=6))
    second = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert second.status == "succeeded"
    first_headers, second_headers = http.headers_for(FEED)
    assert "If-None-Match" not in first_headers
    assert second_headers["If-None-Match"] == 'W/"abc"'
    assert second_headers["If-Modified-Since"] == "Wed, 25 Nov 2026 10:00:00 GMT"
    assert "rss" in second_headers["Accept"]
    assert len(services.ai.calls_for(Task.TRIAGE)) == 1
    after = await source_doc(services, source.id)
    assert after.etag == 'W/"abc"'  # a 304 keeps the validators
    assert after.last_success_at == to_iso(T0 + timedelta(hours=6))
    fetch = second.step("fetch")
    assert fetch is not None
    assert isinstance(fetch.output, dict)
    assert fetch.output["notModified"] == 1


async def test_dedupe_across_runs(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    http.add(FEED, rss_bytes(ENTRIES[:2]))
    http.add(
        FEED, rss_bytes([FeedEntry("A new one", "https://news.example.org/d", T0), *ENTRIES[:2]])
    )
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1), verdict(2)))
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    second = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert second.status == "succeeded"
    calls = services.ai.calls_for(Task.TRIAGE)
    assert len(calls) == 2
    assert "A new one" in items_section(calls[1].request.messages[0].text)
    assert "MCP spec" not in items_section(calls[1].request.messages[0].text)
    assert len(await signals_of(services, source.id)) == 3
    assert len(await ideas(services)) == 3
    third = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert third.status == "succeeded"
    assert len(services.ai.calls_for(Task.TRIAGE)) == 2  # nothing new: no model call


async def test_one_url_in_two_feeds_is_triaged_once(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    first = (await seed.source(FEED)).doc
    second = (await seed.source(OTHER, title="Another feed")).doc
    shared = FeedEntry("Shared story", "https://shared.example.com/story?utm_source=x", T0)
    http.add(FEED, rss_bytes([shared]))
    http.add(OTHER, rss_bytes([FeedEntry("Shared story", "https://shared.example.com/story", T0)]))
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded"
    (call,) = services.ai.calls_for(Task.TRIAGE)
    assert call.request.messages[0].text.count("Shared story") == 1
    (idea,) = await ideas(services)
    for source in (first, second):
        (signal,) = await signals_of(services, source.id)
        assert signal.triaged
        assert signal.idea_id == idea.id


async def test_items_older_than_max_age_are_dropped(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    http.add(
        FEED,
        rss_bytes(
            [
                FeedEntry("Fresh", "https://news.example.org/fresh", T0 - timedelta(days=1)),
                FeedEntry("Stale", "https://news.example.org/stale", T0 - timedelta(days=20)),
                FeedEntry("Undated", "https://news.example.org/undated"),
            ]
        ),
    )
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1), verdict(2)))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    (call,) = services.ai.calls_for(Task.TRIAGE)
    assert "Stale" not in call.request.messages[0].text
    assert len(await signals_of(services, source.id)) == 2


async def test_source_health_failing_and_recovery(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    for _ in range(3):
        http.add(FEED, b"oops", status=503, content_type="text/plain")
    http.add(FEED, b"", error=EgressTimeoutError("timeout"))
    http.add(FEED, rss_bytes([]))
    for run in range(3):
        job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
        assert job.status == "succeeded"  # a bad feed is the source's problem, not the job's
        current = await source_doc(services, source.id)
        assert current.consecutive_failures == run + 1
        assert current.last_error == "http_503"
        services.clock.advance(timedelta(hours=6))
    failing = await source_doc(services, source.id)
    assert failing.status is SourceStatus.FAILING
    requests = len(http.requests)
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert len(http.requests) == requests  # failing sources wait a day
    services.clock.advance(timedelta(hours=19))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert (await source_doc(services, source.id)).last_error == "timeout"
    services.clock.advance(timedelta(hours=25))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    recovered = await source_doc(services, source.id)
    assert recovered.status is SourceStatus.ACTIVE
    assert recovered.consecutive_failures == 0
    assert recovered.last_error is None


async def test_paused_sources_are_not_crawled(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    await seed.source(FEED, status=SourceStatus.PAUSED)
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded"
    assert http.requests == []


class FakeResolver:
    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        return [ipaddress.ip_address("93.184.216.34")]


async def test_a_redirect_to_http_is_refused_by_the_egress_helper(
    seed: SignalsSeed, settings: SignalsSettings, services: FakeServices
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "http://news.example.org/feed.xml"})

    def transport(target: PinnedTarget) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(handler)

    services.egress = SafeHttpClient(  # pyright: ignore[reportAttributeAccessIssue]
        resolver=FakeResolver(), transport_factory=transport
    )
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded"
    after = await source_doc(services, source.id)
    assert after.last_error == "scheme_not_allowed"
    assert await signals_of(services, source.id) == []


async def test_paused_background_crawl_skips_but_keeps_house(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    await seed.paused()
    source = (await seed.source(FEED)).doc
    http.add(FEED, rss_bytes(ENTRIES[:1]))
    stale = sample_document(Idea, time_sensitive=True, expires_at=to_iso(T0 - timedelta(minutes=1)))
    await create_doc(services.store, stale)
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded"
    assert job.result == {"skipped": "paused"}
    assert http.requests == []
    assert await signals_of(services, source.id) == []
    assert (await read_doc(services.store, Idea, stale.id)).doc.status is IdeaStatus.EXPIRED
    # Owner-started work still runs.
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    owner = await run_signals_job(services, SIGNALS_CRAWL, {"origin": "owner"}, settings=settings)
    assert owner.status == "succeeded"
    (call,) = services.ai.calls_for(Task.TRIAGE)
    assert call.origin == "owner"
    assert len(await ideas(services)) == 2


async def test_fails_closed_without_a_saved_blocklist(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    source = (await seed.source(FEED)).doc
    http.add(FEED, rss_bytes(ENTRIES))
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_not_configured"
    assert http.requests == []
    assert await signals_of(services, source.id) == []


async def test_fails_without_a_model(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    await seed.source(FEED)
    services.ai = None
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "ai_unavailable"
    assert http.requests == []


async def test_blocklisted_items_never_reach_the_model(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist("Contoso")
    source = (await seed.source(FEED)).doc
    http.add(
        FEED,
        rss_bytes(
            [
                FeedEntry("Contoso breach report", "https://news.example.org/x", T0),
                FeedEntry("Token binding update", "https://news.example.org/y", T0),
            ]
        ),
    )
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "succeeded"
    (call,) = services.ai.calls_for(Task.TRIAGE)
    assert "contoso" not in call.request.messages[0].text.casefold()
    assert "contoso" not in call.request.system.casefold()
    assert [idea.title for idea in await ideas(services)] == ["Token binding update"]
    assert all(signal.triaged for signal in await signals_of(services, source.id))
    fetch = job.step("fetch")
    assert fetch is not None
    assert isinstance(fetch.output, dict)
    assert fetch.output["blocked"] == 1
    # Decided at fetch time: the matching title is not even checkpointed on the job.
    assert "contoso" not in json.dumps(job.to_body()).casefold()


async def test_a_pillar_matching_the_blocklist_fails_the_job(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist("Okta")  # the default identity pillar's topics mention it
    await seed.source(FEED)
    http.add(FEED, rss_bytes(ENTRIES))
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert http.requests == []


async def test_untriaged_items_are_queued_again(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await seed.blocklist()
    source = (await seed.source(FEED)).doc
    http.add(FEED, rss_bytes(ENTRIES[:2]))
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, ScriptedReply(refused=True))
    first = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert isinstance(first.result, dict)
    assert first.result["refusedBatches"] == 1
    assert await ideas(services) == []
    assert not any(signal.triaged for signal in await signals_of(services, source.id))
    services.ai.add(Task.TRIAGE, triage_output(verdict(1), verdict(2)))
    second = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    fetch = second.step("fetch")
    assert fetch is not None
    assert isinstance(fetch.output, dict)
    assert fetch.output["requeued"] == 2
    assert len(await ideas(services)) == 2
    assert all(signal.triaged for signal in await signals_of(services, source.id))


async def test_a_run_stops_taking_sources_at_the_candidate_cap(
    services: FakeServices,
    http: RecordingHttp,
    seed: SignalsSeed,
    settings: SignalsSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crawl_module, "MAX_CANDIDATES_PER_RUN", 2)
    await seed.blocklist()
    first = (await seed.source(FEED)).doc  # never crawled: first in line
    second = (await seed.source(OTHER, last_crawled_at=to_iso(T0 - timedelta(days=2)))).doc
    http.add(FEED, rss_bytes(ENTRIES[:2]))
    http.add(OTHER, rss_bytes([FeedEntry("Later", "https://other.example.net/1", T0)]))
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1), verdict(2)), triage_output(verdict(1)))
    job = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    fetch = job.step("fetch")
    assert fetch is not None
    assert isinstance(fetch.output, dict)
    assert fetch.output["leftForNextRun"] == 1
    assert [url for url, _ in http.requests] == [FEED]
    assert (await source_doc(services, second.id)).last_crawled_at == to_iso(T0 - timedelta(days=2))
    await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    assert [url for url, _ in http.requests] == [FEED, OTHER, FEED]
    assert len(await signals_of(services, first.id)) == 2
    assert len(await signals_of(services, second.id)) == 1


async def test_a_resumed_fetch_skips_finished_sources(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    done = (await seed.source(FEED)).doc
    await seed.source(OTHER)
    http.add(OTHER, rss_bytes([FeedEntry("Later", "https://other.example.net/1", T0)]))
    saved: list[CrawlProgress] = []

    async def save(progress: CrawlProgress) -> None:
        saved.append(progress)

    progress = await crawl_feeds(
        services.store,
        http,
        settings=settings,
        now=T0,
        progress=CrawlProgress(done=[done.id]),
        save=save,
    )
    assert [url for url, _ in http.requests] == [OTHER]
    assert len(progress.items) == 1
    assert progress.done[0] == done.id
    assert saved
    restored = CrawlProgress.from_json(progress.to_json())
    assert restored.items == progress.items
    assert restored.done == progress.done
    assert CrawlProgress.from_json({"done": "x"}).done == []
    assert CrawlProgress.from_json(None).items == []
    found = await find_docs(services.store, Signal, [Filter("triaged", "==", False)])
    assert len(found) == 1
