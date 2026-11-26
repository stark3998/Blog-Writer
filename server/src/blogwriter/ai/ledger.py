"""The cost ledger: prices, usage records, the sink protocol and the runaway brake.

:class:`PriceTable`
    USD prices per model (per 1M tokens) plus the hosted ``web_search`` price (per 1K requests).
    :meth:`PriceTable.cost` prices one call's :class:`~blogwriter.ai.types.Usage`::

        uncached input  x input price
        cache hits      x cached-input price   (the input price when none is set)
        cache writes    x cache-write price    (the input price when none is set; Anthropic 1 h)
        output          x output price         (reasoning tokens are part of the output)
        web searches    x web_search price / 1000
        images          x image price

:class:`UsageRecord` and :class:`LedgerSink`
    The router writes one record per model call, discarded attempts included. A sink stores the
    records and sums spend over a time range. This package never persists anything itself:
    :class:`MemoryLedger` keeps records in memory (tests, local runs) and the Cosmos-backed sink
    is wired by the app. :meth:`UsageRecord.to_document` gives the ``usage`` container shape
    (partition key ``/month``).
:class:`BudgetBrake`
    The soft daily brake. Background work raises :class:`BrakeEngaged` when today's spend
    exceeds ``max(daily cap, multiplier x the median daily spend of the previous window_days
    days)``. Days run midnight to midnight in the configured time zone; days without records
    count as zero. Owner-initiated calls are never braked. An engaged brake logs
    ``ai_brake_engaged`` once per day, which the out-of-band alert rule watches. If the sink
    cannot be read, background work is refused (fail closed). Spend the sink failed to store
    (:meth:`BudgetBrake.note_unrecorded`) still counts toward today's total, so a sink that
    keeps failing writes cannot blind the brake to a runaway loop.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from blogwriter.ai.types import ProviderName, Task, Usage
from blogwriter.core.logging import describe_exception, register_log_fields

__all__ = [
    "BrakeEngaged",
    "BrakeStatus",
    "BudgetBrake",
    "CallOutcome",
    "LedgerSink",
    "MemoryLedger",
    "ModelPrice",
    "Origin",
    "PriceTable",
    "UnknownPriceError",
    "UsageRecord",
]

type Origin = Literal["owner", "background"]
"""Who started a call: an owner click (never braked) or background work (braked)."""

type CallOutcome = Literal["completed", "refused", "failed"]
"""How one model call ended. Discarded attempts (refused, repaired, retried) are still billed."""

type JsonScalar = str | int | float | bool | None

_PER_MILLION: Final = Decimal(1_000_000)
_PER_THOUSAND: Final = Decimal(1_000)
_ZERO: Final = Decimal(0)

_logger: Final = logging.getLogger("blogwriter.ai.ledger")
register_log_fields("spend_usd", "threshold_usd", "median_usd", "day")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


# --------------------------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------------------------


class ModelPrice(BaseModel):
    """USD prices of one model. Token prices are per 1M tokens; ``image`` is per image.

    ``cached_input`` and ``cache_write`` default to the input price when unset (OpenAI writes
    its prompt cache at no extra charge; Anthropic 1 h cache writes cost more).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: Decimal = Field(ge=0)
    output: Decimal = Field(default=_ZERO, ge=0)
    cached_input: Decimal | None = Field(default=None, ge=0)
    cache_write: Decimal | None = Field(default=None, ge=0)
    image: Decimal = Field(default=_ZERO, ge=0)


class UnknownPriceError(LookupError):
    """No price is configured for a model."""


class PriceTable:
    """Prices per model family plus the ``web_search`` price (per 1K requests)."""

    def __init__(self, models: Mapping[str, ModelPrice], *, web_search_per_1k: Decimal) -> None:
        if web_search_per_1k < 0:
            raise ValueError("web_search_per_1k must not be negative")
        self._models: Mapping[str, ModelPrice] = MappingProxyType(dict(models))
        self._web_search_per_1k = web_search_per_1k

    @property
    def models(self) -> Mapping[str, ModelPrice]:
        return self._models

    @property
    def web_search_per_1k(self) -> Decimal:
        return self._web_search_per_1k

    def __contains__(self, model: object) -> bool:
        return model in self._models

    def price(self, model: str) -> ModelPrice:
        try:
            return self._models[model]
        except KeyError:
            raise UnknownPriceError(f"no price configured for model {model!r}") from None

    def cost(self, model: str, usage: Usage) -> Decimal:
        """The USD cost of one call of ``model`` (exact; never rounded)."""
        price = self.price(model)
        cached_rate = price.cached_input if price.cached_input is not None else price.input
        write_rate = price.cache_write if price.cache_write is not None else price.input
        tokens = (
            usage.uncached_input * price.input
            + usage.cached_input * cached_rate
            + usage.cache_write * write_rate
            + usage.output * price.output
        ) / _PER_MILLION
        searches = usage.web_search_requests * self._web_search_per_1k / _PER_THOUSAND
        return tokens + searches + usage.images * price.image


# --------------------------------------------------------------------------------------------
# Records and sinks
# --------------------------------------------------------------------------------------------


def _new_record_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageRecord:
    """One model call: what it used and what it cost.

    ``model`` is the model family (the price key), ``usage`` holds the token, web search and
    image counts, ``at`` is when the call ended (UTC). ``record_id`` makes writes idempotent.
    Records hold ids and counts only, never prompt or completion text.
    """

    task: Task
    model: str
    deployment: str
    provider: ProviderName
    usage: Usage
    cost_usd: Decimal
    at: datetime
    origin: Origin
    outcome: CallOutcome
    job_id: str | None = None
    response_id: str | None = None
    record_id: str = field(default_factory=_new_record_id)

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _require_aware(self.at, "at").astimezone(UTC))
        if not self.model.strip() or not self.deployment.strip():
            raise ValueError("model and deployment must be non-empty")
        if self.cost_usd < 0:
            raise ValueError("cost_usd must not be negative")
        if not self.record_id:
            raise ValueError("record_id must be non-empty")

    @property
    def web_searches(self) -> int:
        return self.usage.web_search_requests

    @property
    def images(self) -> int:
        return self.usage.images

    @property
    def month(self) -> str:
        """``YYYY-MM`` in UTC: the partition key of the ``usage`` container."""
        return f"{self.at:%Y-%m}"

    def to_document(self) -> dict[str, JsonScalar]:
        """The record as a ``usage`` container document (camelCase, JSON scalars only).

        ``at`` is fixed-width UTC with microseconds (``2026-11-26T15:00:00.000000Z``, the store's
        timestamp format), so string order is time order and range queries on it are exact.
        """
        return {
            "id": self.record_id,
            "month": self.month,
            "at": self.at.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z",
            "task": self.task.value,
            "model": self.model,
            "deployment": self.deployment,
            "provider": self.provider,
            "origin": self.origin,
            "outcome": self.outcome,
            "jobId": self.job_id,
            "responseId": self.response_id,
            "inputTokens": self.usage.input,
            "cachedInputTokens": self.usage.cached_input,
            "cacheWriteTokens": self.usage.cache_write,
            "outputTokens": self.usage.output,
            "reasoningTokens": self.usage.reasoning,
            "webSearches": self.usage.web_search_requests,
            "images": self.usage.images,
            "costUsd": float(self.cost_usd),
        }


class LedgerSink(Protocol):
    """Where usage records go. Implementations: :class:`MemoryLedger`, a Cosmos sink (later)."""

    async def record(self, record: UsageRecord) -> None:
        """Store one record. Writing the same ``record_id`` twice must not double count."""
        ...

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        """Total ``cost_usd`` of the records with ``start <= at < end``."""
        ...


class MemoryLedger:
    """An in-memory sink: records live only as long as the process (tests and local runs)."""

    def __init__(self) -> None:
        self._records: dict[str, UsageRecord] = {}

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        return tuple(self._records.values())

    async def record(self, record: UsageRecord) -> None:
        self._records.setdefault(record.record_id, record)

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        _require_aware(start, "start")
        _require_aware(end, "end")
        return sum(
            (record.cost_usd for record in self._records.values() if start <= record.at < end),
            _ZERO,
        )


# --------------------------------------------------------------------------------------------
# Brake
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrakeStatus:
    """Today's spend against the brake threshold (``day`` is local to the brake's time zone)."""

    day: date
    spend_usd: Decimal
    trailing_median_usd: Decimal
    threshold_usd: Decimal

    @property
    def engaged(self) -> bool:
        return self.spend_usd > self.threshold_usd


class BrakeEngaged(Exception):
    """Background AI work is paused: today's spend is over the threshold, or unknown.

    ``status`` is ``None`` when the ledger could not be read (``reason="ledger_unavailable"``).
    """

    def __init__(
        self,
        status: BrakeStatus | None,
        *,
        reason: Literal["over_budget", "ledger_unavailable"] = "over_budget",
    ) -> None:
        self.status = status
        self.reason: Literal["over_budget", "ledger_unavailable"] = reason
        if status is None:
            message = "budget brake engaged: the spend ledger is unavailable"
        else:
            message = (
                f"budget brake engaged: spent ${status.spend_usd} on {status.day}, "
                f"threshold ${status.threshold_usd}"
            )
        super().__init__(message)


class BudgetBrake:
    """The soft daily brake on background AI work (see the module docstring)."""

    def __init__(
        self,
        sink: LedgerSink,
        *,
        daily_cap_usd: Decimal,
        timezone: tzinfo,
        multiplier: Decimal = Decimal(3),
        window_days: int = 7,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if daily_cap_usd < 0:
            raise ValueError("daily_cap_usd must not be negative")
        if multiplier < 0:
            raise ValueError("multiplier must not be negative")
        if window_days < 1:
            raise ValueError("window_days must be at least 1")
        self._sink = sink
        self._cap = daily_cap_usd
        self._multiplier = multiplier
        self._window = window_days
        self._tz = timezone
        self._now = now
        self._median: tuple[date, Decimal] | None = None
        self._alerted: date | None = None
        self._unrecorded: tuple[date, Decimal] | None = None

    def note_unrecorded(self, record: UsageRecord) -> None:
        """Count a call whose ledger write failed, so the brake still sees what it cost.

        The call ran and was paid for; only its record is missing. The amount is kept in memory
        for the record's day (in the brake's time zone) and added to that day's spend while it
        is today. A write that failed but was stored after all is counted twice, which errs
        toward pausing background work, never toward letting it run.
        """
        day = record.at.astimezone(self._tz).date()
        current = self._unrecorded
        if current is None or day > current[0]:
            self._unrecorded = (day, record.cost_usd)
        elif day == current[0]:
            self._unrecorded = (day, current[1] + record.cost_usd)

    def _day_bounds(self, day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time.min, tzinfo=self._tz)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=self._tz)
        return start, end

    async def _spend_on(self, day: date) -> Decimal:
        start, end = self._day_bounds(day)
        return await self._sink.spend_between(start, end)

    async def _trailing_median(self, today: date) -> Decimal:
        cached = self._median
        if cached is not None and cached[0] == today:
            return cached[1]
        days = [today - timedelta(days=offset) for offset in range(1, self._window + 1)]
        median = statistics.median([await self._spend_on(day) for day in days])
        self._median = (today, median)
        return median

    async def status(self) -> BrakeStatus:
        """Today's spend and threshold (reads the sink; the trailing median is cached daily)."""
        today = _require_aware(self._now(), "now").astimezone(self._tz).date()
        spend = await self._spend_on(today)
        unrecorded = self._unrecorded
        if unrecorded is not None and unrecorded[0] == today:
            spend += unrecorded[1]
        median = await self._trailing_median(today)
        threshold = max(self._cap, self._multiplier * median)
        return BrakeStatus(today, spend, median, threshold)

    async def check(self, origin: Origin) -> BrakeStatus | None:
        """Raise :class:`BrakeEngaged` if background work must pause.

        Owner-initiated calls return ``None`` at once: they are never braked.
        """
        if origin == "owner":
            return None
        try:
            status = await self.status()
        except Exception as exc:
            _logger.error("ai_brake_ledger_unavailable", extra=describe_exception(exc))
            raise BrakeEngaged(None, reason="ledger_unavailable") from exc
        if status.engaged:
            if self._alerted != status.day:
                self._alerted = status.day
                _logger.warning(
                    "ai_brake_engaged",
                    extra={
                        "day": status.day,
                        "spend_usd": float(status.spend_usd),
                        "threshold_usd": float(status.threshold_usd),
                        "median_usd": float(status.trailing_median_usd),
                    },
                )
            raise BrakeEngaged(status)
        return status
