"""Schedule routes of the Settings > Slots and System pages (contract I-2).

::

    GET  /api/schedules                  [ScheduleView] (soonest first)
    POST /api/schedules/{id}/confirm     {run: bool}  -> ScheduleView   (409 schedule_state)
    POST /api/schedules/{id}/pause       ScheduleView
    POST /api/schedules/{id}/resume      ScheduleView                   (409 schedule_state)

The items are the scheduler's (``jobs.scheduler``), seeded from every package's
``schedule_entries`` (``services.ScheduleSeeder``). ``confirm`` settles an item that waits for
the owner after downtime (``needs_confirmation``): ``run`` enqueues the missed slot now (same
deterministic job id, so a double click is harmless), otherwise the slot is skipped. Pausing is
idempotent; ``resume`` restarts from the next slot after now (no catch-up).
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, OrderBy
from blogwriter.domain.api import ApiModel
from blogwriter.domain.deps import problems
from blogwriter.jobs import (
    DailyRule,
    IntervalRule,
    JobsService,
    ScheduleItem,
    ScheduleStateError,
    get_jobs_service,
)
from blogwriter.jobs.models import JOBS_CONTAINER
from blogwriter.jobs.scheduler import SCHEDULE_KIND, Weekday

__all__ = [
    "ScheduleConfirmIn",
    "ScheduleRuleView",
    "ScheduleView",
    "add_routes",
    "get_router",
    "schedule_view",
]

_logger: Final = logging.getLogger("blogwriter.api.schedules")

JobsServiceDep = Annotated[JobsService, Depends(get_jobs_service)]


class ScheduleRuleView(ApiModel):
    """``daily`` (``at``), ``weekly`` (``days``, ``at``) or ``interval`` (``seconds`` from
    ``anchor``); times are local to the item's ``timezone``."""

    kind: Literal["daily", "weekly", "interval"]
    at: str | None = None
    days: list[Weekday] | None = None
    seconds: int | None = None
    anchor: str | None = None


class ScheduleView(ApiModel):
    id: str
    name: str
    job_type: str
    rule: ScheduleRuleView
    timezone: str
    status: Literal["active", "needs_confirmation", "paused"]
    next_run_at: str
    pending_run_at: str | None = None
    last_run_at: str | None = None
    skipped_runs: int


class ScheduleConfirmIn(ApiModel):
    run: bool


def schedule_view(item: ScheduleItem) -> ScheduleView:
    rule = item.rule
    match rule:
        case DailyRule():
            view = ScheduleRuleView(kind="daily", at=rule.at)
        case IntervalRule():
            view = ScheduleRuleView(kind="interval", seconds=rule.seconds, anchor=rule.anchor)
        case _:
            view = ScheduleRuleView(kind="weekly", days=list(rule.days), at=rule.at)
    return ScheduleView(
        id=item.id,
        name=item.name,
        job_type=item.job_type,
        rule=view,
        timezone=item.timezone,
        status=item.status,
        next_run_at=item.next_run_at,
        pending_run_at=item.pending_run_at,
        last_run_at=item.last_run_at,
        skipped_runs=item.skipped_runs,
    )


@contextmanager
def _schedule_problems() -> Generator[None]:
    try:
        with problems():
            yield
    except ScheduleStateError as exc:
        raise ProblemError(
            409,
            code="schedule_state",
            detail="The schedule item cannot do that in its current state.",
            extensions={"reason": exc.code},
        ) from None
    except TimeoutError:
        raise ProblemError(503, code="store_unavailable", headers={"Retry-After": "2"}) from None


async def list_schedules(jobs: JobsServiceDep) -> list[ScheduleView]:
    """Every schedule item, soonest first (an unreadable item is skipped and logged)."""
    with _schedule_problems():
        docs = await jobs.store.find(
            JOBS_CONTAINER,
            [Filter("kind", "==", SCHEDULE_KIND)],
            order_by=OrderBy("nextRunAt"),
        )
    views: list[ScheduleView] = []
    for doc in docs:
        try:
            views.append(schedule_view(ScheduleItem.from_body(doc.body)))
        except ValidationError:
            _logger.warning("schedule_invalid_document", extra={"schedule_id": doc.id})
    return views


async def confirm_schedule(
    schedule_id: str, body: ScheduleConfirmIn, jobs: JobsServiceDep
) -> ScheduleView:
    with _schedule_problems():
        item = await jobs.scheduler.confirm(schedule_id, run=body.run)
    if body.run:
        jobs.runner.wake()
    return schedule_view(item)


async def pause_schedule(schedule_id: str, jobs: JobsServiceDep) -> ScheduleView:
    with _schedule_problems():
        item = await jobs.scheduler.pause(schedule_id)
    return schedule_view(item)


async def resume_schedule(schedule_id: str, jobs: JobsServiceDep) -> ScheduleView:
    with _schedule_problems():
        item = await jobs.scheduler.resume(schedule_id)
    return schedule_view(item)


def add_routes(router: APIRouter) -> None:
    """Add this module's routes to ``router`` (the system router mounts them directly, so the
    app factory's duplicate-route check sees every path)."""
    router.add_api_route(
        "/api/schedules",
        list_schedules,
        methods=["GET"],
        response_model=list[ScheduleView],
        summary="Schedule items, soonest first",
    )
    router.add_api_route(
        "/api/schedules/{schedule_id}/confirm",
        confirm_schedule,
        methods=["POST"],
        response_model=ScheduleView,
        summary="Run or skip the missed slot of an item that waits for confirmation",
    )
    router.add_api_route(
        "/api/schedules/{schedule_id}/pause",
        pause_schedule,
        methods=["POST"],
        response_model=ScheduleView,
        summary="Pause a schedule item",
    )
    router.add_api_route(
        "/api/schedules/{schedule_id}/resume",
        resume_schedule,
        methods=["POST"],
        response_model=ScheduleView,
        summary="Resume a paused schedule item from its next slot",
    )


def get_router() -> APIRouter:
    router = APIRouter(tags=["system"])
    add_routes(router)
    return router
