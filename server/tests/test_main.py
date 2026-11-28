"""The production wiring module: composition (every router, job type, schedule and lifecycle),
ledger sink, logger caps and the quarantine alert."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from blogwriter.ai import AISettings, UsageRecord
from blogwriter.ai.types import Task, Usage
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.db import MemoryStore
from blogwriter.domain.jobtypes import ALL_JOB_TYPES, SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.ports import StateServices, current_services
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.jobs import JobsLifecycle, JobsService, QuarantineNotice
from blogwriter.main import (
    QUIET_LOGGERS,
    SCHEDULE_PROVIDERS,
    AILifecycle,
    CosmosLedgerSink,
    _on_quarantine,  # pyright: ignore[reportPrivateUsage]
    build_app,
    build_lifecycles,
    build_registry,
    package_routers,
    portfolio_status,
)
from blogwriter.publish.settings import publish_settings
from blogwriter.services import SchedulesLifecycle, ServicesLifecycle


def _record(cost: str, at: datetime, record_id: str) -> UsageRecord:
    return UsageRecord(
        task=Task.DRAFT,
        model="claude-opus-5-5",
        deployment="claude-opus-5-5",
        provider="anthropic",
        usage=Usage(input=1000, output=500),
        cost_usd=Decimal(cost),
        at=at,
        origin="owner",
        outcome="completed",
        record_id=record_id,
    )


def _app_with_memory_store() -> tuple[FastAPI, MemoryStore]:
    app = build_app(make_settings())
    store = MemoryStore()
    app.state.store = store
    return app, store


async def test_build_app_starts_with_jobs_sse_and_no_ai() -> None:
    app, store = _app_with_memory_store()
    async with app.router.lifespan_context(app):
        assert app.state.store is store
        assert app.state.ai is None  # BW_AI_* not configured in tests
        assert app.state.jobs is not None
        async with asgi_client(app) as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            owner = principal_headers(OWNER_OID)
            me = await client.get("/api/me", headers=owner)
            assert me.status_code == 200
            spec = await client.get("/api/openapi.json", headers=owner)
            assert spec.status_code == 200
            paths = set(spec.json()["paths"])
            assert {"/api/health", "/api/me", "/api/events"} <= paths
            client.cookies.clear()  # drop the __Host-bw cookie the owner requests set
            anonymous = await client.get("/api/events")
            assert anonymous.status_code == 401  # the SSE stream is behind auth too


async def test_ai_lifecycle_stays_off_when_unconfigured() -> None:
    lifecycle = AILifecycle(AISettings())
    app = FastAPI()
    await lifecycle.startup(app)
    assert app.state.ai is None
    await lifecycle.shutdown(app)


async def test_ledger_sink_sums_a_range_across_months_and_never_double_counts() -> None:
    store = MemoryStore()
    sink = CosmosLedgerSink(store)
    start = datetime(2026, 11, 30, 23, 0, tzinfo=UTC)
    await sink.record(_record("1.25", start, "r1"))
    await sink.record(_record("1.25", start, "r1"))  # retried write: same id, counted once
    await sink.record(_record("0.50", start + timedelta(hours=2), "r2"))  # next month partition
    await sink.record(_record("9.00", start + timedelta(days=3), "r3"))  # outside the range
    total = await sink.spend_between(start, start + timedelta(days=1))
    assert total == Decimal("1.75")


async def test_ledger_sink_refuses_naive_bounds() -> None:
    sink = CosmosLedgerSink(MemoryStore())
    with pytest.raises(ValueError, match="timezone-aware"):
        await sink.spend_between(datetime(2026, 11, 1), datetime(2026, 11, 2))


def test_third_party_loggers_are_capped() -> None:
    build_app(make_settings())
    for name in QUIET_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


async def test_quarantine_hook_emits_an_alert_with_ids_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    notice = QuarantineNotice(
        job_id="job-123", job_type="article.draft", attempts=3, step="draft", error_type="X"
    )
    await _on_quarantine(notice)
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert len(alerts) == 1
    assert getattr(alerts[0], "alert_kind", None) == "quarantined"
    assert getattr(alerts[0], "ref_id", None) == "job-123"


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def test_every_package_router_is_mounted() -> None:
    app = build_app(make_settings())
    spec = app.openapi()["paths"]
    mounted = {
        (method.upper(), path)
        for path, operations in spec.items()
        for method in operations
        if method.upper() in _HTTP_METHODS
    }
    wanted = {
        (method, re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path))  # {name:path} -> {name}
        for router in package_routers()
        for route in router.routes
        if isinstance(route, APIRoute) and route.include_in_schema
        for method in route.methods or ()
        if method in _HTTP_METHODS
    }
    assert len(wanted) > 150
    assert wanted <= mounted
    prefixes = {path.split("/{")[0] for _, path in mounted}
    for prefix in (
        "/api/media",
        "/api/kb/pillars",
        "/api/stories",
        "/api/povs",
        "/api/voice/samples",
        "/api/research/runs",
        "/api/sources",
        "/api/ideas",
        "/api/articles",
        "/api/gates/lint",
        "/api/linkedin/connection",
        "/api/backups",
        "/api/interviews",
        "/api/posts",
        "/api/today",
        "/api/plan/weeks",
        "/api/reminders",
        "/api/engagement/items",
        "/api/analytics/overview",
        "/api/evals/topics",
        "/api/jobs",
        "/api/schedules",
        "/api/system/status",
        "/api/push/key",
        "/api/meta/machines",
        "/api/events",
    ):
        assert prefix in prefixes, prefix


def test_every_job_type_is_registered_once() -> None:
    registry = build_registry()
    assert sorted(registry.names()) == sorted(ALL_JOB_TYPES)


def test_schedule_entries_start_only_scheduled_non_content_jobs() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW)
    entries = [entry for provider in SCHEDULE_PROVIDERS for entry in provider(cadence)]
    ids = [entry.id for entry in entries]
    assert len(ids) == len(set(ids))
    assert {entry.job_type for entry in entries} == SCHEDULED_JOB_TYPES
    assert not [entry.id for entry in entries if generates_content(entry.job_type)]


def test_lifecycles_start_the_runner_after_every_service() -> None:
    lifecycles = build_lifecycles(build_registry())
    names = [type(item).__name__ for item in lifecycles]
    assert lifecycles[0] is db_module()
    assert names[-3:] == ["ServicesLifecycle", "JobsLifecycle", "SchedulesLifecycle"]
    assert isinstance(lifecycles[-3], ServicesLifecycle)
    assert isinstance(lifecycles[-2], JobsLifecycle)
    assert isinstance(lifecycles[-1], SchedulesLifecycle)
    assert names.index("AILifecycle") < names.index("CoreServicesLifecycle")
    assert names.index("RenderLifecycle") < names.index("RenderAdapterLifecycle")


def db_module() -> object:
    from blogwriter import db

    return db


async def test_the_app_wires_every_service_runner_and_schedule() -> None:
    app, store = _app_with_memory_store()
    cadence = CadenceSettings.default(SAMPLE_NOW)
    expected = {entry.id for provider in SCHEDULE_PROVIDERS for entry in provider(cadence)}
    async with app.router.lifespan_context(app):
        state = app.state
        assert state.ai is None
        assert state.embedder is None  # no AI router: gate 4 fails closed
        assert state.blobs is None  # BW_STORAGE_BLOB_ENDPOINT unset
        for name in ("clock", "egress", "notifier", "render", "fetcher", "classifier", "slop"):
            assert getattr(state, name) is not None, name
        assert isinstance(state.services, StateServices)
        assert current_services() is state.services  # job steps see the same services
        jobs_service = state.jobs
        assert isinstance(jobs_service, JobsService)
        assert sorted(jobs_service.registry.names()) == sorted(ALL_JOB_TYPES)
        assert jobs_service.runner.claiming
        assert state.schedules is not None
        seeded = {doc.id for doc in await store.find("jobs", []) if doc.id.startswith("sched-")}
        assert seeded == expected
        async with asgi_client(app) as client:
            owner = principal_headers(OWNER_OID)
            status = await client.get("/api/system/status", headers=owner)
            assert status.status_code == 200
            assert status.json()["services"]["ai"]["state"] == "off"
            schedules = await client.get("/api/schedules", headers=owner)
            assert schedules.status_code == 200
            assert {item["id"] for item in schedules.json()} == expected
            today = await client.get("/api/today", headers=owner)
            assert today.status_code == 200
            pillars = await client.get("/api/kb/pillars", headers=owner)
            assert pillars.status_code == 200
            assert pillars.json()["etag"] is None
    with pytest.raises(Exception, match="services"):
        current_services()  # uninstalled on shutdown


async def test_portfolio_status_follows_the_publish_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_settings.cache_clear()
    try:
        view = await portfolio_status(FastAPI())
        assert (view.state, view.reason) == ("off", "not_configured")
        monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_REPOSITORY", "owner/site")
        monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_WORKFLOW", "publish.yml")
        monkeypatch.setenv("BW_PUBLISH_PORTFOLIO_TOKEN", "test-token-not-a-real-secret")
        monkeypatch.setenv("BW_PUBLISH_SITE_ORIGIN", "https://example.com")
        publish_settings.cache_clear()
        assert (await portfolio_status(FastAPI())).state == "on"
    finally:
        publish_settings.cache_clear()


def test_every_openapi_reference_resolves() -> None:
    """``npm run gen:api`` bundles this document: a ``$ref`` it cannot resolve (a model's own
    ``#/$defs/...`` pasted into ``openapi_extra``) stops the web types from generating."""
    spec = build_app(make_settings()).openapi()
    schemas = set(spec["components"]["schemas"])
    refs: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in cast(dict[str, object], node).items():
                if key == "$ref" and isinstance(value, str):
                    refs.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in cast(list[object], node):
                walk(item)

    walk(spec)
    prefix = "#/components/schemas/"
    assert refs
    assert [ref for ref in refs if not ref.startswith(prefix)] == []
    assert sorted({ref.removeprefix(prefix) for ref in refs} - schemas) == []
