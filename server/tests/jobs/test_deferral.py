"""Job deferral (contract I-7): a step raising ``JobDeferred`` puts its job back in the queue
until later without using up an attempt; 200 deferrals in a row end in quarantine.

Lease and ``runAfter`` times follow the fake clock; the claim loops run on real time with a
10 ms poll (``fast_settings``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import JsonValue

from blogwriter.db import MemoryStore, Store
from blogwriter.jobs import (
    MAX_DEFERRALS,
    JobDeferred,
    JobRegistry,
    JobRunner,
    QuarantineNotice,
    Step,
    StepContext,
    enqueue,
    get_job,
    provide_input,
    retry_job,
    to_iso,
)
from blogwriter.jobs.runner import MAX_DEFER, MIN_DEFER
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
DEFERRED_TYPE = "blogwriter.jobs.runner.JobDeferred"


def single_step(name: str, outcome: Callable[[StepContext], Exception | None]) -> JobRegistry:
    """A one-step job type whose step raises what ``outcome`` returns (or returns ``None``)."""

    async def step(ctx: StepContext) -> JsonValue:
        error = outcome(ctx)
        if error is not None:
            raise error
        return None

    registry = JobRegistry()
    registry.register(name, [Step("work", step)])
    return registry


def status_is(store: Store, job_id: str, status: str) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return (await get_job(store, job_id)).status == status

    return check


async def set_deferrals(store: Store, job_id: str, count: int) -> None:
    """Pretend the job was already deferred ``count`` times in a row."""
    raw = await store.read("jobs", job_id)
    await store.replace("jobs", {**raw.body, "deferrals": count}, if_match=raw.etag)


# ----------------------------------------------------------------------------------------------
# The exception
# ----------------------------------------------------------------------------------------------


def test_job_deferred_needs_an_aware_time_and_a_code() -> None:
    with pytest.raises(ValueError, match="aware"):
        JobDeferred(datetime(2026, 11, 26, 12), "brake_over_budget")
    with pytest.raises(ValueError, match="reason"):
        JobDeferred(T0, "Not A Code")
    local = datetime(2026, 11, 26, 7, 0, tzinfo=ZoneInfo("America/New_York"))
    deferred = JobDeferred(local, "services_starting")
    assert (deferred.run_after, deferred.reason) == (local, "services_starting")
    assert str(deferred) == "deferred until 2026-11-26T12:00:00.000000Z: services_starting"


# ----------------------------------------------------------------------------------------------
# The runner
# ----------------------------------------------------------------------------------------------


async def test_a_deferred_job_waits_without_using_an_attempt(
    store: MemoryStore, clock: FakeClock
) -> None:
    runs: list[tuple[int, bool]] = []

    async def wait_then_work(ctx: StepContext) -> JsonValue:
        runs.append((ctx.attempt, ctx.resumed))
        await ctx.checkpoint({"seen": len(runs)})
        if len(runs) < 3:
            raise JobDeferred(clock.now() + timedelta(minutes=10), "services_starting")
        return {"output": ctx.output}

    registry = JobRegistry()
    registry.register("patient", [Step("work", wait_then_work)])
    job = await enqueue(store, "patient", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.deferred == 1)
        deferred = await get_job(store, job.id)
        assert deferred.status == "queued"
        assert deferred.run_after == to_iso(T0 + timedelta(minutes=10))
        assert deferred.attempt == 0  # given back, like a release
        assert deferred.deferrals == 1
        assert deferred.error is None
        assert (deferred.owner, deferred.lease_until) == (None, None)
        assert deferred.steps[0].status == "pending"
        assert deferred.steps[0].output == {"seen": 1}  # checkpoints survive
        assert deferred.events[-1].type == "status"
        assert deferred.events[-1].data == {
            "status": "queued",
            "reason": "deferred",
            "code": "services_starting",
        }
        await asyncio.sleep(0.05)
        assert len(runs) == 1  # not before its time
        clock.advance(timedelta(minutes=10))
        await eventually(lambda: runner.stats.deferred == 2)
        assert (await get_job(store, job.id)).deferrals == 2
        clock.advance(timedelta(minutes=10))
        await eventually(status_is(store, job.id, "succeeded"))
    finally:
        await runner.stop()
    done = await get_job(store, job.id)
    assert runs == [(1, False), (1, True), (1, True)]  # never a second attempt
    assert done.attempt == 1
    assert done.deferrals == 0
    assert done.result == {"output": {"seen": 3}}
    assert done.steps[0].runs == 3
    assert [event.type for event in done.events].count("step.resumed") == 2
    assert (runner.stats.retried, runner.stats.quarantined, runner.stats.deferred) == (0, 0, 2)


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        (timedelta(seconds=-30), timedelta(seconds=1)),
        (timedelta(0), timedelta(seconds=1)),
        (timedelta(minutes=10), timedelta(minutes=10)),
        (timedelta(hours=24), timedelta(hours=24)),
        (timedelta(days=3), timedelta(hours=24)),
    ],
)
async def test_the_time_a_job_waits_is_clamped_to_one_second_and_one_day(
    store: MemoryStore, clock: FakeClock, asked: timedelta, expected: timedelta
) -> None:
    registry = single_step(
        "defer", lambda _ctx: JobDeferred(clock.now() + asked, "brake_over_budget")
    )
    job = await enqueue(store, "defer", clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.deferred == 1)
    finally:
        await runner.stop()
    deferred = await get_job(store, job.id)
    assert deferred.run_after == to_iso(T0 + expected)
    assert MIN_DEFER <= expected <= MAX_DEFER


async def test_a_job_deferred_200_times_in_a_row_is_quarantined_on_the_next(
    store: MemoryStore, clock: FakeClock
) -> None:
    runs: list[int] = []

    def stuck(ctx: StepContext) -> JobDeferred:
        runs.append(ctx.attempt)
        return JobDeferred(clock.now(), "services_starting")  # never ready

    notices: list[QuarantineNotice] = []

    async def alert(notice: QuarantineNotice) -> None:
        notices.append(notice)

    def deferred(count: int) -> Callable[[], bool]:
        return lambda: runner.stats.deferred >= count

    job = await enqueue(store, "stuck", clock=clock)
    runner = JobRunner(
        store,
        single_step("stuck", stuck),
        settings=fast_settings(),
        clock=clock,
        on_quarantine=alert,
    )
    await runner.start()
    try:
        for count in range(1, MAX_DEFERRALS + 1):
            await eventually(deferred(count), interval=0.001)
            clock.advance(MIN_DEFER)
            runner.wake()
        await eventually(status_is(store, job.id, "quarantined"))
    finally:
        await runner.stop()
    assert MAX_DEFERRALS == 200
    assert len(runs) == MAX_DEFERRALS + 1
    assert set(runs) == {1}  # 201 runs, not one attempt burned
    quarantined = await get_job(store, job.id)
    assert quarantined.deferrals == MAX_DEFERRALS
    assert quarantined.attempt == 1
    assert quarantined.owner is None
    assert quarantined.error is not None
    assert quarantined.error.code == "max_deferrals"
    assert (quarantined.error.step, quarantined.error.error_type) == ("work", DEFERRED_TYPE)
    assert quarantined.steps[0].status == "pending"
    assert quarantined.ttl is not None  # finished jobs are garbage-collected
    assert notices == [
        QuarantineNotice(
            job_id=job.id, job_type="stuck", attempts=1, step="work", error_type=DEFERRED_TYPE
        )
    ]
    assert runner.stats.deferred == MAX_DEFERRALS
    assert (runner.stats.quarantined, runner.stats.retried) == (1, 0)

    retried = await retry_job(store, job.id, clock=clock)  # the owner, from System
    assert (retried.status, retried.attempt, retried.deferrals) == ("queued", 0, 0)
    assert retried.error is None


async def test_the_200th_deferral_in_a_row_still_waits(
    store: MemoryStore, clock: FakeClock
) -> None:
    job = await enqueue(store, "stuck", clock=clock)
    await set_deferrals(store, job.id, MAX_DEFERRALS - 1)
    registry = single_step("stuck", lambda _ctx: JobDeferred(clock.now(), "services_starting"))
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.deferred == 1)
        waiting = await get_job(store, job.id)
        assert (waiting.status, waiting.deferrals) == ("queued", MAX_DEFERRALS)
        clock.advance(MIN_DEFER)
        await eventually(status_is(store, job.id, "quarantined"))
    finally:
        await runner.stop()
    assert runner.stats.deferred == 1


async def test_a_succeeded_step_ends_a_run_of_deferrals(
    store: MemoryStore, clock: FakeClock
) -> None:
    async def first(ctx: StepContext) -> JsonValue:
        return "done"

    async def second(ctx: StepContext) -> JsonValue:
        raise JobDeferred(clock.now() + timedelta(hours=1), "brake_over_budget")

    registry = JobRegistry()
    registry.register("two", [Step("first", first), Step("second", second)])
    job = await enqueue(store, "two", clock=clock)
    await set_deferrals(store, job.id, MAX_DEFERRALS)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.deferred == 1)
    finally:
        await runner.stop()
    waiting = await get_job(store, job.id)
    assert waiting.status == "queued"  # progress was made: a new run of deferrals
    assert waiting.deferrals == 1
    assert [step.status for step in waiting.steps] == ["succeeded", "pending"]
    assert runner.stats.quarantined == 0


async def test_a_retry_ends_a_run_of_deferrals(store: MemoryStore, clock: FakeClock) -> None:
    runs: list[int] = []

    def flaky_then_waiting(ctx: StepContext) -> Exception:
        runs.append(ctx.attempt)
        if len(runs) == 1:
            return ConnectionError("transient")
        return JobDeferred(clock.now() + timedelta(minutes=10), "services_starting")

    job = await enqueue(store, "flaky", clock=clock)
    await set_deferrals(store, job.id, MAX_DEFERRALS)
    registry = single_step("flaky", flaky_then_waiting)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.retried == 1)
        assert (await get_job(store, job.id)).deferrals == 0
        clock.advance(30)  # the retry backoff
        await eventually(lambda: runner.stats.deferred == 1)
    finally:
        await runner.stop()
    waiting = await get_job(store, job.id)
    assert (waiting.status, waiting.deferrals) == ("queued", 1)
    assert runs == [1, 2]
    assert waiting.attempt == 1  # the error used one; the deferral gave its own back
    assert waiting.error is not None
    assert waiting.error.code == "step_error"  # what the last error was; the job waits


async def test_an_input_wait_ends_a_run_of_deferrals(store: MemoryStore, clock: FakeClock) -> None:
    async def ask(ctx: StepContext) -> JsonValue:
        answer = await ctx.wait_for_input("Pick one.")
        raise JobDeferred(clock.now() + timedelta(minutes=10), f"waiting_{answer}")

    registry = JobRegistry()
    registry.register("ask", [Step("work", ask)])
    job = await enqueue(store, "ask", clock=clock)
    await set_deferrals(store, job.id, MAX_DEFERRALS)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(status_is(store, job.id, "waiting_input"))
        assert (await get_job(store, job.id)).deferrals == 0
        await provide_input(store, job.id, "a", clock=clock)
        runner.wake()
        await eventually(lambda: runner.stats.deferred == 1)
    finally:
        await runner.stop()
    waiting = await get_job(store, job.id)
    assert (waiting.status, waiting.deferrals) == ("queued", 1)
    assert waiting.events[-1].data["code"] == "waiting_a"


async def test_a_deferral_during_shutdown_grace_is_still_recorded(
    store: MemoryStore, clock: FakeClock
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(ctx: StepContext) -> JsonValue:
        started.set()
        await release.wait()
        raise JobDeferred(clock.now() + timedelta(minutes=5), "services_starting")

    registry = JobRegistry()
    registry.register("slow", [Step("work", slow)])
    job = await enqueue(store, "slow", clock=clock)
    runner = JobRunner(
        store, registry, settings=fast_settings(shutdown_grace_seconds=5.0), clock=clock
    )
    await runner.start()
    await asyncio.wait_for(started.wait(), 5)
    stopping = asyncio.create_task(runner.stop())
    await asyncio.sleep(0.02)
    release.set()
    await stopping
    deferred = await get_job(store, job.id)
    assert (deferred.status, deferred.attempt, deferred.deferrals) == ("queued", 0, 1)
    assert deferred.run_after == to_iso(T0 + timedelta(minutes=5))
    assert runner.stats.deferred == 1
