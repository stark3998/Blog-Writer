"""The job runner: exactly-once steps across runners, leases, fencing, resume, outcomes.

Lease expiry is driven by fake clocks (no test waits 60 s); the claim loops run on real time
with a 10 ms poll (``fast_settings``).
"""

from __future__ import annotations

import asyncio
import gc
import json
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast, override

import pytest
from pydantic import JsonValue

from blogwriter.db import Doc, Filter, MemoryStore, Store, StoreUnavailable
from blogwriter.jobs import (
    MAX_EVENTS,
    JobFailed,
    JobRegistry,
    JobRunner,
    QuarantineNotice,
    Step,
    StepContext,
    enqueue,
    get_job,
    provide_input,
    request_cancel,
)
from blogwriter.jobs.broker import Broker, DeltaMessage, JobEventMessage, Message
from blogwriter.jobs.models import Job
from blogwriter.jobs.testing import DelegatingStore, FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class FakeRemote:
    """A remote API with long-running work: ``create`` issues an id, ``poll`` finishes it."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.polled: list[str] = []

    async def create(self) -> str:
        await asyncio.sleep(0)
        remote_id = f"resp-{len(self.created) + 1}"
        self.created.append(remote_id)
        return remote_id

    async def poll(self, remote_id: str) -> str:
        await asyncio.sleep(0)
        self.polled.append(remote_id)
        return f"done:{remote_id}"


async def all_jobs(store: Store) -> list[Job]:
    docs = await store.find("jobs", [Filter("kind", "==", "job")])
    return [Job.from_body(doc.body) for doc in docs]


async def status_of(store: Store, job_id: str) -> str:
    return (await get_job(store, job_id)).status


def is_status(store: Store, job_id: str, status: str) -> Any:
    async def check() -> bool:
        return await status_of(store, job_id) == status

    return check


# ----------------------------------------------------------------------------------------------
# Exactly once
# ----------------------------------------------------------------------------------------------


async def test_two_runners_sharing_a_store_run_every_step_exactly_once(
    store: MemoryStore,
) -> None:
    calls: Counter[tuple[str, str]] = Counter()

    def counted(name: str) -> Step:
        async def run(ctx: StepContext) -> JsonValue:
            calls[(ctx.job_id, ctx.step)] += 1
            await asyncio.sleep(0.001 * (len(calls) % 3))
            done: list[JsonValue] = [*sorted(ctx.outputs)]
            return {"step": name, "outputsSoFar": done}

        return Step(name, run)

    registry = JobRegistry()
    registry.register("multi", [counted("a"), counted("b"), counted("c")])
    jobs = [await enqueue(store, "multi", {"n": n}) for n in range(24)]
    settings = fast_settings(concurrency=3)
    first = JobRunner(store, registry, settings=settings, owner="replica-a:1")
    second = JobRunner(store, registry, settings=settings, owner="replica-b:2")
    await first.start()
    await second.start()
    try:

        async def finished() -> bool:
            return all(job.status == "succeeded" for job in await all_jobs(store))

        await eventually(finished, within=20)
    finally:
        await first.stop()
        await second.stop()

    assert calls == Counter({(job.id, step): 1 for job in jobs for step in "abc"})
    for job in await all_jobs(store):
        assert job.fence == 1
        assert job.attempt == 1
        assert job.owner is None
        assert [step.runs for step in job.steps] == [1, 1, 1]
        assert job.result == {"step": "c", "outputsSoFar": ["a", "b"]}
    assert first.stats.claimed + second.stats.claimed == 24
    assert first.stats.claimed > 0
    assert second.stats.claimed > 0
    assert first.stats.claims_lost == second.stats.claims_lost == 0


# ----------------------------------------------------------------------------------------------
# Crashes, leases and fencing
# ----------------------------------------------------------------------------------------------


def remote_step(remote: FakeRemote, started: asyncio.Event, gate: asyncio.Event) -> Step:
    """Records the remote id before awaiting the work; the first run blocks on ``gate``."""

    async def generate(ctx: StepContext) -> JsonValue:
        response_id = ctx.remote_ids.get("response")
        if response_id is None:
            response_id = await remote.create()
            await ctx.record_remote_id("response", response_id)
        if not ctx.resumed:
            started.set()
            await gate.wait()
            return {"by": "first run"}
        return {"by": "resumed run", "text": await remote.poll(response_id)}

    return Step("generate", generate)


async def test_a_crashed_runner_is_resumed_only_after_its_lease_expires(
    store: MemoryStore, clock: FakeClock
) -> None:
    remote, started, gate = FakeRemote(), asyncio.Event(), asyncio.Event()
    registry = JobRegistry()
    registry.register("draft", [remote_step(remote, started, gate)])
    job = await enqueue(store, "draft", clock=clock)

    crashed = JobRunner(store, registry, settings=fast_settings(), owner="a:1", clock=clock)
    await crashed.start()
    await asyncio.wait_for(started.wait(), 5)
    await crashed.stop(release=False)  # SIGKILL: nothing is written
    orphan = await get_job(store, job.id)
    assert (orphan.status, orphan.owner, orphan.fence, orphan.attempt) == ("running", "a:1", 1, 1)
    assert orphan.steps[0].remote_ids == {"response": "resp-1"}

    survivor = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=clock)
    await survivor.start()
    try:
        clock.advance(30)  # the lease runs until T0 + 60 s
        await asyncio.sleep(0.1)
        assert survivor.stats.claimed == 0
        clock.advance(34)  # T0 + 64 s: expired, but inside the 5 s skew allowance
        await asyncio.sleep(0.1)
        assert survivor.stats.claimed == 0
        assert (await get_job(store, job.id)).owner == "a:1"
        clock.advance(2)  # T0 + 66 s
        await eventually(lambda: survivor.stats.succeeded == 1)
    finally:
        await survivor.stop()

    done = await get_job(store, job.id)
    assert done.status == "succeeded"
    assert (done.fence, done.attempt) == (2, 2)
    assert done.result == {"by": "resumed run", "text": "done:resp-1"}
    assert remote.created == ["resp-1"]  # re-attached, never re-issued
    assert remote.polled == ["resp-1"]
    assert done.steps[0].runs == 2
    types = [event.type for event in done.events]
    assert types.count("step.started") == 1
    assert types.count("step.resumed") == 1


async def test_a_stale_runner_commit_is_rejected_by_fencing(store: MemoryStore) -> None:
    """The first runner stalls mid-step (its clock and renewals stop); the second takes over
    after expiry and finishes. When the first wakes up, its commit is rejected."""
    remote, started, gate = FakeRemote(), asyncio.Event(), asyncio.Event()
    registry = JobRegistry()
    registry.register("draft", [remote_step(remote, started, gate)])
    stale_clock, fresh_clock = FakeClock(T0), FakeClock(T0)
    job = await enqueue(store, "draft", clock=stale_clock)

    stale = JobRunner(store, registry, settings=fast_settings(), owner="a:1", clock=stale_clock)
    await stale.start()
    await asyncio.wait_for(started.wait(), 5)

    fresh = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=fresh_clock)
    await fresh.start()
    try:
        fresh_clock.advance(66)
        await eventually(lambda: fresh.stats.succeeded == 1)
        gate.set()  # the stale runner wakes up and commits its step output
        await eventually(lambda: stale.stats.claims_lost == 1)
        await eventually(lambda: not stale.active_job_ids)
    finally:
        await stale.stop()
        await fresh.stop()

    done = await get_job(store, job.id)
    assert done.status == "succeeded"
    assert done.fence == 2
    assert done.steps[0].output == {"by": "resumed run", "text": "done:resp-1"}
    assert done.result == {"by": "resumed run", "text": "done:resp-1"}
    assert remote.created == ["resp-1"]
    assert stale.stats.succeeded == 0


async def test_a_runner_that_cannot_renew_abandons_its_step(
    store: MemoryStore, clock: FakeClock
) -> None:
    """Self-fencing: renewals fail until leaseUntil - skew, so the step is cancelled before
    anyone else may take over."""
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def long_step(ctx: StepContext) -> JsonValue:
        if ctx.resumed:
            return "finished by the survivor"
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None  # pragma: no cover

    class Partitioned(DelegatingStore):
        broken = False

        @override
        async def replace(
            self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
        ) -> Doc:
            if self.broken:
                raise StoreUnavailable("partitioned")
            return await self.inner.replace(container, body, if_match=if_match)

    registry = JobRegistry()
    registry.register("long", [Step("work", long_step)])
    job = await enqueue(store, "long", clock=clock)
    partitioned = Partitioned(store)
    cut_off = JobRunner(
        partitioned,
        registry,
        settings=fast_settings(renew_seconds=0.02),
        owner="a:1",
        clock=clock,
    )
    await cut_off.start()
    await asyncio.wait_for(started.wait(), 5)
    partitioned.broken = True
    clock.advance(30)
    await asyncio.sleep(0.1)
    assert not cancelled.is_set()  # renewals fail, but the lease is still good locally
    clock.advance(26)  # T0 + 56 s: past leaseUntil - skew
    await asyncio.wait_for(cancelled.wait(), 5)
    await eventually(lambda: cut_off.stats.claims_lost == 1)

    survivor = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=clock)
    await survivor.start()
    try:
        clock.advance(10)  # T0 + 66 s
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await survivor.stop()
        await cut_off.stop()
    done = await get_job(store, job.id)
    assert done.result == "finished by the survivor"
    assert done.fence == 2


async def test_renewals_keep_other_runners_away_from_a_long_step(
    store: MemoryStore, clock: FakeClock
) -> None:
    gate = asyncio.Event()

    async def long_step(ctx: StepContext) -> JsonValue:
        await gate.wait()
        return ctx.fence

    registry = JobRegistry()
    registry.register("long", [Step("work", long_step)])
    job = await enqueue(store, "long", clock=clock)
    holder = JobRunner(
        store, registry, settings=fast_settings(renew_seconds=0.02), owner="a:1", clock=clock
    )
    other = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=clock)
    await holder.start()
    await eventually(lambda: bool(holder.active_job_ids))
    await other.start()
    try:
        for _ in range(6):  # three minutes of fake time, far beyond one lease
            clock.advance(30)
            await asyncio.sleep(0.08)
            assert other.stats.claimed == 0
        gate.set()
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await holder.stop()
        await other.stop()
    done = await get_job(store, job.id)
    assert done.result == 1
    assert done.fence == 1


async def test_graceful_stop_releases_jobs_for_an_immediate_resume(
    store: MemoryStore, clock: FakeClock
) -> None:
    remote, started, gate = FakeRemote(), asyncio.Event(), asyncio.Event()
    registry = JobRegistry()
    registry.register("draft", [remote_step(remote, started, gate)])
    job = await enqueue(store, "draft", clock=clock)
    leaving = JobRunner(store, registry, settings=fast_settings(), owner="a:1", clock=clock)
    await leaving.start()
    await asyncio.wait_for(started.wait(), 5)
    await leaving.stop()  # redeploy: release, give the attempt back
    released = await get_job(store, job.id)
    assert (released.status, released.owner, released.lease_until) == ("queued", None, None)
    assert released.attempt == 0
    assert released.steps[0].status == "pending"
    assert released.steps[0].remote_ids == {"response": "resp-1"}
    assert leaving.stats.released == 1

    arriving = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=clock)
    await arriving.start()  # no clock advance: a released job needs no lease expiry
    try:
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await arriving.stop()
    done = await get_job(store, job.id)
    assert (done.attempt, done.fence) == (1, 2)
    assert done.result == {"by": "resumed run", "text": "done:resp-1"}
    assert remote.created == ["resp-1"]


async def test_graceful_stop_lets_a_step_finish_within_the_grace_period(
    store: MemoryStore, clock: FakeClock
) -> None:
    started = asyncio.Event()

    async def short(ctx: StepContext) -> JsonValue:
        started.set()
        await asyncio.sleep(0.05)
        return "done"

    registry = JobRegistry()
    registry.register("short", [Step("work", short)])
    job = await enqueue(store, "short", clock=clock)
    runner = JobRunner(
        store, registry, settings=fast_settings(shutdown_grace_seconds=2), clock=clock
    )
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    await runner.stop()
    assert await status_of(store, job.id) == "succeeded"
    assert runner.stats.released == 0


async def test_claiming_stops_as_soon_as_shutdown_is_requested(
    store: MemoryStore, clock: FakeClock
) -> None:
    async def noop(ctx: StepContext) -> JsonValue:
        return None

    registry = JobRegistry()
    registry.register("noop", [Step("work", noop)])
    exiting: list[bool] = []
    runner = JobRunner(
        store,
        registry,
        settings=fast_settings(),
        clock=clock,
        stop_requested=lambda: bool(exiting),
    )
    await runner.start()
    assert runner.claiming
    exiting.append(True)
    await eventually(lambda: not runner.claiming)
    job = await enqueue(store, "noop", clock=clock)
    runner.wake()
    await asyncio.sleep(0.1)
    assert await status_of(store, job.id) == "queued"
    await runner.stop()


# ----------------------------------------------------------------------------------------------
# Waiting for input
# ----------------------------------------------------------------------------------------------


async def test_waiting_for_input_releases_the_claim_and_the_slot(
    store: MemoryStore, clock: FakeClock
) -> None:
    entered: list[int] = []

    async def ask(ctx: StepContext) -> JsonValue:
        entered.append(ctx.attempt)
        await ctx.checkpoint({"question": "drafted"})
        answer = await ctx.wait_for_input("What changed since last week?", key="answer")
        return {"answer": answer, "checkpoint": ctx.output}

    async def quick(ctx: StepContext) -> JsonValue:
        return "quick done"

    registry = JobRegistry()
    registry.register("interview", [Step("ask", ask)])
    registry.register("quick", [Step("go", quick)])
    runner = JobRunner(store, registry, settings=fast_settings(concurrency=1), clock=clock)
    interview = await enqueue(store, "interview", clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, interview.id, "waiting_input"))
        waiting = await get_job(store, interview.id)
        assert (waiting.owner, waiting.lease_until, waiting.attempt) == (None, None, 0)
        assert waiting.awaiting is not None
        assert waiting.awaiting.prompt == "What changed since last week?"
        assert waiting.awaiting.key == "answer"
        assert waiting.steps[0].status == "waiting_input"
        await eventually(lambda: not runner.active_job_ids)

        other = await enqueue(store, "quick", clock=clock)  # the only slot is free again
        runner.wake()
        await eventually(is_status(store, other.id, "succeeded"))

        await provide_input(store, interview.id, "A new pillar.", key="answer", clock=clock)
        runner.wake()
        await eventually(is_status(store, interview.id, "succeeded"))
    finally:
        await runner.stop()
    done = await get_job(store, interview.id)
    assert done.result == {"answer": "A new pillar.", "checkpoint": {"question": "drafted"}}
    assert done.attempt == 1  # the wait gave its attempt back
    assert done.steps[0].runs == 2
    assert entered == [1, 1]
    assert runner.stats.waiting == 1
    assert "A new pillar." not in json.dumps([event.model_dump() for event in done.events])


# ----------------------------------------------------------------------------------------------
# Failures, retries and quarantine
# ----------------------------------------------------------------------------------------------


async def test_a_failing_job_is_retried_with_backoff_then_quarantined_after_three_attempts(
    store: MemoryStore, clock: FakeClock
) -> None:
    attempts: list[int] = []

    async def flaky(ctx: StepContext) -> JsonValue:
        attempts.append(ctx.attempt)
        raise RuntimeError("upstream said something private")

    notices: list[QuarantineNotice] = []

    async def alert(notice: QuarantineNotice) -> None:
        notices.append(notice)

    registry = JobRegistry()
    registry.register("flaky", [Step("call", flaky)])
    job = await enqueue(store, "flaky", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock, on_quarantine=alert)
    await runner.start()
    try:
        await eventually(lambda: attempts == [1])
        await eventually(is_status(store, job.id, "queued"))
        retrying = await get_job(store, job.id)
        assert retrying.error is not None
        assert retrying.error.code == "step_error"
        assert retrying.run_after == "2026-11-26T12:00:30.000000Z"  # 30 s backoff
        await asyncio.sleep(0.05)
        assert attempts == [1]  # not before the backoff
        clock.advance(30)
        await eventually(lambda: attempts == [1, 2])
        await eventually(is_status(store, job.id, "queued"))
        assert (await get_job(store, job.id)).run_after == "2026-11-26T12:01:30.000000Z"  # 60 s
        clock.advance(60)
        await eventually(is_status(store, job.id, "quarantined"))
    finally:
        await runner.stop()
    quarantined = await get_job(store, job.id)
    assert attempts == [1, 2, 3]
    assert quarantined.attempt == 3
    assert quarantined.error is not None
    assert quarantined.error.code == "max_attempts"
    assert quarantined.error.step == "call"
    assert quarantined.error.error_type == "RuntimeError"
    assert quarantined.owner is None
    assert notices == [
        QuarantineNotice(
            job_id=job.id, job_type="flaky", attempts=3, step="call", error_type="RuntimeError"
        )
    ]
    assert "private" not in json.dumps(quarantined.to_body())
    assert runner.stats.retried == 2
    assert runner.stats.quarantined == 1


async def test_a_job_that_keeps_crashing_its_runner_is_quarantined_without_a_fourth_run(
    store: MemoryStore, clock: FakeClock
) -> None:
    entered: list[int] = []
    started = asyncio.Event()

    async def deadly(ctx: StepContext) -> JsonValue:
        entered.append(ctx.attempt)
        started.set()
        await asyncio.Event().wait()  # the process dies here every time
        return None  # pragma: no cover

    registry = JobRegistry()
    registry.register("deadly", [Step("work", deadly)])
    job = await enqueue(store, "deadly", clock=clock)
    for number in range(3):
        started.clear()
        runner = JobRunner(
            store, registry, settings=fast_settings(), owner=f"r{number}:x", clock=clock
        )
        await runner.start()
        await asyncio.wait_for(started.wait(), 5)
        await runner.stop(release=False)
        clock.advance(66)

    notices: list[QuarantineNotice] = []

    async def alert(notice: QuarantineNotice) -> None:
        notices.append(notice)

    last = JobRunner(store, registry, settings=fast_settings(), clock=clock, on_quarantine=alert)
    await last.start()
    try:
        await eventually(is_status(store, job.id, "quarantined"))
    finally:
        await last.stop()
    done = await get_job(store, job.id)
    assert entered == [1, 2, 3]
    assert done.attempt == 3
    assert done.owner is None
    assert done.error is not None
    assert done.error.error_type == "crash"
    assert done.steps[0].status == "pending"
    assert [notice.job_id for notice in notices] == [job.id]


async def test_job_failed_ends_the_job_without_retry(store: MemoryStore, clock: FakeClock) -> None:
    calls: list[str] = []

    async def first(ctx: StepContext) -> JsonValue:
        calls.append("first")
        return 1

    async def refuse(ctx: StepContext) -> JsonValue:
        calls.append("refuse")
        raise JobFailed("model_refused", "The model declined this topic.")

    registry = JobRegistry()
    registry.register("strict", [Step("first", first), Step("refuse", refuse)])
    job = await enqueue(store, "strict", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "failed"))
    finally:
        await runner.stop()
    failed = await get_job(store, job.id)
    assert calls == ["first", "refuse"]
    assert failed.error is not None
    assert (failed.error.code, failed.error.step) == ("model_refused", "refuse")
    assert failed.error.detail == "The model declined this topic."
    assert [step.status for step in failed.steps] == ["succeeded", "failed"]
    assert runner.stats.failed == 1


async def test_output_that_is_not_json_fails_the_job(store: MemoryStore, clock: FakeClock) -> None:
    async def bad(ctx: StepContext) -> Any:
        return {"when": datetime.now(UTC)}

    registry = JobRegistry()
    registry.register("bad", [Step("work", bad)])
    job = await enqueue(store, "bad", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "failed"))
    finally:
        await runner.stop()
    failed = await get_job(store, job.id)
    assert failed.error is not None
    assert failed.error.code == "invalid_output"


async def test_completed_steps_are_not_rerun_after_a_retry(
    store: MemoryStore, clock: FakeClock
) -> None:
    calls: Counter[str] = Counter()

    async def first(ctx: StepContext) -> JsonValue:
        calls["first"] += 1
        return "one"

    async def second(ctx: StepContext) -> JsonValue:
        calls["second"] += 1
        if ctx.attempt == 1:
            raise ConnectionError("transient")
        return {"previous": ctx.outputs["first"]}

    registry = JobRegistry()
    registry.register("pair", [Step("first", first), Step("second", second)])
    job = await enqueue(store, "pair", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: calls["second"] == 1)
        await eventually(is_status(store, job.id, "queued"))
        clock.advance(30)
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await runner.stop()
    assert calls == Counter({"first": 1, "second": 2})
    assert (await get_job(store, job.id)).result == {"previous": "one"}


# ----------------------------------------------------------------------------------------------
# Cancellation
# ----------------------------------------------------------------------------------------------


def blocking_registry(started: asyncio.Event, cancelled: asyncio.Event) -> JobRegistry:
    async def block(ctx: StepContext) -> JsonValue:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None  # pragma: no cover

    registry = JobRegistry()
    registry.register("block", [Step("work", block)])
    return registry


async def test_cancelling_a_running_job_stops_it_through_the_local_runner(
    store: MemoryStore, clock: FakeClock
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    registry = blocking_registry(started, cancelled)
    job = await enqueue(store, "block", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await asyncio.wait_for(started.wait(), 5)
        requested = await request_cancel(store, job.id, clock=clock)
        assert (requested.status, requested.cancel_requested) == ("running", True)
        assert runner.cancel_local(job.id)
        await eventually(is_status(store, job.id, "cancelled"))
    finally:
        await runner.stop()
    assert cancelled.is_set()
    assert not runner.cancel_local(job.id)
    assert runner.stats.cancelled == 1


async def test_a_cancel_request_reaches_the_runner_at_its_next_renewal(
    store: MemoryStore, clock: FakeClock
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    registry = blocking_registry(started, cancelled)
    job = await enqueue(store, "block", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(renew_seconds=0.02), clock=clock)
    await runner.start()
    try:
        await asyncio.wait_for(started.wait(), 5)
        await request_cancel(store, job.id, clock=clock)  # another replica handled the request
        await eventually(is_status(store, job.id, "cancelled"))
    finally:
        await runner.stop()
    assert cancelled.is_set()
    done = await get_job(store, job.id)
    assert done.owner is None
    assert "cancel.requested" in [event.type for event in done.events]


async def test_a_crashed_job_with_a_cancel_request_is_cancelled_without_running(
    store: MemoryStore, clock: FakeClock
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    registry = blocking_registry(started, cancelled)
    job = await enqueue(store, "block", clock=clock)
    crashed = JobRunner(store, registry, settings=fast_settings(), owner="a:1", clock=clock)
    await crashed.start()
    await asyncio.wait_for(started.wait(), 5)
    await crashed.stop(release=False)
    await request_cancel(store, job.id, clock=clock)
    started.clear()
    survivor = JobRunner(store, registry, settings=fast_settings(), owner="b:2", clock=clock)
    await survivor.start()
    try:
        clock.advance(66)
        await eventually(is_status(store, job.id, "cancelled"))
    finally:
        await survivor.stop()
    assert not started.is_set()
    assert survivor.stats.claimed == 0


# ----------------------------------------------------------------------------------------------
# Limits, registry, events and deltas
# ----------------------------------------------------------------------------------------------


async def test_the_concurrency_limit_is_respected(store: MemoryStore, clock: FakeClock) -> None:
    running: set[str] = set()
    peak: list[int] = [0]
    gate = asyncio.Event()

    async def work(ctx: StepContext) -> JsonValue:
        running.add(ctx.job_id)
        peak[0] = max(peak[0], len(running))
        await gate.wait()
        running.discard(ctx.job_id)
        return None

    registry = JobRegistry()
    registry.register("work", [Step("work", work)])
    jobs = [await enqueue(store, "work", clock=clock) for _ in range(5)]
    runner = JobRunner(store, registry, settings=fast_settings(concurrency=2), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: len(running) == 2)
        await asyncio.sleep(0.1)
        assert len(runner.active_job_ids) == 2
        gate.set()

        async def finished() -> bool:
            return all(job.status == "succeeded" for job in await all_jobs(store))

        await eventually(finished)
    finally:
        await runner.stop()
    assert peak[0] == 2
    assert len(jobs) == runner.stats.succeeded == 5


async def test_jobs_of_unregistered_types_are_left_alone(
    store: MemoryStore, clock: FakeClock
) -> None:
    async def noop(ctx: StepContext) -> JsonValue:
        return None

    registry = JobRegistry()
    registry.register("known", [Step("work", noop)])
    unknown = await enqueue(store, "unknown", clock=clock)
    known = await enqueue(store, "known", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, known.id, "succeeded"))
        await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    assert await status_of(store, unknown.id) == "queued"


async def test_step_events_are_capped_on_the_job_and_all_published(
    store: MemoryStore, clock: FakeClock
) -> None:
    async def chatty(ctx: StepContext) -> JsonValue:
        for number in range(60):
            await ctx.emit("progress", {"n": number})
        ctx.delta("Hello ")
        ctx.delta("world")
        return "ok"

    registry = JobRegistry()
    registry.register("chatty", [Step("talk", chatty)])
    broker = Broker(queue_size=1000)
    subscription = broker.subscribe()
    job = await enqueue(store, "chatty", clock=clock, broker=broker)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock, broker=broker)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await runner.stop()
    done = await get_job(store, job.id)
    assert len(done.events) == MAX_EVENTS
    assert done.events[-1].seq == done.seq
    messages: list[Message] = []
    while (message := await subscription.get(max_wait=0)) is not None:
        messages.append(message)
    seqs = [m.seq for m in messages if isinstance(m, JobEventMessage)]
    assert seqs == list(range(1, done.seq + 1))  # every event, in order, exactly once
    deltas = [m.text for m in messages if isinstance(m, DeltaMessage)]
    assert deltas == ["Hello ", "world"]
    progress = [m for m in messages if isinstance(m, JobEventMessage) and m.type == "progress"]
    assert progress[0].step == "talk"
    assert progress[-1].data == {"n": 59}


async def test_step_context_rules(store: MemoryStore, clock: FakeClock) -> None:
    problems: list[str] = []
    kept: list[StepContext] = []

    async def probe(ctx: StepContext) -> JsonValue:
        kept.append(ctx)
        for label, call in (
            ("reserved", ctx.emit("status")),
            ("bad name", ctx.emit("Bad Name")),
            ("big data", ctx.emit("progress", {"blob": "x" * 5000})),
            ("bad key", ctx.record_remote_id("Bad Key", "x")),
            ("empty id", ctx.record_remote_id("response", "")),
            ("not json", ctx.checkpoint(cast(JsonValue, {"x": {1, 2}}))),
            ("no prompt", ctx.wait_for_input("")),
        ):
            try:
                await call
            except (ValueError, TypeError):
                problems.append(label)
        return None

    registry = JobRegistry()
    registry.register("probe", [Step("probe", probe)])
    job = await enqueue(store, "probe", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await runner.stop()
    assert problems == [
        "reserved",
        "bad name",
        "big data",
        "bad key",
        "empty id",
        "not json",
        "no prompt",
    ]
    with pytest.raises(RuntimeError, match="finished"):
        kept[0].delta("too late")


def test_registry_rules() -> None:
    async def noop(ctx: StepContext) -> JsonValue:
        return None

    registry = JobRegistry()
    job_type = registry.register("article.draft", [Step("plan", noop), Step("write", noop)])
    assert job_type.step_names == ("plan", "write")
    assert "article.draft" in registry
    assert registry.names() == ("article.draft",)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("article.draft", [Step("x", noop)])
    with pytest.raises(ValueError, match="at least one step"):
        registry.register("empty", [])
    with pytest.raises(ValueError, match="unique"):
        registry.register("dupe", [Step("a", noop), Step("a", noop)])
    with pytest.raises(ValueError, match="job type"):
        registry.register("Bad Type", [Step("a", noop)])
    with pytest.raises(ValueError, match="step name"):
        Step("Bad Step", noop)
    with pytest.raises(ValueError, match="code"):
        JobFailed("Not A Code")
    with pytest.raises(ValueError, match="detail"):
        JobFailed("too_long", "x" * 501)


async def test_a_runner_starts_once_and_stops_idempotently(store: MemoryStore) -> None:
    runner = JobRunner(store, JobRegistry(), settings=fast_settings())
    await runner.start()
    with pytest.raises(RuntimeError):
        await runner.start()
    await runner.stop()
    await runner.stop()
    assert not runner.claiming
    assert runner.owner.startswith("test-replica:")


# ----------------------------------------------------------------------------------------------
# Store faults on the step path
# ----------------------------------------------------------------------------------------------


class HangingStore(DelegatingStore):
    """Once ``hang`` is set, every replace and read waits until cancelled (a stuck request)."""

    hang = False

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        if self.hang:
            await asyncio.Event().wait()
        return await self.inner.replace(container, body, if_match=if_match)

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        if self.hang:
            await asyncio.Event().wait()
        return await self.inner.read(container, id, partition_key=partition_key)


async def test_a_renewal_stuck_in_the_store_cannot_keep_a_step_past_its_deadline(
    store: MemoryStore, clock: FakeClock
) -> None:
    """Self-fencing may not depend on the store answering: a renewal stuck longer than the
    lease has left must not let the step run into another replica's takeover."""
    timeline: list[str] = []
    started = asyncio.Event()

    async def held_step(ctx: StepContext) -> JsonValue:
        timeline.append(f"started fence={ctx.fence}")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            timeline.append(f"cancelled fence={ctx.fence}")
            raise
        return None  # pragma: no cover

    async def survivor_step(ctx: StepContext) -> JsonValue:
        timeline.append(f"started fence={ctx.fence}")
        return "finished by the survivor"

    holder_registry = JobRegistry()
    holder_registry.register("long", [Step("work", held_step)])
    job = await enqueue(store, "long", clock=clock)
    hanging = HangingStore(store)
    holder = JobRunner(
        hanging,
        holder_registry,
        settings=fast_settings(renew_seconds=0.02, store_timeout_seconds=30),
        owner="a:1",
        clock=clock,
    )
    await holder.start()
    await asyncio.wait_for(started.wait(), 5)
    hanging.hang = True
    await asyncio.sleep(0.1)  # renewals are now stuck in the store (30 s store timeout)
    assert timeline == ["started fence=1"]  # the lease is still good locally
    clock.advance(56)  # T0 + 56 s: past leaseUntil - skew
    await eventually(lambda: "cancelled fence=1" in timeline, within=2)
    await eventually(lambda: holder.stats.claims_lost == 1)
    assert not holder.active_job_ids

    survivor_registry = JobRegistry()
    survivor_registry.register("long", [Step("work", survivor_step)])
    survivor = JobRunner(
        store, survivor_registry, settings=fast_settings(), owner="b:2", clock=clock
    )
    await survivor.start()
    try:
        clock.advance(10)  # T0 + 66 s: the takeover is allowed now
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await survivor.stop()
        await holder.stop(release=False)
    assert timeline == ["started fence=1", "cancelled fence=1", "started fence=2"]
    assert (await get_job(store, job.id)).fence == 2


class OneOutage(DelegatingStore):
    """The first replace matching ``when`` gets an unknown outcome (its request is lost, or its
    response after it was applied) and the read that follows fails too: a short outage."""

    def __init__(
        self, inner: Store, when: Callable[[Mapping[str, Any]], bool], *, applied: bool
    ) -> None:
        super().__init__(inner)
        self.when = when
        self.applied = applied
        self.armed = True
        self.failing_reads = 0

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        if self.armed and self.when(body):
            self.armed = False
            self.failing_reads = 1
            if self.applied:
                await self.inner.replace(container, body, if_match=if_match)
            raise StoreUnavailable("503 after the SDK's retries")
        return await self.inner.replace(container, body, if_match=if_match)

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        if self.failing_reads:
            self.failing_reads -= 1
            raise StoreUnavailable("503 after the SDK's retries")
        return await self.inner.read(container, id, partition_key=partition_key)


def first_step_of(body: Mapping[str, Any]) -> Mapping[str, Any]:
    steps: object = body.get("steps")
    if isinstance(steps, list) and steps:
        first: object = cast(list[object], steps)[0]
        if isinstance(first, dict):
            return cast(dict[str, Any], first)
    return {}


OUTAGE_ON: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "remote-id": lambda body: bool(first_step_of(body).get("remoteIds")),
    "step-succeeded": lambda body: first_step_of(body).get("status") == "succeeded",
}


@pytest.mark.parametrize("renewing", [False, True], ids=["quiet", "renewing"])
@pytest.mark.parametrize("applied", [True, False], ids=["response-lost", "request-lost"])
@pytest.mark.parametrize("write", sorted(OUTAGE_ON))
async def test_a_short_outage_on_a_durable_write_neither_reruns_the_step_nor_doubles_it(
    store: MemoryStore,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    write: str,
    applied: bool,
    renewing: bool,
) -> None:
    """Before, the error reached the step: it was retried and re-issued its remote work.

    ``renewing``: the lease keeper renews while the write waits for its retry, so a renewal
    lands between the in-doubt attempt and the retry (the claim settles the attempt first).
    """
    monkeypatch.setattr("blogwriter.jobs.runner._RETRY_SECONDS", 0.2 if renewing else 0.01)
    settings = fast_settings(renew_seconds=0.02) if renewing else fast_settings()
    remote = FakeRemote()

    async def generate(ctx: StepContext) -> JsonValue:
        response_id = ctx.remote_ids.get("response")
        if response_id is None:
            response_id = await remote.create()
            await ctx.record_remote_id("response", response_id)
            await ctx.emit("remote.started", {"id": response_id})
        return {"text": await remote.poll(response_id)}

    registry = JobRegistry()
    registry.register("draft", [Step("generate", generate)])
    job = await enqueue(store, "draft", clock=clock)
    outage = OneOutage(store, OUTAGE_ON[write], applied=applied)
    runner = JobRunner(outage, registry, settings=settings, clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "succeeded"))
    finally:
        await runner.stop()
    assert not outage.armed  # the outage did happen
    done = await get_job(store, job.id)
    assert remote.created == ["resp-1"]  # never re-issued
    assert done.result == {"text": "done:resp-1"}
    assert (done.attempt, done.steps[0].runs) == (1, 1)
    assert done.steps[0].remote_ids == {"response": "resp-1"}
    types = [event.type for event in done.events]
    assert types.count("remote.started") == 1  # applied once, even when its write was retried
    assert types.count("step.succeeded") == 1
    assert runner.stats.retried == 0


async def test_a_durable_write_gives_up_at_the_lease_deadline(
    store: MemoryStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("blogwriter.jobs.runner._RETRY_SECONDS", 0.01)
    attempted = asyncio.Event()

    class Down(DelegatingStore):
        down = False

        @override
        async def replace(
            self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
        ) -> Doc:
            if self.down:
                attempted.set()
                raise StoreUnavailable("down")
            return await self.inner.replace(container, body, if_match=if_match)

    down = Down(store)
    errors: list[str] = []

    async def record(ctx: StepContext) -> JsonValue:
        down.down = True
        try:
            await ctx.record_remote_id("response", "resp-1")
        except Exception as exc:
            errors.append(type(exc).__name__)
            raise
        return None  # pragma: no cover

    registry = JobRegistry()
    registry.register("record", [Step("work", record)])
    await enqueue(store, "record", clock=clock)
    runner = JobRunner(down, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await asyncio.wait_for(attempted.wait(), 5)
        await asyncio.sleep(0.05)
        assert errors == []  # still retrying: the lease has time left
        clock.advance(56)  # past leaseUntil - skew
        await eventually(lambda: runner.stats.claims_lost == 1)
    finally:
        await runner.stop(release=False)
    assert errors in ([], ["ClaimLost"])  # cancelled by the lease guard, or gave up itself
    assert runner.stats.retried == 0  # not a step error: the job is left for a takeover


async def test_a_step_that_raises_cancelled_error_itself_is_an_error_not_a_release(
    store: MemoryStore, clock: FakeClock
) -> None:
    """Releasing gives the attempt back and re-queues at once: a step that keeps raising
    CancelledError (a cancelled future it awaited, say) would spin forever."""
    attempts: list[int] = []

    async def confused(ctx: StepContext) -> JsonValue:
        attempts.append(ctx.attempt)
        raise asyncio.CancelledError

    registry = JobRegistry()
    registry.register("confused", [Step("work", confused)])
    job = await enqueue(store, "confused", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.retried == 1)
        await asyncio.sleep(0.1)
        assert attempts == [1]  # backing off, not spinning
        retrying = await get_job(store, job.id)
        assert (retrying.status, retrying.attempt) == ("queued", 1)
        assert retrying.error is not None
        assert retrying.error.error_type == "asyncio.exceptions.CancelledError"
        clock.advance(30)
        await eventually(lambda: attempts == [1, 2])
        await eventually(is_status(store, job.id, "queued"))
        clock.advance(60)
        await eventually(is_status(store, job.id, "quarantined"))
    finally:
        await runner.stop()
    assert attempts == [1, 2, 3]
    assert runner.stats.released == 0


async def test_an_output_too_large_to_store_fails_the_job_without_a_rerun(
    store: MemoryStore, clock: FakeClock
) -> None:
    runs: list[int] = []

    async def huge(ctx: StepContext) -> JsonValue:
        runs.append(ctx.attempt)
        return "x" * 2_000_000  # the job document would pass the 2 MB limit

    registry = JobRegistry()
    registry.register("huge", [Step("work", huge)])
    job = await enqueue(store, "huge", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(is_status(store, job.id, "failed"))
    finally:
        await runner.stop()
    failed = await get_job(store, job.id)
    assert runs == [1]
    assert failed.error is not None
    assert (failed.error.code, failed.error.step) == ("invalid_output", "work")
    assert failed.steps[0].status == "failed"
    assert failed.steps[0].output is None
    assert runner.stats.retried == 0


async def test_a_step_ending_with_an_error_after_losing_its_lease_logs_no_stray_traceback(
    store: MemoryStore, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """When the lease guard wins, the step's own exception is moot; it used to be left
    unretrieved, so asyncio logged it with a full traceback (outside the log allowlist)."""
    started = asyncio.Event()

    async def stubborn(ctx: StepContext) -> JsonValue:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("cleanup failed") from None
        return None  # pragma: no cover

    class Partitioned(DelegatingStore):
        broken = False

        @override
        async def replace(
            self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
        ) -> Doc:
            if self.broken:
                raise StoreUnavailable("partitioned")
            return await self.inner.replace(container, body, if_match=if_match)

    registry = JobRegistry()
    registry.register("stubborn", [Step("work", stubborn)])
    await enqueue(store, "stubborn", clock=clock)
    partitioned = Partitioned(store)
    runner = JobRunner(
        partitioned, registry, settings=fast_settings(renew_seconds=0.02), clock=clock
    )
    await runner.start()
    try:
        await asyncio.wait_for(started.wait(), 5)
        partitioned.broken = True
        clock.advance(56)  # past leaseUntil - skew: the guard cancels the step
        await eventually(lambda: runner.stats.claims_lost == 1)
        await eventually(lambda: not runner.active_job_ids)
    finally:
        await runner.stop(release=False)
    gc.collect()
    await asyncio.sleep(0)
    assert "never retrieved" not in caplog.text
