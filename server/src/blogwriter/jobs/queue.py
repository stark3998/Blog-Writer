"""Job operations that do not hold a claim: enqueue, read, cancel, answer input, retry.

These are what the API, the scheduler and other packages call. Each is one CAS write on the
job document (retried on 412), and each publishes the events it stored when given a broker.
None of them touches the claim fields, so a runner holding the job keeps its claim: it sees the
change on its next write (a cancel request stops it at its next renewal or step operation).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final

from pydantic import JsonValue

from blogwriter.db import NotFound, Store
from blogwriter.jobs.broker import Broker
from blogwriter.jobs.claims import Body, bounded, cas_update
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock, to_iso
from blogwriter.jobs.models import (
    DEFAULT_MAX_ATTEMPTS,
    JOB_KIND,
    JOBS_CONTAINER,
    NAME_PATTERN,
    Job,
    StepState,
    check_json,
    new_job_id,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "JobStateError",
    "enqueue",
    "get_job",
    "provide_input",
    "request_cancel",
    "retry_job",
]

DEFAULT_TIMEOUT: Final = 10.0
_NAME_RE: Final = re.compile(NAME_PATTERN)


class JobStateError(Exception):
    """The job is not in a state that allows the operation (``code`` says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def enqueue(
    store: Store,
    job_type: str,
    input: JsonValue = None,
    *,
    job_id: str | None = None,
    steps: Sequence[str] = (),
    run_after: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    schedule_id: str | None = None,
    scheduled_for: datetime | None = None,
    ttl: int | None = None,
    clock: Clock = SYSTEM_CLOCK,
    broker: Broker | None = None,
    store_timeout: float = DEFAULT_TIMEOUT,
) -> Job:
    """Create a queued job. ``job_id`` makes it idempotent: an existing id raises ``Conflict``.

    ``steps`` pre-fills the step list (the runner reconciles it with the registry anyway).
    """
    if not _NAME_RE.fullmatch(job_type):
        raise ValueError(f"job type must match {NAME_PATTERN}")
    payload = check_json(input, what="job input")
    now = clock.now()
    stamp = to_iso(now)
    job = Job(
        id=job_id or new_job_id(),
        type=job_type,
        steps=[StepState(name=name) for name in steps],
        max_attempts=max_attempts,
        run_after=to_iso(run_after) if run_after is not None else stamp,
        input=payload,
        schedule_id=schedule_id,
        scheduled_for=to_iso(scheduled_for) if scheduled_for is not None else None,
        created_at=stamp,
        updated_at=stamp,
        ttl=ttl,
    )
    job.add_event("status", at=stamp, data={"status": "queued"})
    created = await bounded(store.create(JOBS_CONTAINER, job.to_body()), store_timeout)
    stored = Job.from_body(created.body)
    if broker is not None:
        broker.publish_job_events(stored, stored.events)
    return stored


async def get_job(store: Store, job_id: str, *, store_timeout: float = DEFAULT_TIMEOUT) -> Job:
    """Read a job; :class:`NotFound` if there is none (or the id names a schedule item)."""
    doc = await bounded(store.read(JOBS_CONTAINER, job_id), store_timeout)
    if doc.body.get("kind") != JOB_KIND:
        raise NotFound(f"read {JOBS_CONTAINER}/{job_id}: not a job")
    return Job.from_body(doc.body)


async def _update(
    store: Store,
    job_id: str,
    change: Callable[[Job, str], bool],
    *,
    clock: Clock,
    broker: Broker | None,
    store_timeout: float,
) -> Job:
    """CAS-apply ``change(job, now)``; it returns ``False`` to leave the job unchanged."""
    before: list[int] = []
    unchanged: list[Job] = []

    def mutate(body: Body) -> Body | None:
        if body.get("kind") != JOB_KIND:
            raise NotFound(f"read {JOBS_CONTAINER}/{job_id}: not a job")
        job = Job.from_body(body)
        before[:] = [job.seq]
        now = to_iso(clock.now())
        if not change(job, now):
            unchanged[:] = [job]
            return None
        job.updated_at = now
        return job.to_body()

    doc = await cas_update(store, JOBS_CONTAINER, job_id, mutate, store_timeout=store_timeout)
    if doc is None:
        return unchanged[0]
    job = Job.from_body(doc.body)
    if broker is not None:
        broker.publish_job_events(job, job.events_after(before[0]))
    return job


async def request_cancel(
    store: Store,
    job_id: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    broker: Broker | None = None,
    store_timeout: float = DEFAULT_TIMEOUT,
) -> Job:
    """Cancel a job. Queued or waiting jobs are cancelled at once; a running job gets
    ``cancelRequested`` and its runner stops it. A quarantined job is dismissed (cancelled), so
    it stops asking for attention once the owner has dealt with it (for example after a rerun
    succeeded). Other finished jobs are returned unchanged."""

    def change(job: Job, now: str) -> bool:
        if job.status == "quarantined":
            job.status = "cancelled"
            job.add_event("status", at=now, data={"status": "cancelled", "reason": "dismissed"})
            return True
        if job.is_terminal:
            return False
        if job.status in ("queued", "waiting_input"):
            job.status = "cancelled"
            job.awaiting = None
            job.finished_at = now
            job.expire_when_finished()
            for step in job.steps:
                if step.status in ("running", "waiting_input"):
                    step.status = "pending"
            job.add_event("status", at=now, data={"status": "cancelled"})
            return True
        if job.cancel_requested:
            return False
        job.cancel_requested = True
        job.add_event("cancel.requested", at=now)
        return True

    return await _update(
        store, job_id, change, clock=clock, broker=broker, store_timeout=store_timeout
    )


async def provide_input(
    store: Store,
    job_id: str,
    value: JsonValue,
    *,
    key: str | None = None,
    clock: Clock = SYSTEM_CLOCK,
    broker: Broker | None = None,
    store_timeout: float = DEFAULT_TIMEOUT,
) -> Job:
    """Answer the job's pending ``wait_for_input`` and re-queue it.

    Raises :class:`JobStateError` (``not_waiting``, ``wrong_key``) when the job is not waiting
    for this input. The answer is stored on the step, never in an event or a log.
    """
    answer = check_json(value, what="input")

    def change(job: Job, now: str) -> bool:
        request = job.awaiting
        if job.status != "waiting_input" or request is None:
            raise JobStateError("not_waiting", "the job is not waiting for input")
        if key is not None and key != request.key:
            raise JobStateError("wrong_key", "the job is waiting for a different input")
        step = job.step(request.step)
        if step is None:
            raise JobStateError("not_waiting", "the job has no step waiting for input")
        step.inputs[request.key] = answer
        step.status = "pending"
        job.awaiting = None
        job.status = "queued"
        job.run_after = now
        job.add_event("input.received", at=now, step=step.name, data={"key": request.key})
        job.add_event("status", at=now, data={"status": "queued"})
        return True

    return await _update(
        store, job_id, change, clock=clock, broker=broker, store_timeout=store_timeout
    )


async def retry_job(
    store: Store,
    job_id: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    broker: Broker | None = None,
    store_timeout: float = DEFAULT_TIMEOUT,
) -> Job:
    """Re-queue a failed, quarantined or cancelled job with its attempts and deferrals reset.

    Steps that succeeded stay done; the job resumes at the first unfinished step. Raises
    :class:`JobStateError` (``not_retryable``) for jobs that are still active or succeeded.
    """

    def change(job: Job, now: str) -> bool:
        if job.status not in ("failed", "quarantined", "cancelled"):
            raise JobStateError("not_retryable", "only failed, quarantined or cancelled jobs")
        job.status = "queued"
        job.attempt = 0
        job.deferrals = 0
        job.error = None
        job.cancel_requested = False
        job.finished_at = None
        job.keep_while_active()
        job.run_after = now
        for step in job.steps:
            if step.status != "succeeded":
                step.status = "pending"
        job.add_event("status", at=now, data={"status": "queued", "reason": "retry"})
        return True

    return await _update(
        store, job_id, change, clock=clock, broker=broker, store_timeout=store_timeout
    )
