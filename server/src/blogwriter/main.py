"""Production wiring: the one module that composes every package into the running app.

Run it with::

    uvicorn blogwriter.main:app --factory --no-access-log --no-server-header

Composition (contract section 5, I-1). Lifecycles start in this order; shutdown is the reverse,
so the job runner stops (and releases its running jobs) before any service it uses closes:

1. ``db``: opens the Cosmos store (``BW_COSMOS_*``) and verifies every container exists.
2. :class:`AILifecycle`: the router, built only when ``BW_AI_*`` endpoints are configured. Its
   ledger writes to the ``usage`` container so the spend brake survives restarts. With
   ``BW_AI_IMAGE_BASE_URL`` it also serves ``gpt-image-2`` from that separate resource (same
   Entra token provider).
3. ``CoreServicesLifecycle``: ``clock``, ``egress``, ``vault``, ``notifier`` (+ ``push``) and the
   ``embedder`` (served when the AI router has a ``Task.EMBED`` route).
4. Package runtime services: storage (``blobs``), render (the renderer, then the
   ``RenderService`` adapter on ``render``), research (``fetcher``), stories (``classifier``),
   gates (``slop``) and the LinkedIn transport (client, settings and its startup sweep).
5. ``ServicesLifecycle``: ``app.state.services = StateServices(app)`` and ``install_services``
   (job steps), plus the System page's status probes.
6. ``JobsLifecycle``: records the boot, then starts the runner with :func:`build_registry`
   (every package's job types). Every service is set by now, so a missing one means "not
   configured". A quarantined job raises an out-of-band alert. The scheduler is held.
7. ``SchedulesLifecycle``: ``app.state.schedules`` and a first reseed of every package's
   :data:`SCHEDULE_PROVIDERS` entries (it needs ``app.state.jobs.scheduler``); once a reseed
   lands it releases the scheduler, so no item of an earlier cadence fires at boot.

Routers: core's ``/api/health`` and ``/api/me``, the system router (jobs, schedules, push,
status, meta), the render preview (``/api/render/mermaid``), the SSE stream (``/api/events``)
and every package router (:func:`package_routers`). Packages never import each other's
internals; this module is the only place they meet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from fastapi import APIRouter, FastAPI

from blogwriter import (
    analytics,
    articles,
    backups,
    db,
    engagement,
    gates,
    illustrations,
    jobs,
    kb,
    pipeline,
    planner,
    research,
    signals,
    storage,
    stories,
    voice,
)
from blogwriter.ai import (
    AIRouter,
    AISettings,
    UsageRecord,
    create_image_provider,
    create_providers,
)
from blogwriter.ai.providers.auth import entra_token_provider
from blogwriter.api import render as render_api
from blogwriter.api import system as system_api
from blogwriter.core.app import Lifecycle, create_app
from blogwriter.core.logging import describe_exception
from blogwriter.core.settings import CoreSettings
from blogwriter.db import Filter, Store
from blogwriter.domain.ports import ScheduleProvider, current_services
from blogwriter.jobs import JobRegistry, JobsLifecycle, QuarantineNotice
from blogwriter.linkedin import posts as linkedin_posts
from blogwriter.linkedin import transport as linkedin_transport
from blogwriter.notify import emit_alert
from blogwriter.publish import jobs as publish_jobs
from blogwriter.publish import routes as publish_routes
from blogwriter.publish.jobs import PublishRuntime
from blogwriter.publish.recovery import PUBLISH_JOB_TYPES, after_quarantine
from blogwriter.publish.settings import PublishSettings, publish_settings
from blogwriter.render import RenderLifecycle
from blogwriter.services import (
    CoreServicesLifecycle,
    RenderAdapterLifecycle,
    SchedulesLifecycle,
    ServicesLifecycle,
    ServiceView,
)

__all__ = [
    "SCHEDULE_PROVIDERS",
    "AILifecycle",
    "CosmosLedgerSink",
    "app",
    "build_app",
    "build_lifecycles",
    "build_registry",
    "package_routers",
    "portfolio_status",
    "quiet_third_party_loggers",
]

log = logging.getLogger(__name__)

#: Third-party loggers that log URLs, request options or prompts below WARNING.
QUIET_LOGGERS: Final = (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity",
    "openai",
    "anthropic",
    "httpx",
    "httpx2",
    "httpcore",
)

SCHEDULE_PROVIDERS: Final[tuple[ScheduleProvider, ...]] = (
    signals.schedule_entries,
    planner.schedule_entries,
    voice.schedule_entries,
    analytics.schedule_entries,
    linkedin_transport.schedule_entries,
    gates.schedule_entries,
    backups.schedule_entries,
    engagement.schedule_entries,
    research.schedule_entries,
    stories.schedule_entries,
    pipeline.schedule_entries,
    publish_jobs.schedule_entries,
    linkedin_posts.schedule_entries,
    illustrations.schedule_entries,
)
"""Every package's ``schedule_entries(cadence)`` (contract section 8), seeded at startup and
again when kb saves the cadence. Packages without schedule items return none."""


def quiet_third_party_loggers() -> None:
    """Cap chatty SDK loggers at WARNING (they log URLs and, at DEBUG, full prompts)."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class CosmosLedgerSink:
    """:class:`blogwriter.ai.LedgerSink` over the ``usage`` container (partition key ``/month``).

    ``record`` upserts by ``record_id``, so a retried write never double counts.
    ``spend_between`` sums ``costUsd`` over ``start <= at < end`` across month partitions;
    ``at`` is fixed-width UTC, so string comparison is time comparison.
    """

    CONTAINER: Final = "usage"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def record(self, record: UsageRecord) -> None:
        await self._store.upsert(self.CONTAINER, dict(record.to_document()))

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        docs = await self._store.find(
            self.CONTAINER,
            [Filter("at", ">=", _iso(start)), Filter("at", "<", _iso(end))],
        )
        total = Decimal(0)
        for doc in docs:
            cost = doc.body.get("costUsd")
            if isinstance(cost, (int, float)):
                total += Decimal(str(cost))
        return total


def _iso(moment: datetime) -> str:
    """The ledger's fixed-width UTC timestamp format (matches ``UsageRecord.to_document``)."""
    if moment.tzinfo is None:
        raise ValueError("ledger range bounds must be timezone-aware")
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


class AILifecycle:
    """Builds :class:`AIRouter` on ``app.state.ai`` when ``BW_AI_*`` is configured.

    Unconfigured (local development, tests): ``app.state.ai`` is ``None`` and nothing calls a
    model. The router needs ``app.state.store`` for its ledger, so start it after ``db``.
    """

    def __init__(self, settings: AISettings | None = None) -> None:
        self._settings = settings
        self._credential: object | None = None

    async def startup(self, app: FastAPI) -> None:
        settings = self._settings if self._settings is not None else AISettings()
        if not settings.configured:
            app.state.ai = None
            log.info("ai_not_configured")
            return
        store: object = getattr(app.state, "store", None)
        if not isinstance(store, Store):
            raise RuntimeError("blogwriter.ai needs app.state.store; start blogwriter.db first")
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
        self._credential = credential
        token = entra_token_provider(credential)
        providers = create_providers(settings, token)
        images = create_image_provider(settings, token)
        app.state.ai = AIRouter.from_settings(
            settings, providers, ledger=CosmosLedgerSink(store), images=images
        )

    async def shutdown(self, app: FastAPI) -> None:
        router: object = getattr(app.state, "ai", None)
        if isinstance(router, AIRouter):
            await router.aclose()
        credential = self._credential
        self._credential = None
        if credential is not None:
            close = getattr(credential, "close", None)
            if close is not None:
                await close()


async def _on_quarantine(notice: QuarantineNotice) -> None:
    """Out-of-band alert (Azure Monitor emails the owner); ids only, never job content. A
    quarantined publish job ran none of its failure handling: its article is released to
    ``publish_failed`` (``publish.recovery.after_quarantine``) so the owner can retry it."""
    emit_alert("quarantined", notice.job_id)
    if notice.job_type not in PUBLISH_JOB_TYPES:
        return
    try:
        await after_quarantine(current_services(), notice.job_id)
    except Exception as exc:  # the alert went out; the owner's exits (publish-now) remain
        log.warning(
            "publish_release_failed", extra={"job_id": notice.job_id, **describe_exception(exc)}
        )


async def portfolio_status(app: FastAPI) -> ServiceView:
    """The System page's ``portfolio`` service: ``on`` when the publish job has everything a
    publish needs (``BW_PUBLISH_PORTFOLIO_*`` and ``BW_PUBLISH_SITE_ORIGIN``), else ``off``.
    The PAT's expiry is not recorded anywhere yet, so ``expiresAt`` stays unknown."""
    del app  # the settings come from the environment
    if publish_settings().portfolio_configured:
        return ServiceView(state="on")
    return ServiceView(state="off", reason="not_configured")


def package_routers(
    *, publish: Callable[[], PublishSettings] = publish_settings
) -> list[APIRouter]:
    """The system router, the SSE stream and every package router (full paths each).

    ``publish``: the publish routes' settings (default: the environment; the end-to-end test
    passes settings for its mocked portfolio).
    """
    return [
        system_api.get_router(),
        render_api.get_router(),
        jobs.get_router(),
        storage.get_router(),
        kb.get_router(),
        stories.get_router(),
        voice.get_router(),
        research.get_router(),
        signals.get_router(),
        articles.get_router(),
        gates.get_router(),
        linkedin_transport.get_router(),
        backups.get_router(),
        pipeline.get_router(),
        publish_routes.get_router(settings=publish),
        linkedin_posts.get_router(),
        planner.get_router(),
        engagement.get_router(),
        analytics.get_router(),
        illustrations.get_router(),
    ]


def build_registry(*, publish_runtime: PublishRuntime | None = None) -> JobRegistry:
    """One registry with every package's job types (contract section 7).

    ``publish_runtime``: what the publish steps use besides the services (default: the
    environment's settings and the GitHub client; the end-to-end test mocks the portfolio).
    """
    registry = JobRegistry()
    research.register_jobs(registry)
    signals.register_jobs(registry)
    stories.register_jobs(registry)
    voice.register_jobs(registry)
    gates.register_jobs(registry)
    linkedin_transport.register_jobs(registry)
    backups.register_jobs(registry)
    pipeline.register_jobs(registry)
    publish_jobs.register_jobs(registry, runtime=publish_runtime)
    linkedin_posts.register_jobs(registry)
    planner.register_jobs(registry)
    engagement.register_jobs(registry)
    analytics.register_jobs(registry)
    illustrations.register_jobs(registry)
    return registry


def build_lifecycles(
    registry: JobRegistry, providers: Sequence[ScheduleProvider] = SCHEDULE_PROVIDERS
) -> list[Lifecycle]:
    """The lifecycles in start order (see the module docs; the runner starts last but one)."""
    return [
        db,
        AILifecycle(),
        CoreServicesLifecycle(),
        storage.StorageLifecycle(),
        RenderLifecycle(),
        RenderAdapterLifecycle(),
        research.ResearchLifecycle(),
        stories.StoriesLifecycle(),
        gates.GatesLifecycle(),
        linkedin_transport.TransportLifecycle(),
        ServicesLifecycle(status_probes={"portfolio": portfolio_status}),
        JobsLifecycle(registry, on_quarantine=_on_quarantine, hold_scheduler=True),
        SchedulesLifecycle(providers),
    ]


def build_app(settings: CoreSettings | None = None) -> FastAPI:
    """The production app: core plus every package's routes, jobs, schedules and services."""
    quiet_third_party_loggers()
    return create_app(
        settings,
        routers=package_routers(),
        lifecycles=build_lifecycles(build_registry()),
    )


def app() -> FastAPI:
    """Zero-argument factory for ``uvicorn blogwriter.main:app --factory``."""
    return build_app()
