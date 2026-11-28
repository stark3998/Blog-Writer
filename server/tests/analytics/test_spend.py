"""The spend view sums the ``usage`` container like the ledger's ``spend_between``, plus the
brake's numbers, the fallback rate and the review-only rate."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from blogwriter.ai.ledger import CallOutcome, MemoryLedger, UsageRecord
from blogwriter.ai.types import Task, Usage
from blogwriter.analytics.spend import BrakeParams, build_spend, fallback_rate, usage_entries
from blogwriter.analytics.testing import TEST_NOW, posted_post
from blogwriter.domain.articles import Article
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.testing import FakeServices, sample_document

ZONE = ZoneInfo("America/New_York")
PARAMS = BrakeParams(
    daily_cap_usd=Decimal("0.5"),
    multiplier=Decimal(3),
    window_days=7,
    timezone="America/New_York",
    primary_models={
        "synthesis": "gpt-6.2-sol",
        "draft": "claude-opus-5-5",
        "critique": "gpt-6.2-astra",
    },
)


def record(
    at: datetime,
    cost: str,
    *,
    task: Task = Task.SYNTHESIS,
    model: str = "gpt-6.2-sol",
    outcome: CallOutcome = "completed",
) -> UsageRecord:
    return UsageRecord(
        task=task,
        model=model,
        deployment=model,
        provider="anthropic" if model.startswith("claude") else "openai",
        usage=Usage(input=1000, output=200),
        cost_usd=Decimal(cost),
        at=at,
        origin="background",
        outcome=outcome,
    )


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """A New York wall time in November 2026, as UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=ZONE).astimezone(UTC)


RECORDS = (
    # Today (Thursday 26th) up to noon.
    record(local(26, 9), "0.125"),
    record(local(26, 10), "1.5", task=Task.DRAFT, model="claude-opus-5-5"),
    record(local(26, 11), "0.25", task=Task.DRAFT, model="gpt-6.2-sol"),  # a fallback
    record(local(26, 11, 30), "0.05", task=Task.SYNTHESIS, model="gpt-6.2-astra"),  # fallback
    record(local(26, 11, 45), "0.30", task=Task.DRAFT, outcome="refused"),  # billed, not served
    record(local(26, 11, 50), "0.01", task=Task.TRIAGE, model="gpt-6-luna"),  # no route here
    # 23:30 on the 25th in New York is the 26th in UTC: it counts for the 25th.
    record(local(25, 23, 30), "2.0"),
    record(local(24, 8), "4.0", task=Task.CRITIQUE, model="gpt-6.2-astra"),
    record(local(20, 8), "6.0"),
    record(local(19, 8), "1.0"),
    record(local(12, 8), "99.0"),  # outside the view and the brake window
)


async def store_records(services: FakeServices) -> MemoryLedger:
    ledger = MemoryLedger()
    for item in RECORDS:
        await services.store.upsert("usage", dict(item.to_document()))
        await ledger.record(item)
    return ledger


async def test_days_sum_the_usage_container_like_the_ledger(services: FakeServices) -> None:
    ledger = await store_records(services)
    view = await build_spend(services.store, now=TEST_NOW, days=7, params=PARAMS)
    assert [day.date for day in view.days] == [f"2026-11-{day}" for day in range(20, 27)]
    for day in view.days:
        start = datetime.combine(datetime.fromisoformat(day.date).date(), time.min, tzinfo=ZONE)
        expected = await ledger.spend_between(start, start + timedelta(days=1))
        assert day.cost_usd == round(float(expected), 6), day.date
    today = view.days[-1]
    assert today.cost_usd == 2.235
    assert today.by_task == {"draft": 2.05, "synthesis": 0.175, "triage": 0.01}
    assert today.by_model["claude-opus-5-5"] == 1.5
    assert view.days[-2].cost_usd == 2.0  # 23:30 local on the 25th
    assert view.total == 14.235  # 2.235 + 2.0 + 4.0 + 6.0


async def test_the_brake_numbers_match_the_brake(services: FakeServices) -> None:
    await store_records(services)
    view = await build_spend(services.store, now=TEST_NOW, days=30, params=PARAMS)
    # The 7 days before today: 25th 2.0, 24th 4.0, 23rd-21st 0, 20th 6.0, 19th 1.0 -> 1.0.
    assert view.trailing_median == 1.0
    assert view.brake_threshold == 3.0  # max(0.5 cap, 3 x 1.0)
    assert len(view.days) == 30
    assert view.timezone == "America/New_York"


def test_the_fallback_rate_counts_served_calls_by_a_non_primary_model() -> None:
    entries = usage_entries([item.to_document() for item in RECORDS[:6]], ZONE)
    # Routed completed calls: synthesis/sol, draft/opus, draft/sol (fallback), synthesis/astra
    # (fallback); the refused call and the triage call (no route here) are left out.
    assert fallback_rate(entries, PARAMS.primary_models) == 0.5
    assert fallback_rate([], PARAMS.primary_models) is None


def test_malformed_usage_documents_are_skipped() -> None:
    good = dict(RECORDS[0].to_document())
    entries = usage_entries(
        [
            good,
            {**good, "costUsd": "1.0"},
            {**good, "costUsd": True},
            {**good, "at": "yesterday"},
            {**good, "costUsd": float("nan")},
            {**good, "costUsd": -1},
        ],
        ZONE,
    )
    assert [entry.cost for entry in entries] == [Decimal("0.125")]


def served(at: str) -> list[ServedByRecord]:
    return [
        ServedByRecord(
            step="draft",
            at=at,
            task="draft",
            model="claude-opus-5-5",
            deployment="claude-opus-5-5",
            provider="anthropic",
        )
    ]


async def test_the_review_only_rate(services: FakeServices) -> None:
    recent = "2026-11-24T15:00:00.000000Z"
    await posted_post(services, posted_at=recent, served_by=served(recent), review_only=True)
    await posted_post(services, posted_at=recent, served_by=served(recent))
    await posted_post(services, posted_at=recent, served_by=served("2026-09-01T15:00:00.000000Z"))
    flagship = sample_document(Article, served_by=served(recent), updated_at=recent)
    evaluation = sample_document(
        Article, purpose="eval", served_by=served(recent), review_only=True, updated_at=recent
    )
    await create_doc(services.store, flagship)
    await create_doc(services.store, evaluation)
    view = await build_spend(services.store, now=TEST_NOW, days=7, params=PARAMS)
    assert view.review_only_rate == round(1 / 3, 4)
    empty = await build_spend(services.store, now=TEST_NOW, days=1, params=PARAMS)
    assert empty.review_only_rate is None


async def test_spend_route(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await store_records(services)
    response = await client.get("/api/analytics/spend", params={"days": 7}, headers=read_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "days",
        "total",
        "trailingMedian",
        "brakeThreshold",
        "fallbackRate",
        "reviewOnlyRate",
        "timezone",
    }
    assert set(body["days"][0]) == {"date", "costUsd", "byTask", "byModel"}
    assert len(body["days"]) == 7
    for days in (0, 91):
        bad = await client.get("/api/analytics/spend", params={"days": days}, headers=read_headers)
        assert bad.status_code == 422
