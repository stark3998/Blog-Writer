"""Invariants: feed items never generate content without an owner action; signals hold hashes
only; no titles, URLs, summaries or blocklist terms in logs, events or stray documents."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Final

import pytest

from blogwriter.ai.types import Citation, Task
from blogwriter.db import Store
from blogwriter.db.base import CONTAINERS
from blogwriter.domain.base import to_iso
from blogwriter.domain.jobtypes import (
    SCHEDULED_JOB_TYPES,
    SIGNALS_CRAWL,
    SIGNALS_SCOUT,
    generates_content,
)
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import FakeServices, ScriptedReply
from blogwriter.jobs import Job, JobRegistry
from blogwriter.signals import register_jobs, schedule_entries
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

FEED: Final = "https://news.example.org/feed.xml"
TITLE_MARK: Final = "TitleMarkerZq7"
URL_MARK: Final = "urlmarkerzq7"
SUMMARY_MARK: Final = "SummaryMarkerZq7"
SIGNAL_KEYS: Final = frozenset(
    {
        "id",
        "kind",
        "schemaVersion",
        "createdAt",
        "updatedAt",
        "sourceId",
        "urlSha256",
        "titleSha256",
        "itemPublishedAt",
        "seenAt",
        "triaged",
        "ideaId",
    }
)

type Dump = dict[tuple[str, str, str], dict[str, Any]]


async def dump(store: Store) -> Dump:
    out: Dump = {}
    for container in CONTAINERS:
        for doc in await store.find(container):
            out[(container, doc.partition_key, doc.id)] = doc.body
    return out


async def prepare(services: FakeServices, http: RecordingHttp, seed: SignalsSeed) -> None:
    """Marked content for a crawl and a scouting run, with only TRIAGE and QUICK_SEARCH
    scripted: any other model task fails the run."""
    await seed.blocklist("Contoso")
    await seed.source(FEED)
    await seed.scout("agent identity news")
    http.add(
        FEED,
        rss_bytes(
            [
                FeedEntry(
                    f"{TITLE_MARK} spec change",
                    f"https://news.example.org/{URL_MARK}/a",
                    T0 - timedelta(hours=1),
                    summary=f"{SUMMARY_MARK} body text",
                ),
                FeedEntry("Contoso incident", "https://news.example.org/c", T0),
                FeedEntry("Quiet item", "https://news.example.org/q", T0 - timedelta(hours=2)),
            ]
        ),
    )
    assert services.ai is not None
    services.ai.add(Task.TRIAGE, triage_output(verdict(1), verdict(2, 0.2)))
    services.ai.add(
        Task.QUICK_SEARCH,
        ScriptedReply(
            text=f"{SUMMARY_MARK} the answer text is discarded",
            citations=(
                Citation(url=f"https://genai.owasp.org/{URL_MARK}", title=f"{TITLE_MARK} 2"),
            ),
        ),
        ScriptedReply(text="Nothing new."),
        ScriptedReply(text="Nothing new."),
        ScriptedReply(text="Nothing new."),
    )
    services.ai.add(Task.TRIAGE, triage_output(verdict(1)))


async def run_both(services: FakeServices, settings: SignalsSettings) -> tuple[Job, Job]:
    crawl = await run_signals_job(services, SIGNALS_CRAWL, {}, settings=settings)
    scout = await run_signals_job(services, SIGNALS_SCOUT, {}, settings=settings)
    assert crawl.status == "succeeded", crawl.error
    assert scout.status == "succeeded", scout.error
    return crawl, scout


async def test_feed_items_never_generate_content(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await prepare(services, http, seed)
    before = await dump(services.store)
    crawl, scout = await run_both(services, settings)
    after = await dump(services.store)

    changed = {key for key, value in after.items() if before.get(key) != value}
    assert {container for container, _, _ in changed} <= {"sources", "signals", "ideas", "jobs"}
    assert {after[key].get("kind") for key in changed} <= {"source", "signal", "idea", "job"}
    jobs = {doc_id for container, _, doc_id in changed if container == "jobs"}
    assert jobs == {crawl.id, scout.id}  # nothing else was enqueued
    assert services.jobs.types() == [SIGNALS_CRAWL, SIGNALS_SCOUT]

    assert services.ai is not None
    assert {call.task for call in services.ai.calls} <= {Task.TRIAGE, Task.QUICK_SEARCH}
    assert services.notifier.sent == []
    blobs, render, fetcher = services.blobs, services.render, services.fetcher
    classifier, embedder = services.classifier, services.embedder
    assert blobs is not None
    assert render is not None
    assert fetcher is not None
    assert classifier is not None
    assert embedder is not None
    assert blobs.blobs == {}
    assert render.calls == []
    assert fetcher.fetched == []
    assert classifier.calls == []
    assert embedder.calls == []
    ideas = [after[key] for key in changed if key[0] == "ideas"]
    assert ideas
    for idea in ideas:
        assert idea["status"] == "new"
        assert idea.get("articleId") is None
        assert idea.get("postId") is None


def test_schedules_and_job_types_never_generate_content() -> None:
    entries = schedule_entries(CadenceSettings.default(to_iso(T0)))
    assert [entry.job_type for entry in entries] == [SIGNALS_CRAWL, SIGNALS_SCOUT]
    for entry in entries:
        assert entry.job_type in SCHEDULED_JOB_TYPES
        assert not generates_content(entry.job_type)
    registry = JobRegistry()
    register_jobs(registry)
    assert registry.names() == (SIGNALS_CRAWL, SIGNALS_SCOUT)
    assert not any(generates_content(name) for name in registry.names())


async def test_signals_hold_hashes_only(
    services: FakeServices, http: RecordingHttp, seed: SignalsSeed, settings: SignalsSettings
) -> None:
    await prepare(services, http, seed)
    await run_both(services, settings)
    signals = [
        body
        for (container, _, _), body in (await dump(services.store)).items()
        if container == "signals"
    ]
    assert len(signals) == 4  # three feed items and one citation
    for body in signals:
        assert set(body) <= SIGNAL_KEYS
    raw = json.dumps(signals).casefold()
    for marker in (
        TITLE_MARK,
        URL_MARK,
        SUMMARY_MARK,
        "news.example.org",
        "contoso",
        "genai.owasp",
    ):
        assert marker.casefold() not in raw


async def test_no_content_in_logs_events_or_stray_documents(
    services: FakeServices,
    http: RecordingHttp,
    seed: SignalsSeed,
    settings: SignalsSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await prepare(services, http, seed)
    with caplog.at_level(logging.DEBUG):
        crawl, scout = await run_both(services, settings)
    logged = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in caplog.records
    ).casefold()
    for marker in (TITLE_MARK, URL_MARK, SUMMARY_MARK, "news.example.org", "contoso"):
        assert marker.casefold() not in logged
    for job in (crawl, scout):
        events = json.dumps([event.data for event in job.events]).casefold()
        assert TITLE_MARK.casefold() not in events
        assert URL_MARK not in events
    everything = await dump(services.store)
    text = json.dumps(
        [body for (_, _, doc_id), body in everything.items() if doc_id != "blocklist"]
    ).casefold()
    assert SUMMARY_MARK.casefold() not in text  # summaries and answers are never kept
    assert "contoso" not in text  # the blocklisted headline is decided before any checkpoint
