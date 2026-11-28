"""The integrator's lifecycles (contract I-1): core services, the render adapter, the services
registry and schedule seeding.

Start order in ``blogwriter.main`` (shutdown is the reverse)::

    db, AILifecycle,
    CoreServicesLifecycle(),          # clock, egress, vault, notifier (+ push), embedder
    StorageLifecycle, render, RenderAdapterLifecycle(), ResearchLifecycle, StoriesLifecycle,
    GatesLifecycle, linkedin TransportLifecycle,
    ServicesLifecycle(),              # app.state.services = StateServices(app); install_services
    JobsLifecycle(registry, on_quarantine=...),
    SchedulesLifecycle(providers),    # app.state.schedules; reseed

Each lifecycle keeps a value a test (or an earlier lifecycle) already put on ``app.state`` and
on shutdown clears only what it set itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from fastapi import FastAPI

from blogwriter.ai.router import AIRouter
from blogwriter.ai.types import Task
from blogwriter.core.crypto import TokenVault, VaultError, VaultSettings
from blogwriter.core.egress import SafeHttpClient
from blogwriter.core.logging import describe_exception
from blogwriter.db import Store
from blogwriter.domain.ports import (
    HttpGetter,
    Notifier,
    ScheduleProvider,
    Services,
    StateServices,
    install_services,
    uninstall_services,
)
from blogwriter.jobs import JobsService
from blogwriter.jobs.clock import Clock, SystemClock
from blogwriter.notify.settings import NotifySettings
from blogwriter.notify.webpush import WebPushFunction, WebPushNotifier
from blogwriter.render import Renderer
from blogwriter.services._logging import logger
from blogwriter.services.embedder import RouterEmbedder
from blogwriter.services.notifier import NullNotifier, PushNotifier
from blogwriter.services.push_repo import CosmosSubscriptionRepo
from blogwriter.services.render import RenderServiceAdapter
from blogwriter.services.schedules import (
    RESEED_RETRY_MAX_SECONDS,
    RESEED_RETRY_SECONDS,
    ScheduleSeeder,
)
from blogwriter.services.status import StatusProbe, probes_from, register_status_probe

__all__ = [
    "CoreServicesLifecycle",
    "RenderAdapterLifecycle",
    "SchedulesLifecycle",
    "ServicesLifecycle",
]

_SERVICE_ATTRIBUTES: Final = (
    "ai",
    "embedder",
    "blobs",
    "fetcher",
    "render",
    "vault",
    "slop",
    "classifier",
)


class CoreServicesLifecycle:
    """Sets ``app.state`` ``clock``, ``egress``, ``vault``, ``notifier``, ``push`` and
    ``embedder`` (contract I-1 step 3; start it after ``db`` and ``AILifecycle``).

    - ``clock``: :class:`~blogwriter.jobs.SystemClock`.
    - ``egress``: :class:`~blogwriter.core.egress.SafeHttpClient` (stateless, nothing to close).
    - ``vault``: :meth:`TokenVault.from_settings`, or ``None`` when ``BW_VAULT_KEYS`` is unset.
      A key ring that is set but invalid stops the boot.
    - ``notifier``: a :class:`PushNotifier` over web push with the Cosmos subscription repo when
      ``BW_NOTIFY_VAPID_*`` is set (``push`` is the same object, for the push routes), else a
      :class:`NullNotifier` (``push`` is ``None``). An invalid VAPID key stops the boot.
    - ``embedder``: a :class:`RouterEmbedder` when ``app.state.ai`` is an :class:`AIRouter`
      with a ``Task.EMBED`` route, else ``None`` (gate 4 then fails closed).
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        egress: HttpGetter | None = None,
        vault_settings: VaultSettings | None = None,
        notify_settings: NotifySettings | None = None,
        webpush: WebPushFunction | None = None,
    ) -> None:
        self._clock = clock
        self._egress = egress
        self._vault_settings = vault_settings
        self._notify_settings = notify_settings
        self._webpush = webpush
        self._set: dict[str, object] = {}

    def _put(self, app: FastAPI, name: str, value: object) -> None:
        if getattr(app.state, name, None) is not None:
            return  # injected (tests) or set by an earlier lifecycle: keep it
        setattr(app.state, name, value)
        if value is not None:
            self._set[name] = value

    def _vault(self) -> TokenVault | None:
        try:
            return TokenVault.from_settings(self._vault_settings)
        except VaultError as exc:
            if exc.reason != "not_configured":
                raise  # a ring that is set but wrong is a deployment error: stop the boot
            logger.info("vault_not_configured")
            return None

    def _notifier(self, app: FastAPI) -> tuple[Notifier, PushNotifier | None]:
        settings = self._notify_settings if self._notify_settings is not None else NotifySettings()
        if not settings.push_configured:
            logger.info("push_not_configured")
            return NullNotifier(), None
        store: object = getattr(app.state, "store", None)
        if not isinstance(store, Store):
            raise RuntimeError("push needs app.state.store; start blogwriter.db first")
        repo = CosmosSubscriptionRepo(store)
        sender = WebPushNotifier.from_settings(settings, repo, webpush=self._webpush)
        push = PushNotifier(sender, repo)
        logger.info("push_configured")
        return push, push

    @staticmethod
    def _embedder(app: FastAPI) -> RouterEmbedder | None:
        router: object = getattr(app.state, "ai", None)
        if isinstance(router, AIRouter) and Task.EMBED in router.routing:
            return RouterEmbedder(router)
        logger.info("embeddings_not_configured")
        return None

    async def startup(self, app: FastAPI) -> None:
        self._put(app, "clock", self._clock if self._clock is not None else SystemClock())
        self._put(app, "egress", self._egress if self._egress is not None else SafeHttpClient())
        if getattr(app.state, "vault", None) is None:
            self._put(app, "vault", self._vault())
        if getattr(app.state, "notifier", None) is None:
            notifier, push = self._notifier(app)
            self._put(app, "notifier", notifier)
            self._put(app, "push", push)
        if getattr(app.state, "embedder", None) is None:
            self._put(app, "embedder", self._embedder(app))

    async def shutdown(self, app: FastAPI) -> None:
        for name, value in self._set.items():
            if getattr(app.state, name, None) is value:
                setattr(app.state, name, None)
        self._set.clear()


class RenderAdapterLifecycle:
    """Puts the ``RenderService`` adapter on ``app.state.render`` (contract I-1 step 4).

    Start it right after the ``render`` module lifecycle, which leaves its
    :class:`~blogwriter.render.Renderer` on ``app.state.render``: this wraps it in a
    :class:`RenderServiceAdapter` (headshots read from ``app.state.blobs`` at call time) and on
    shutdown hands the renderer back, so the render lifecycle can uninstall it. Without a
    renderer, ``app.state.render`` stays ``None`` (routes answer 503 ``render_unavailable``).
    """

    def __init__(self) -> None:
        self._renderer: Renderer | None = None
        self._adapter: RenderServiceAdapter | None = None

    async def startup(self, app: FastAPI) -> None:
        current: object = getattr(app.state, "render", None)
        if isinstance(current, Renderer):
            adapter = RenderServiceAdapter(current, blobs=lambda: StateServices(app).blobs)
            self._renderer, self._adapter = current, adapter
            app.state.render = adapter
            return
        if current is None:
            app.state.render = None
            logger.warning("render_not_configured")

    async def shutdown(self, app: FastAPI) -> None:
        if self._adapter is not None and getattr(app.state, "render", None) is self._adapter:
            app.state.render = self._renderer
        self._renderer = None
        self._adapter = None


class ServicesLifecycle:
    """``app.state.services = StateServices(app)`` and :func:`install_services` (contract I-1
    step 5); shutdown uninstalls. ``status_probes`` report integrations on the System page
    (see :mod:`blogwriter.services.status`)."""

    def __init__(self, *, status_probes: Mapping[str, StatusProbe] | None = None) -> None:
        self._probes = probes_from(status_probes)
        self._installed: Services | None = None

    async def startup(self, app: FastAPI) -> None:
        services = StateServices(app)
        app.state.services = services
        install_services(services)
        self._installed = services
        for name, probe in self._probes.items():
            register_status_probe(app, name, probe)
        configured = [name for name in _SERVICE_ATTRIBUTES if getattr(app.state, name, None)]
        missing = [name for name in _SERVICE_ATTRIBUTES if name not in configured]
        logger.info("services_installed", extra={"configured": configured, "missing": missing})

    async def shutdown(self, app: FastAPI) -> None:
        uninstall_services()
        if getattr(app.state, "services", None) is self._installed:
            app.state.services = None
        self._installed = None


class SchedulesLifecycle:
    """``app.state.schedules = ScheduleSeeder(...)`` and a first reseed (contract I-1 step 7).

    Needs ``app.state.jobs`` (the :class:`~blogwriter.jobs.JobsService`; start it after
    ``JobsLifecycle``). A first reseed that fails (the store is down, a provider bug) does not
    stop the boot: the items of the previous boot stay, ``schedule_reseed_failed`` is logged and
    the seeder retries in the background (5 s doubling to 5 min) until it succeeds; it does the
    same for any later reseed (a cadence save). Every reseed that lands releases the jobs
    service's scheduler (``JobsLifecycle(hold_scheduler=True)`` keeps it off until then), so a
    schedule item of an earlier cadence never fires at boot.
    """

    def __init__(
        self,
        providers: Sequence[ScheduleProvider],
        *,
        retry_seconds: float = RESEED_RETRY_SECONDS,
        retry_max_seconds: float = RESEED_RETRY_MAX_SECONDS,
    ) -> None:
        if retry_seconds <= 0 or retry_max_seconds < retry_seconds:
            raise ValueError("retry delays must be positive and max >= initial")
        self._providers = tuple(providers)
        self._retry_seconds = retry_seconds
        self._retry_max = retry_max_seconds
        self._seeder: ScheduleSeeder | None = None

    async def startup(self, app: FastAPI) -> None:
        jobs: object = getattr(app.state, "jobs", None)
        if not isinstance(jobs, JobsService):
            raise RuntimeError("schedules need app.state.jobs; start JobsLifecycle first")
        seeder = ScheduleSeeder(
            jobs.store,
            jobs.scheduler,
            self._providers,
            clock=jobs.clock,
            retry_seconds=self._retry_seconds,
            retry_max_seconds=self._retry_max,
            on_seeded=jobs.release_scheduler,
        )
        self._seeder = seeder
        app.state.schedules = seeder
        try:
            await seeder.reseed()
        except Exception as exc:  # the seeder retries it in the background
            logger.error("schedule_reseed_failed", extra=describe_exception(exc))

    @property
    def retrying(self) -> bool:
        return self._seeder is not None and self._seeder.retrying

    async def shutdown(self, app: FastAPI) -> None:
        seeder = self._seeder
        if seeder is not None:
            await seeder.close()
        if getattr(app.state, "schedules", None) is seeder:
            app.state.schedules = None
        self._seeder = None
