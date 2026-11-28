"""The integrator's lifecycles (contract I-1): what each puts on ``app.state`` and takes back.

Plan: "Reliability model" (the runner starts only after every service exists; crash-only) and
section 1.1 (routes reach services through ``app.state.services``, job steps through
``current_services()``). The last test runs the whole order on a real app and a real runner.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from fastapi import FastAPI
from pydantic import JsonValue, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import AISettings
from blogwriter.ai.ledger import MemoryLedger
from blogwriter.ai.router import AIRouter
from blogwriter.ai.types import Completion, ModelSpec, ProviderName, Request, StreamEvent
from blogwriter.core.app import create_app
from blogwriter.core.crypto import TokenVault, VaultError, VaultSettings
from blogwriter.core.egress import SafeHttpClient
from blogwriter.core.testing import make_settings
from blogwriter.db import Doc, MemoryStore, StoreUnavailable
from blogwriter.domain.jobtypes import SIGNALS_CRAWL
from blogwriter.domain.ports import (
    ScheduleEntry,
    ServiceUnavailable,
    StateServices,
    current_services,
)
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import FakeRenderService, ScriptedModelRunner
from blogwriter.jobs import (
    JobRegistry,
    JobsLifecycle,
    JobsService,
    Step,
    StepContext,
    SystemClock,
    every,
)
from blogwriter.jobs.testing import DelegatingStore, FakeClock, eventually, fast_settings
from blogwriter.notify.payload import NotificationPayload
from blogwriter.notify.settings import NotifySettings
from blogwriter.render import Renderer
from blogwriter.services import (
    CoreServicesLifecycle,
    NullNotifier,
    PushNotifier,
    RenderAdapterLifecycle,
    RenderServiceAdapter,
    RouterEmbedder,
    ScheduleSeeder,
    SchedulesLifecycle,
    ServicesLifecycle,
    ServiceView,
)
from blogwriter.services.status import STATUS_PROBES_ATTR
from blogwriter.services.testing import RecordingPush, vapid_private_key_b64

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class ArgsOnlyAISettings(AISettings):
    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class IdleProvider:
    """A provider that is never called (the routing table only needs one per name)."""

    def __init__(self, name: ProviderName) -> None:
        self._name: ProviderName = name

    @property
    def name(self) -> ProviderName:
        return self._name

    async def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        raise AssertionError("no model call expected")
        yield  # pragma: no cover

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        raise AssertionError("no model call expected")

    async def aclose(self) -> None:
        return None


def ai_router() -> AIRouter:
    return AIRouter.from_settings(
        ArgsOnlyAISettings(),
        {"openai": IdleProvider("openai"), "anthropic": IdleProvider("anthropic")},
        ledger=MemoryLedger(),
    )


def push_settings() -> NotifySettings:
    return NotifySettings(
        vapid_private_key=SecretStr(vapid_private_key_b64()),
        vapid_subject="mailto:owner@example.com",
    )


def vault_settings() -> VaultSettings:
    key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    return VaultSettings(keys=SecretStr(f"k1:{key}"))


def bare_app() -> FastAPI:
    app = create_app(make_settings())
    app.state.store = MemoryStore()
    return app


# --------------------------------------------------------------------------------------------
# CoreServicesLifecycle
# --------------------------------------------------------------------------------------------


async def test_core_services_default_to_off_when_nothing_is_configured() -> None:
    app = bare_app()
    lifecycle = CoreServicesLifecycle()
    await lifecycle.startup(app)
    assert isinstance(app.state.clock, SystemClock)
    assert isinstance(app.state.egress, SafeHttpClient)
    assert app.state.vault is None
    assert isinstance(app.state.notifier, NullNotifier)
    assert app.state.push is None
    assert app.state.embedder is None
    await lifecycle.shutdown(app)
    for name in ("clock", "egress", "notifier"):
        assert getattr(app.state, name) is None


async def test_core_services_build_the_vault_push_and_embedder_when_configured() -> None:
    app = bare_app()
    router = ai_router()
    app.state.ai = router
    push = RecordingPush()
    lifecycle = CoreServicesLifecycle(
        vault_settings=vault_settings(), notify_settings=push_settings(), webpush=push
    )
    await lifecycle.startup(app)
    assert isinstance(app.state.vault, TokenVault)
    assert isinstance(app.state.notifier, PushNotifier)
    assert app.state.push is app.state.notifier
    assert isinstance(app.state.embedder, RouterEmbedder)
    assert app.state.embedder.dimensions == 1024
    result = await app.state.notifier.notify(
        NotificationPayload(title="Test", url="/settings/system", tag="test")
    )
    assert result.delivered == 0  # no device subscribed yet
    await lifecycle.shutdown(app)
    assert app.state.push is None
    assert app.state.embedder is None
    assert app.state.ai is router  # not ours to clear


async def test_a_misconfigured_vault_stops_the_boot() -> None:
    lifecycle = CoreServicesLifecycle(vault_settings=VaultSettings(keys=SecretStr("k1:short")))
    with pytest.raises(VaultError) as caught:
        await lifecycle.startup(bare_app())
    assert caught.value.reason == "misconfigured"


async def test_push_needs_the_store() -> None:
    app = create_app(make_settings())
    lifecycle = CoreServicesLifecycle(notify_settings=push_settings(), webpush=RecordingPush())
    with pytest.raises(RuntimeError, match=r"app\.state\.store"):
        await lifecycle.startup(app)


async def test_a_model_runner_that_is_not_the_router_serves_no_embeddings() -> None:
    app = bare_app()
    app.state.ai = ScriptedModelRunner()
    lifecycle = CoreServicesLifecycle()
    await lifecycle.startup(app)
    assert app.state.embedder is None


async def test_injected_services_are_kept_and_left_alone_on_shutdown() -> None:
    app = bare_app()
    clock = FakeClock(T0)
    notifier = NullNotifier()
    app.state.clock = clock
    app.state.notifier = notifier
    lifecycle = CoreServicesLifecycle(notify_settings=push_settings(), webpush=RecordingPush())
    await lifecycle.startup(app)
    assert app.state.clock is clock
    assert app.state.notifier is notifier
    assert getattr(app.state, "push", None) is None
    await lifecycle.shutdown(app)
    assert app.state.clock is clock
    assert app.state.notifier is notifier


# --------------------------------------------------------------------------------------------
# RenderAdapterLifecycle
# --------------------------------------------------------------------------------------------


async def test_the_render_adapter_wraps_the_renderer_and_hands_it_back() -> None:
    app = bare_app()
    renderer = Renderer()
    app.state.render = renderer
    lifecycle = RenderAdapterLifecycle()
    await lifecycle.startup(app)
    adapter = app.state.render
    assert isinstance(adapter, RenderServiceAdapter)
    assert adapter.renderer is renderer
    assert StateServices(app).render is adapter
    await lifecycle.shutdown(app)
    assert app.state.render is renderer


async def test_no_renderer_means_render_is_not_configured() -> None:
    app = bare_app()
    lifecycle = RenderAdapterLifecycle()
    await lifecycle.startup(app)
    assert app.state.render is None
    fake = FakeRenderService()
    app.state.render = fake
    await RenderAdapterLifecycle().startup(app)
    assert app.state.render is fake  # an injected service is kept


# --------------------------------------------------------------------------------------------
# ServicesLifecycle
# --------------------------------------------------------------------------------------------


async def test_services_are_installed_for_routes_and_steps_then_uninstalled() -> None:
    app = bare_app()

    async def portfolio(app: FastAPI) -> ServiceView:
        return ServiceView(state="on")

    lifecycle = ServicesLifecycle(status_probes={"portfolio": portfolio})
    await lifecycle.startup(app)
    services = app.state.services
    assert isinstance(services, StateServices)
    assert current_services() is services
    assert services.store is app.state.store
    assert getattr(app.state, STATUS_PROBES_ATTR) == {"portfolio": portfolio}
    await lifecycle.shutdown(app)
    assert app.state.services is None
    with pytest.raises(ServiceUnavailable):
        current_services()


def test_unknown_status_probes_are_refused() -> None:
    async def probe(app: FastAPI) -> ServiceView:
        return ServiceView(state="on")

    with pytest.raises(ValueError, match="unknown status services"):
        ServicesLifecycle(status_probes={"github": probe})


# --------------------------------------------------------------------------------------------
# SchedulesLifecycle
# --------------------------------------------------------------------------------------------


def crawl_entries(cadence: CadenceSettings) -> Sequence[ScheduleEntry]:
    return [
        ScheduleEntry(
            id="sched-signals-crawl",
            name="Crawl the feeds",
            job_type=SIGNALS_CRAWL,
            rule=every(cadence.crawl_interval_hours * 3600),
            timezone=cadence.timezone,
            max_lateness=timedelta(hours=6),
        )
    ]


def crawl_registry() -> JobRegistry:
    async def crawl(ctx: StepContext) -> JsonValue:
        return {"crawled": 0}

    jobs = JobRegistry()
    jobs.register(SIGNALS_CRAWL, [Step("crawl", crawl)])
    return jobs


@asynccontextmanager
async def running(app: FastAPI) -> AsyncGenerator[FastAPI]:
    async with app.router.lifespan_context(app):
        yield app


async def test_schedules_are_seeded_at_startup() -> None:
    lifecycles = [
        JobsLifecycle(crawl_registry(), settings=fast_settings(), stop_requested=None),
        SchedulesLifecycle([crawl_entries]),
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    app.state.store = MemoryStore()
    async with running(app):
        seeder = app.state.schedules
        assert isinstance(seeder, ScheduleSeeder)
        jobs = app.state.jobs
        assert isinstance(jobs, JobsService)
        items = await jobs.scheduler.list_items()
        assert [item.id for item in items] == ["sched-signals-crawl"]
    assert app.state.schedules is None


async def test_schedules_need_the_jobs_service() -> None:
    with pytest.raises(RuntimeError, match="JobsLifecycle"):
        await SchedulesLifecycle([crawl_entries]).startup(bare_app())


class SettingsDown(DelegatingStore):
    """Reads of the ``settings`` container fail until ``down`` is cleared."""

    down = True

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        if self.down and container == "settings" and id == "cadence":
            raise StoreUnavailable("down")
        return await self.inner.read(container, id, partition_key=partition_key)


async def test_a_failed_first_reseed_retries_in_the_background() -> None:
    store = SettingsDown(MemoryStore())
    schedules = SchedulesLifecycle([crawl_entries], retry_seconds=0.01, retry_max_seconds=0.02)
    lifecycles = [
        JobsLifecycle(crawl_registry(), settings=fast_settings(), stop_requested=None),
        schedules,
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    app.state.store = store
    async with running(app):
        jobs = app.state.jobs
        assert isinstance(jobs, JobsService)
        assert schedules.retrying
        assert await jobs.scheduler.list_items() == []
        store.down = False

        async def seeded() -> bool:
            return len(await jobs.scheduler.list_items()) == 1

        await eventually(seeded)
        await eventually(lambda: not schedules.retrying)


async def test_the_scheduler_starts_after_the_first_reseed_at_boot() -> None:
    lifecycles = [
        JobsLifecycle(
            crawl_registry(), settings=fast_settings(), stop_requested=None, hold_scheduler=True
        ),
        SchedulesLifecycle([crawl_entries]),
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    app.state.store = MemoryStore()
    async with running(app):
        jobs = app.state.jobs
        assert isinstance(jobs, JobsService)
        assert not jobs.scheduler_held
        assert jobs.scheduler.running
        assert [item.id for item in await jobs.scheduler.list_items()] == ["sched-signals-crawl"]


async def test_a_failed_first_reseed_keeps_the_scheduler_held_until_a_retry_lands() -> None:
    """A schedule item of the previous cadence must not fire while the boot's reseed fails."""
    store = SettingsDown(MemoryStore())
    schedules = SchedulesLifecycle([crawl_entries], retry_seconds=0.01, retry_max_seconds=0.02)
    lifecycles = [
        JobsLifecycle(
            crawl_registry(), settings=fast_settings(), stop_requested=None, hold_scheduler=True
        ),
        schedules,
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    app.state.store = store
    async with running(app):
        jobs = app.state.jobs
        assert isinstance(jobs, JobsService)
        assert jobs.runner.claiming  # jobs still run
        assert schedules.retrying
        assert not jobs.scheduler.running
        await asyncio.sleep(0.05)
        assert not jobs.scheduler.running
        store.down = False
        await eventually(lambda: jobs.scheduler.running)
        assert not schedules.retrying


# --------------------------------------------------------------------------------------------
# The whole order
# --------------------------------------------------------------------------------------------


async def test_the_contract_order_gives_job_steps_every_service() -> None:
    seen: dict[str, object] = {}
    ran = asyncio.Event()

    async def check(ctx: StepContext) -> JsonValue:
        services = current_services()
        seen["notifier"] = services.notifier
        seen["clock"] = services.clock
        seen["egress"] = services.egress
        seen["render"] = services.render
        seen["schedules"] = services.schedules
        ran.set()
        return {"ok": True}

    registry = crawl_registry()
    registry.register("t.check", [Step("check", check)])
    lifecycles = [
        CoreServicesLifecycle(),
        RenderAdapterLifecycle(),
        ServicesLifecycle(),
        JobsLifecycle(registry, settings=fast_settings(), stop_requested=None),
        SchedulesLifecycle([crawl_entries]),
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    app.state.store = MemoryStore()
    async with running(app):
        jobs = app.state.jobs
        assert isinstance(jobs, JobsService)
        await jobs.enqueue("t.check", {})
        await asyncio.wait_for(ran.wait(), timeout=5)
    assert isinstance(seen["notifier"], NullNotifier)
    assert isinstance(seen["clock"], SystemClock)
    assert isinstance(seen["egress"], SafeHttpClient)
    assert seen["render"] is None
    assert isinstance(seen["schedules"], ScheduleSeeder)
    with pytest.raises(ServiceUnavailable):
        current_services()
