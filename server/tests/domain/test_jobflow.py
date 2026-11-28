"""Enqueue once, parent and child jobs on a real runner, deferral and service checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import JsonValue

from blogwriter.ai.ledger import BrakeEngaged, BrakeStatus, BudgetBrake, Origin, UsageRecord
from blogwriter.db import MemoryStore, NotFound
from blogwriter.domain.jobflow import (
    BRAKE_DEFER,
    LEDGER_DEFER,
    SERVICES_DEFER,
    JobDeferred,
    cancel_job,
    child_result_of,
    defer_on_brake,
    deliver_child_result,
    enqueue_once,
    finish_child,
    parent_of,
    past_max_lateness,
    require_service,
    rerun_job_id,
    step_services,
    wait_for_child,
)
from blogwriter.domain.jobtypes import ChildResult, ParentRef
from blogwriter.domain.ports import services_installed
from blogwriter.domain.testing import StoreJobQueue, make_services
from blogwriter.jobs import (
    InputRequest,
    Job,
    JobFailed,
    JobRegistry,
    JobRunner,
    Step,
    StepContext,
    enqueue,
    get_job,
    to_iso,
)
from blogwriter.jobs import runner as jobs_runner
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 12, tzinfo=UTC)
WINDOW = timedelta(milliseconds=200)
INTERVAL = timedelta(milliseconds=10)

# ---------------------------------------------------------------------------------------------
# enqueue_once
# ---------------------------------------------------------------------------------------------


async def test_reuse_returns_whatever_exists() -> None:
    queue = StoreJobQueue(MemoryStore())
    first = await enqueue_once(queue, "gates.run", {"n": 1}, job_id="gates-a")
    again = await enqueue_once(queue, "gates.run", {"n": 2}, job_id="gates-a")
    assert again.id == first.id
    assert again.input == {"n": 1}
    await queue.cancel(first.id)
    assert (await enqueue_once(queue, "gates.run", None, job_id="gates-a")).status == "cancelled"


async def test_rerun_keeps_active_jobs_and_reruns_finished_ones() -> None:
    queue = StoreJobQueue(MemoryStore())
    first = await enqueue_once(queue, "publish.article", {"v": 1}, job_id="publish-a")
    active = await enqueue_once(
        queue, "publish.article", {"v": 2}, job_id="publish-a", on_conflict="rerun"
    )
    assert active.id == first.id
    await queue.cancel(first.id)  # a veto cancelled it; the owner approves again
    later = datetime(2026, 12, 7, 14, tzinfo=UTC)
    rerun = await enqueue_once(
        queue, "publish.article", {"v": 2}, job_id="publish-a", run_after=later, on_conflict="rerun"
    )
    assert rerun.id == rerun_job_id("publish-a", 1) == "publish-a-r1"
    assert rerun.input == {"v": 2}
    assert rerun.run_after == "2026-12-07T14:00:00.000000Z"
    again = await enqueue_once(
        queue, "publish.article", {"v": 3}, job_id="publish-a", on_conflict="rerun"
    )
    assert again.id == "publish-a-r1"


async def test_replace_cancels_the_active_job() -> None:
    queue = StoreJobQueue(MemoryStore())
    scheduled = await enqueue_once(queue, "publish.article", {"at": "monday"}, job_id="publish-b")
    now = await enqueue_once(
        queue, "publish.article", {"at": "now"}, job_id="publish-b", on_conflict="replace"
    )
    assert now.id == "publish-b-r1"
    assert (await queue.get(scheduled.id)).status == "cancelled"


async def test_enqueue_once_refuses_id_collisions_and_unregistered_types() -> None:
    store = MemoryStore()
    queue = StoreJobQueue(store, registered={"gates.run"})
    await enqueue_once(queue, "gates.run", None, job_id="shared-id")
    with pytest.raises(ValueError, match=r"belongs to a gates\.run job"):
        await enqueue_once(StoreJobQueue(store), "posts.check", None, job_id="shared-id")
    with pytest.raises(ValueError, match="not registered"):
        await enqueue_once(queue, "posts.check", None, job_id="other-id")
    with pytest.raises(ValueError, match="start at 1"):
        rerun_job_id("x", 0)


# ---------------------------------------------------------------------------------------------
# Child results without a runner
# ---------------------------------------------------------------------------------------------


def job(status: str, **fields: object) -> Job:
    stamp = "2026-11-26T12:00:00.000000Z"
    return Job.model_validate(
        {
            "id": "job-child",
            "type": "research.run",
            "status": status,
            "runAfter": stamp,
            "createdAt": stamp,
            "updatedAt": stamp,
            **fields,
        }
    )


def test_child_results_from_the_child_itself() -> None:
    assert child_result_of(job("running")) is None
    assert child_result_of(job("quarantined")) is None
    failed = job("failed", error={"code": "blocklist_hit", "at": "2026-11-26T12:00:00.000000Z"})
    assert child_result_of(failed) == ChildResult(ok=False, error_code="blocklist_hit")
    assert child_result_of(job("cancelled")) == ChildResult(ok=False, error_code="cancelled")
    done = job("succeeded", result={"ok": True, "ref": "brief-1"})
    assert child_result_of(done) == ChildResult(ok=True, ref="brief-1")
    odd = job("succeeded", result=["not", "a", "child", "result"])
    assert child_result_of(odd) == ChildResult(ok=False, error_code="child_result_missing")


def test_a_recorded_result_wins_until_the_child_is_retried() -> None:
    recorded = job("running")
    recorded.add_event("child.result", at=recorded.created_at, data={"ok": True, "ref": "b-1"})
    assert child_result_of(recorded) == ChildResult(ok=True, ref="b-1")
    retried = recorded.model_copy(deep=True)
    retried.add_event(
        "status", at=recorded.created_at, data={"status": "queued", "reason": "retry"}
    )
    assert child_result_of(retried) is None


async def test_delivery_stops_when_the_parent_does_not_need_telling() -> None:
    queue = StoreJobQueue(MemoryStore())
    parent = await queue.enqueue("pipeline.article", None, job_id="parent-1")
    ref = ParentRef(job_id=parent.id, key="research")
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = ChildResult(ok=True, ref="brief-1")
    assert not await deliver_child_result(queue, ref, result, sleep=sleep)  # queued: it reads us
    assert sleeps == []
    missing = ParentRef(job_id="no-such-job", key="research")
    assert not await deliver_child_result(queue, missing, result, sleep=sleep)
    with pytest.raises(ValueError, match="interval"):
        await deliver_child_result(queue, ref, result, interval=timedelta(0))


async def test_delivery_retries_while_the_parent_is_still_running() -> None:
    store = MemoryStore()
    queue = StoreJobQueue(store)
    stamp = "2026-11-26T12:00:00.000000Z"
    running = job("running", steps=[{"name": "research", "status": "running"}])
    parent = running.model_copy(update={"id": "parent-2", "type": "pipeline.article"})
    await store.create("jobs", parent.to_body())
    slept: list[float] = []

    async def parent_suspends_meanwhile(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 2:
            raw = await store.read("jobs", "parent-2")
            waiting = Job.from_body(raw.body)
            waiting.status = "waiting_input"
            waiting.awaiting = InputRequest(
                step="research", key="research", prompt="Research is running.", requested_at=stamp
            )
            step = waiting.step("research")
            assert step is not None
            step.status = "waiting_input"
            await store.replace("jobs", waiting.to_body(), if_match=raw.etag)

    result = ChildResult(ok=True, ref="brief-9")
    ref = ParentRef(job_id="parent-2", key="research")
    delivered = await deliver_child_result(queue, ref, result, sleep=parent_suspends_meanwhile)
    assert delivered
    assert slept == [5.0, 5.0]
    resumed = await queue.get("parent-2")
    assert resumed.status == "queued"
    research = resumed.step("research")
    assert research is not None
    assert ChildResult.from_json(research.inputs["research"]) == result


# ---------------------------------------------------------------------------------------------
# Parent and child on a real runner
# ---------------------------------------------------------------------------------------------

type ChildBehaviour = Callable[[StepContext, StoreJobQueue, ParentRef], Awaitable[JsonValue]]


async def run_parent_and_child(
    child: ChildBehaviour, *, before_wait: Callable[[StoreJobQueue], Awaitable[None]] | None = None
) -> tuple[Job, Job]:
    store = MemoryStore()
    clock = FakeClock(T0)
    queue = StoreJobQueue(store, clock=clock)

    async def parent_step(ctx: StepContext) -> JsonValue:
        child_id = f"child-{ctx.job_id}"
        payload: JsonValue = {"parent": {"jobId": ctx.job_id, "key": "child"}}
        await enqueue_once(queue, "test.child", payload, job_id=child_id)
        if before_wait is not None:
            await before_wait(queue)
        result = await wait_for_child(ctx, queue, child_id, key="child", prompt="Child running.")
        return result.to_json()

    async def child_step(ctx: StepContext) -> JsonValue:
        assert isinstance(ctx.input, dict)
        parent = ParentRef.model_validate(ctx.input["parent"])
        return await child(ctx, queue, parent)

    registry = JobRegistry()
    registry.register("test.parent", [Step("wait", parent_step)])
    registry.register("test.child", [Step("work", child_step)])
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await queue.enqueue("test.parent", None, job_id="parent")
    await runner.start()
    try:

        async def settled() -> bool:
            if not (await queue.get("parent")).is_terminal:
                return False
            try:
                return (await queue.get("child-parent")).is_terminal
            except NotFound:
                return False

        await eventually(settled, within=10)
    finally:
        await runner.stop()
    return await queue.get("parent"), await queue.get("child-parent")


def waited(parent: Job) -> bool:
    return any(event.type == "input.requested" for event in parent.events)


async def test_the_parent_waits_and_the_child_delivers() -> None:
    async def child(ctx: StepContext, queue: StoreJobQueue, parent: ParentRef) -> JsonValue:
        async def parent_waiting() -> bool:
            return (await queue.get(parent.job_id)).status == "waiting_input"

        await eventually(parent_waiting, within=5)
        result = ChildResult(ok=True, ref="brief-7")
        await finish_child(ctx, queue, parent, result, window=WINDOW, interval=INTERVAL)
        return result.to_json()

    parent, child_job = await run_parent_and_child(child)
    assert parent.status == "succeeded"
    assert ChildResult.from_json(parent.result) == ChildResult(ok=True, ref="brief-7")
    assert waited(parent)
    assert child_job.status == "succeeded"


async def test_a_child_that_finishes_before_the_parent_suspends_is_not_lost() -> None:
    async def child(ctx: StepContext, queue: StoreJobQueue, parent: ParentRef) -> JsonValue:
        result = ChildResult(ok=True, ref="brief-fast")
        # The parent is still running: delivery fails, the recorded result does not.
        await finish_child(ctx, queue, parent, result, window=WINDOW, interval=INTERVAL)
        return result.to_json()

    async def let_the_child_finish(queue: StoreJobQueue) -> None:
        async def child_done() -> bool:
            try:
                return (await queue.get("child-parent")).is_terminal
            except NotFound:
                return False

        await eventually(child_done, within=5)

    parent, child_job = await run_parent_and_child(child, before_wait=let_the_child_finish)
    assert child_job.status == "succeeded"
    assert parent.status == "succeeded"
    assert ChildResult.from_json(parent.result) == ChildResult(ok=True, ref="brief-fast")
    assert not waited(parent)


async def test_a_failing_child_reports_its_code() -> None:
    async def child(ctx: StepContext, queue: StoreJobQueue, parent: ParentRef) -> JsonValue:
        async def parent_waiting() -> bool:
            return (await queue.get(parent.job_id)).status == "waiting_input"

        await eventually(parent_waiting, within=5)
        failure = ChildResult(ok=False, error_code="blocklist_hit")
        await finish_child(ctx, queue, parent, failure, window=WINDOW, interval=INTERVAL)
        raise JobFailed("blocklist_hit")

    parent, child_job = await run_parent_and_child(child)
    assert child_job.status == "failed"
    assert ChildResult.from_json(parent.result) == ChildResult(ok=False, error_code="blocklist_hit")


async def test_a_missing_child_fails_the_parent() -> None:
    store = MemoryStore()
    clock = FakeClock(T0)
    queue = StoreJobQueue(store, clock=clock)

    async def parent_step(ctx: StepContext) -> JsonValue:
        await wait_for_child(ctx, queue, "never-enqueued", key="child", prompt="Waiting.")
        return None

    registry = JobRegistry()
    registry.register("test.orphan", [Step("wait", parent_step)])
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await queue.enqueue("test.orphan", None, job_id="orphan")
    await runner.start()
    try:

        async def done() -> bool:
            return (await queue.get("orphan")).is_terminal

        await eventually(done, within=5)
    finally:
        await runner.stop()
    failed = await queue.get("orphan")
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "child_missing"


# ---------------------------------------------------------------------------------------------
# Deferral, services and lateness
# ---------------------------------------------------------------------------------------------


def test_the_brake_defers_instead_of_failing() -> None:
    status = BrakeStatus(T0.date(), Decimal(30), Decimal(5), Decimal(15))
    with pytest.raises(JobDeferred) as caught, defer_on_brake(T0):
        raise BrakeEngaged(status)
    assert caught.value.run_after == T0 + BRAKE_DEFER
    assert caught.value.reason == "brake_over_budget"
    with pytest.raises(JobDeferred) as unknown, defer_on_brake(T0):
        raise BrakeEngaged(None, reason="ledger_unavailable")
    assert unknown.value.run_after == T0 + LEDGER_DEFER
    with pytest.raises(RuntimeError), defer_on_brake(T0):
        raise RuntimeError("other errors pass through")


def test_job_deferred_is_validated() -> None:
    with pytest.raises(ValueError, match="aware"):
        JobDeferred(datetime(2026, 11, 26, 12), "brake_over_budget")
    with pytest.raises(ValueError, match="reason"):
        JobDeferred(T0, "Not A Code")


def test_job_deferred_is_the_runners_own_class() -> None:
    """Contract I-7: the class lives next to ``JobFailed`` (``jobs`` cannot import ``domain``);
    ``domain.jobflow`` re-exports it, so steps and the runner agree on one class."""
    assert JobDeferred is jobs_runner.JobDeferred


class _SpendOnT0:
    """A ledger with ``usd`` spent on T0's day (UTC) and nothing on any other day."""

    def __init__(self, usd: Decimal) -> None:
        self.usd = usd

    async def record(self, record: UsageRecord) -> None:
        raise AssertionError("no model call is made while braked")

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        return self.usd if start <= T0 < end else Decimal(0)


class _UnreadableLedger:
    async def record(self, record: UsageRecord) -> None:
        raise AssertionError("no model call is made while braked")

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        raise ConnectionError("the ledger is down")


def _model_call_registry(brake: BudgetBrake, clock: FakeClock, calls: list[Origin]) -> JobRegistry:
    """One step that makes an AI call the way packages do: the router's brake check, inside
    ``defer_on_brake`` (the job input says whose call it is)."""

    async def call_model(ctx: StepContext) -> JsonValue:
        origin: Origin = "owner" if ctx.input == {"origin": "owner"} else "background"
        calls.append(origin)
        with defer_on_brake(clock.now()):
            await brake.check(origin)
        return {"origin": origin, "attempt": ctx.attempt}

    registry = JobRegistry()
    registry.register("signals.triage", [Step("triage", call_model)])
    return registry


def _deferred(runner: JobRunner, count: int) -> Callable[[], bool]:
    return lambda: runner.stats.deferred >= count


async def test_background_work_waits_out_the_brake_without_burning_attempts() -> None:
    """The brake path: a background call that meets an engaged brake defers its job (1 h)
    instead of failing it; the job never uses up its three attempts, never reaches quarantine,
    and runs once the brake lets go (the next day). Owner calls are never braked."""
    clock = FakeClock(T0)
    store = MemoryStore()
    brake = BudgetBrake(
        _SpendOnT0(Decimal(5)), daily_cap_usd=Decimal(1), timezone=UTC, now=clock.now
    )
    calls: list[Origin] = []
    registry = _model_call_registry(brake, clock, calls)
    background = await enqueue(store, "signals.triage", {"origin": "background"}, clock=clock)
    owner = await enqueue(store, "signals.triage", {"origin": "owner"}, clock=clock)
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await runner.start()
    try:
        await eventually(lambda: runner.stats.succeeded == 1)  # the owner's job is not braked
        hours_left_today = 12
        for count in range(1, hours_left_today + 1):
            await eventually(_deferred(runner, count))
            waiting = await get_job(store, background.id)
            assert waiting.status == "queued"
            assert waiting.attempt == 0  # given back every time
            assert waiting.error is None
            assert waiting.deferrals == count
            assert waiting.run_after == to_iso(clock.now() + BRAKE_DEFER)
            assert waiting.events[-1].data == {
                "status": "queued",
                "reason": "deferred",
                "code": "brake_over_budget",
            }
            clock.advance(BRAKE_DEFER)
            runner.wake()
        await eventually(lambda: runner.stats.succeeded == 2)  # midnight: the brake let go
    finally:
        await runner.stop()
    done = await get_job(store, background.id)
    assert done.status == "succeeded"
    assert done.result == {"origin": "background", "attempt": 1}
    assert (done.attempt, done.deferrals) == (1, 0)
    assert (await get_job(store, owner.id)).result == {"origin": "owner", "attempt": 1}
    assert calls.count("background") == hours_left_today + 1
    assert runner.stats.deferred == hours_left_today
    assert (runner.stats.retried, runner.stats.quarantined) == (0, 0)


async def test_an_unreadable_ledger_defers_background_work_for_ten_minutes() -> None:
    clock = FakeClock(T0)
    store = MemoryStore()
    brake = BudgetBrake(_UnreadableLedger(), daily_cap_usd=Decimal(1), timezone=UTC, now=clock.now)
    calls: list[Origin] = []
    job = await enqueue(store, "signals.triage", {"origin": "background"}, clock=clock)
    runner = JobRunner(
        store, _model_call_registry(brake, clock, calls), settings=fast_settings(), clock=clock
    )
    await runner.start()
    try:
        await eventually(_deferred(runner, 1))
    finally:
        await runner.stop()
    waiting = await get_job(store, job.id)
    assert (waiting.status, waiting.attempt, waiting.error) == ("queued", 0, None)
    assert waiting.run_after == to_iso(T0 + LEDGER_DEFER)
    assert waiting.events[-1].data == {
        "status": "queued",
        "reason": "deferred",
        "code": "brake_ledger_unavailable",
    }


def test_services_starting_defers_and_missing_services_fail() -> None:
    with pytest.raises(JobDeferred) as caught:
        step_services(now=T0)
    assert caught.value.run_after == T0 + SERVICES_DEFER
    assert caught.value.reason == "services_starting"
    services = make_services(ai=None)
    with services_installed(services):
        assert step_services() is services
        with pytest.raises(JobFailed) as failed:
            require_service(services.ai, "ai")
        assert failed.value.code == "ai_unavailable"
        assert require_service(services.blobs, "storage") is services.blobs


def test_lateness() -> None:
    meant = "2026-12-07T14:00:00.000000Z"
    hour = timedelta(hours=1)
    assert not past_max_lateness(meant, at_minutes(meant, 60), hour)
    assert past_max_lateness(meant, at_minutes(meant, 61), hour)
    assert not past_max_lateness(T0, T0 - timedelta(days=1), hour)


def at_minutes(stamp: str, minutes: int) -> datetime:
    return datetime.fromisoformat(stamp[:-1]).replace(tzinfo=UTC) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------------------------
# Cancelling a child (it runs no step code of its own, so cancel_job tells the parent)
# ---------------------------------------------------------------------------------------------


def test_a_child_with_a_pending_cancel_request_reads_as_cancelled() -> None:
    assert child_result_of(job("running", cancelRequested=True)) == ChildResult(
        ok=False, error_code="cancelled"
    )


async def test_cancelling_a_queued_child_wakes_its_waiting_parent() -> None:
    """The child is delayed (``run_after`` an hour out) and the parent waits for it; a System
    cancel of the child must deliver ``cancelled`` instead of leaving the parent waiting."""
    store = MemoryStore()
    clock = FakeClock(T0)
    queue = StoreJobQueue(store, clock=clock)

    async def parent_step(ctx: StepContext) -> JsonValue:
        payload: JsonValue = {"parent": {"jobId": ctx.job_id, "key": "c"}}
        await enqueue_once(
            queue, "test.child", payload, job_id="child-1", run_after=T0 + timedelta(hours=1)
        )
        result = await wait_for_child(ctx, queue, "child-1", key="c", prompt="Child running.")
        return result.to_json()

    async def child_step(ctx: StepContext) -> JsonValue:
        raise AssertionError("a cancelled child never runs")

    registry = JobRegistry()
    registry.register("test.parent", [Step("wait", parent_step)])
    registry.register("test.child", [Step("work", child_step)])
    runner = JobRunner(store, registry, settings=fast_settings(), clock=clock)
    await queue.enqueue("test.parent", None, job_id="parent")
    await runner.start()
    try:

        async def parent_waiting() -> bool:
            return (await queue.get("parent")).status == "waiting_input"

        await eventually(parent_waiting, within=5)
        cancelled = await cancel_job(queue, "child-1", window=WINDOW, interval=INTERVAL)
        assert cancelled.status == "cancelled"

        async def parent_done() -> bool:
            return (await queue.get("parent")).is_terminal

        await eventually(parent_done, within=5)
    finally:
        await runner.stop()
    parent = await queue.get("parent")
    assert parent.status == "succeeded"
    assert ChildResult.from_json(parent.result) == ChildResult(ok=False, error_code="cancelled")


async def test_cancelling_a_job_without_a_parent_tells_no_one() -> None:
    queue = StoreJobQueue(MemoryStore())
    lone = await queue.enqueue("test.child", {"origin": "owner"}, job_id="lone")
    assert parent_of(lone) is None
    assert (await cancel_job(queue, "lone")).status == "cancelled"
    finished = await cancel_job(queue, "lone")  # a finished job is returned unchanged
    assert finished.status == "cancelled"
