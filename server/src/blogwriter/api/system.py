"""System routes (contract I-2), thin over ``blogwriter.services`` and ``blogwriter.jobs``.

::

    GET  /api/system/status            SystemStatus (services.system_status)
    POST /api/system/safe-mode/exit    SystemStatus (clears the crash-loop guard, starts the
                                       runner and the scheduler; idempotent)
    POST /api/system/test-alert        {push: bool} -> 202 {alert: "sent", push: {...} | null}
    GET  /api/meta/machines            {<machine>: MachineView} (domain.states): every edge,
                                       the edges the owner takes, the states the owner edits in

plus the job, schedule and push routes of :mod:`blogwriter.api.jobs`,
:mod:`blogwriter.api.schedules` and :mod:`blogwriter.api.push`: :func:`get_router` adds them all
to one router (plain routes, so the app factory's duplicate check sees every path) and
``blogwriter.main`` includes just that one.

The test alert writes one ``alert_event`` (kind ``test``, the request id as its reference) for
Azure Monitor to e-mail, and with ``push`` sends a test notification (title and a link to the
System page only) to every subscribed device.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, Request

from blogwriter.api import jobs as jobs_routes
from blogwriter.api import push as push_routes
from blogwriter.api import schedules as schedule_routes
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import current_request_id
from blogwriter.domain.api import ApiModel
from blogwriter.domain.deps import NotifierDep, ServicesDep, problems
from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ARTICLE_WRITER,
    MACHINES,
    OWNER_EDGES,
    OWNER_WRITES,
)
from blogwriter.jobs import JobsService, get_jobs_service
from blogwriter.notify.alerts import emit_alert
from blogwriter.notify.payload import NotificationPayload
from blogwriter.services.status import SystemStatus, system_status

__all__ = [
    "TEST_PUSH",
    "AlertCheckIn",
    "AlertCheckView",
    "MachineView",
    "PushResultView",
    "get_router",
    "machine_views",
]

TEST_PUSH: Final = NotificationPayload(
    title="Test notification from Blog Writer", url="/settings/system", tag="test-alert"
)

JobsServiceDep = Annotated[JobsService, Depends(get_jobs_service)]


class AlertCheckIn(ApiModel):
    push: bool = False


class PushResultView(ApiModel):
    delivered: int
    failed: int
    expired: int


class AlertCheckView(ApiModel):
    alert: Literal["sent"] = "sent"
    push: PushResultView | None = None


async def read_status(request: Request, services: ServicesDep) -> SystemStatus:
    return await system_status(request.app, services)


async def exit_safe_mode(
    request: Request, jobs: JobsServiceDep, services: ServicesDep
) -> SystemStatus:
    """Leave safe mode: clear the boot log, start the runner and the scheduler."""
    try:
        with problems():
            await jobs.exit_safe_mode()
    except TimeoutError:
        raise ProblemError(503, code="store_unavailable", headers={"Retry-After": "2"}) from None
    return await system_status(request.app, services)


async def send_test_alert(body: AlertCheckIn, notifier: NotifierDep) -> AlertCheckView:
    """Raise the ``test`` out-of-band alert and, with ``push``, a test notification."""
    emit_alert("test", current_request_id())
    if not body.push:
        return AlertCheckView()
    result = await notifier.notify(TEST_PUSH)
    return AlertCheckView(
        push=PushResultView(
            delivered=result.delivered, failed=result.failed, expired=result.expired
        )
    )


class MachineView(ApiModel):
    """One status machine (contract section 2) and the owner's part in it."""

    initial: str
    states: list[str]
    edges: dict[str, list[str]]
    """Every edge: ``{state: [targets]}`` (sorted; a terminal state has none)."""
    owner_edges: dict[str, list[str]]
    """The edges the owner asks for with a request (a subset of ``edges``, same shape). Every
    other edge is system-only: jobs, schedules, a publish attempt's answer, a side effect."""
    owner_writes: list[str]
    """The states in which the owner may edit the document's content (the who-writes rule;
    for articles ``ARTICLE_WRITER``'s owner states). Empty: the owner never edits it in place."""
    writers: dict[str, str] | None = None
    """Who writes the content in each state (``owner``, ``pipeline``, ``publish``, ``none``),
    for machines with a who-writes table (``article``)."""


def machine_views() -> dict[str, MachineView]:
    """Every machine as :class:`MachineView` (``domain.states``)."""
    views: dict[str, MachineView] = {}
    for name, machine in MACHINES.items():
        edges = machine.as_table()
        owner = OWNER_EDGES.get(name, {})
        writes = OWNER_WRITES.get(name, frozenset[str]())
        views[name] = MachineView(
            initial=str(machine.initial),
            states=list(edges),
            edges=edges,
            owner_edges={state: sorted(owner.get(state, frozenset[str]())) for state in edges},
            owner_writes=[state for state in edges if state in writes],
            writers=(
                {status.value: writer for status, writer in ARTICLE_WRITER.items()}
                if name == ARTICLE_MACHINE.name
                else None
            ),
        )
    return views


async def machines() -> dict[str, MachineView]:
    """Every status machine with the owner's part in it (the web enables buttons from it and
    keeps the editor read-only outside the owner's states)."""
    return machine_views()


def get_router() -> APIRouter:
    """The system router, with the job, schedule and push routes included."""
    router = APIRouter(tags=["system"])
    router.add_api_route(
        "/api/system/status",
        read_status,
        methods=["GET"],
        response_model=SystemStatus,
        summary="Safe mode, runner, brake, services and expiries",
    )
    router.add_api_route(
        "/api/system/safe-mode/exit",
        exit_safe_mode,
        methods=["POST"],
        response_model=SystemStatus,
        summary="Leave safe mode (start the runner and the scheduler)",
    )
    router.add_api_route(
        "/api/system/test-alert",
        send_test_alert,
        methods=["POST"],
        status_code=202,
        response_model=AlertCheckView,
        summary="Send a test alert (and optionally a test push)",
    )
    router.add_api_route(
        "/api/meta/machines",
        machines,
        methods=["GET"],
        response_model=dict[str, MachineView],
        summary="Every status machine: its edges, the owner's edges and the owner's states",
    )
    jobs_routes.add_routes(router)
    schedule_routes.add_routes(router)
    push_routes.add_routes(router)
    return router
