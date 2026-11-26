"""The job document (container ``jobs``, ``kind: "job"``) and its parts.

The ``jobs`` container holds two kinds of claim documents: jobs (this module) and schedule items
(``kind: "schedule"``, :mod:`blogwriter.jobs.scheduler`). Every query filters on ``kind``.

Documents are camelCase JSON (``leaseUntil``, ``maxAttempts``, ``remoteIds``...). Timestamps are
fixed-width ISO-8601 UTC strings (:func:`blogwriter.jobs.clock.to_iso`) so they compare correctly
as strings in queries. Unknown fields are kept on load and written back unchanged, so a document
written by a newer revision survives a write by an older one (revision overlap).

Status machine::

    queued -> running -> succeeded | failed | quarantined | cancelled
    running -> queued            retry after an error (backoff), or released at shutdown
    running -> waiting_input     claim and slot released; the owner's answer re-queues it
    running -> running           lease expired, another owner took over (attempt + 1)
    queued | waiting_input -> cancelled
    failed | quarantined | cancelled -> queued     the owner retries (attempts reset)

Claim fields (shared with :mod:`blogwriter.jobs.claims`): ``owner`` (replica + boot UUID),
``leaseUntil``, ``fence`` (incremented on every acquire) and ``writeId`` (the token of the last
claim write, which makes writes with an unknown outcome idempotent).

Events: every transition and every ``emit`` appends a :class:`JobEvent` with the next ``seq``.
Only the last :data:`MAX_EVENTS` are kept; ``seq`` keeps counting, so ``<jobId>:<seq>`` is a
stable SSE event id.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic.alias_generators import to_camel

from blogwriter.db.base import validate_id
from blogwriter.jobs.clock import parse_iso

__all__ = [
    "ACTIVE_STATUSES",
    "DEFAULT_MAX_ATTEMPTS",
    "JOBS_CONTAINER",
    "JOB_KIND",
    "MAX_DETAIL_CHARS",
    "MAX_EVENTS",
    "MAX_EVENT_DATA_BYTES",
    "MAX_PROMPT_CHARS",
    "MAX_REMOTE_ID_CHARS",
    "NAME_PATTERN",
    "TERMINAL_STATUSES",
    "DocId",
    "DocModel",
    "InputRequest",
    "Job",
    "JobError",
    "JobEvent",
    "JobStatus",
    "Name",
    "StepState",
    "StepStatus",
    "Timestamp",
    "check_json",
    "event_data_size",
    "new_job_id",
]

JOBS_CONTAINER: Final = "jobs"
JOB_KIND: Final = "job"
MAX_EVENTS: Final = 50
"""Events kept on a job document (older ones are dropped; ``seq`` keeps counting)."""
MAX_EVENT_DATA_BYTES: Final = 4096
"""Largest ``data`` of one event, as compact UTF-8 JSON."""
MAX_PROMPT_CHARS: Final = 4000
MAX_DETAIL_CHARS: Final = 500
MAX_REMOTE_ID_CHARS: Final = 2048
DEFAULT_MAX_ATTEMPTS: Final = 3
NAME_PATTERN: Final = r"^[a-z][a-z0-9_.-]{0,63}$"
"""Job types, step names, event types and keys: lowercase identifiers (dots and dashes allowed)."""

type JobStatus = Literal[
    "queued", "running", "waiting_input", "succeeded", "failed", "quarantined", "cancelled"
]
type StepStatus = Literal["pending", "running", "waiting_input", "succeeded", "failed"]

ACTIVE_STATUSES: Final[frozenset[JobStatus]] = frozenset({"queued", "running", "waiting_input"})
TERMINAL_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {"succeeded", "failed", "quarantined", "cancelled"}
)


def _check_timestamp(value: str) -> str:
    parse_iso(value)
    return value


def _check_doc_id(value: str) -> str:
    return validate_id(value)


type Timestamp = Annotated[str, AfterValidator(_check_timestamp)]
type DocId = Annotated[str, AfterValidator(_check_doc_id)]
type Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]


_JSON: Final = TypeAdapter[JsonValue](JsonValue)


def check_json(value: object, *, what: str) -> JsonValue:
    """Return ``value`` if it is plain JSON (dict, list, str, int, float, bool, None)."""
    try:
        return _JSON.validate_python(value, strict=True)
    except ValidationError:
        raise ValueError(f"{what} must be JSON (dict, list, str, int, float, bool, None)") from None


def event_data_size(data: Mapping[str, JsonValue]) -> int:
    """Size of event ``data`` as compact UTF-8 JSON (limit: :data:`MAX_EVENT_DATA_BYTES`)."""
    return len(json.dumps(dict(data), separators=(",", ":"), ensure_ascii=False).encode())


def new_job_id() -> str:
    """A fresh random job id (``job-<32 hex>``)."""
    return f"job-{uuid.uuid4().hex}"


class DocModel(BaseModel):
    """Base of stored documents: camelCase on the wire, unknown fields preserved."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="allow",
    )


class JobError(DocModel):
    """Why a job failed, was quarantined or is being retried. Codes and type names only."""

    code: Name
    """``step_error`` (retryable exception), ``job_failed`` (raised :class:`JobFailed`),
    ``max_attempts`` (quarantined), ``invalid_output``..."""
    at: Timestamp
    step: str | None = None
    error_type: str | None = Field(default=None, max_length=200)
    """Exception class of a ``step_error``; never the message (it may carry content)."""
    detail: str | None = Field(default=None, max_length=MAX_DETAIL_CHARS)
    """Owner-facing text a step chose to attach to :class:`JobFailed`."""


class StepState(DocModel):
    """Durable state of one step: status, checkpointed output and remote ids."""

    name: Name
    status: StepStatus = "pending"
    output: JsonValue = None
    """The step's final output once succeeded; before that, its last checkpoint."""
    remote_ids: dict[str, Annotated[str, Field(max_length=MAX_REMOTE_ID_CHARS)]] = Field(
        default_factory=dict[str, str]
    )
    """Ids of remote work (a Responses id, an upload URN, a publish intent), recorded before
    awaiting it, so a resumed step re-attaches instead of re-issuing."""
    inputs: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Owner answers to ``wait_for_input``, by key."""
    runs: int = Field(default=0, ge=0)
    """How many times the step function was entered."""
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None


class InputRequest(DocModel):
    """What a ``waiting_input`` job is waiting for."""

    step: Name
    key: Name
    prompt: str = Field(max_length=MAX_PROMPT_CHARS)
    requested_at: Timestamp


class JobEvent(DocModel):
    """One step or status event (SSE id ``<jobId>:<seq>``)."""

    seq: int = Field(ge=1)
    at: Timestamp
    type: Name
    step: str | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class Job(DocModel):
    """A job document. Build new ones with :func:`blogwriter.jobs.queue.enqueue`."""

    id: DocId
    kind: Literal["job"] = "job"
    type: Name
    status: JobStatus = "queued"
    steps: list[StepState] = Field(default_factory=list[StepState])
    attempt: int = Field(default=0, ge=0)
    """Claims of this job that did not end in a clean release (crashes, failures) plus the
    current one. Releases at shutdown and waits for input give the attempt back."""
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=20)
    owner: str | None = None
    lease_until: Timestamp | None = None
    fence: int = Field(default=0, ge=0)
    write_id: str | None = None
    run_after: Timestamp
    """A queued job is not started before this time (retry backoff, delayed start)."""
    input: JsonValue = None
    result: JsonValue = None
    error: JobError | None = None
    awaiting: InputRequest | None = None
    cancel_requested: bool = False
    schedule_id: str | None = None
    scheduled_for: Timestamp | None = None
    seq: int = Field(default=0, ge=0)
    events: list[JobEvent] = Field(default_factory=list[JobEvent])
    created_at: Timestamp
    updated_at: Timestamp
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    ttl: int | None = None
    """Cosmos TTL in seconds (garbage collection only); left out of the document when unset."""

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("step names must be unique")
        if self.events and self.events[-1].seq > self.seq:
            raise ValueError("event seq is ahead of the job seq")
        if self.ttl is not None and (self.ttl == 0 or self.ttl < -1):
            raise ValueError("ttl must be -1 or at least 1")
        return self

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> Job:
        """Parse a stored body (raises ``pydantic.ValidationError`` when it is not a job)."""
        return cls.model_validate(dict(body))

    def to_body(self) -> dict[str, Any]:
        """The JSON body to store."""
        body = self.model_dump(mode="json", by_alias=True)
        if body.get("ttl") is None:
            body.pop("ttl", None)
        return body

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def step(self, name: str) -> StepState | None:
        """The state of step ``name``, if the job has it."""
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def add_event(
        self,
        event_type: str,
        *,
        at: str,
        step: str | None = None,
        data: Mapping[str, JsonValue] | None = None,
    ) -> JobEvent:
        """Append an event with the next ``seq``, dropping the oldest beyond :data:`MAX_EVENTS`.

        Raises ``ValueError`` when ``data`` is larger than :data:`MAX_EVENT_DATA_BYTES`.
        """
        payload: dict[str, JsonValue] = dict(data or {})
        if event_data_size(payload) > MAX_EVENT_DATA_BYTES:
            raise ValueError(f"event data is larger than {MAX_EVENT_DATA_BYTES} bytes")
        event = JobEvent(seq=self.seq + 1, at=at, type=event_type, step=step, data=payload)
        self.seq = event.seq
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]
        return event

    def events_after(self, seq: int) -> list[JobEvent]:
        """Retained events with a ``seq`` greater than ``seq``."""
        return [event for event in self.events if event.seq > seq]
