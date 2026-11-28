"""The cost ledger and the runaway brake.

Plan: "AI layer" > Cost (a cost ledger; a soft daily brake above 3x the trailing 7-day median or a
fixed amount pauses background AI work and fires an out-of-band alert; owner-clicked actions are
never blocked) and the per-model prices (cache writes and hits, web_search per 1K requests).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import override
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import DEFAULT_PRICES, DEFAULT_WEB_SEARCH_USD_PER_1K, AISettings
from blogwriter.ai.ledger import (
    BrakeEngaged,
    BudgetBrake,
    MemoryLedger,
    ModelPrice,
    PriceTable,
    UnknownPriceError,
    UsageRecord,
)
from blogwriter.ai.types import Task, Usage

NEW_YORK = ZoneInfo("America/New_York")
# 2026-11-26 10:00 in New York.
NOW = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)


class ArgsOnlyAISettings(AISettings):
    """``AISettings`` that ignore the process environment."""

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


@pytest.fixture
def prices() -> PriceTable:
    return PriceTable(DEFAULT_PRICES, web_search_per_1k=DEFAULT_WEB_SEARCH_USD_PER_1K)


def record(
    cost: str,
    at: datetime,
    *,
    task: Task = Task.RESEARCH,
    record_id: str | None = None,
) -> UsageRecord:
    item = UsageRecord(
        task=task,
        model="gpt-6.2-sol",
        deployment="gpt-6.2-sol",
        provider="openai",
        usage=Usage(input=1_000, output=100),
        cost_usd=Decimal(cost),
        at=at,
        origin="background",
        outcome="completed",
        job_id="job-1",
    )
    return item if record_id is None else replace(item, record_id=record_id)


# --------------------------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------------------------


def test_default_prices_match_the_plan() -> None:
    sol = DEFAULT_PRICES["gpt-6.2-sol"]
    assert (sol.input, sol.output, sol.cached_input) == (Decimal(2), Decimal(10), Decimal("0.10"))
    astra = DEFAULT_PRICES["gpt-6.2-astra"]
    assert (astra.input, astra.output, astra.cached_input) == (
        Decimal(8),
        Decimal(40),
        Decimal("0.40"),
    )
    luna = DEFAULT_PRICES["gpt-6-luna"]
    assert (luna.input, luna.output) == (Decimal("0.10"), Decimal("0.50"))
    opus = DEFAULT_PRICES["claude-opus-5-5"]
    assert (opus.input, opus.output, opus.cache_write, opus.cached_input) == (
        Decimal(4),
        Decimal(20),
        Decimal(8),
        Decimal("0.20"),
    )
    assert DEFAULT_PRICES["text-embedding-3-large"].input == Decimal("0.13")
    assert Decimal(14) == DEFAULT_WEB_SEARCH_USD_PER_1K


def test_sol_bills_uncached_input_cache_hits_and_output(prices: PriceTable) -> None:
    usage = Usage(input=1_000_000, cached_input=400_000, output=100_000, reasoning=40_000)
    # 600K x $2 + 400K x $0.10 + 100K x $10 (reasoning is part of the output), per 1M
    assert prices.cost("gpt-6.2-sol", usage) == Decimal("2.24")


def test_opus_cache_writes_and_hits_have_their_own_prices(prices: PriceTable) -> None:
    usage = Usage(input=50_000, cached_input=25_000, cache_write=20_000, output=4_000)
    # 5K uncached x $4 + 25K hits x $0.20 + 20K 1 h writes x $8 + 4K out x $20, per 1M
    expected = Decimal("0.02") + Decimal("0.005") + Decimal("0.16") + Decimal("0.08")
    assert prices.cost("claude-opus-5-5", usage) == expected == Decimal("0.265")


def test_web_search_is_priced_per_thousand_requests(prices: PriceTable) -> None:
    searches = Usage(web_search_requests=3)
    assert prices.cost("gpt-6.2-sol", searches) == Decimal("0.042")
    combined = Usage(input=10_000, output=2_000, web_search_requests=5)
    assert prices.cost("gpt-6.2-sol", combined) == Decimal("0.02") + Decimal("0.02") + Decimal(
        "0.07"
    )


def test_unset_cache_prices_fall_back_to_the_input_price(prices: PriceTable) -> None:
    luna = Usage(input=1_000_000, cached_input=500_000, cache_write=100_000)
    assert prices.cost("gpt-6-luna", luna) == Decimal("0.10")
    # OpenAI writes its prompt cache at no extra charge: writes cost the input price.
    assert prices.cost("gpt-6.2-sol", Usage(input=1_000, cache_write=1_000)) == Decimal("0.002")


def test_embeddings_and_astra(prices: PriceTable) -> None:
    assert prices.cost("text-embedding-3-large", Usage(input=2_000_000)) == Decimal("0.26")
    astra = Usage(input=100_000, cached_input=50_000, output=10_000)
    assert prices.cost("gpt-6.2-astra", astra) == Decimal("0.4") + Decimal("0.02") + Decimal("0.4")


def test_images_are_priced_per_image() -> None:
    table = PriceTable(
        {"gpt-image-2": ModelPrice(input=Decimal(5), image=Decimal("0.04"))},
        web_search_per_1k=Decimal(14),
    )
    assert table.cost("gpt-image-2", Usage(input=200_000, images=3)) == Decimal("1.12")


def test_prices_are_overridable_from_settings() -> None:
    settings = ArgsOnlyAISettings(
        price_overrides={"gpt-6.2-sol": ModelPrice(input=Decimal("1.5"), output=Decimal(8))},
        web_search_usd_per_1k=Decimal(10),
    )
    table = settings.price_table()
    usage = Usage(input=1_000_000, cached_input=1_000_000, output=1_000_000, web_search_requests=2)
    # The override sets no cached price, so hits bill at its input price.
    assert table.cost("gpt-6.2-sol", usage) == Decimal("1.5") + Decimal(8) + Decimal("0.02")
    assert table.cost("claude-opus-5-5", Usage(output=1_000_000)) == Decimal(20)


def test_unknown_models_and_bad_prices_are_refused(prices: PriceTable) -> None:
    assert "gpt-6.2-sol" in prices
    assert "gpt-9" not in prices
    with pytest.raises(UnknownPriceError, match="gpt-9"):
        prices.cost("gpt-9", Usage())
    with pytest.raises(ValidationError):
        ModelPrice(input=Decimal(-1))
    with pytest.raises(ValidationError):
        ModelPrice(input=Decimal(1), unknown=Decimal(1))  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValueError, match="web_search_per_1k"):
        PriceTable({}, web_search_per_1k=Decimal(-1))


# --------------------------------------------------------------------------------------------
# Records and the memory sink
# --------------------------------------------------------------------------------------------


def test_usage_records_are_utc_and_validated() -> None:
    eastern = timezone(timedelta(hours=-5))
    item = record("0.265", datetime(2026, 11, 30, 21, 30, tzinfo=eastern))
    assert item.at == datetime(2026, 12, 1, 2, 30, tzinfo=UTC)
    assert item.month == "2026-12"
    assert item.web_searches == 0
    assert item.images == 0
    assert len(item.record_id) == 32
    document = item.to_document()
    assert document == {
        "id": item.record_id,
        "month": "2026-12",
        "at": "2026-12-01T02:30:00.000000Z",
        "task": "research",
        "model": "gpt-6.2-sol",
        "deployment": "gpt-6.2-sol",
        "provider": "openai",
        "origin": "background",
        "outcome": "completed",
        "jobId": "job-1",
        "responseId": None,
        "inputTokens": 1_000,
        "cachedInputTokens": 0,
        "cacheWriteTokens": 0,
        "outputTokens": 100,
        "reasoningTokens": 0,
        "webSearches": 0,
        "images": 0,
        "costUsd": 0.265,
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        record("1", datetime(2026, 11, 26, 12, 0))  # naive on purpose
    with pytest.raises(ValueError, match="negative"):
        record("-0.01", NOW)
    with pytest.raises(ValueError, match="record_id"):
        record("1", NOW, record_id="")


def test_document_timestamps_are_fixed_width_so_string_order_is_time_order() -> None:
    # isoformat() drops a zero fraction, and "...:00Z" sorts after "...:00.5Z": a store range
    # query on the string would then put a record on the wrong side of a day boundary.
    stamps: list[str] = []
    for micros in (0, 500_000, 999_999):
        at = record("1", datetime(2026, 11, 26, 5, 0, 0, micros, tzinfo=UTC)).to_document()["at"]
        assert isinstance(at, str)
        stamps.append(at)
    assert stamps == [
        "2026-11-26T05:00:00.000000Z",
        "2026-11-26T05:00:00.500000Z",
        "2026-11-26T05:00:00.999999Z",
    ]
    assert stamps == sorted(stamps)
    assert stamps[0] < "2026-11-26T05:00:01.000000Z"


async def test_the_memory_ledger_sums_spend_over_half_open_ranges() -> None:
    ledger = MemoryLedger()
    ten = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    for hours, cost in ((0, "1"), (1, "2"), (2, "4")):
        await ledger.record(record(cost, ten + timedelta(hours=hours)))
    assert await ledger.spend_between(ten, ten + timedelta(hours=2)) == Decimal(3)
    assert await ledger.spend_between(ten, ten + timedelta(hours=3)) == Decimal(7)
    assert await ledger.spend_between(ten - timedelta(hours=1), ten) == Decimal(0)
    duplicate = record("8", ten, record_id="same")
    await ledger.record(duplicate)
    await ledger.record(duplicate)  # a retried write must not double count
    assert len(ledger.records) == 4
    assert await ledger.spend_between(ten, ten + timedelta(hours=3)) == Decimal(15)
    with pytest.raises(ValueError, match="timezone-aware"):
        await ledger.spend_between(datetime(2026, 11, 26), ten)  # naive on purpose


# --------------------------------------------------------------------------------------------
# Brake
# --------------------------------------------------------------------------------------------


def local(day: date, hour: int = 12) -> datetime:
    """Noon (by default) on a New York calendar day."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=NEW_YORK)


TODAY = date(2026, 11, 26)


async def ledger_with(today: str, *previous: str) -> MemoryLedger:
    """Today's spend plus the spend of the previous days (yesterday first)."""
    ledger = MemoryLedger()
    if Decimal(today):
        await ledger.record(record(today, local(TODAY)))
    for offset, cost in enumerate(previous, start=1):
        if Decimal(cost):
            await ledger.record(record(cost, local(TODAY - timedelta(days=offset))))
    return ledger


def brake(sink: MemoryLedger, *, cap: str = "25", now: datetime = NOW) -> BudgetBrake:
    return BudgetBrake(sink, daily_cap_usd=Decimal(cap), timezone=NEW_YORK, now=lambda: now)


async def test_the_fixed_cap_is_the_threshold_without_history() -> None:
    status = await brake(MemoryLedger()).status()
    assert status.day == TODAY
    assert status.spend_usd == 0
    assert status.trailing_median_usd == 0
    assert status.threshold_usd == Decimal(25)
    assert not status.engaged


async def test_spend_must_exceed_the_threshold_to_engage() -> None:
    at_threshold = brake(await ledger_with("25"))
    status = await at_threshold.check("background")
    assert status is not None
    assert not status.engaged
    over = brake(await ledger_with("25.01"))
    with pytest.raises(BrakeEngaged) as engaged:
        await over.check("background")
    assert engaged.value.reason == "over_budget"
    assert engaged.value.status is not None
    assert engaged.value.status.spend_usd == Decimal("25.01")
    assert engaged.value.status.threshold_usd == Decimal(25)


async def test_three_times_the_trailing_median_raises_the_threshold() -> None:
    previous = ("10", "20", "20", "30", "0", "40", "50")  # median 20
    status = await brake(await ledger_with("59", *previous)).status()
    assert status.trailing_median_usd == Decimal(20)
    assert status.threshold_usd == Decimal(60)
    assert not status.engaged
    with pytest.raises(BrakeEngaged):
        await brake(await ledger_with("61", *previous)).check("background")


async def test_the_cap_still_applies_when_the_median_is_low() -> None:
    previous = ("5", "5", "5", "5", "5", "5", "5")  # 3 x 5 = 15 < 25
    status = await brake(await ledger_with("20", *previous)).status()
    assert status.threshold_usd == Decimal(25)
    assert not status.engaged


async def test_today_and_older_days_stay_out_of_the_median() -> None:
    # Three expensive days, four days without spend (they count as zero), and an eighth
    # day that is outside the window.
    previous = ("100", "100", "100", "0", "0", "0", "0", "900")
    status = await brake(await ledger_with("5000", *previous)).status()
    assert status.trailing_median_usd == 0
    assert status.threshold_usd == Decimal(25)
    assert status.engaged


async def test_the_window_and_multiplier_are_configurable() -> None:
    sink = await ledger_with("31", "10", "20", "30")
    custom = BudgetBrake(
        sink,
        daily_cap_usd=Decimal(0),
        timezone=NEW_YORK,
        multiplier=Decimal("1.5"),
        window_days=2,
        now=lambda: NOW,
    )
    status = await custom.status()
    assert status.trailing_median_usd == Decimal(15)  # median of 10 and 20
    assert status.threshold_usd == Decimal("22.5")
    assert status.engaged


async def test_days_follow_the_brake_time_zone() -> None:
    sink = MemoryLedger()
    # 03:00 UTC on the 26th is 22:00 on the 25th in New York: yesterday's spend.
    await sink.record(record("30", datetime(2026, 11, 26, 3, 0, tzinfo=UTC)))
    status = await brake(sink).status()
    assert status.spend_usd == 0
    # 05:30 UTC is 00:30 in New York: today.
    await sink.record(record("30", datetime(2026, 11, 26, 5, 30, tzinfo=UTC)))
    assert (await brake(sink).status()).spend_usd == Decimal(30)


async def test_owner_calls_are_never_braked() -> None:
    class Unreadable(MemoryLedger):
        @override
        async def spend_between(self, start: datetime, end: datetime) -> Decimal:
            raise AssertionError("owner calls must not even read the ledger")

    assert await brake(Unreadable()).check("owner") is None
    assert await brake(await ledger_with("1000")).check("owner") is None


async def test_an_unreadable_ledger_pauses_background_work() -> None:
    class Broken(MemoryLedger):
        @override
        async def spend_between(self, start: datetime, end: datetime) -> Decimal:
            raise ConnectionError("store down")

    with pytest.raises(BrakeEngaged) as engaged:
        await brake(Broken()).check("background")
    assert engaged.value.reason == "ledger_unavailable"
    assert engaged.value.status is None
    assert isinstance(engaged.value.__cause__, ConnectionError)
    assert "unavailable" in str(engaged.value)


async def test_spend_the_sink_failed_to_store_still_counts_today() -> None:
    sink = await ledger_with("10")
    current = NOW
    meter = BudgetBrake(sink, daily_cap_usd=Decimal(25), timezone=NEW_YORK, now=lambda: current)
    meter.note_unrecorded(record("50", local(TODAY - timedelta(days=1))))  # not today's spend
    assert (await meter.status()).spend_usd == Decimal(10)
    meter.note_unrecorded(record("7", local(TODAY, 9)))
    meter.note_unrecorded(record("8", local(TODAY, 11)))
    meter.note_unrecorded(record("100", local(TODAY - timedelta(days=2))))  # late and old
    status = await meter.check("background")
    assert status is not None
    assert status.spend_usd == Decimal(25)  # 10 stored + 15 unrecorded: at the cap
    meter.note_unrecorded(record("0.01", local(TODAY, 12)))
    with pytest.raises(BrakeEngaged) as engaged:
        await meter.check("background")
    assert engaged.value.status is not None
    assert engaged.value.status.spend_usd == Decimal("25.01")
    current = NOW + timedelta(days=1)  # a new day starts from what the sink holds
    assert (await meter.status()).spend_usd == Decimal(0)


async def test_engaging_alerts_once_per_day(caplog: pytest.LogCaptureFixture) -> None:
    sink = await ledger_with("100")
    current = NOW
    meter = BudgetBrake(sink, daily_cap_usd=Decimal(25), timezone=NEW_YORK, now=lambda: current)
    with caplog.at_level(logging.WARNING, logger="blogwriter.ai.ledger"):
        for _ in range(2):
            with pytest.raises(BrakeEngaged):
                await meter.check("background")
        alerts = [item for item in caplog.records if item.msg == "ai_brake_engaged"]
        assert len(alerts) == 1
        assert alerts[0].__dict__["spend_usd"] == 100.0
        assert alerts[0].__dict__["threshold_usd"] == 25.0
        await sink.record(record("100", local(TODAY + timedelta(days=1))))
        current = NOW + timedelta(days=1)
        with pytest.raises(BrakeEngaged):
            await meter.check("background")
        alerts = [item for item in caplog.records if item.msg == "ai_brake_engaged"]
        assert len(alerts) == 2


async def test_the_trailing_median_is_read_once_per_day() -> None:
    class Counting(MemoryLedger):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        @override
        async def spend_between(self, start: datetime, end: datetime) -> Decimal:
            self.reads += 1
            return await super().spend_between(start, end)

    sink = Counting()
    meter = brake(sink)
    await meter.status()
    assert sink.reads == 8  # today plus seven days of history
    await meter.status()
    assert sink.reads == 9  # today only


def test_brake_settings_are_validated() -> None:
    sink = MemoryLedger()
    with pytest.raises(ValueError, match="daily_cap_usd"):
        BudgetBrake(sink, daily_cap_usd=Decimal(-1), timezone=NEW_YORK)
    with pytest.raises(ValueError, match="multiplier"):
        BudgetBrake(sink, daily_cap_usd=Decimal(1), timezone=NEW_YORK, multiplier=Decimal(-1))
    with pytest.raises(ValueError, match="window_days"):
        BudgetBrake(sink, daily_cap_usd=Decimal(1), timezone=NEW_YORK, window_days=0)


async def test_naive_clocks_are_refused() -> None:
    naive = BudgetBrake(
        MemoryLedger(),
        daily_cap_usd=Decimal(1),
        timezone=NEW_YORK,
        now=lambda: datetime(2026, 11, 26, 12, 0),  # naive on purpose
    )
    with pytest.raises(BrakeEngaged) as engaged:
        await naive.check("background")
    assert engaged.value.reason == "ledger_unavailable"


def test_settings_build_the_brake_inputs() -> None:
    settings = ArgsOnlyAISettings(
        brake_daily_cap_usd=Decimal(40),
        brake_multiplier=Decimal(4),
        brake_window_days=14,
        brake_timezone="Europe/London",
    )
    assert settings.brake_zone() == ZoneInfo("Europe/London")
    assert (settings.brake_daily_cap_usd, settings.brake_multiplier) == (Decimal(40), Decimal(4))
    with pytest.raises(ValidationError, match="time zone"):
        ArgsOnlyAISettings(brake_timezone="Mars/Olympus_Mons")
    with pytest.raises(ValidationError):
        ArgsOnlyAISettings(brake_daily_cap_usd=Decimal(-1))
