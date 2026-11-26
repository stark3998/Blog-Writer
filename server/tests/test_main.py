"""The production wiring module: composition, ledger sink, logger caps and the quarantine alert."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI

from blogwriter.ai import AISettings, UsageRecord
from blogwriter.ai.types import Task, Usage
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.db import MemoryStore
from blogwriter.jobs import QuarantineNotice
from blogwriter.main import (
    QUIET_LOGGERS,
    AILifecycle,
    CosmosLedgerSink,
    _on_quarantine,  # pyright: ignore[reportPrivateUsage]
    build_app,
)


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
