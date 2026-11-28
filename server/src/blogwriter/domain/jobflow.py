"""Using jobs across packages: enqueue once, parent and child jobs, deferral, service checks.

Every package uses these instead of re-deriving the rules (contract section 1.7):

- :func:`enqueue_once` enqueues under a deterministic id and says what a ``Conflict`` means:
  ``reuse`` (a resumed step that already enqueued it), ``rerun`` (the owner asks again: an
  active job is returned, a finished one gets a new run ``<id>-r<n>``) or ``replace`` (cancel the
  active one, then a new run).
- :func:`wait_for_child` / :func:`finish_child`: a parent waits for a child job without losing
  a result the child produced before the parent suspended. The child stores its
  :class:`~blogwriter.domain.jobtypes.ChildResult` on its own job (event ``child.result``)
  first, then tells the parent; the parent reads the child each time it enters its wait.
- :func:`cancel_job`: cancel a job and, when it is a child, tell its waiting parent
  ``ChildResult(ok=False, error_code="cancelled")``. A cancelled child runs no step code of its
  own, so every route that cancels a job another may wait for goes through it; a child with a
  pending cancel request already reads as cancelled (:func:`child_result_of`).
- :class:`JobDeferred`: pause a job until later without using up an attempt (the AI brake,
  services still starting). It lives in :mod:`blogwriter.jobs.runner` (``jobs`` cannot import
  ``domain``) and is re-exported here; the runner parks the steps, sets ``runAfter``, gives
  the attempt back and quarantines a job only after ``MAX_DEFERRALS`` deferrals in a row
  (contract I-7).
- :func:`step_services`, :func:`require_service`: "not ready yet" defers, "not configured"
  fails.
- :func:`past_max_lateness`: a delayed job that starts long after its time holds for the owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import JsonValue, ValidationError

from blogwriter.ai.ledger import BrakeEngaged
from blogwriter.db import Conflict, NotFound
from blogwriter.domain.base import parse_iso, utc_now
from blogwriter.domain.jobtypes import CHILD_RESULT, ChildResult, ParentRef
from blogwriter.domain.ports import JobQueue, Services, ServiceUnavailable, current_services
from blogwriter.jobs.models import Job
from blogwriter.jobs.queue import JobStateError
from blogwriter.jobs.runner import JobDeferred, JobFailed, StepContext

__all__ = [
    "BRAKE_DEFER",
    "CANCEL_DELIVERY_INTERVAL",
    "CANCEL_DELIVERY_WINDOW",
    "CHILD_DELIVERY_INTERVAL",
    "CHILD_DELIVERY_WINDOW",
    "LEDGER_DEFER",
    "MAX_RERUNS",
    "SERVICES_DEFER",
    "JobDeferred",
    "OnConflict",
    "cancel_job",
    "child_result_of",
    "defer_on_brake",
    "deliver_child_result",
    "enqueue_once",
    "finish_child",
    "parent_of",
    "past_max_lateness",
    "require_service",
    "rerun_job_id",
    "step_services",
    "wait_for_child",
]

BRAKE_DEFER: Final = timedelta(hours=1)
"""How long background AI work waits when the daily spend brake is engaged."""
LEDGER_DEFER: Final = timedelta(minutes=10)
"""How long it waits when the brake cannot read the ledger (it fails closed)."""
SERVICES_DEFER: Final = timedelta(seconds=30)
CHILD_DELIVERY_WINDOW: Final = timedelta(seconds=90)
"""How long a child keeps telling a parent that is still running. Longer than a lease (60 s)
plus skew: by then the parent has suspended (delivery lands) or lost its claim (it resumes,
re-enters its wait and reads the recorded result)."""
CHILD_DELIVERY_INTERVAL: Final = timedelta(seconds=5)
CANCEL_DELIVERY_WINDOW: Final = timedelta(seconds=2)
"""How long :func:`cancel_job` keeps telling a parent that is still running (it reads the child
when it enters its wait; this covers a parent between that read and its suspension)."""
CANCEL_DELIVERY_INTERVAL: Final = timedelta(milliseconds=250)
MAX_RERUNS: Final = 50


# --------------------------------------------------------------------------------------------
# Deferral and services
# --------------------------------------------------------------------------------------------


@contextmanager
def defer_on_brake(now: datetime) -> Generator[None]:
    """Turn the AI spend brake into a deferral around background AI calls::

        with defer_on_brake(services.clock.now()):
            result = await services.ai.run(task, request, origin="background", job_id=...)

    Background AI work pauses (plan: "background AI work pauses") instead of burning attempts
    into a quarantine. Owner-origin calls are never braked, so this is a no-op for them.
    """
    try:
        yield
    except BrakeEngaged as exc:
        delay = LEDGER_DEFER if exc.reason == "ledger_unavailable" else BRAKE_DEFER
        raise JobDeferred(now + delay, f"brake_{exc.reason}") from None


def step_services(*, now: datetime | None = None) -> Services:
    """:func:`~blogwriter.domain.ports.current_services` for job steps: services that are not
    installed yet (the app is still starting) defer the job instead of failing it."""
    try:
        return current_services()
    except ServiceUnavailable:
        moment = now if now is not None else utc_now()
        raise JobDeferred(moment + SERVICES_DEFER, "services_starting") from None


def require_service[T](service: T | None, name: str) -> T:
    """An optional service (``services.ai``, ``.blobs``...) or ``JobFailed("<name>_unavailable")``.

    ``None`` means "not configured": the runner starts only after every lifecycle has run
    (contract I-1), so a missing service is never a service that is still starting.
    """
    if service is None:
        raise JobFailed(f"{name}_unavailable", "This feature is not configured.")
    return service


def past_max_lateness(intended: str | datetime, now: datetime, max_lateness: timedelta) -> bool:
    """Whether a delayed job meant for ``intended`` starts more than ``max_lateness`` late."""
    moment = parse_iso(intended) if isinstance(intended, str) else intended
    return now - moment > max_lateness


# --------------------------------------------------------------------------------------------
# Enqueue once
# --------------------------------------------------------------------------------------------

type OnConflict = Literal["reuse", "rerun", "replace"]


def rerun_job_id(job_id: str, run: int) -> str:
    """The id of the ``run``-th re-run of a deterministic job id (``<id>-r<run>``)."""
    if run < 1:
        raise ValueError("re-runs start at 1")
    return f"{job_id}-r{run}"


async def enqueue_once(
    jobs: JobQueue,
    job_type: str,
    input: JsonValue = None,
    *,
    job_id: str,
    run_after: datetime | None = None,
    on_conflict: OnConflict = "reuse",
) -> Job:
    """Enqueue ``job_type`` under a deterministic id and resolve a ``Conflict``.

    ``reuse``
        Any existing job with that id is the one (a resumed step enqueuing again).
    ``rerun``
        An active job (queued, running, waiting) is returned; a finished one (succeeded,
        failed, cancelled, quarantined) gets a fresh job ``<id>-r<n>`` with this input and
        ``run_after`` (the owner re-approves after a veto, re-runs gates on unchanged content).
    ``replace``
        An active job is cancelled first, then as ``rerun`` (publish-now on a scheduled
        article). A running job only gets a cancel request: check the document's status first.

    ``ValueError`` when the id belongs to a job of another type (an id collision) or from
    ``JobsService.enqueue`` for an unregistered type.
    """
    candidates = [job_id, *(rerun_job_id(job_id, run) for run in range(1, MAX_RERUNS + 1))]
    index = 0
    lookups = 0
    while index < len(candidates):
        candidate = candidates[index]
        try:
            return await jobs.enqueue(job_type, input, job_id=candidate, run_after=run_after)
        except Conflict:
            pass
        try:
            existing = await jobs.get(candidate)
        except NotFound:
            lookups += 1  # deleted between the create and the read (TTL): create it again
            if lookups > len(candidates):
                break
            continue
        if existing.type != job_type:
            raise ValueError(f"job id {candidate} belongs to a {existing.type} job")
        if on_conflict == "reuse":
            return existing
        if existing.is_active:
            if on_conflict == "rerun":
                return existing
            await jobs.cancel(existing.id)
        index += 1
    raise RuntimeError(f"{job_id}: more than {MAX_RERUNS} re-runs")


# --------------------------------------------------------------------------------------------
# Parent and child jobs
# --------------------------------------------------------------------------------------------


def _recorded_result(job: Job) -> ChildResult | None:
    """The newest ``child.result`` event of the job's latest run (a retry starts a new run)."""
    for event in reversed(job.events):
        if event.type == CHILD_RESULT:
            try:
                return ChildResult.model_validate(event.data)
            except ValidationError:
                return None
        if event.type == "status" and event.data.get("reason") == "retry":
            return None
    return None


def child_result_of(job: Job) -> ChildResult | None:
    """A child job's outcome, or ``None`` while it may still produce one.

    The recorded ``child.result`` of its current run wins; else a finished job speaks for
    itself (``failed`` -> its error code, ``cancelled``, ``succeeded`` -> its ``result`` if that
    is a :class:`ChildResult`), and a running child with a pending cancel request is
    ``cancelled`` already (the runner stops it without running any step code). Queued, running,
    waiting and quarantined children have no outcome yet: a quarantined child is retried by the
    owner from System, then delivers.
    """
    recorded = _recorded_result(job)
    if recorded is not None:
        return recorded
    if job.is_active and job.cancel_requested:
        return ChildResult(ok=False, error_code="cancelled")
    match job.status:
        case "succeeded":
            try:
                return ChildResult.from_json(job.result)
            except (ValueError, ValidationError):
                return ChildResult(ok=False, error_code="child_result_missing")
        case "failed":
            return ChildResult(ok=False, error_code=job.error.code if job.error else "failed")
        case "cancelled":
            return ChildResult(ok=False, error_code="cancelled")
        case _:
            return None


async def wait_for_child(
    ctx: StepContext, jobs: JobQueue, child_job_id: str, *, key: str, prompt: str
) -> ChildResult:
    """The child's result: read from the child itself when it has one, else wait for it.

    Call it after enqueuing the child (:func:`enqueue_once`, ``reuse``) and on every resume:
    a child that finished before the parent suspended is read, not lost. ``JobFailed``
    ``child_missing`` when the child job does not exist.
    """
    try:
        child = await jobs.get(child_job_id)
    except NotFound:
        raise JobFailed("child_missing") from None
    found = child_result_of(child)
    if found is not None:
        return found
    value = await ctx.wait_for_input(prompt, key=key)
    try:
        return ChildResult.from_json(value)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_child_result") from None


async def deliver_child_result(
    jobs: JobQueue,
    parent: ParentRef,
    result: ChildResult,
    *,
    window: timedelta = CHILD_DELIVERY_WINDOW,
    interval: timedelta = CHILD_DELIVERY_INTERVAL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Tell a waiting parent; ``True`` when it took the result.

    While the parent is still running (it read the child before the result existed and has not
    suspended yet) this retries every ``interval`` for ``window``. A parent that is queued,
    finished or waiting for something else does not need telling: it reads the recorded result
    when it (re)enters its wait.
    """
    if interval <= timedelta(0) or window < timedelta(0):
        raise ValueError("interval must be positive and window not negative")
    tries = int(window / interval) + 1
    for attempt in range(tries):
        try:
            await jobs.provide_input(parent.job_id, result.to_json(), key=parent.key)
            return True
        except JobStateError as exc:
            if exc.code != "not_waiting":
                return False
        except NotFound:
            return False
        try:
            current = await jobs.get(parent.job_id)
        except NotFound:
            return False
        awaiting = current.awaiting
        waiting_for_us = awaiting is not None and awaiting.key == parent.key
        if current.status == "waiting_input" and waiting_for_us:
            continue  # it suspended between our two calls: tell it again at once
        if current.status != "running":
            return False
        if attempt + 1 < tries:
            await sleep(interval.total_seconds())
    return False


def parent_of(job: Job) -> ParentRef | None:
    """The parent a job's input names (``parent``), if any."""
    value = job.input.get("parent") if isinstance(job.input, dict) else None
    if not isinstance(value, dict):
        return None
    try:
        return ParentRef.model_validate(value)
    except ValidationError:
        return None


async def cancel_job(
    jobs: JobQueue,
    job_id: str,
    *,
    window: timedelta = CANCEL_DELIVERY_WINDOW,
    interval: timedelta = CANCEL_DELIVERY_INTERVAL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Job:
    """Cancel ``job_id`` (``JobQueue.cancel``: a finished job is returned unchanged) and, when it
    is (being) cancelled and its input names a ``parent``, tell that parent
    ``ChildResult(ok=False, error_code="cancelled")`` (best effort: a parent that does not take
    it reads the child when it enters its wait)."""
    job = await jobs.cancel(job_id)
    if job.status == "cancelled" or (job.is_active and job.cancel_requested):
        parent = parent_of(job)
        if parent is not None:
            await deliver_child_result(
                jobs,
                parent,
                ChildResult(ok=False, error_code="cancelled"),
                window=window,
                interval=interval,
                sleep=sleep,
            )
    return job


async def finish_child(
    ctx: StepContext,
    jobs: JobQueue,
    parent: ParentRef | None,
    result: ChildResult,
    *,
    window: timedelta = CHILD_DELIVERY_WINDOW,
    interval: timedelta = CHILD_DELIVERY_INTERVAL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """End a child job's work: record ``result`` on the child (durable, before anything else),
    then tell the parent if there is one. Call it on every outcome, success or failure (before
    raising ``JobFailed``), after the produced document (the brief) is saved."""
    await ctx.emit(CHILD_RESULT, result.to_json())
    if parent is not None:
        await deliver_child_result(
            jobs, parent, result, window=window, interval=interval, sleep=sleep
        )
