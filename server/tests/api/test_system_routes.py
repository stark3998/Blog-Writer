"""System routes (contract I-2): status, safe mode, the test alert and the state machines.

Plan: "Reliability model" (crash-loop guard and safe mode; the owner exits it), "AI layer"
(the spend brake), "CI/CD and operations" (out-of-band alerts; the expiry watchlist on System:
LinkedIn token, PAT) and the web map (buttons enabled from ``/api/meta/machines``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, override

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import JsonValue
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import AISettings
from blogwriter.ai.ledger import MemoryLedger, UsageRecord
from blogwriter.ai.router import AIRouter
from blogwriter.ai.types import Completion, ModelSpec, ProviderName, Request, StreamEvent, Task
from blogwriter.ai.types import Usage as AIUsage
from blogwriter.api.system import TEST_PUSH, get_router
from blogwriter.articles import OWNER_TRANSITIONS
from blogwriter.core.logging import JsonFormatter
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import StoreUnavailable
from blogwriter.domain.docstore import save_singleton
from blogwriter.domain.jobtypes import SIGNALS_CRAWL
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings, LinkedInConnection
from blogwriter.domain.states import (
    ARTICLE_WRITER,
    MACHINES,
    OWNER_EDGES,
    OWNER_WRITES,
    ConnectionStatus,
)
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, make_app, make_services
from blogwriter.jobs import (
    Broker,
    JobRegistry,
    JobRunner,
    JobsService,
    Scheduler,
    Step,
    StepContext,
    every,
)
from blogwriter.jobs.bootguard import BootGuard
from blogwriter.jobs.scheduler import ScheduleItem
from blogwriter.jobs.testing import fast_settings
from blogwriter.linkedin.posts.routes import EDITABLE
from blogwriter.notify.settings import load_vapid_private_key
from blogwriter.notify.subscriptions import MemorySubscriptionRepo, PushSubscription
from blogwriter.notify.webpush import WebPushNotifier
from blogwriter.render import RenderStatus
from blogwriter.services.notifier import PushNotifier
from blogwriter.services.schedules import ScheduleSeeder
from blogwriter.services.status import ServiceView, register_status_probe
from blogwriter.services.testing import RecordingPush, browser_subscription, vapid_private_key_b64

SETTINGS = make_settings(build_sha="abc1234")
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
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


def ai_router(ledger: MemoryLedger) -> AIRouter:
    return AIRouter.from_settings(
        ArgsOnlyAISettings(),
        {"openai": IdleProvider("openai"), "anthropic": IdleProvider("anthropic")},
        ledger=ledger,
        now=lambda: T0,
    )


def jobs_service(services: FakeServices, *, max_boots: int = 5) -> JobsService:
    async def work(ctx: StepContext) -> JsonValue:
        return None

    registry = JobRegistry()
    registry.register("t.work", [Step("work", work)])
    settings = fast_settings(crash_loop_max_boots=max_boots)
    owner = "test-replica:boot"
    broker = Broker(queue_size=64, max_subscribers=4)
    runner = JobRunner(
        services.store, registry, settings=settings, owner=owner, clock=services.clock
    )
    scheduler = Scheduler(
        services.store, settings=settings, owner=owner, clock=services.clock, registry=registry
    )
    guard = BootGuard(
        services.store, max_boots=max_boots, window=timedelta(hours=1), clock=services.clock
    )
    return JobsService(
        store=services.store,
        registry=registry,
        settings=settings,
        clock=services.clock,
        owner=owner,
        broker=broker,
        runner=runner,
        scheduler=scheduler,
        boot_guard=guard,
    )


@asynccontextmanager
async def client(
    services: FakeServices, *, jobs: JobsService | None = None
) -> AsyncGenerator[tuple[httpx.AsyncClient, FastAPI]]:
    app = make_app(services, [get_router()], settings=SETTINGS)
    app.state.jobs = jobs
    async with asgi_client(app) as http:
        yield http, app


def services_of(body: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = body["services"]
    assert isinstance(raw, dict)
    return raw  # pyright: ignore[reportUnknownVariableType]


# --------------------------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------------------------


async def test_status_reports_every_part_with_the_fakes() -> None:
    services = make_services()
    async with client(services, jobs=jobs_service(services)) as (http, _app):
        response = await http.get("/api/system/status", headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert body["buildSha"] == "abc1234"
    assert body["safeMode"] is False
    assert body["boot"] is None
    assert body["runner"] == {"running": False, "claimed": 0, "schedulerRunning": False}
    assert body["brake"] is None  # the scripted runner has no brake
    assert body["expiry"] == {"linkedinTokenDay": None, "patExpiresAt": None}
    views = services_of(body)
    assert set(views) == {
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
    }
    for name in ("ai", "storage", "render", "fetcher", "vault", "embeddings"):
        assert views[name]["state"] == "on", name
    assert views["push"] == {"state": "off", "reason": "not_configured", "expiresAt": None}
    assert views["linkedin"]["state"] == "off"
    assert views["linkedin"]["reason"] == "disconnected"
    assert views["portfolio"] == {"state": "unknown", "reason": "no_probe", "expiresAt": None}
    assert views["webiq"]["state"] == "unknown"


async def test_unconfigured_services_are_off() -> None:
    services = make_services(
        ai=None, embedder=None, blobs=None, fetcher=None, render=None, vault=None
    )
    async with client(services) as (http, _app):
        body = (await http.get("/api/system/status", headers=READ)).json()
    views = services_of(body)
    for name in ("ai", "storage", "render", "fetcher", "webiq", "vault", "embeddings"):
        assert views[name] == {"state": "off", "reason": "not_configured", "expiresAt": None}
    assert body["runner"]["running"] is False


async def test_blob_storage_that_cannot_be_reached_is_degraded() -> None:
    reachable = make_services(blobs=SimpleNamespace(degraded=False))
    unreachable = make_services(blobs=SimpleNamespace(degraded=True))
    async with client(reachable) as (http, _app):
        up = services_of((await http.get("/api/system/status", headers=READ)).json())
    async with client(unreachable) as (http, _app):
        down = services_of((await http.get("/api/system/status", headers=READ)).json())
    assert up["storage"] == {"state": "on", "reason": None, "expiresAt": None}
    assert down["storage"] == {"state": "degraded", "reason": "unreachable", "expiresAt": None}


class FailingPut:
    """A scheduler whose ``put`` fails until ``down`` is cleared."""

    def __init__(self, inner: Scheduler) -> None:
        self.inner = inner
        self.down = True

    async def put(self, item_id: str, **fields: Any) -> ScheduleItem:
        if self.down:
            raise StoreUnavailable("throttled")
        return await self.inner.put(item_id, **fields)

    async def delete(self, item_id: str) -> None:
        await self.inner.delete(item_id)


def crawl_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    return [
        ScheduleEntry(
            id="sched-signals-crawl",
            name="Crawl the feeds",
            job_type=SIGNALS_CRAWL,
            rule=every(3600),
            timezone=cadence.timezone,
        )
    ]


async def test_schedules_report_whether_they_match_the_cadence() -> None:
    services = make_services()
    async with client(services) as (http, _app):
        unwired = (await http.get("/api/system/status", headers=READ)).json()["schedules"]
    assert unwired == {
        "state": "unknown",
        "reason": "not_wired",
        "seededAt": None,
        "retrying": False,
    }

    registry = JobRegistry()

    async def crawl(ctx: StepContext) -> JsonValue:
        return None

    registry.register(SIGNALS_CRAWL, [Step("crawl", crawl)])
    scheduler = Scheduler(
        services.store, settings=fast_settings(), clock=services.clock, registry=registry
    )
    backend = FailingPut(scheduler)
    seeder = ScheduleSeeder(
        services.store,
        backend,
        [crawl_entries],
        clock=services.clock,
        retry_seconds=60,
        retry_max_seconds=60,
    )
    services.schedules = seeder  # pyright: ignore[reportAttributeAccessIssue]
    async with client(services) as (http, _app):
        pending = (await http.get("/api/system/status", headers=READ)).json()["schedules"]
        with pytest.raises(StoreUnavailable):
            await seeder.reseed()
        failing = (await http.get("/api/system/status", headers=READ)).json()["schedules"]
        backend.down = False
        await seeder.run()
        await seeder.close()
        current = (await http.get("/api/system/status", headers=READ)).json()["schedules"]
    assert pending == {
        "state": "out_of_date",
        "reason": "not_seeded",
        "seededAt": None,
        "retrying": False,
    }
    assert failing["state"] == "out_of_date"
    assert failing["reason"] == "reseed_failed"
    assert failing["retrying"] is True
    assert current == {
        "state": "current",
        "reason": None,
        "seededAt": SAMPLE_NOW,
        "retrying": False,
    }


async def test_degraded_render_and_webiq_report_their_reason() -> None:
    missing = RenderStatus(False, "no chromium", "chromium_missing", None, None, "sha")
    services = make_services(
        render=SimpleNamespace(status=missing), fetcher=SimpleNamespace(state="degraded")
    )
    async with client(services) as (http, _app):
        views = services_of((await http.get("/api/system/status", headers=READ)).json())
    assert views["render"] == {"state": "degraded", "reason": "chromium_missing", "expiresAt": None}
    assert views["webiq"] == {"state": "degraded", "reason": "circuit_open", "expiresAt": None}


async def test_the_brake_shows_todays_spend_against_the_threshold() -> None:
    ledger = MemoryLedger()
    await ledger.record(
        UsageRecord(
            task=Task.DRAFT,
            model="claude-opus-5-5",
            deployment="claude-opus-5-5",
            provider="anthropic",
            usage=AIUsage(input=1000, output=100),
            cost_usd=Decimal("2.50"),
            at=T0,
            origin="owner",
            outcome="completed",
        )
    )
    router = ai_router(ledger)
    services = make_services(ai=router)
    async with client(services) as (http, _app):
        brake = (await http.get("/api/system/status", headers=READ)).json()["brake"]
    await router.aclose()
    assert brake == {"spendTodayUsd": 2.5, "thresholdUsd": 25.0, "engaged": False}


async def test_an_unreadable_ledger_reports_the_brake_engaged() -> None:
    class DownLedger(MemoryLedger):
        @override
        async def spend_between(self, start: datetime, end: datetime) -> Decimal:
            raise ConnectionError("down")

    router = ai_router(DownLedger())
    async with client(make_services(ai=router)) as (http, _app):
        brake = (await http.get("/api/system/status", headers=READ)).json()["brake"]
    await router.aclose()
    assert brake == {"spendTodayUsd": None, "thresholdUsd": None, "engaged": True}


async def test_push_is_on_only_with_a_subscribed_device() -> None:
    repo = MemorySubscriptionRepo()
    sender = WebPushNotifier(
        repo=repo,
        vapid_private_key=load_vapid_private_key(vapid_private_key_b64()),
        vapid_subject="mailto:owner@example.com",
        webpush=RecordingPush(),
    )
    services = make_services()
    async with client(services) as (http, app):
        app.state.push = PushNotifier(sender, repo)
        before = services_of((await http.get("/api/system/status", headers=READ)).json())
        await repo.upsert(PushSubscription.from_browser(browser_subscription(), now=T0))
        after = services_of((await http.get("/api/system/status", headers=READ)).json())
    assert before["push"] == {"state": "degraded", "reason": "no_subscriptions", "expiresAt": None}
    assert after["push"]["state"] == "on"


async def test_linkedin_reports_the_token_day_and_its_expiry() -> None:
    services = make_services()
    services.clock.advance(timedelta(days=53))
    connection = LinkedInConnection.default(SAMPLE_NOW).model_copy(
        update={
            "status": ConnectionStatus.CONNECTED,
            "status_changed_at": SAMPLE_NOW,
            "connected_at": SAMPLE_NOW,
            "expires_at": "2027-01-25T12:00:00.000000Z",
        }
    )
    await save_singleton(services.store, connection, if_match=None)
    async with client(services) as (http, _app):
        body = (await http.get("/api/system/status", headers=READ)).json()
    assert services_of(body)["linkedin"] == {
        "state": "on",
        "reason": None,
        "expiresAt": "2027-01-25T12:00:00.000000Z",
    }
    assert body["expiry"]["linkedinTokenDay"] == 53


async def test_linkedin_without_an_app_registration_is_not_configured() -> None:
    services = make_services()
    connection = LinkedInConnection.default(SAMPLE_NOW).model_copy(
        update={
            "status": ConnectionStatus.CONNECTED,
            "status_changed_at": SAMPLE_NOW,
            "connected_at": SAMPLE_NOW,
            "expires_at": "2027-01-25T12:00:00.000000Z",
        }
    )
    await save_singleton(services.store, connection, if_match=None)
    async with client(services) as (http, app):
        app.state.linkedin_settings = SimpleNamespace(configured=False)
        unconfigured = (await http.get("/api/system/status", headers=READ)).json()
        app.state.linkedin_settings = SimpleNamespace(configured=True)
        configured = (await http.get("/api/system/status", headers=READ)).json()
    assert services_of(unconfigured)["linkedin"] == {
        "state": "off",
        "reason": "not_configured",
        "expiresAt": None,
    }
    assert unconfigured["expiry"]["linkedinTokenDay"] == 0  # the token day is still reported
    assert services_of(configured)["linkedin"]["state"] == "on"


async def test_a_probe_reports_the_portfolio_and_its_pat_expiry() -> None:
    async def portfolio(app: FastAPI) -> ServiceView:
        return ServiceView(state="on", expires_at="2027-02-01T00:00:00.000000Z")

    async def broken(app: FastAPI) -> ServiceView:
        raise RuntimeError("probe bug")

    async with client(make_services()) as (http, app):
        register_status_probe(app, "portfolio", portfolio)
        register_status_probe(app, "linkedin", broken)
        body = (await http.get("/api/system/status", headers=READ)).json()
    views = services_of(body)
    assert views["portfolio"]["state"] == "on"
    assert body["expiry"]["patExpiresAt"] == "2027-02-01T00:00:00.000000Z"
    assert views["linkedin"] == {"state": "unknown", "reason": "probe_failed", "expiresAt": None}
    with pytest.raises(ValueError, match="unknown status service"):
        register_status_probe(app, "github", portfolio)


# --------------------------------------------------------------------------------------------
# Safe mode
# --------------------------------------------------------------------------------------------


async def test_the_owner_leaves_safe_mode_and_the_runner_starts() -> None:
    services = make_services()
    jobs = jobs_service(services, max_boots=1)
    guard = BootGuard(services.store, max_boots=1, window=timedelta(hours=1), clock=services.clock)
    await guard.record_boot("earlier:0")
    await jobs.start()
    try:
        assert jobs.safe_mode
        async with client(services, jobs=jobs) as (http, _app):
            before = (await http.get("/api/system/status", headers=READ)).json()
            exited = await http.post("/api/system/safe-mode/exit", headers=WRITE)
            again = await http.post("/api/system/safe-mode/exit", headers=WRITE)
    finally:
        await jobs.stop()
    assert before["safeMode"] is True
    assert before["boot"] == {"boots": 2, "safeMode": True, "reason": "crash_loop"}
    assert exited.status_code == 200
    body = exited.json()
    assert body["safeMode"] is False
    assert body["runner"]["running"] is True
    assert body["runner"]["schedulerRunning"] is True
    assert again.json()["safeMode"] is False


async def test_safe_mode_exit_needs_the_jobs_service_and_csrf() -> None:
    async with client(make_services()) as (http, _app):
        no_jobs = await http.post("/api/system/safe-mode/exit", headers=WRITE)
        no_csrf = await http.post("/api/system/safe-mode/exit", headers=READ)
    assert no_jobs.status_code == 503
    assert no_jobs.json()["code"] == "jobs_unavailable"
    assert no_csrf.status_code == 403


# --------------------------------------------------------------------------------------------
# Test alert
# --------------------------------------------------------------------------------------------


async def test_the_test_alert_logs_an_alert_event_and_optionally_pushes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = make_services()
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        async with client(services) as (http, _app):
            plain = await http.post("/api/system/test-alert", json={}, headers=WRITE)
            pushed = await http.post("/api/system/test-alert", json={"push": True}, headers=WRITE)
    assert plain.status_code == 202
    assert plain.json() == {"alert": "sent", "push": None}
    assert pushed.status_code == 202
    assert pushed.json() == {"alert": "sent", "push": {"delivered": 1, "failed": 0, "expired": 0}}
    assert services.notifier.sent == [(TEST_PUSH, "normal")]
    assert TEST_PUSH.url == "/settings/system"
    formatter = JsonFormatter()
    alerts = [json.loads(formatter.format(record)) for record in caplog.records]
    assert [line["alert_kind"] for line in alerts] == ["test", "test"]
    assert alerts[0]["ref_id"] == plain.headers["x-request-id"]


async def test_the_test_alert_refuses_unknown_members_and_needs_csrf() -> None:
    async with client(make_services()) as (http, _app):
        extra = await http.post("/api/system/test-alert", json={"sms": True}, headers=WRITE)
        no_csrf = await http.post("/api/system/test-alert", json={}, headers=READ)
    assert extra.status_code == 422
    assert no_csrf.status_code == 403


# --------------------------------------------------------------------------------------------
# Machines and wiring
# --------------------------------------------------------------------------------------------


async def test_machines_are_served_from_the_domain_tables() -> None:
    async with client(make_services()) as (http, _app):
        response = await http.get("/api/meta/machines", headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(MACHINES)
    assert body["article"]["edges"] == MACHINES["article"].as_table()
    assert body["article"]["initial"] == "planned"
    assert body["article"]["states"] == list(MACHINES["article"].as_table())
    assert body["post"]["edges"]["publishing"] == sorted(body["post"]["edges"]["publishing"])
    for name, view in body.items():
        assert set(view["ownerEdges"]) == set(view["edges"]), name
        for state, targets in view["ownerEdges"].items():
            assert set(targets) <= set(view["edges"][state]), (name, state)
            assert targets == sorted(OWNER_EDGES[name].get(state, ())), (name, state)
        assert set(view["ownerWrites"]) == set(OWNER_WRITES[name]), name


async def test_machines_say_who_writes_and_which_edges_the_owner_takes() -> None:
    """The web stops mirroring ARTICLE_WRITER and the owner transitions (contract section 10:
    read-only editor unless the owner writes; buttons only for edges the owner can take)."""
    async with client(make_services()) as (http, _app):
        body = (await http.get("/api/meta/machines", headers=READ)).json()
    article = body["article"]
    assert article["ownerWrites"] == ["planned", "in_review", "needs_attention"]
    assert article["writers"] == {status.value: writer for status, writer in ARTICLE_WRITER.items()}
    owner = article["ownerEdges"]
    assert owner["publish_failed"] == ["in_review", "unpublishing"]  # give up, or retry unpublish
    assert owner["approved"] == ["in_review"]  # pull back; publishing is the publish job's
    assert owner["publishing"] == []
    assert owner["gating"] == ["needs_attention"]  # stop the run
    for source, targets in OWNER_TRANSITIONS.items():
        assert {target.value for target in targets} <= set(owner[source.value]), source
    post = body["post"]
    assert set(post["ownerWrites"]) == {status.value for status in EDITABLE}
    assert post["ownerEdges"]["publishing"] == []  # LinkedIn's answer settles a tap
    assert "staged" not in post["ownerEdges"]["approved"]  # staging is the job's
    assert post["writers"] is None
    assert body["kit"]["ownerWrites"] == []
    assert body["research_run"]["ownerEdges"]["running"] == ["cancelled"]


def test_the_system_router_mounts_every_integrator_route() -> None:
    routes = [route for route in get_router().routes if isinstance(route, APIRoute)]
    assert len(routes) == len(get_router().routes)  # plain routes: the duplicate check sees all
    paths = {(method, route.path) for route in routes for method in route.methods or ()}
    assert paths == {
        ("GET", "/api/system/status"),
        ("POST", "/api/system/safe-mode/exit"),
        ("POST", "/api/system/test-alert"),
        ("GET", "/api/meta/machines"),
        ("GET", "/api/jobs"),
        ("GET", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/cancel"),
        ("POST", "/api/jobs/{job_id}/retry"),
        ("GET", "/api/schedules"),
        ("POST", "/api/schedules/{schedule_id}/confirm"),
        ("POST", "/api/schedules/{schedule_id}/pause"),
        ("POST", "/api/schedules/{schedule_id}/resume"),
        ("GET", "/api/push/key"),
        ("POST", "/api/push/subscriptions"),
        ("DELETE", "/api/push/subscriptions/{subscription_id}"),
    }
