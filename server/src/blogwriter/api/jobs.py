"""Job routes of the System page (contract I-2): list, read, cancel, retry.

::

    GET  /api/jobs?status=&type=&limit=&cursor=   Page[JobView] (newest update first)
    GET  /api/jobs/{id}                           JobView (with its retained events)
    POST /api/jobs/{id}/cancel                    JobView (a finished job is returned unchanged;
                                                  a child's parent is told, a publish job's
                                                  article released to publish_failed)
    POST /api/jobs/{id}/retry                     JobView (409 job_state unless failed,
                                                  quarantined or cancelled)

:class:`JobView` is the SSE ``snapshot`` shape (``jobs.sse.snapshot_payload``): status, steps,
the pending input request, the error and the events; never the job's input, step outputs,
checkpoints or result. List items leave ``events`` out (``null``); the detail and the actions
carry them. An id that cannot exist is 404.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Final

from fastapi import APIRouter, Query
from pydantic import JsonValue, ValidationError

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, OrderBy
from blogwriter.domain.api import (
    ApiModel,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.deps import JobsDep, ServicesDep, StoreDep, problems
from blogwriter.domain.jobflow import cancel_job as cancel_and_tell_parent
from blogwriter.jobs import Job, JobStateError, JobStatus, StepStatus
from blogwriter.jobs.models import JOB_KIND, JOBS_CONTAINER, NAME_PATTERN
from blogwriter.jobs.sse import snapshot_payload
from blogwriter.publish.recovery import PUBLISH_JOB_TYPES, after_cancel

__all__ = [
    "JobAwaitingView",
    "JobErrorView",
    "JobEventView",
    "JobStepView",
    "JobView",
    "add_routes",
    "get_router",
    "job_problems",
    "job_view",
]

_NEWEST_FIRST: Final = OrderBy("updatedAt", descending=True)
_logger: Final = logging.getLogger("blogwriter.api.jobs")


class JobStepView(ApiModel):
    name: str
    status: StepStatus
    runs: int
    started_at: str | None = None
    finished_at: str | None = None


class JobAwaitingView(ApiModel):
    step: str
    key: str
    prompt: str
    requested_at: str


class JobErrorView(ApiModel):
    code: str
    step: str | None = None
    error_type: str | None = None
    detail: str | None = None
    at: str


class JobEventView(ApiModel):
    seq: int
    at: str
    type: str
    step: str | None = None
    data: dict[str, JsonValue]


class JobView(ApiModel):
    """A job as the System page and the timelines show it (the SSE snapshot shape)."""

    job_id: str
    type: str
    status: JobStatus
    attempt: int
    max_attempts: int
    seq: int
    cancel_requested: bool
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    scheduled_for: str | None = None
    steps: list[JobStepView]
    awaiting: JobAwaitingView | None = None
    error: JobErrorView | None = None
    events: list[JobEventView] | None = None
    """The retained events (detail and actions); ``null`` in lists."""


def job_view(job: Job, *, events: bool = True) -> JobView:
    """The :class:`JobView` of ``job`` (built from the SSE snapshot payload)."""
    payload = snapshot_payload(job, since=0 if events else job.seq)
    if not events:
        payload["events"] = None
    return JobView.model_validate(payload)


@contextmanager
def job_problems() -> Generator[None]:
    """:func:`~blogwriter.domain.deps.problems` plus job states (409 ``job_state``) and store
    timeouts (503 ``store_unavailable``)."""
    try:
        with problems():
            yield
    except JobStateError as exc:
        raise ProblemError(
            409,
            code="job_state",
            detail="The job cannot do that in its current state.",
            extensions={"reason": exc.code},
        ) from None
    except TimeoutError:
        raise ProblemError(503, code="store_unavailable", headers={"Retry-After": "2"}) from None


async def list_jobs(
    store: StoreDep,
    status: Annotated[JobStatus | None, Query()] = None,
    job_type: Annotated[str | None, Query(alias="type", pattern=NAME_PATTERN)] = None,
    limit: Annotated[int | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[JobView]:
    """Jobs, most recently updated first (``?status=`` and ``?type=`` filter)."""
    size = clamp_limit(limit)
    with job_problems():
        filters = [Filter("kind", "==", JOB_KIND)]
        if status is not None:
            filters.append(Filter("status", "==", status))
        if job_type is not None:
            filters.append(Filter("type", "==", job_type))
        if cursor is not None:
            filters.append(Filter("updatedAt", "<", decode_cursor(cursor)))
        docs = await store.find(JOBS_CONTAINER, filters, order_by=_NEWEST_FIRST, limit=size + 1)
    items: list[Versioned[JobView]] = []
    for doc in docs[:size]:
        try:
            job = Job.from_body(doc.body)
        except ValidationError:
            _logger.warning("jobs_invalid_document", extra={"job_id": doc.id})
            continue
        items.append(Versioned[JobView](item=job_view(job, events=False), etag=doc.etag))
    next_cursor: str | None = None
    if len(docs) > size:
        last: object = docs[size - 1].body.get("updatedAt")
        next_cursor = encode_cursor(last) if isinstance(last, str) and last else None
    return Page[JobView](items=items, next_cursor=next_cursor)


async def read_job(job_id: str, jobs: JobsDep) -> JobView:
    with job_problems():
        job = await jobs.get(job_id)
    return job_view(job)


async def cancel_job(job_id: str, jobs: JobsDep, services: ServicesDep) -> JobView:
    """Cancel (a running job gets a cancel request; a finished one is returned unchanged).

    A child job's waiting parent is told (``jobflow.cancel_job``), and a publish job's article
    is released to ``publish_failed`` once the run stopped (``publish.recovery.after_cancel``),
    so neither is left waiting on a job that ended without running its own code."""
    with job_problems():
        job = await cancel_and_tell_parent(jobs, job_id)
        if job.type in PUBLISH_JOB_TYPES:
            await after_cancel(services, job_id)
            job = await jobs.get(job_id)
    return job_view(job)


async def retry_job(job_id: str, jobs: JobsDep) -> JobView:
    """Re-queue a failed, quarantined or cancelled job (attempts reset)."""
    with job_problems():
        job = await jobs.retry(job_id)
    return job_view(job)


def add_routes(router: APIRouter) -> None:
    """Add this module's routes to ``router`` (the system router mounts them directly, so the
    app factory's duplicate-route check sees every path)."""
    router.add_api_route(
        "/api/jobs",
        list_jobs,
        methods=["GET"],
        response_model=Page[JobView],
        summary="Jobs, newest update first",
    )
    router.add_api_route(
        "/api/jobs/{job_id}",
        read_job,
        methods=["GET"],
        response_model=JobView,
        summary="One job with its events",
    )
    router.add_api_route(
        "/api/jobs/{job_id}/cancel",
        cancel_job,
        methods=["POST"],
        response_model=JobView,
        summary="Cancel a job",
    )
    router.add_api_route(
        "/api/jobs/{job_id}/retry",
        retry_job,
        methods=["POST"],
        response_model=JobView,
        summary="Retry a failed, quarantined or cancelled job",
    )


def get_router() -> APIRouter:
    router = APIRouter(tags=["system"])
    add_routes(router)
    return router
