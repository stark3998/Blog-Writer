"""``GET /api/system/status``: the System page's view of the running app (contract I-2).

:func:`system_status` reads ``app.state`` (never a secret, never content) and never fails as a
whole: a part that cannot be read reports ``unknown`` or ``degraded`` with a reason code.

- ``safeMode``, ``boot``, ``runner``: the jobs service (crash-loop guard, claim loop, scheduler).
- ``brake``: today's AI spend against the brake threshold (``null`` without models). A ledger
  that cannot be read reports ``engaged: true``, like the brake itself (fail closed).
- ``services``: one :class:`ServiceView` per integration, ``state`` ``on`` / ``off`` (not
  configured) / ``degraded`` (configured, not working: Blob storage unreachable, Chromium
  missing, Web IQ circuit open, a LinkedIn token to reconnect, push without a subscribed device)
  / ``unknown``, plus a ``reason`` code and, for credentials that expire, ``expiresAt``.
- ``schedules``: whether the schedule items match the cadence (:class:`SchedulesView`): the
  first reseed at boot has not landed yet, or a reseed failed and is being retried (the items
  of an earlier cadence may fire meanwhile; the scheduler starts only after the first reseed).
- ``linkedin``: ``off`` / ``not_configured`` without the app registration
  (``app.state.linkedin_settings.configured``), else the connection's state.
- ``expiry``: the LinkedIn token's day (the plan's reconnect banner is at day 53) and the
  portfolio PAT's expiry when a probe reports it.

Integrations owned by packages this module cannot see (the portfolio PAT lives with the publish
package) are reported by **status probes**: ``register_status_probe(app, "portfolio", probe)``
(or ``ServicesLifecycle(status_probes=...)``). A probe overrides the built-in view of its name;
without one, ``portfolio`` is ``unknown``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Final, Literal, cast, get_args

from fastapi import FastAPI

from blogwriter.ai.ledger import BudgetBrake
from blogwriter.core.logging import describe_exception
from blogwriter.core.settings import CoreSettings
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.ports import Services, ServiceUnavailable, StateServices
from blogwriter.domain.settings import LinkedInConnection
from blogwriter.domain.states import ConnectionStatus
from blogwriter.jobs import JobsService
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock
from blogwriter.services._logging import logger
from blogwriter.services.notifier import PushNotifier
from blogwriter.services.schedules import ScheduleSeeder

__all__ = [
    "SERVICE_NAMES",
    "STATUS_PROBES_ATTR",
    "BootView",
    "BrakeView",
    "ExpiryView",
    "RunnerView",
    "SchedulesState",
    "SchedulesView",
    "ServiceState",
    "ServiceView",
    "ServicesView",
    "StatusProbe",
    "SystemStatus",
    "register_status_probe",
    "system_status",
]

type ServiceState = Literal["on", "off", "degraded", "unknown"]
type ServiceName = Literal[
    "ai",
    "storage",
    "render",
    "fetcher",
    "webiq",
    "vault",
    "push",
    "portfolio",
    "embeddings",
    "linkedin",
]
SERVICE_NAMES: Final[tuple[str, ...]] = get_args(ServiceName.__value__)
STATUS_PROBES_ATTR: Final = "status_probes"
"""``app.state`` attribute holding the registered probes (``dict[str, StatusProbe]``)."""


class ServiceView(ApiModel):
    state: ServiceState
    reason: str | None = None
    """A code: ``not_configured``, ``chromium_missing``, ``circuit_open``, ``needs_reconnect``,
    ``disconnected``, ``no_subscriptions``, ``unreadable``, ``no_probe``..."""
    expires_at: str | None = None
    """When the credential behind it expires (LinkedIn token, portfolio PAT), if known."""


type StatusProbe = Callable[[FastAPI], Awaitable[ServiceView]]
"""Reports one integration's :class:`ServiceView` (registered by the integrator)."""


class BootView(ApiModel):
    boots: int
    """Boots inside the crash-loop window, this one included."""
    safe_mode: bool
    reason: str | None = None


class RunnerView(ApiModel):
    running: bool
    """The claim loop accepts jobs."""
    claimed: int
    """Jobs this replica holds now."""
    scheduler_running: bool


class BrakeView(ApiModel):
    spend_today_usd: float | None
    threshold_usd: float | None
    engaged: bool


class ServicesView(ApiModel):
    ai: ServiceView
    storage: ServiceView
    render: ServiceView
    fetcher: ServiceView
    webiq: ServiceView
    vault: ServiceView
    push: ServiceView
    portfolio: ServiceView
    embeddings: ServiceView
    linkedin: ServiceView


class ExpiryView(ApiModel):
    linkedin_token_day: int | None = None
    """Days since the LinkedIn token was issued (``null`` when not connected)."""
    pat_expires_at: str | None = None


type SchedulesState = Literal["current", "out_of_date", "unknown"]


class SchedulesView(ApiModel):
    """Whether the schedule items match the cadence (contract I-4)."""

    state: SchedulesState
    reason: str | None = None
    """``not_seeded`` (the boot's first reseed has not landed), ``reseed_failed`` (retrying in
    the background), ``partial`` (a package's schedule could not be read: its old items are
    kept) or ``not_wired``."""
    seeded_at: str | None = None
    """When the last reseed landed."""
    retrying: bool = False


class SystemStatus(ApiModel):
    safe_mode: bool
    boot: BootView | None
    runner: RunnerView
    brake: BrakeView | None
    services: ServicesView
    schedules: SchedulesView
    expiry: ExpiryView
    build_sha: str


_ON: Final = ServiceView(state="on")
_NOT_CONFIGURED: Final = ServiceView(state="off", reason="not_configured")


def register_status_probe(app: FastAPI, name: str, probe: StatusProbe) -> None:
    """Report service ``name`` (one of :data:`SERVICE_NAMES`) with ``probe``."""
    if name not in SERVICE_NAMES:
        raise ValueError(f"unknown status service {name!r}")
    probes = _probes(app)
    probes[name] = probe
    setattr(app.state, STATUS_PROBES_ATTR, probes)


def _probes(app: FastAPI) -> dict[str, StatusProbe]:
    raw: object = getattr(app.state, STATUS_PROBES_ATTR, None)
    if isinstance(raw, dict):
        return dict(cast(dict[str, StatusProbe], raw))
    return {}


def _configured(value: object) -> ServiceView:
    return _ON if value is not None else _NOT_CONFIGURED


def _storage_view(blobs: object) -> ServiceView:
    if blobs is None:
        return _NOT_CONFIGURED
    if getattr(blobs, "degraded", None) is True:
        return ServiceView(state="degraded", reason="unreachable")
    return _ON


def _schedules_view(services: Services) -> SchedulesView:
    seeder: object
    try:
        seeder = services.schedules
    except ServiceUnavailable:
        seeder = None
    if not isinstance(seeder, ScheduleSeeder):
        return SchedulesView(state="unknown", reason="not_wired")
    seeded_at = to_iso(seeder.seeded_at) if seeder.seeded_at is not None else None
    if seeder.retrying:
        return SchedulesView(
            state="out_of_date", reason="reseed_failed", seeded_at=seeded_at, retrying=True
        )
    report = seeder.last_report
    if report is None:
        return SchedulesView(state="out_of_date", reason="not_seeded", seeded_at=seeded_at)
    if not report.complete:
        return SchedulesView(state="out_of_date", reason="partial", seeded_at=seeded_at)
    return SchedulesView(state="current", seeded_at=seeded_at)


def _render_view(render: object) -> ServiceView:
    if render is None:
        return _NOT_CONFIGURED
    status: object = getattr(render, "status", None)
    available: object = getattr(status, "available", None)
    if available is False:
        code: object = getattr(status, "reason_code", None)
        return ServiceView(
            state="degraded", reason=code if isinstance(code, str) else "unavailable"
        )
    return _ON


def _webiq_view(fetcher: object) -> ServiceView:
    if fetcher is None:
        return _NOT_CONFIGURED
    state: object = getattr(fetcher, "state", None)
    if state == "on":
        return _ON
    if state == "off":
        return _NOT_CONFIGURED
    if state == "degraded":
        return ServiceView(state="degraded", reason="circuit_open")
    return ServiceView(state="unknown", reason="no_probe")


async def _push_view(app: FastAPI) -> ServiceView:
    push: object = getattr(app.state, "push", None)
    if not isinstance(push, PushNotifier):
        return _NOT_CONFIGURED
    try:
        active = await push.subscriptions.list_active()
    except Exception as exc:
        logger.warning("status_push_unreadable", extra=describe_exception(exc))
        return ServiceView(state="degraded", reason="unreadable")
    return _ON if active else ServiceView(state="degraded", reason="no_subscriptions")


async def _linkedin(services: Services, now: datetime) -> tuple[ServiceView, int | None]:
    try:
        connection, _ = await read_singleton(services.store, LinkedInConnection, now=to_iso(now))
    except Exception as exc:  # no store, store down, an unreadable document
        logger.warning("status_linkedin_unreadable", extra=describe_exception(exc))
        return ServiceView(state="unknown", reason="unreadable"), None
    match connection.status:
        case ConnectionStatus.CONNECTED:
            view = ServiceView(state="on", expires_at=connection.expires_at)
            return view, connection.token_day(now)
        case ConnectionStatus.NEEDS_RECONNECT:
            view = ServiceView(
                state="degraded", reason="needs_reconnect", expires_at=connection.expires_at
            )
            return view, connection.token_day(now)
        case _:
            return ServiceView(state="off", reason="disconnected"), None


async def _brake_view(services: Services) -> BrakeView | None:
    brake: object = getattr(services.ai, "brake", None)
    if not isinstance(brake, BudgetBrake):
        return None
    try:
        status = await brake.status()
    except Exception as exc:  # the brake fails closed on an unreadable ledger; so does this
        logger.warning("status_brake_unreadable", extra=describe_exception(exc))
        return BrakeView(spend_today_usd=None, threshold_usd=None, engaged=True)
    return BrakeView(
        spend_today_usd=float(status.spend_usd),
        threshold_usd=float(status.threshold_usd),
        engaged=status.engaged,
    )


def _jobs_parts(app: FastAPI) -> tuple[bool, BootView | None, RunnerView]:
    jobs: object = getattr(app.state, "jobs", None)
    if not isinstance(jobs, JobsService):
        return False, None, RunnerView(running=False, claimed=0, scheduler_running=False)
    report = jobs.boot
    boot = (
        BootView(boots=report.boots, safe_mode=report.safe_mode, reason=report.reason)
        if report is not None
        else None
    )
    runner = RunnerView(
        running=jobs.runner.claiming,
        claimed=len(jobs.runner.active_job_ids),
        scheduler_running=jobs.scheduler.running,
    )
    return jobs.safe_mode, boot, runner


def _linkedin_unconfigured(app: FastAPI) -> bool:
    """``app.state.linkedin_settings`` (set by the LinkedIn transport lifecycle) says the app
    registration (``BW_LINKEDIN_CLIENT_ID`` / ``_SECRET``) is missing."""
    settings: object = getattr(app.state, "linkedin_settings", None)
    return getattr(settings, "configured", None) is False


def _clock(services: Services) -> Clock:
    try:
        return services.clock
    except ServiceUnavailable:
        return SYSTEM_CLOCK


async def _probe(app: FastAPI, name: str, probe: StatusProbe) -> ServiceView:
    try:
        return await probe(app)
    except Exception as exc:
        logger.warning("status_probe_failed", extra={"provider": name, **describe_exception(exc)})
        return ServiceView(state="unknown", reason="probe_failed")


def _services(app: FastAPI) -> Services:
    """``app.state.services`` (what routes and steps see), else a live view of ``app.state``."""
    installed: object = getattr(app.state, "services", None)
    if installed is not None:
        return cast(Services, installed)
    return StateServices(app)


async def system_status(app: FastAPI, services: Services | None = None) -> SystemStatus:
    """The status of the running app (see the module docs)."""
    services = services if services is not None else _services(app)
    now = _clock(services).now()
    safe_mode, boot, runner = _jobs_parts(app)
    linkedin, token_day = await _linkedin(services, now)
    if _linkedin_unconfigured(app):
        linkedin = _NOT_CONFIGURED  # no app registration: the connection cannot be used
    views: dict[str, ServiceView] = {
        "ai": _configured(services.ai),
        "storage": _storage_view(services.blobs),
        "render": _render_view(services.render),
        "fetcher": _configured(services.fetcher),
        "webiq": _webiq_view(services.fetcher),
        "vault": _configured(services.vault),
        "push": await _push_view(app),
        "portfolio": ServiceView(state="unknown", reason="no_probe"),
        "embeddings": _configured(services.embedder),
        "linkedin": linkedin,
    }
    for name, probe in _probes(app).items():
        if name in views:
            views[name] = await _probe(app, name, probe)
    settings: object = getattr(app.state, "settings", None)
    build_sha = settings.build_sha if isinstance(settings, CoreSettings) else "unknown"
    return SystemStatus(
        safe_mode=safe_mode,
        boot=boot,
        runner=runner,
        brake=await _brake_view(services),
        services=ServicesView.model_validate(views),
        schedules=_schedules_view(services),
        expiry=ExpiryView(
            linkedin_token_day=token_day, pat_expires_at=views["portfolio"].expires_at
        ),
        build_sha=build_sha,
    )


def probes_from(mapping: Mapping[str, StatusProbe] | None) -> dict[str, StatusProbe]:
    """Validated probes (unknown names refused) for :class:`ServicesLifecycle`."""
    probes = dict(mapping or {})
    unknown = sorted(name for name in probes if name not in SERVICE_NAMES)
    if unknown:
        raise ValueError(f"unknown status services: {', '.join(unknown)}")
    return probes
