"""``GET /api/events`` through the real app (auth middleware included) over httpx ASGI.

httpx's ASGI transport returns a response only once the app has finished it, so every test
configures a short ``sse_max_stream_seconds``: the server closes the stream, and the test reads
everything that was sent, heartbeats included.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, override

from fastapi import FastAPI
from pydantic import JsonValue

from blogwriter.core.app import create_app
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.db import Doc, Filter, MemoryStore, OrderBy, Store, StoreUnavailable
from blogwriter.jobs import (
    JobRegistry,
    JobsLifecycle,
    JobsService,
    JobsSettings,
    Step,
    StepContext,
    enqueue,
    get_router,
    request_cancel,
)
from blogwriter.jobs.broker import JobEventMessage
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock
from blogwriter.jobs.models import Job
from blogwriter.jobs.sse import MAX_FILTER_JOBS, parse_job_filter, parse_last_event_id
from blogwriter.jobs.testing import DelegatingStore, FakeClock, eventually, fast_settings

OWNER = principal_headers(OWNER_OID)
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@dataclass
class Frame:
    """One SSE event block as a client parses it."""

    event: str | None = None
    id: str | None = None
    retry: int | None = None
    data: list[str] = field(default_factory=list[str])
    comments: list[str] = field(default_factory=list[str])

    def json(self) -> Any:
        return json.loads("\n".join(self.data))


def parse_sse(text: str) -> list[Frame]:
    frames: list[Frame] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        frame = Frame()
        for line in block.split("\n"):
            if line.startswith(":"):
                frame.comments.append(line[1:].strip())
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "event":
                frame.event = value
            elif name == "id":
                frame.id = value
            elif name == "retry":
                frame.retry = int(value)
            elif name == "data":
                frame.data.append(value)
        frames.append(frame)
    return frames


def sse_settings(**overrides: object) -> JobsSettings:
    values: dict[str, object] = {
        "sse_heartbeat_seconds": 0.05,
        "sse_max_stream_seconds": 0.4,
        "sse_retry_ms": 1500,
    }
    values.update(overrides)
    return fast_settings(**values)


@asynccontextmanager
async def running_app(
    store: Store,
    registry: JobRegistry,
    settings: JobsSettings,
    *,
    clock: Clock = SYSTEM_CLOCK,
) -> AsyncGenerator[tuple[FastAPI, JobsService]]:
    lifecycle = JobsLifecycle(registry, settings=settings, clock=clock, stop_requested=None)
    app = create_app(make_settings(), routers=[get_router()], lifecycles=[lifecycle])
    app.state.store = store
    async with app.router.lifespan_context(app):
        service: object = app.state.jobs
        assert isinstance(service, JobsService)
        yield app, service


def data_frames(frames: Sequence[Frame]) -> list[Frame]:
    return [frame for frame in frames if frame.event is not None]


async def test_the_stream_sends_heartbeats_and_the_server_closes_it(store: MemoryStore) -> None:
    async with running_app(store, JobRegistry(), sse_settings()) as (app, service):
        async with asgi_client(app) as client:
            started = time.monotonic()
            response = await client.get("/api/events", headers=OWNER)
            elapsed = time.monotonic() - started
        assert service.broker.subscriber_count == 0  # the stream unsubscribed
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    frames = parse_sse(response.text)
    assert frames[0].retry == 1500
    assert sum("heartbeat" in frame.comments for frame in frames) >= 3
    events = data_frames(frames)
    assert [frame.event for frame in events] == ["close"]  # no jobs: no snapshot
    assert events[-1].json() == {"reason": "max_duration"}
    assert 0.35 <= elapsed < 3


async def test_a_resumed_stream_gets_a_snapshot_then_only_newer_live_events(
    store: MemoryStore,
) -> None:
    progressed, gate, snapshot_read = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(ctx: StepContext) -> JsonValue:
        for number in range(3):
            await ctx.emit("progress", {"n": number})
        progressed.set()
        await gate.wait()
        ctx.delta("Hello")
        await ctx.emit("progress", {"n": 3})
        return "done"

    class SnapshotSpy(DelegatingStore):
        """Signals the snapshot read (the runner is parked on the gate, so it reads nothing)."""

        @override
        async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
            doc = await self.inner.read(container, id, partition_key=partition_key)
            if progressed.is_set():
                snapshot_read.set()
            return doc

    registry = JobRegistry()
    registry.register("demo", [Step("work", work)])
    settings = sse_settings(sse_max_stream_seconds=1.0)
    async with running_app(SnapshotSpy(store), registry, settings) as (app, service):
        job = await service.enqueue("demo")
        await asyncio.wait_for(progressed.wait(), 5)

        async def finish_after_the_snapshot() -> None:
            await snapshot_read.wait()
            gate.set()

        finisher = asyncio.create_task(finish_after_the_snapshot())
        async with asgi_client(app) as client:
            response = await client.get(
                f"/api/events?jobs={job.id}",
                headers={**OWNER, "Last-Event-ID": f"{job.id}:4"},
            )
        await finisher

    events = data_frames(parse_sse(response.text))
    snapshot = events[0]
    assert snapshot.event == "snapshot"
    body = snapshot.json()
    # Seqs so far: 1 queued, 2 running, 3 step.started, 4-6 progress. The client saw up to 4.
    assert snapshot.id == f"{job.id}:6"
    assert (body["jobId"], body["status"], body["seq"]) == (job.id, "running", 6)
    assert [event["seq"] for event in body["events"]] == [5, 6]
    assert body["steps"] == [
        {
            "name": "work",
            "status": "running",
            "runs": 1,
            "startedAt": body["steps"][0]["startedAt"],
            "finishedAt": None,
        }
    ]
    assert "input" not in body
    live = [frame for frame in events[1:] if frame.event == "job"]
    assert [frame.json()["seq"] for frame in live] == [7, 8, 9]
    assert [frame.id for frame in live] == [f"{job.id}:7", f"{job.id}:8", f"{job.id}:9"]
    assert [frame.json()["type"] for frame in live] == ["progress", "step.succeeded", "status"]
    assert live[-1].json()["status"] == "succeeded"
    deltas = [frame for frame in events if frame.event == "delta"]
    assert len(deltas) == 1
    assert deltas[0].id is None  # deltas are not durable: they never move Last-Event-ID
    assert deltas[0].json() == {"jobId": job.id, "step": "work", "text": "Hello", "gap": False}
    assert events.index(deltas[0]) < events.index(live[0])
    assert events[-1].event == "close"


async def test_the_app_wide_snapshot_covers_active_recent_and_resumed_jobs(
    store: MemoryStore,
) -> None:
    clock = FakeClock(T0)

    async def put_job(job_id: str, status: str, minutes_ago: int) -> None:
        then = FakeClock(T0 - timedelta(minutes=minutes_ago))
        await enqueue(store, "demo", job_id=job_id, clock=then)
        doc = await store.read("jobs", job_id)
        await store.replace("jobs", {**doc.body, "status": status}, if_match=doc.etag)

    await put_job("test-old-done", "succeeded", 60)
    await put_job("test-recent-done", "failed", 5)
    await put_job("test-old-queued", "queued", 120)
    await put_job("test-resumed", "succeeded", 90)
    settings = sse_settings()
    async with (
        running_app(store, JobRegistry(), settings, clock=clock) as (app, _),
        asgi_client(app) as client,
    ):
        response = await client.get(
            "/api/events", headers={**OWNER, "Last-Event-ID": "test-resumed:1"}
        )
    snapshots = [frame.json() for frame in data_frames(parse_sse(response.text))[:-1]]
    assert [snapshot["jobId"] for snapshot in snapshots] == [
        "test-recent-done",  # updated 5 min ago
        "test-resumed",  # named by Last-Event-ID
        "test-old-queued",  # active
    ]
    assert snapshots[1]["events"] == []  # it saw seq 1 already
    assert [event["seq"] for event in snapshots[0]["events"]] == [1]


async def test_an_invalid_job_document_is_left_out_of_the_snapshot(store: MemoryStore) -> None:
    await store.create("jobs", {"id": "test-broken", "kind": "job", "status": "mystery"})
    good = await enqueue(store, "demo")
    async with (
        running_app(store, JobRegistry(), sse_settings()) as (app, _),
        asgi_client(app) as client,
    ):
        response = await client.get(f"/api/events?jobs=test-broken,{good.id}", headers=OWNER)
    events = data_frames(parse_sse(response.text))
    assert [frame.event for frame in events] == ["snapshot", "close"]
    assert events[0].json()["jobId"] == good.id


async def test_the_jobs_filter_limits_snapshots_and_live_events(store: MemoryStore) -> None:
    async with running_app(store, JobRegistry(), sse_settings()) as (app, service):
        followed = await enqueue(store, "demo", broker=service.broker)
        other = await enqueue(store, "demo", broker=service.broker)

        async def cancel_both() -> None:
            await eventually(lambda: service.broker.subscriber_count == 1)
            await request_cancel(store, followed.id, broker=service.broker)
            await request_cancel(store, other.id, broker=service.broker)

        canceller = asyncio.create_task(cancel_both())
        async with asgi_client(app) as client:
            response = await client.get(f"/api/events?jobs={followed.id}", headers=OWNER)
        await canceller
    assert other.id not in response.text
    events = data_frames(parse_sse(response.text))
    assert events[0].event == "snapshot"
    snapshot_seqs = [event["seq"] for event in events[0].json()["events"]]
    live_seqs = [frame.json()["seq"] for frame in events if frame.event == "job"]
    assert sorted(snapshot_seqs + live_seqs) == [1, 2]  # the cancel event exactly once


async def test_closing_the_broker_ends_open_streams(store: MemoryStore) -> None:
    settings = sse_settings(sse_max_stream_seconds=30)
    async with running_app(store, JobRegistry(), settings) as (app, service):

        async def shut_down() -> None:
            await eventually(lambda: service.broker.subscriber_count == 1)
            service.broker.close()

        closer = asyncio.create_task(shut_down())
        async with asgi_client(app) as client:
            started = time.monotonic()
            response = await client.get("/api/events", headers=OWNER)
            elapsed = time.monotonic() - started
        await closer
    assert elapsed < 5
    events = data_frames(parse_sse(response.text))
    assert events[-1].event == "close"
    assert events[-1].json() == {"reason": "shutdown"}


async def test_a_store_failure_ends_the_stream_cleanly(store: MemoryStore) -> None:
    class FailingQueries(DelegatingStore):
        failing = False

        @override
        async def find(
            self,
            container: str,
            filters: Sequence[Filter] = (),
            *,
            order_by: OrderBy | None = None,
            limit: int | None = None,
            partition_key: str | None = None,
        ) -> list[Doc]:
            if self.failing:
                raise StoreUnavailable("throttled")
            return await self.inner.find(
                container, filters, order_by=order_by, limit=limit, partition_key=partition_key
            )

    failing = FailingQueries(store)
    async with running_app(failing, JobRegistry(), sse_settings()) as (app, _):
        failing.failing = True
        async with asgi_client(app) as client:
            response = await client.get("/api/events", headers=OWNER)
        failing.failing = False
    events = data_frames(parse_sse(response.text))
    assert [frame.event for frame in events] == ["close"]
    assert events[0].json() == {"reason": "unavailable"}


async def test_requests_are_validated_and_authenticated(store: MemoryStore) -> None:
    async with running_app(store, JobRegistry(), sse_settings()) as (app, service):
        async with asgi_client(app) as client:
            anonymous = await client.get("/api/events")
            bad_id = await client.get("/api/events?jobs=bad%2Fid", headers=OWNER)
            empty = await client.get("/api/events?jobs=,,", headers=OWNER)
            too_many = await client.get(
                "/api/events?jobs=" + ",".join(f"job-{n}" for n in range(MAX_FILTER_JOBS + 1)),
                headers=OWNER,
            )
            head = await client.head("/api/events", headers=OWNER)
        assert service.broker.subscriber_count == 0  # nothing leaked a subscription
    assert anonymous.status_code == 401
    for response in (bad_id, empty, too_many):
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_jobs_filter"
    assert head.status_code == 405  # the route is GET only; a HEAD never opens a stream


async def test_too_many_streams_get_a_503(store: MemoryStore) -> None:
    settings = sse_settings(broker_max_subscribers=1)
    async with running_app(store, JobRegistry(), settings) as (app, service):
        held = service.broker.subscribe()
        async with asgi_client(app) as client:
            response = await client.get("/api/events", headers=OWNER)
        held.close()
    assert response.status_code == 503
    assert response.json()["code"] == "too_many_streams"
    assert response.headers["retry-after"] == "5"


async def test_the_route_needs_a_running_jobs_service() -> None:
    app = create_app(make_settings(), routers=[get_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/events", headers=OWNER)
    assert response.status_code == 503
    assert response.json()["code"] == "jobs_unavailable"


def test_last_event_id_parsing() -> None:
    assert parse_last_event_id("job-1:7") == ("job-1", 7)
    assert parse_last_event_id("sched-a-20261102T140000Z:12") == ("sched-a-20261102T140000Z", 12)
    assert parse_last_event_id("a:b:3") == ("a:b", 3)
    for junk in (
        None,
        "",
        "job-1",
        "job-1:",
        ":7",
        "job-1:x",
        "job-1:-1",
        "bad/id:1",
        "a:" + "9" * 16,
        "job-1:\u00b2",  # superscript two: str.isdigit() says yes, int() says no
        "job-1:\u0663",  # Arabic-Indic three
        "job-1: 7",
    ):
        assert parse_last_event_id(junk) is None


def test_job_filter_parsing() -> None:
    assert parse_job_filter(None) is None
    assert parse_job_filter(" a , b,a ") == frozenset({"a", "b"})


def progress(job_id: str, seq: int) -> JobEventMessage:
    return JobEventMessage(
        job_id=job_id,
        seq=seq,
        at="2026-11-26T12:00:00.000000Z",
        type="progress",
        step="work",
        status="running",
        data={"n": seq},
    )


async def test_live_events_published_out_of_seq_order_are_all_delivered(
    store: MemoryStore,
) -> None:
    """A job's runner and an API call publish independently, so seq 3 can reach the broker
    before seq 2. Keeping only the highest seq sent would drop seq 2 for good."""
    async with running_app(store, JobRegistry(), sse_settings()) as (app, service):
        job = await enqueue(store, "demo")  # seq 1, covered by the snapshot

        async def publish() -> None:
            await eventually(lambda: service.broker.subscriber_count == 1)
            for seq in (3, 2, 3, 1, 4):
                service.broker.publish(progress(job.id, seq))

        publisher = asyncio.create_task(publish())
        async with asgi_client(app) as client:
            response = await client.get(f"/api/events?jobs={job.id}", headers=OWNER)
        await publisher
    events = data_frames(parse_sse(response.text))
    assert events[0].event == "snapshot"
    assert events[0].json()["seq"] == 1
    live = [frame for frame in events if frame.event == "job"]
    assert [frame.json()["seq"] for frame in live] == [3, 2, 4]  # each once, the late 2 too
    assert [frame.id for frame in live] == [f"{job.id}:3", f"{job.id}:2", f"{job.id}:4"]


async def test_a_stream_stops_waiting_for_a_missing_seq_after_many_later_ones(
    store: MemoryStore,
) -> None:
    """The out-of-order bookkeeping is bounded: after 256 events past a gap the stream stops
    waiting for it (a seq that late is dropped), and it still never repeats an event."""
    settings = sse_settings(broker_queue_size=2000)
    async with running_app(store, JobRegistry(), settings) as (app, service):

        async def publish() -> None:
            await eventually(lambda: service.broker.subscriber_count == 1)
            for seq in range(2, 302):  # seq 1 is missing...
                service.broker.publish(progress("job-gap", seq))
            service.broker.publish(progress("job-gap", 1))  # ...and arrives far too late
            service.broker.publish(progress("job-gap", 150))  # a repeat
            service.broker.publish(progress("job-gap", 302))

        publisher = asyncio.create_task(publish())
        async with asgi_client(app) as client:
            response = await client.get("/api/events?jobs=job-gap", headers=OWNER)
        await publisher
    seqs = [frame.json()["seq"] for frame in data_frames(parse_sse(response.text))[:-1]]
    assert seqs == list(range(2, 303))


async def test_a_last_event_id_with_non_ascii_digits_is_treated_as_a_fresh_connect(
    store: MemoryStore,
) -> None:
    """Starlette decodes headers as latin-1, so a raw 0xB2 byte arrives as a superscript two.
    It used to reach int() and turn the request into a 500."""
    headers = [(name.encode(), value.encode()) for name, value in OWNER.items()]
    headers.append((b"last-event-id", b"job-1:\xb2"))
    async with (
        running_app(store, JobRegistry(), sse_settings()) as (app, _),
        asgi_client(app) as client,
    ):
        response = await client.get("/api/events", headers=headers)
    assert response.status_code == 200
    assert [frame.event for frame in data_frames(parse_sse(response.text))] == ["close"]


async def test_the_resumed_job_keeps_its_snapshot_when_newer_jobs_fill_the_limit(
    store: MemoryStore,
) -> None:
    """With a backlog of newer jobs, truncating to sse_snapshot_limit used to drop the very job
    named by Last-Event-ID: the events it missed were then in no snapshot and no live feed."""
    clock = FakeClock(T0)
    for minutes_ago in (1, 2, 3):
        await enqueue(
            store,
            "demo",
            job_id=f"test-new-{minutes_ago}",
            clock=FakeClock(T0 - timedelta(minutes=minutes_ago)),
        )
    resumed = await enqueue(
        store, "demo", job_id="test-resumed", clock=FakeClock(T0 - timedelta(minutes=10))
    )
    doc = await store.read("jobs", resumed.id)
    changed = Job.from_body(doc.body)
    changed.add_event("progress", at=changed.updated_at, data={"n": 1})  # seq 2: missed
    await store.replace("jobs", changed.to_body(), if_match=doc.etag)
    settings = sse_settings(sse_snapshot_limit=2)
    async with (
        running_app(store, JobRegistry(), settings, clock=clock) as (app, _),
        asgi_client(app) as client,
    ):
        response = await client.get(
            "/api/events", headers={**OWNER, "Last-Event-ID": "test-resumed:1"}
        )
    snapshots = [frame.json() for frame in data_frames(parse_sse(response.text))[:-1]]
    assert [snapshot["jobId"] for snapshot in snapshots] == ["test-new-1", "test-resumed"]
    assert [event["seq"] for event in snapshots[1]["events"]] == [2]
