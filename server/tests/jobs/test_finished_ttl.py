"""Finished jobs expire (contract I-5): a 30-day TTL on every terminal status, none while active.

Plan: "Reliability model" (TTL is garbage collection only; code still checks explicit times).
Job documents used to accumulate: only ``jobs.queue.enqueue`` took a ``ttl``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from blogwriter.db import MemoryStore
from blogwriter.jobs import (
    JobFailed,
    JobRegistry,
    JobRunner,
    Step,
    StepContext,
    enqueue,
    get_job,
    request_cancel,
    retry_job,
)
from blogwriter.jobs.models import FINISHED_JOB_TTL_SECONDS, Job
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
THIRTY_DAYS = 30 * 24 * 3600


def registry() -> JobRegistry:
    async def ok(ctx: StepContext) -> JsonValue:
        return {"ref": "doc-1"}

    async def fails(ctx: StepContext) -> JsonValue:
        raise JobFailed("not_possible")

    async def crashes(ctx: StepContext) -> JsonValue:
        raise RuntimeError("boom")

    async def waits(ctx: StepContext) -> JsonValue:
        return await ctx.wait_for_input("Which angle?")

    jobs = JobRegistry()
    jobs.register("t.ok", [Step("ok", ok)])
    jobs.register("t.fails", [Step("fails", fails)])
    jobs.register("t.crashes", [Step("crashes", crashes)])
    jobs.register("t.waits", [Step("waits", waits)])
    return jobs


async def run_until(store: MemoryStore, job_id: str, status: str) -> Job:
    runner = JobRunner(
        store,
        registry(),
        settings=fast_settings(retry_base_seconds=0.0, retry_max_seconds=0.0, max_attempts=1),
        clock=FakeClock(T0),
    )
    await runner.start()
    try:

        async def reached() -> bool:
            return (await get_job(store, job_id)).status == status

        await eventually(reached)
    finally:
        await runner.stop()
    return await get_job(store, job_id)


def test_the_ttl_is_thirty_days() -> None:
    assert FINISHED_JOB_TTL_SECONDS == THIRTY_DAYS


@pytest.mark.parametrize(
    ("job_type", "status"),
    [("t.ok", "succeeded"), ("t.fails", "failed"), ("t.crashes", "quarantined")],
)
async def test_every_terminal_status_from_the_runner_sets_the_ttl(
    job_type: str, status: str
) -> None:
    store = MemoryStore()
    job = await enqueue(store, job_type, {}, clock=FakeClock(T0), max_attempts=1)
    assert job.ttl is None
    finished = await run_until(store, job.id, status)
    assert finished.ttl == THIRTY_DAYS
    assert (await store.read("jobs", job.id)).body["ttl"] == THIRTY_DAYS


async def test_active_and_waiting_jobs_never_expire() -> None:
    store = MemoryStore()
    job = await enqueue(store, "t.waits", {}, clock=FakeClock(T0))
    waiting = await run_until(store, job.id, "waiting_input")
    assert waiting.ttl is None
    assert "ttl" not in (await store.read("jobs", job.id)).body


async def test_cancelling_a_queued_job_sets_the_ttl_and_a_retry_drops_it() -> None:
    store = MemoryStore()
    clock = FakeClock(T0)
    job = await enqueue(store, "t.ok", {}, clock=clock)
    cancelled = await request_cancel(store, job.id, clock=clock)
    assert (cancelled.status, cancelled.ttl) == ("cancelled", THIRTY_DAYS)
    again = await retry_job(store, job.id, clock=clock)
    assert (again.status, again.ttl) == ("queued", None)
    assert "ttl" not in (await store.read("jobs", job.id)).body
    done = await run_until(store, job.id, "succeeded")
    assert done.ttl == THIRTY_DAYS


async def test_a_running_job_cancelled_by_its_runner_gets_the_ttl() -> None:
    store = MemoryStore()
    started = asyncio.Event()

    async def slow(ctx: StepContext) -> JsonValue:
        started.set()
        await asyncio.sleep(30)
        return None

    jobs = JobRegistry()
    jobs.register("t.slow", [Step("slow", slow)])
    clock = FakeClock(T0)
    runner = JobRunner(store, jobs, settings=fast_settings(), clock=clock)
    job = await enqueue(store, "t.slow", {}, clock=clock)
    await runner.start()
    try:
        await started.wait()
        await request_cancel(store, job.id, clock=clock)
        runner.cancel_local(job.id)

        async def cancelled() -> bool:
            return (await get_job(store, job.id)).status == "cancelled"

        await eventually(cancelled)
    finally:
        await runner.stop()
    assert (await get_job(store, job.id)).ttl == THIRTY_DAYS


async def test_a_shorter_ttl_given_at_enqueue_is_kept() -> None:
    store = MemoryStore()
    job = await enqueue(store, "t.ok", {}, clock=FakeClock(T0), ttl=3600)
    finished = await run_until(store, job.id, "succeeded")
    assert finished.ttl == 3600


async def test_an_explicit_never_expire_is_kept() -> None:
    store = MemoryStore()
    clock = FakeClock(T0)
    job = await enqueue(store, "t.ok", {}, clock=clock, ttl=-1)
    cancelled = await request_cancel(store, job.id, clock=clock)
    assert cancelled.ttl == -1
    again = await retry_job(store, job.id, clock=clock)
    assert again.ttl == -1


async def test_a_longer_ttl_given_at_enqueue_is_shortened_when_finished() -> None:
    store = MemoryStore()
    clock = FakeClock(T0)
    job = await enqueue(store, "t.ok", {}, clock=clock, ttl=90 * 24 * 3600)
    cancelled = await request_cancel(store, job.id, clock=clock)
    assert cancelled.ttl == THIRTY_DAYS
