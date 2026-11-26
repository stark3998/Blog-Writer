"""Job operations without a claim: enqueue, read, cancel, answer input, retry."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import JsonValue

from blogwriter.db import Conflict, MemoryStore, NotFound
from blogwriter.jobs.broker import Broker, JobEventMessage
from blogwriter.jobs.models import InputRequest, Job
from blogwriter.jobs.queue import (
    JobStateError,
    enqueue,
    get_job,
    provide_input,
    request_cancel,
    retry_job,
)
from blogwriter.jobs.testing import FakeClock


async def save(store: MemoryStore, job: Job) -> None:
    current = await store.read("jobs", job.id)
    await store.replace("jobs", job.to_body(), if_match=current.etag)


async def test_enqueue_creates_a_queued_job_and_publishes_its_event(
    store: MemoryStore, clock: FakeClock
) -> None:
    broker = Broker()
    subscription = broker.subscribe()
    job = await enqueue(
        store, "article.draft", {"topic": "x"}, steps=["plan", "write"], clock=clock, broker=broker
    )
    assert job.id.startswith("job-")
    assert (job.status, job.attempt, job.fence, job.seq) == ("queued", 0, 0, 1)
    assert [step.name for step in job.steps] == ["plan", "write"]
    assert job.input == {"topic": "x"}
    assert job.run_after == job.created_at == "2026-11-26T12:00:00.000000Z"
    message = await subscription.get(max_wait=0)
    assert isinstance(message, JobEventMessage)
    assert (message.job_id, message.seq, message.type) == (job.id, 1, "status")
    assert message.data == {"status": "queued"}
    assert await get_job(store, job.id) == job


async def test_enqueue_with_an_id_is_idempotent(store: MemoryStore, clock: FakeClock) -> None:
    await enqueue(store, "crawl", job_id="test-fixed", clock=clock)
    with pytest.raises(Conflict):
        await enqueue(store, "crawl", job_id="test-fixed", clock=clock)


async def test_enqueue_validates_its_arguments(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="job type"):
        await enqueue(store, "Not A Type")
    with pytest.raises(ValueError, match="JSON"):
        await enqueue(store, "crawl", cast(JsonValue, {"bytes": b"raw"}))
    with pytest.raises(ValueError, match="forbidden character"):
        await enqueue(store, "crawl", job_id="bad/id")


async def test_get_job_only_returns_jobs(store: MemoryStore) -> None:
    with pytest.raises(NotFound):
        await get_job(store, "missing")
    await store.create("jobs", {"id": "sched-x", "kind": "schedule"})
    with pytest.raises(NotFound, match="not a job"):
        await get_job(store, "sched-x")


async def test_cancelling_a_queued_job_is_immediate(store: MemoryStore, clock: FakeClock) -> None:
    broker = Broker()
    job = await enqueue(store, "crawl", clock=clock)
    subscription = broker.subscribe([job.id])
    cancelled = await request_cancel(store, job.id, clock=clock, broker=broker)
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None
    message = await subscription.get(max_wait=0)
    assert isinstance(message, JobEventMessage)
    assert message.data == {"status": "cancelled"}
    assert message.status == "cancelled"
    again = await request_cancel(store, job.id, clock=clock, broker=broker)
    assert again == cancelled  # finished jobs are left alone
    assert await subscription.get(max_wait=0) is None


async def test_cancelling_a_running_job_only_flags_it(store: MemoryStore, clock: FakeClock) -> None:
    job = await enqueue(store, "crawl", clock=clock)
    job.status = "running"
    job.owner = "a:1"
    job.fence = 3
    await save(store, job)
    flagged = await request_cancel(store, job.id, clock=clock)
    assert (flagged.status, flagged.cancel_requested) == ("running", True)
    assert (flagged.owner, flagged.fence) == ("a:1", 3)  # the claim is untouched
    assert flagged.events[-1].type == "cancel.requested"
    again = await request_cancel(store, job.id, clock=clock)
    assert again.seq == flagged.seq


async def test_cancelling_a_waiting_job_is_immediate(store: MemoryStore, clock: FakeClock) -> None:
    job = await enqueue(store, "interview", steps=["ask"], clock=clock)
    job.status = "waiting_input"
    job.steps[0].status = "waiting_input"
    job.awaiting = InputRequest(step="ask", key="answer", prompt="Q?", requested_at=job.created_at)
    await save(store, job)
    cancelled = await request_cancel(store, job.id, clock=clock)
    assert cancelled.status == "cancelled"
    assert cancelled.awaiting is None
    assert cancelled.steps[0].status == "pending"


async def test_provide_input_requeues_the_waiting_job(store: MemoryStore, clock: FakeClock) -> None:
    job = await enqueue(store, "interview", steps=["ask"], clock=clock)
    with pytest.raises(JobStateError) as not_waiting:
        await provide_input(store, job.id, "early", clock=clock)
    assert not_waiting.value.code == "not_waiting"
    job.status = "waiting_input"
    job.steps[0].status = "waiting_input"
    job.awaiting = InputRequest(step="ask", key="answer", prompt="Q?", requested_at=job.created_at)
    await save(store, job)
    with pytest.raises(JobStateError) as wrong_key:
        await provide_input(store, job.id, "x", key="other", clock=clock)
    assert wrong_key.value.code == "wrong_key"
    with pytest.raises(ValueError, match="JSON"):
        await provide_input(store, job.id, cast(JsonValue, {1, 2}), clock=clock)
    clock.advance(30)
    answered = await provide_input(
        store, job.id, {"text": "private answer"}, key="answer", clock=clock
    )
    assert answered.status == "queued"
    assert answered.awaiting is None
    assert answered.run_after == "2026-11-26T12:00:30.000000Z"
    assert answered.steps[0].inputs == {"answer": {"text": "private answer"}}
    assert answered.steps[0].status == "pending"
    received = [event for event in answered.events if event.type == "input.received"]
    assert received[0].data == {"key": "answer"}  # the answer itself is never in an event


async def test_retry_resets_attempts_and_keeps_finished_steps(
    store: MemoryStore, clock: FakeClock
) -> None:
    job = await enqueue(store, "pair", steps=["first", "second"], clock=clock)
    with pytest.raises(JobStateError) as active:
        await retry_job(store, job.id, clock=clock)
    assert active.value.code == "not_retryable"
    job.status = "quarantined"
    job.attempt = 3
    job.steps[0].status = "succeeded"
    job.steps[1].status = "failed"
    await save(store, job)
    retried = await retry_job(store, job.id, clock=clock)
    assert (retried.status, retried.attempt, retried.error) == ("queued", 0, None)
    assert [step.status for step in retried.steps] == ["succeeded", "pending"]
    assert retried.events[-1].data == {"status": "queued", "reason": "retry"}
    retried.status = "succeeded"
    await save(store, retried)
    with pytest.raises(JobStateError):
        await retry_job(store, job.id, clock=clock)


async def test_operations_on_missing_jobs_raise_not_found(store: MemoryStore) -> None:
    with pytest.raises(NotFound):
        await request_cancel(store, "missing")
    with pytest.raises(NotFound):
        await provide_input(store, "missing", "x")
    await store.create("jobs", {"id": "sched-x", "kind": "schedule"})
    with pytest.raises(NotFound):
        await retry_job(store, "sched-x")
