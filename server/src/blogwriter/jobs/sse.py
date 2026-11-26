"""``GET /api/events``: the app-wide SSE stream (plan: "Reliability model", "Streaming").

::

    GET /api/events[?jobs=<jobId>,<jobId>...]
    Last-Event-ID: <jobId>:<seq>          (EventSource sends it when it reconnects)

The stream, in order:

1. ``retry: <ms>``, the reconnect delay for ``EventSource``.
2. One ``snapshot`` event per affected job (``id: <jobId>:<seq>``): status, attempt, steps,
   the pending input request, the error, and the retained job events the client has not seen
   (for the job named by ``Last-Event-ID``, only those after its ``seq``). Affected jobs: the
   ``jobs`` filter when given; otherwise every active job, the jobs updated in the last
   ``sse_recent_seconds`` and the ``Last-Event-ID`` job.
3. Live ``job`` events (``id: <jobId>:<seq>``) and ``delta`` events (no id: token deltas are not
   durable). A job event already covered by a snapshot or already sent is not sent again. Two
   writers of one job (its runner and an API call) can publish out of ``seq`` order, so a late
   lower ``seq`` is still delivered: clients de-duplicate by ``(jobId, seq)`` and must not
   assume ascending order.
4. ``: heartbeat`` comment lines every ``sse_heartbeat_seconds``.
5. A final ``close`` event, ``{"reason": ...}``: ``max_duration`` after
   ``sse_max_stream_seconds`` (the platform cuts streams at 240 s), ``overflow`` when this
   client fell too far behind, ``shutdown`` when the app stops, ``unavailable`` when the store
   could not be read. ``EventSource`` reconnects with ``Last-Event-ID`` and gets a snapshot, so
   no job event is ever lost, only re-synced.

The subscription to the broker is taken before the snapshot is read, so nothing published in
between is missed. Payloads carry ids, statuses, step names and event data only; never step
outputs or job input.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, Query
from pydantic import JsonValue
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from blogwriter.core.auth import current_principal
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import describe_exception
from blogwriter.db import Filter, InvalidRequest, NotFound, OrderBy, StoreError
from blogwriter.db.base import validate_id
from blogwriter.jobs._logging import logger
from blogwriter.jobs.broker import BrokerFull, JobEventMessage, Subscription
from blogwriter.jobs.claims import bounded
from blogwriter.jobs.clock import to_iso
from blogwriter.jobs.lifecycle import JobsService, get_jobs_service
from blogwriter.jobs.models import ACTIVE_STATUSES, JOB_KIND, JOBS_CONTAINER, Job
from blogwriter.jobs.queue import get_job

__all__ = [
    "EVENTS_PATH",
    "MAX_FILTER_JOBS",
    "get_router",
    "parse_job_filter",
    "parse_last_event_id",
    "snapshot_payload",
]

EVENTS_PATH: Final = "/api/events"
MAX_FILTER_JOBS: Final = 20
_MAX_FILTER_CHARS: Final = 20 * 256
_MAX_LAST_EVENT_ID_CHARS: Final = 300
_SEQ_RE: Final = re.compile(r"[0-9]{1,15}")
_MAX_OUT_OF_ORDER: Final = 256
"""Sent seqs above a job's contiguous floor that one stream remembers."""

type LastEvent = tuple[str, int]


class _SentSeqs:
    """Which seqs of one job a stream has covered: everything up to ``floor``, plus ``above``.

    The floor starts at the job's snapshot seq (0 for jobs that were not in the snapshot) and
    moves up over contiguous runs, so ``above`` only holds seqs that arrived out of order.
    """

    __slots__ = ("above", "floor")

    def __init__(self, floor: int) -> None:
        self.floor = floor
        self.above: set[int] = set()

    def first_time(self, seq: int) -> bool:
        """Record ``seq``; ``False`` if it was covered already."""
        if seq <= self.floor or seq in self.above:
            return False
        self.above.add(seq)
        if len(self.above) > _MAX_OUT_OF_ORDER:
            self.floor = min(self.above) - 1  # stop waiting for the oldest gap
        while self.floor + 1 in self.above:
            self.floor += 1
            self.above.discard(self.floor)
        return True


def parse_job_filter(raw: str | None) -> frozenset[str] | None:
    """``a,b,c`` -> the job ids (1-20 valid ids); ``None`` when absent. 422 problem if invalid."""
    if raw is None:
        return None
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not ids or len(ids) > MAX_FILTER_JOBS:
        raise ProblemError(
            422,
            code="invalid_jobs_filter",
            detail=f"jobs must list 1 to {MAX_FILTER_JOBS} job ids separated by commas.",
        )
    for job_id in ids:
        try:
            validate_id(job_id)
        except InvalidRequest:
            raise ProblemError(
                422, code="invalid_jobs_filter", detail="jobs contains an invalid job id."
            ) from None
    return frozenset(ids)


def parse_last_event_id(raw: str | None) -> LastEvent | None:
    """``<jobId>:<seq>`` -> ``(jobId, seq)``; anything else -> ``None`` (a fresh connect)."""
    if not raw:
        return None
    job_id, separator, seq_text = raw.strip().rpartition(":")
    # ASCII digits only: str.isdigit() also accepts "²" (latin-1, so it can arrive in a header),
    # which int() rejects.
    if not separator or _SEQ_RE.fullmatch(seq_text) is None:
        return None
    try:
        validate_id(job_id)
    except InvalidRequest:
        return None
    return job_id, int(seq_text)


def snapshot_payload(job: Job, *, since: int = 0) -> dict[str, JsonValue]:
    """The ``snapshot`` event body for ``job`` with its retained events after ``since``."""
    awaiting = job.awaiting
    error = job.error
    return {
        "jobId": job.id,
        "type": job.type,
        "status": job.status,
        "attempt": job.attempt,
        "maxAttempts": job.max_attempts,
        "seq": job.seq,
        "cancelRequested": job.cancel_requested,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "scheduledFor": job.scheduled_for,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "runs": step.runs,
                "startedAt": step.started_at,
                "finishedAt": step.finished_at,
            }
            for step in job.steps
        ],
        "awaiting": None
        if awaiting is None
        else {
            "step": awaiting.step,
            "key": awaiting.key,
            "prompt": awaiting.prompt,
            "requestedAt": awaiting.requested_at,
        },
        "error": None
        if error is None
        else {
            "code": error.code,
            "step": error.step,
            "errorType": error.error_type,
            "detail": error.detail,
            "at": error.at,
        },
        "events": [
            {
                "seq": event.seq,
                "at": event.at,
                "type": event.type,
                "step": event.step,
                "data": dict(event.data),
            }
            for event in job.events_after(since)
        ],
    }


def _encode(payload: Mapping[str, JsonValue]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _heartbeat() -> ServerSentEvent:
    return ServerSentEvent(comment="heartbeat")


async def _snapshot_jobs(
    service: JobsService, job_filter: frozenset[str] | None, last: LastEvent | None
) -> list[Job]:
    settings = service.settings
    timeout = settings.store_timeout_seconds
    limit = settings.sse_snapshot_limit
    jobs: dict[str, Job] = {}
    if job_filter is not None:
        for job_id in sorted(job_filter):
            try:
                jobs[job_id] = await get_job(service.store, job_id, store_timeout=timeout)
            except NotFound:
                continue
            except ValueError:  # not a valid job document: skip it like the app-wide branch
                logger.warning("jobs_invalid_document", extra={"job_id": job_id})
    else:
        now: datetime = service.clock.now()
        base = [Filter("kind", "==", JOB_KIND)]
        active = await bounded(
            service.store.find(
                JOBS_CONTAINER,
                [*base, Filter("status", "in", sorted(ACTIVE_STATUSES))],
                limit=limit,
            ),
            timeout,
        )
        recent = await bounded(
            service.store.find(
                JOBS_CONTAINER,
                [
                    *base,
                    Filter(
                        "updatedAt",
                        ">=",
                        to_iso(now - timedelta(seconds=settings.sse_recent_seconds)),
                    ),
                ],
                order_by=OrderBy("updatedAt", descending=True),
                limit=limit,
            ),
            timeout,
        )
        for doc in (*active, *recent):
            try:
                jobs[doc.id] = Job.from_body(doc.body)
            except ValueError:
                logger.warning("jobs_invalid_document", extra={"job_id": doc.id})
        if last is not None and last[0] not in jobs:
            with contextlib.suppress(NotFound):
                jobs[last[0]] = await get_job(service.store, last[0], store_timeout=timeout)
    ordered = sorted(jobs.values(), key=lambda job: job.updated_at, reverse=True)
    selected = ordered[:limit]
    resumed = jobs.get(last[0]) if last is not None else None
    if resumed is not None and all(job.id != resumed.id for job in selected):
        # The job the client is resuming always gets its snapshot: without it, the events it
        # missed before this stream opened would be in neither the snapshot nor the live feed.
        selected = [*selected[: limit - 1], resumed]
    return selected


async def _stream(
    service: JobsService,
    subscription: Subscription,
    job_filter: frozenset[str] | None,
    last: LastEvent | None,
) -> AsyncIterator[ServerSentEvent]:
    settings = service.settings
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.sse_max_stream_seconds
    async with subscription:
        yield ServerSentEvent(retry=settings.sse_retry_ms, comment="stream open")
        try:
            jobs = await _snapshot_jobs(service, job_filter, last)
        except (StoreError, TimeoutError) as exc:
            logger.warning("jobs_snapshot_failed", extra=describe_exception(exc))
            yield ServerSentEvent(data=_encode({"reason": "unavailable"}), event="close")
            return
        sent: dict[str, _SentSeqs] = {}
        for job in jobs:
            since = last[1] if last is not None and last[0] == job.id else 0
            sent[job.id] = _SentSeqs(job.seq)
            yield ServerSentEvent(
                data=_encode(snapshot_payload(job, since=since)),
                event="snapshot",
                id=f"{job.id}:{job.seq}",
            )
        reason = "max_duration"
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            message = await subscription.get(max_wait=remaining)
            if message is None:
                if subscription.closed:
                    reason = subscription.close_reason or "closed"
                    break
                continue
            if isinstance(message, JobEventMessage):
                seqs = sent.get(message.job_id)
                if seqs is None:
                    seqs = sent[message.job_id] = _SentSeqs(0)
                if not seqs.first_time(message.seq):
                    continue
                yield ServerSentEvent(
                    data=_encode(message.payload()), event="job", id=message.event_id
                )
            else:
                yield ServerSentEvent(data=_encode(message.payload()), event="delta")
        yield ServerSentEvent(data=_encode({"reason": reason}), event="close")


async def stream_events(
    service: Annotated[JobsService, Depends(get_jobs_service)],
    jobs: Annotated[str | None, Query(max_length=_MAX_FILTER_CHARS)] = None,
    last_event_id: Annotated[str | None, Header(max_length=_MAX_LAST_EVENT_ID_CHARS)] = None,
) -> EventSourceResponse:
    """The SSE stream (see the module docs)."""
    job_filter = parse_job_filter(jobs)
    last = parse_last_event_id(last_event_id)
    try:
        subscription = service.broker.subscribe(job_filter)
    except BrokerFull:
        raise ProblemError(
            503,
            code="too_many_streams",
            detail="Too many open event streams.",
            headers={"Retry-After": "5"},
        ) from None

    async def unsubscribe() -> None:
        # Also covers a response whose stream never starts (the client left first).
        subscription.close()

    settings = service.settings
    return EventSourceResponse(
        _stream(service, subscription, job_filter, last),
        ping=settings.sse_heartbeat_seconds,
        ping_message_factory=_heartbeat,
        send_timeout=settings.sse_send_timeout_seconds,
        background=BackgroundTask(unsubscribe),
    )


def get_router() -> APIRouter:
    """``GET /api/events`` (owner only, like every route but ``/api/health``)."""
    router = APIRouter(tags=["jobs"])
    router.add_api_route(
        EVENTS_PATH,
        stream_events,
        methods=["GET"],
        dependencies=[Depends(current_principal)],
        response_model=None,
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": "Server-sent events: snapshot, job, delta, close; heartbeats.",
            }
        },
        summary="App-wide job event stream (SSE)",
    )
    return router
