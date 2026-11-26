"""The durable job runner (plan: "Reliability model: crash-only, one claim mechanism").

A job type is an ordered list of async step functions registered in a :class:`JobRegistry`.
A :class:`JobRunner` claims jobs, runs their steps in order and commits every step with a fenced
CAS write before it starts the next one, so a SIGKILL at any moment loses at most the step in
flight, and that step resumes from its last checkpoint with its remote ids.

Claim loop
    Every ``poll_seconds`` (or at once when woken by an enqueue or a finished job) the runner
    looks for jobs of the types it knows: running jobs whose lease ended more than ``skew`` ago
    (their owner died) and queued jobs whose ``runAfter`` has passed. It acquires them with a
    CAS write (:func:`blogwriter.jobs.claims.acquire`), at most ``concurrency`` at a time. A job
    whose attempts are used up is quarantined instead of started. Claiming stops for good as
    soon as ``stop_requested()`` says the process is shutting down.

Running a job
    One task runs the steps, a second renews the lease every ``renew_seconds`` and a third
    guards the self-fencing deadline. A step function gets a :class:`StepContext`:
    ``checkpoint(output)``, ``record_remote_id(key, value)`` (call it *before* awaiting the
    remote work), ``emit(event_type, data)``, ``delta(text)`` (token stream, broker only) and
    ``wait_for_input(prompt)``. Every durable call is a fenced CAS write that rides out
    transient store failures (same write token, so it lands at most once) until the lease
    deadline; a step that already finished is therefore not re-run because one commit hit a
    blip. When the runner loses a CAS to another owner (:class:`ClaimLost`), or
    ``leaseUntil - skew`` passes without a successful renewal (even while a renewal or a step
    write is still stuck in the store), it cancels the step and writes nothing more.

Outcomes
    - The step returns: its output is committed; after the last step the job ``succeeded`` with
      the last step's output as ``result`` (keep outputs small: references, not documents).
    - :class:`JobFailed`: the job ``failed`` (no retry).
    - Any other exception (a ``CancelledError`` the step raised itself included): retried after
      a backoff (``retry_base_seconds`` doubling, capped), or ``quarantined`` once
      ``maxAttempts`` attempts are used (the quarantine hook alerts).
    - An output too large to store: the job ``failed`` with ``invalid_output`` (no retry).
    - ``wait_for_input``: the job goes to ``waiting_input``; the claim and the slot are released
      and the attempt is given back. :func:`blogwriter.jobs.queue.provide_input` re-queues it and
      the step runs again from the start, this time getting the answer.
    - A cancel request (stored by :func:`blogwriter.jobs.queue.request_cancel`): the step is
      cancelled and the job ends ``cancelled``.
    - Shutdown (:meth:`JobRunner.stop`): running steps get ``shutdown_grace_seconds``, then they
      are cancelled and their jobs released back to ``queued`` (attempt given back), so the next
      replica resumes them without waiting for the lease. With ``release=False`` (what a crash
      looks like) nothing is written and the leases simply lapse.

Attempts count claims that did not end cleanly: crashes and errors. A job that crashes the
process every time is quarantined on its fourth claim without running again.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import Final, Literal

from pydantic import JsonValue, ValidationError

from blogwriter.core.logging import describe_exception
from blogwriter.db import Doc, Filter, InvalidRequest, OrderBy, Store, StoreError, StoreUnavailable
from blogwriter.jobs._logging import logger
from blogwriter.jobs.broker import Broker
from blogwriter.jobs.claims import (
    Body,
    Claim,
    ClaimLost,
    LeasePolicy,
    Mutation,
    acquire,
    bounded,
    cas_update,
    new_owner,
    new_write_token,
)
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock, to_iso
from blogwriter.jobs.models import (
    JOB_KIND,
    JOBS_CONTAINER,
    MAX_DETAIL_CHARS,
    MAX_EVENT_DATA_BYTES,
    MAX_PROMPT_CHARS,
    MAX_REMOTE_ID_CHARS,
    NAME_PATTERN,
    InputRequest,
    Job,
    JobError,
    StepState,
    check_json,
    event_data_size,
)
from blogwriter.jobs.settings import JobsSettings, replica_name

__all__ = [
    "RESERVED_EVENT_TYPES",
    "JobFailed",
    "JobRegistry",
    "JobRunner",
    "JobType",
    "QuarantineHook",
    "QuarantineNotice",
    "RunnerStats",
    "Step",
    "StepContext",
    "StepFunction",
]

_NAME_RE: Final = re.compile(NAME_PATTERN)
_STOP_CHECK_SECONDS: Final = 1.0
"""The claim loop checks ``stop_requested`` at least this often."""
_RETRY_SECONDS: Final = 1.0
"""Pause between retries of a renewal or a durable write after a transient store error."""
_DEADLINE_CHECK_SECONDS: Final = 1.0
"""The lease guard looks at the self-fencing deadline at least this often."""
_CANCEL_WAIT_SECONDS: Final = 2.0
_RELEASE_BUDGET_SECONDS: Final = 5.0
_ALERT_TIMEOUT_SECONDS: Final = 5.0

RESERVED_EVENT_TYPES: Final = frozenset(
    {
        "status",
        "step.started",
        "step.resumed",
        "step.succeeded",
        "step.failed",
        "input.requested",
        "input.received",
        "cancel.requested",
    }
)
"""Event types the runner and the queue operations write; ``emit`` may not use them."""

type StepFunction = Callable[[StepContext], Awaitable[JsonValue]]
type Change = Callable[[Job, datetime], None]


# ----------------------------------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a job type: a name (``[a-z][a-z0-9_.-]*``) and an async function."""

    name: str
    run: StepFunction

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError(f"step name must match {NAME_PATTERN}")


@dataclass(frozen=True, slots=True)
class JobType:
    """A registered job type: its steps, run in order."""

    name: str
    steps: tuple[Step, ...]

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)


class JobRegistry:
    """Job type name -> steps. Runners only claim jobs whose type is registered here."""

    def __init__(self) -> None:
        self._types: dict[str, JobType] = {}

    def register(self, name: str, steps: Sequence[Step]) -> JobType:
        """Register ``name`` with ``steps``; ``ValueError`` for a duplicate or an invalid one."""
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"job type must match {NAME_PATTERN}")
        if name in self._types:
            raise ValueError(f"job type {name!r} is already registered")
        if not steps:
            raise ValueError("a job type needs at least one step")
        names = [step.name for step in steps]
        if len(names) != len(set(names)):
            raise ValueError("step names must be unique within a job type")
        job_type = JobType(name=name, steps=tuple(steps))
        self._types[name] = job_type
        return job_type

    def get(self, name: str) -> JobType | None:
        return self._types.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._types

    def names(self) -> tuple[str, ...]:
        """Registered type names, sorted."""
        return tuple(sorted(self._types))


# ----------------------------------------------------------------------------------------------
# Step-facing API
# ----------------------------------------------------------------------------------------------


class JobFailed(Exception):
    """Raise from a step to fail the job for good (no retry).

    ``code`` is a machine-readable reason (``[a-z][a-z0-9_.-]*``). ``detail`` is optional
    owner-facing text stored on the job (never logged); keep content out of it.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        if not _NAME_RE.fullmatch(code):
            raise ValueError(f"JobFailed code must match {NAME_PATTERN}")
        if detail is not None and len(detail) > MAX_DETAIL_CHARS:
            raise ValueError(f"JobFailed detail may have at most {MAX_DETAIL_CHARS} characters")
        super().__init__(code)
        self.code = code
        self.detail = detail


class _SuspendForInput(BaseException):
    """Unwinds a step that waits for owner input (a BaseException, so step code that catches
    ``Exception`` cannot swallow it)."""

    def __init__(self, step: str, key: str, prompt: str) -> None:
        super().__init__(step)
        self.step = step
        self.key = key
        self.prompt = prompt


class _CancelRequested(BaseException):
    """Unwinds a step whose job has a cancel request."""


@dataclass(frozen=True, slots=True)
class QuarantineNotice:
    """What the quarantine hook gets: ids and codes only, never job content."""

    job_id: str
    job_type: str
    attempts: int
    step: str | None
    error_type: str | None


type QuarantineHook = Callable[[QuarantineNotice], Awaitable[None]]


class StepContext:
    """Passed to a step function. Durable calls are fenced CAS writes on the job.

    A durable call retries transient store failures itself (it lands at most once) until the
    lease deadline, and raises :class:`ClaimLost` when the runner no longer owns the job or
    that deadline passed (do not catch it). Step code must be re-entrant: after a crash, a
    release or an input wait the step runs again from the start, with :attr:`output` (last
    checkpoint) and :attr:`remote_ids` restored and :attr:`resumed` set.
    """

    def __init__(
        self,
        handle: _JobHandle,
        step: str,
        outputs: Mapping[str, JsonValue],
        *,
        resumed: bool,
    ) -> None:
        job = handle.job
        self._handle = handle
        self._open = True
        self.job_id: Final = job.id
        self.job_type: Final = job.type
        self.step: Final = step
        self.attempt: Final = job.attempt
        self.fence: Final = handle.claim.fence
        """The fencing token of this claim; pass it to remote systems that accept one."""
        self.resumed: Final = resumed
        """The step ran before (crash, release or input wait): re-attach, do not re-issue."""
        self.input: Final[JsonValue] = copy.deepcopy(job.input)
        self.outputs: Final[Mapping[str, JsonValue]] = MappingProxyType(dict(outputs))
        """Outputs of the steps that already succeeded, by step name."""
        self.scheduled_for: Final = job.scheduled_for

    def _state(self) -> StepState:
        state = self._handle.job.step(self.step)
        if state is None:  # pragma: no cover - the runner creates every step state
            raise RuntimeError(f"job {self.job_id} has no step {self.step!r}")
        return state

    @property
    def output(self) -> JsonValue:
        """The last checkpointed output of this step (``None`` if there is none)."""
        return copy.deepcopy(self._state().output)

    @property
    def remote_ids(self) -> Mapping[str, str]:
        """Remote ids recorded by earlier runs of this step."""
        return MappingProxyType(dict(self._state().remote_ids))

    def _check_open(self) -> None:
        if not self._open:
            raise RuntimeError("the step has finished; its context can no longer be used")

    def _check_cancel(self) -> None:
        if self._handle.job.cancel_requested:
            raise _CancelRequested()

    def close(self) -> None:
        """Called by the runner when the step ends."""
        self._open = False

    async def checkpoint(self, output: JsonValue) -> None:
        """Durably store the step's partial ``output`` (restored as :attr:`output` on resume)."""
        self._check_open()
        value = check_json(output, what="checkpoint output")

        def change(job: Job, _now: datetime) -> None:
            _require_step(job, self.step).output = copy.deepcopy(value)

        await self._handle.write_durably(change)
        self._check_cancel()

    async def record_remote_id(self, key: str, value: str) -> None:
        """Durably record a remote id (call it before awaiting the remote work)."""
        self._check_open()
        if not _NAME_RE.fullmatch(key):
            raise ValueError(f"remote id key must match {NAME_PATTERN}")
        if not value or len(value) > MAX_REMOTE_ID_CHARS:
            raise ValueError(f"remote id must have 1-{MAX_REMOTE_ID_CHARS} characters")

        def change(job: Job, _now: datetime) -> None:
            _require_step(job, self.step).remote_ids[key] = value

        await self._handle.write_durably(change)
        self._check_cancel()

    async def emit(self, event_type: str, data: Mapping[str, JsonValue] | None = None) -> None:
        """Store a step event on the job (capped history) and publish it to SSE subscribers.

        Never put fetched passages, private text or other content in ``data``: ids, counts,
        short labels.
        """
        self._check_open()
        if not _NAME_RE.fullmatch(event_type) or event_type in RESERVED_EVENT_TYPES:
            raise ValueError(f"event type must match {NAME_PATTERN} and not be reserved")
        payload = check_json(dict(data or {}), what="event data")
        if not isinstance(payload, dict):  # pragma: no cover - a dict stays a dict
            raise TypeError("event data must be a mapping")
        _check_event_size(payload)

        def change(job: Job, now: datetime) -> None:
            job.add_event(event_type, at=to_iso(now), step=self.step, data=payload)

        await self._handle.write_durably(change)
        self._check_cancel()

    def delta(self, text: str) -> None:
        """Stream a token delta to SSE subscribers (not stored; may be dropped under load)."""
        self._check_open()
        broker = self._handle.broker
        if broker is not None:
            broker.publish_delta(self.job_id, self.step, text)

    async def wait_for_input(self, prompt: str, *, key: str = "input") -> JsonValue:
        """Return the owner's answer for ``key``, or suspend the job until there is one.

        Suspending moves the job to ``waiting_input``, releases the claim and the runner slot,
        and unwinds the step. The answer re-queues the job and the step runs again from the
        start, so checkpoint any work done before the question.
        """
        self._check_open()
        if not _NAME_RE.fullmatch(key):
            raise ValueError(f"input key must match {NAME_PATTERN}")
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt must have 1-{MAX_PROMPT_CHARS} characters")
        inputs = self._state().inputs
        if key in inputs:
            return copy.deepcopy(inputs[key])
        raise _SuspendForInput(self.step, key, prompt)


def _require_step(job: Job, name: str) -> StepState:
    state = job.step(name)
    if state is None:
        raise RuntimeError(f"job {job.id} has no step {name!r}")
    return state


def _check_event_size(payload: Mapping[str, JsonValue]) -> None:
    if event_data_size(payload) > MAX_EVENT_DATA_BYTES:
        raise ValueError(f"event data is larger than {MAX_EVENT_DATA_BYTES} bytes")


# ----------------------------------------------------------------------------------------------
# Job state changes (each is applied inside one CAS write)
# ----------------------------------------------------------------------------------------------


def _reconcile_steps(job: Job, job_type: JobType) -> None:
    """Order the job's steps like the type's; keep states by name and unknown ones at the end."""
    existing = {step.name: step for step in job.steps}
    ordered = [existing.get(name) or StepState(name=name) for name in job_type.step_names]
    extra = [step for step in job.steps if step.name not in job_type.step_names]
    job.steps = ordered + extra


def _start(job_type: JobType, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    resumed = job.attempt > 0 or any(step.runs > 0 for step in job.steps)
    _reconcile_steps(job, job_type)
    job.status = "running"
    job.attempt += 1
    job.awaiting = None
    job.started_at = job.started_at or stamp
    job.add_event(
        "status", at=stamp, data={"status": "running", "attempt": job.attempt, "resumed": resumed}
    )


def _step_started(name: str, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _require_step(job, name)
    resumed = step.runs > 0
    step.runs += 1
    step.status = "running"
    step.started_at = step.started_at or stamp
    job.add_event("step.resumed" if resumed else "step.started", at=stamp, step=name)


def _step_succeeded(name: str, output: JsonValue, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _require_step(job, name)
    step.status = "succeeded"
    step.output = copy.deepcopy(output)
    step.finished_at = stamp
    job.add_event("step.succeeded", at=stamp, step=name)


def _unfinished_step(job: Job) -> StepState | None:
    for step in job.steps:
        if step.status in ("running", "waiting_input"):
            return step
    return None


def _park_steps(job: Job) -> None:
    """Running or waiting steps go back to pending (they resume later)."""
    for step in job.steps:
        if step.status in ("running", "waiting_input"):
            step.status = "pending"


def _succeed(result: JsonValue, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    job.status = "succeeded"
    job.result = copy.deepcopy(result)
    job.error = None
    job.finished_at = stamp
    job.add_event("status", at=stamp, data={"status": "succeeded"})


def _suspend(request: _SuspendForInput, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _require_step(job, request.step)
    step.status = "waiting_input"
    job.status = "waiting_input"
    job.awaiting = InputRequest(
        step=request.step, key=request.key, prompt=request.prompt, requested_at=stamp
    )
    job.attempt = max(0, job.attempt - 1)
    job.add_event("input.requested", at=stamp, step=request.step, data={"key": request.key})
    job.add_event("status", at=stamp, data={"status": "waiting_input"})


def _fail(code: str, detail: str | None, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _unfinished_step(job)
    if step is not None:
        step.status = "failed"
        step.finished_at = stamp
        job.add_event("step.failed", at=stamp, step=step.name, data={"code": code})
    job.status = "failed"
    job.error = JobError(code=code, at=stamp, step=step.name if step else None, detail=detail)
    job.finished_at = stamp
    job.add_event("status", at=stamp, data={"status": "failed", "code": code})


def _retry(error_type: str, delay: timedelta, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _unfinished_step(job)
    _park_steps(job)
    job.status = "queued"
    job.run_after = to_iso(now + delay)
    job.error = JobError(
        code="step_error", at=stamp, step=step.name if step else None, error_type=error_type
    )
    job.add_event(
        "status",
        at=stamp,
        data={"status": "queued", "reason": "retry", "attempt": job.attempt},
    )


def _quarantine(error_type: str | None, job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    step = _unfinished_step(job)
    _park_steps(job)
    job.status = "quarantined"
    job.error = JobError(
        code="max_attempts", at=stamp, step=step.name if step else None, error_type=error_type
    )
    job.finished_at = stamp
    job.add_event("status", at=stamp, data={"status": "quarantined", "attempt": job.attempt})


def _cancel(job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    _park_steps(job)
    job.status = "cancelled"
    job.awaiting = None
    job.finished_at = stamp
    job.add_event("status", at=stamp, data={"status": "cancelled"})


def _release(job: Job, now: datetime) -> None:
    stamp = to_iso(now)
    _park_steps(job)
    job.status = "queued"
    job.run_after = stamp
    job.attempt = max(0, job.attempt - 1)
    job.add_event("status", at=stamp, data={"status": "queued", "reason": "released"})


def _error_type(exc: BaseException) -> str:
    cls = type(exc)
    if cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"[:200]


# ----------------------------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------------------------


@dataclass
class RunnerStats:
    """Counters of one runner (for health and tests)."""

    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    quarantined: int = 0
    waiting: int = 0
    cancelled: int = 0
    released: int = 0
    claims_lost: int = 0


class _JobHandle:
    """A claimed job: the claim plus the typed job and event publication."""

    def __init__(self, runner: JobRunner, claim: Claim, job: Job, *, published_seq: int) -> None:
        self._runner = runner
        self.claim = claim
        self.job = job
        self._published_seq = published_seq

    @property
    def broker(self) -> Broker | None:
        return self._runner.broker

    def publish_new(self) -> None:
        """Publish the job's stored events this handle has not published yet."""
        fresh = self.job.events_after(self._published_seq)
        broker = self._runner.broker
        if fresh and broker is not None:
            broker.publish_job_events(self.job, fresh)
        self._published_seq = max(self._published_seq, self.job.seq)

    async def write(
        self,
        change: Change | None,
        *,
        release: bool = False,
        token: str | None = None,
    ) -> Job:
        """Apply ``change`` in one fenced CAS write (``None``: just renew the lease)."""
        body = await self.claim.commit(self._runner.mutation(change), release=release, token=token)
        self.job = Job.from_body(body)
        self.publish_new()
        return self.job

    async def write_durably(self, change: Change) -> Job:
        """:meth:`write` for the step path: retries transient store failures until the lease
        deadline, with one token so the change lands at most once.

        Without it a blip on a step's final commit would re-run a step that already finished,
        re-issuing its remote work. Raises :class:`ClaimLost` once the deadline has passed.
        """
        token = new_write_token()
        while True:
            try:
                return await self.write(change, token=token)
            except (StoreUnavailable, TimeoutError):
                if self._runner.clock.now() >= self.claim.deadline:
                    self.claim.mark_lost()
                    raise ClaimLost(
                        f"{JOBS_CONTAINER}/{self.job.id}: lease deadline passed"
                    ) from None
                await asyncio.sleep(_RETRY_SECONDS)

    async def renew(self) -> Job:
        return await self.write(None)


@dataclass(eq=False)
class _Active:
    handle: _JobHandle
    task: asyncio.Task[None]
    work: asyncio.Task[JsonValue] | None = None
    cancel_requested: bool = False


def _never() -> bool:
    return False


class JobRunner:
    """Claims and runs jobs of the registered types (see the module docs)."""

    def __init__(
        self,
        store: Store,
        registry: JobRegistry,
        *,
        settings: JobsSettings | None = None,
        owner: str | None = None,
        clock: Clock = SYSTEM_CLOCK,
        broker: Broker | None = None,
        on_quarantine: QuarantineHook | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._settings = settings if settings is not None else JobsSettings()
        self._policy = LeasePolicy.from_settings(self._settings)
        self._owner = owner or new_owner(replica_name(self._settings))
        self._clock = clock
        self._broker = broker
        self._on_quarantine = on_quarantine
        self._stop_requested = stop_requested or _never
        self._active: dict[str, _Active] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._claiming = False
        self._stopped = False
        self.stats = RunnerStats()

    # Properties -------------------------------------------------------------------------------

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def broker(self) -> Broker | None:
        return self._broker

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def registry(self) -> JobRegistry:
        return self._registry

    @property
    def claiming(self) -> bool:
        """Whether the claim loop is running and accepting new jobs."""
        return self._claiming

    @property
    def active_job_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    # Lifecycle --------------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the claim loop."""
        if self._loop_task is not None or self._stopped:
            raise RuntimeError("a runner can be started once")
        self._claiming = True
        self._loop_task = asyncio.create_task(self._claim_loop(), name="jobs-claim-loop")
        logger.info(
            "jobs_runner_started",
            extra={"owner": self._owner, "count": self._settings.concurrency},
        )

    async def stop(self, *, release: bool = True) -> None:
        """Stop claiming at once, then stop running jobs.

        With ``release`` (graceful shutdown) running steps get ``shutdown_grace_seconds`` to
        finish; the rest are cancelled and their jobs released to ``queued`` for the next
        replica. Without it the jobs are abandoned exactly like a crash would (no writes).
        """
        if self._stopped:
            return
        self._stopped = True
        self._claiming = False
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        actives = list(self._active.values())
        grace = self._settings.shutdown_grace_seconds
        if release and actives and grace > 0:
            await asyncio.wait([active.task for active in actives], timeout=grace)
        remaining = [active for active in actives if not active.task.done()]
        for active in remaining:
            active.task.cancel()
        if remaining:
            await asyncio.wait([active.task for active in remaining], timeout=_CANCEL_WAIT_SECONDS)
        if release:
            held = [active.handle for active in remaining if active.handle.claim.held]
            if held:
                await asyncio.gather(*(self._release(handle) for handle in held))
        logger.info(
            "jobs_runner_stopped",
            extra={
                "owner": self._owner,
                "count": len(remaining),
                "outcome": "released" if release else "abandoned",
            },
        )

    def wake(self) -> None:
        """Look for work now (after an enqueue or an answered input)."""
        self._wake.set()

    def cancel_local(self, job_id: str) -> bool:
        """Cancel the running step of ``job_id`` if this runner holds it (after the durable
        cancel request is stored). Returns whether it did."""
        active = self._active.get(job_id)
        if active is None or active.work is None:
            return False
        active.cancel_requested = True
        active.work.cancel()
        return True

    # Mutations --------------------------------------------------------------------------------

    def mutation(self, change: Change | None) -> Mutation:
        """A body-level claim mutation that applies ``change`` to the typed job."""

        def mutate(body: Body) -> Body:
            job = Job.from_body(body)
            if change is not None:
                now = self._clock.now()
                change(job, now)
                job.updated_at = to_iso(now)
            return job.to_body()

        return mutate

    # Claim loop -------------------------------------------------------------------------------

    async def _claim_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_poll = loop.time()
        while self._claiming:
            if self._stop_requested():
                self._claiming = False
                logger.info("jobs_claiming_stopped", extra={"owner": self._owner})
                return
            woken = self._wake.is_set()
            self._wake.clear()
            free = self._settings.concurrency - len(self._active)
            if free > 0 and (woken or loop.time() >= next_poll):
                try:
                    await self._claim_some(free)
                except (StoreError, TimeoutError) as exc:
                    logger.warning("jobs_poll_failed", extra=describe_exception(exc))
                except Exception as exc:
                    logger.error("jobs_poll_crashed", extra=describe_exception(exc))
                next_poll = loop.time() + self._settings.poll_seconds
            wait = _STOP_CHECK_SECONDS
            if free > 0:
                wait = max(0.0, min(next_poll - loop.time(), _STOP_CHECK_SECONDS))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(wait):
                    await self._wake.wait()

    async def _claim_some(self, free: int) -> None:
        names = self._registry.names()
        if not names:
            return
        now = self._clock.now()
        timeout = self._policy.store_timeout
        base = [Filter("kind", "==", JOB_KIND), Filter("type", "in", list(names))]
        expired = await bounded(
            self._store.find(
                JOBS_CONTAINER,
                [
                    *base,
                    Filter("status", "==", "running"),
                    Filter("leaseUntil", "<", self._policy.takeover_cutoff(now)),
                ],
                order_by=OrderBy("leaseUntil"),
                limit=free * 2,
            ),
            timeout,
        )
        queued = await bounded(
            self._store.find(
                JOBS_CONTAINER,
                [*base, Filter("status", "==", "queued"), Filter("runAfter", "<=", to_iso(now))],
                order_by=OrderBy("runAfter"),
                limit=free * 2,
            ),
            timeout,
        )
        for doc in (*expired, *queued):
            if free <= 0 or not self._claiming or self._stop_requested():
                return
            if doc.id in self._active:
                continue
            if await self._try_start(doc):
                free -= 1

    async def _try_start(self, doc: Doc) -> bool:
        try:
            job = Job.from_body(doc.body)
        except ValidationError:
            logger.warning("jobs_invalid_document", extra={"job_id": doc.id})
            return False
        job_type = self._registry.get(job.type)
        if job_type is None or job.status not in ("queued", "running"):
            return False
        if not self._policy.is_claimable(doc.body, self._clock.now()):
            return False
        if job.cancel_requested:
            await self._settle_unclaimed(doc, "cancelled")
            return False
        if job.attempt >= job.max_attempts:
            await self._settle_unclaimed(doc, "quarantined")
            return False
        claim = await acquire(
            self._store,
            JOBS_CONTAINER,
            doc,
            owner=self._owner,
            policy=self._policy,
            clock=self._clock,
            mutate=self.mutation(partial(_start, job_type)),
        )
        if claim is None:
            return False
        handle = _JobHandle(self, claim, Job.from_body(claim.body), published_seq=job.seq)
        handle.publish_new()
        active = _Active(handle=handle, task=asyncio.create_task(self._execute(handle)))
        self._active[job.id] = active
        self.stats.claimed += 1
        logger.info(
            "job_claimed",
            extra={
                "job_id": job.id,
                "job_type": job.type,
                "attempt": handle.job.attempt,
                "fence": claim.fence,
                "owner": self._owner,
            },
        )
        return True

    async def _settle_unclaimed(
        self, doc: Doc, outcome: Literal["cancelled", "quarantined"]
    ) -> None:
        """End a claimable job without running it (cancel request, attempts used up)."""
        before = Job.from_body(doc.body).seq

        def change(body: Body) -> Body | None:
            now = self._clock.now()
            if not self._policy.is_claimable(body, now):
                return None
            job = Job.from_body(body)
            if job.status not in ("queued", "running"):
                return None
            if outcome == "cancelled":
                if not job.cancel_requested:
                    return None
                _cancel(job, now)
            else:
                if job.attempt < job.max_attempts:
                    return None
                crashed = _unfinished_step(job) is not None
                _quarantine("crash" if crashed else None, job, now)
            job.owner = None
            job.lease_until = None
            job.updated_at = to_iso(now)
            return job.to_body()

        stored = await cas_update(
            self._store,
            JOBS_CONTAINER,
            doc.id,
            change,
            store_timeout=self._policy.store_timeout,
            current=doc,
        )
        if stored is None:
            return
        job = Job.from_body(stored.body)
        if self._broker is not None:
            self._broker.publish_job_events(job, job.events_after(before))
        if outcome == "cancelled":
            self.stats.cancelled += 1
            logger.info("job_cancelled", extra={"job_id": job.id, "job_type": job.type})
        else:
            self.stats.quarantined += 1
            logger.error(
                "job_quarantined",
                extra={"job_id": job.id, "job_type": job.type, "attempt": job.attempt},
            )
            await self._alert(job)

    # Running a job ----------------------------------------------------------------------------

    async def _execute(self, handle: _JobHandle) -> None:
        job_id = handle.job.id
        work = asyncio.create_task(self._run_steps(handle))
        keeper = asyncio.create_task(self._keep_alive(handle))
        guard = asyncio.create_task(self._guard_lease(handle))
        active = self._active.get(job_id)
        if active is not None:
            active.work = work
        try:
            await asyncio.wait({work, keeper, guard}, return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                await _cancel_and_wait(keeper)
                await _cancel_and_wait(guard)
                await self._settle(handle, work)
            else:
                verdict = _keeper_verdict(keeper) if keeper.done() else "lost"
                await _cancel_and_wait(keeper)
                await _cancel_and_wait(guard)
                await _cancel_and_wait(work)
                if verdict == "cancel":
                    await self._finish(handle, _cancel, "cancelled")
                else:
                    self._lost(handle)
        except asyncio.CancelledError:
            for task in (work, keeper, guard):
                task.cancel()
            await asyncio.wait({work, keeper, guard}, timeout=_CANCEL_WAIT_SECONDS)
            for task in (work, keeper, guard):
                _reap(task)
            raise
        except ClaimLost:
            self._lost(handle)
        except Exception as exc:
            logger.error(
                "job_execution_crashed", extra={"job_id": job_id, **describe_exception(exc)}
            )
        finally:
            self._active.pop(job_id, None)
            self._wake.set()

    async def _run_steps(self, handle: _JobHandle) -> JsonValue:
        job_type = self._registry.get(handle.job.type)
        if job_type is None:  # pragma: no cover - only registered types are claimed
            raise JobFailed("unknown_type")
        outputs: dict[str, JsonValue] = {}
        last: JsonValue = None
        for spec in job_type.steps:
            state = _require_step(handle.job, spec.name)
            if state.status == "succeeded":
                outputs[spec.name] = state.output
                last = state.output
                continue
            resumed = state.runs > 0
            await handle.write_durably(partial(_step_started, spec.name))
            if handle.job.cancel_requested:
                raise _CancelRequested()
            context = StepContext(handle, spec.name, outputs, resumed=resumed)
            try:
                output = await spec.run(context)
            finally:
                context.close()
            try:
                value = check_json(output, what=f"output of step {spec.name}")
            except ValueError:
                raise JobFailed("invalid_output") from None
            try:
                await handle.write_durably(partial(_step_succeeded, spec.name, value))
            except InvalidRequest:
                # The output makes the document unstorable (too large); re-running the step
                # would only produce it again.
                raise JobFailed("invalid_output") from None
            outputs[spec.name] = value
            last = value
        return last

    async def _keep_alive(self, handle: _JobHandle) -> Literal["cancel", "lost"]:
        every = self._policy.renew_every.total_seconds()
        delay = every
        while True:
            await asyncio.sleep(delay)
            try:
                job = await handle.renew()
            except ClaimLost:
                return "lost"
            except (StoreError, TimeoutError) as exc:
                if self._clock.now() >= handle.claim.deadline:
                    handle.claim.mark_lost()
                    logger.warning(
                        "job_lease_expired_locally",
                        extra={"job_id": handle.job.id, **describe_exception(exc)},
                    )
                    return "lost"
                delay = min(every, _RETRY_SECONDS)
                continue
            if job.cancel_requested:
                return "cancel"
            delay = every

    async def _guard_lease(self, handle: _JobHandle) -> Literal["lost"]:
        """Self-fencing: give the job up once ``leaseUntil - skew`` passes without a renewal.

        The keeper alone cannot promise this: a renewal (or a step write holding the claim)
        can sit in the store well past the deadline, and another replica may take the job over
        at ``leaseUntil + skew``. Every successful write moves the deadline forward.
        """
        check = min(self._policy.renew_every.total_seconds(), _DEADLINE_CHECK_SECONDS)
        while True:
            remaining = (handle.claim.deadline - self._clock.now()).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, check))
        if handle.claim.held:
            handle.claim.mark_lost()
            logger.warning("job_lease_expired_locally", extra={"job_id": handle.job.id})
        return "lost"

    async def _settle(self, handle: _JobHandle, work: asyncio.Task[JsonValue]) -> None:
        """Write the outcome of a finished step task."""
        if work.cancelled():
            active = self._active.get(handle.job.id)
            if active is not None and active.cancel_requested:
                await self._finish(handle, _cancel, "cancelled")
            elif self._stopped:
                await self._release(handle)
            else:
                # Neither a cancel request nor shutdown: the step raised CancelledError itself
                # (a cancelled future it awaited, say). Releasing would give the attempt back
                # and re-run it at once, forever; count it as an error instead.
                await self._handle_error(handle, asyncio.CancelledError())
            return
        exc = work.exception()
        if exc is None:
            try:
                await self._finish(handle, partial(_succeed, work.result()), "succeeded")
            except InvalidRequest:
                # The result made the document unstorable (too large): fail without it.
                await self._finish(handle, partial(_fail, "invalid_output", None), "failed")
            return
        if isinstance(exc, ClaimLost):
            self._lost(handle)
        elif isinstance(exc, _SuspendForInput):
            await self._finish(handle, partial(_suspend, exc), "waiting_input")
        elif isinstance(exc, _CancelRequested):
            await self._finish(handle, _cancel, "cancelled")
        elif isinstance(exc, JobFailed):
            await self._finish(handle, partial(_fail, exc.code, exc.detail), "failed")
        elif isinstance(exc, Exception):
            await self._handle_error(handle, exc)
        else:  # pragma: no cover - other BaseExceptions end the process anyway
            self._lost(handle)

    async def _handle_error(self, handle: _JobHandle, exc: BaseException) -> None:
        job = handle.job
        error_type = _error_type(exc)
        logger.warning(
            "job_step_error",
            extra={
                "job_id": job.id,
                "job_type": job.type,
                "attempt": job.attempt,
                **describe_exception(exc),
            },
        )
        if job.attempt >= job.max_attempts:
            stored = await self._finish(handle, partial(_quarantine, error_type), "quarantined")
            if stored is not None:
                await self._alert(stored)
            return
        base = self._settings.retry_base_seconds * (2 ** max(0, job.attempt - 1))
        delay = timedelta(seconds=min(self._settings.retry_max_seconds, base))
        await self._finish(handle, partial(_retry, error_type, delay), "retried")

    async def _finish(self, handle: _JobHandle, change: Change, outcome: str) -> Job | None:
        """Write the job's outcome and release the claim, retrying transient errors until the
        lease deadline (one token, so a retry never applies it twice)."""
        token = new_write_token()
        while True:
            try:
                job = await handle.write(change, release=True, token=token)
            except ClaimLost:
                self._lost(handle)
                return None
            except (StoreUnavailable, TimeoutError) as exc:
                if self._clock.now() >= handle.claim.deadline:
                    handle.claim.mark_lost()
                    logger.error(
                        "job_settle_failed",
                        extra={
                            "job_id": handle.job.id,
                            "outcome": outcome,
                            **describe_exception(exc),
                        },
                    )
                    return None
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            self._count(outcome)
            log = logger.error if outcome == "quarantined" else logger.info
            log(
                "job_settled",
                extra={
                    "job_id": job.id,
                    "job_type": job.type,
                    "job_status": job.status,
                    "attempt": job.attempt,
                    "outcome": outcome,
                },
            )
            return job

    async def _release(self, handle: _JobHandle) -> None:
        """Give a job back (shutdown): ``queued`` again, attempt returned."""
        try:
            await bounded(handle.write(_release, release=True), _RELEASE_BUDGET_SECONDS)
        except ClaimLost:
            self._lost(handle)
            return
        except (StoreError, TimeoutError) as exc:
            logger.warning(
                "job_release_failed", extra={"job_id": handle.job.id, **describe_exception(exc)}
            )
            return
        self.stats.released += 1
        logger.info("job_released", extra={"job_id": handle.job.id, "job_type": handle.job.type})

    def _count(self, outcome: str) -> None:
        match outcome:
            case "succeeded":
                self.stats.succeeded += 1
            case "failed":
                self.stats.failed += 1
            case "retried":
                self.stats.retried += 1
            case "quarantined":
                self.stats.quarantined += 1
            case "waiting_input":
                self.stats.waiting += 1
            case "cancelled":
                self.stats.cancelled += 1
            case _:
                pass

    def _lost(self, handle: _JobHandle) -> None:
        handle.claim.mark_lost()
        self.stats.claims_lost += 1
        logger.warning(
            "job_claim_lost",
            extra={"job_id": handle.job.id, "fence": handle.claim.fence, "owner": self._owner},
        )

    async def _alert(self, job: Job) -> None:
        if self._on_quarantine is None:
            return
        error = job.error
        notice = QuarantineNotice(
            job_id=job.id,
            job_type=job.type,
            attempts=job.attempt,
            step=error.step if error is not None else None,
            error_type=error.error_type if error is not None else None,
        )
        try:
            await bounded(self._on_quarantine(notice), _ALERT_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.error(
                "job_quarantine_alert_failed",
                extra={"job_id": job.id, **describe_exception(exc)},
            )


def _keeper_verdict(keeper: asyncio.Task[Literal["cancel", "lost"]]) -> Literal["cancel", "lost"]:
    if keeper.cancelled() or keeper.exception() is not None:
        return "lost"
    return keeper.result()


async def _cancel_and_wait(task: asyncio.Task[object] | asyncio.Task[JsonValue]) -> None:
    if not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=_CANCEL_WAIT_SECONDS)
    _reap(task)


def _reap(task: asyncio.Task[object] | asyncio.Task[JsonValue]) -> None:
    """Mark a finished task's exception as seen: the job's outcome was decided elsewhere (the
    lease guard won, say), and asyncio would otherwise log it with a traceback on collection."""
    if task.done() and not task.cancelled():
        task.exception()
