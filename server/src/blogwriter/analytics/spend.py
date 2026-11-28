"""The spend view: daily AI spend from the ``usage`` ledger, the brake, fallback and review-only
rates (read only; the ai package writes the ledger).

- **Days** are calendar days in the brake's time zone (``BW_AI_BRAKE_TIMEZONE``), the same days
  the brake counts. A day's ``costUsd`` is the sum of ``costUsd`` over the usage records with
  ``start <= at < end``, exactly what the ledger's ``spend_between`` adds up (records are summed
  as ``Decimal(str(costUsd))``, like the Cosmos sink). Days without records are 0.
- **trailingMedian / brakeThreshold**: the brake's numbers for today: the median daily spend of
  the ``BW_AI_BRAKE_WINDOW_DAYS`` days before today, and ``max(daily cap, multiplier x median)``.
- **fallbackRate**: of the completed model calls in the window, the share served by a model
  other than the primary model the routing table names for their task (calls of a task without a
  route are left out). ``None`` without calls.
- **reviewOnlyRate**: of the flagship articles and posts an AI step wrote in the window
  (a ``servedBy`` record dated in it), the share that is review-only. ``None`` without any.

Usage records carry ids, counts and costs only; nothing here reads or returns content.
"""

from __future__ import annotations

import functools
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

from blogwriter.ai import AISettings
from blogwriter.db import Filter, OrderBy, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import Article
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import find_docs
from blogwriter.domain.posts import Post

__all__ = [
    "DEFAULT_DAYS",
    "MAX_DAYS",
    "USAGE_CONTAINER",
    "BrakeParams",
    "SpendDay",
    "SpendView",
    "UsageEntry",
    "brake_params",
    "build_spend",
    "fallback_rate",
    "load_usage",
    "review_only_rate",
    "spend_view",
    "usage_entries",
]

USAGE_CONTAINER: Final = "usage"
DEFAULT_DAYS: Final = 30
MAX_DAYS: Final = 90
MAX_REVIEWED_DOCS: Final = 200
_ZERO: Final = Decimal(0)
_MONEY_DIGITS: Final = 6
_RATE_DIGITS: Final = 4


@dataclass(frozen=True, slots=True)
class BrakeParams:
    """The brake settings and each task's primary model (from ``AISettings``)."""

    daily_cap_usd: Decimal
    multiplier: Decimal
    window_days: int
    timezone: str
    primary_models: Mapping[str, str]
    """Task value -> the primary model family of its route."""

    @classmethod
    def from_settings(cls, settings: AISettings) -> BrakeParams:
        routes = settings.routes()
        return cls(
            daily_cap_usd=settings.brake_daily_cap_usd,
            multiplier=settings.brake_multiplier,
            window_days=settings.brake_window_days,
            timezone=settings.brake_timezone,
            primary_models={task.value: route.primary.model for task, route in routes.items()},
        )


@functools.cache
def brake_params() -> BrakeParams:
    """The process's brake parameters (``BW_AI_*``, read once)."""
    return BrakeParams.from_settings(AISettings())


class SpendDay(ApiModel):
    date: str
    """Local calendar date (``YYYY-MM-DD``) in the brake's time zone."""
    cost_usd: float
    by_task: dict[str, float]
    by_model: dict[str, float]


class SpendView(ApiModel):
    """``GET /api/analytics/spend``."""

    days: list[SpendDay]
    """Oldest first, one entry per day (zero days included)."""
    total: float
    trailing_median: float
    brake_threshold: float
    fallback_rate: float | None
    review_only_rate: float | None
    timezone: str


@dataclass(frozen=True, slots=True)
class UsageEntry:
    """One usage record as the view needs it."""

    day: date
    task: str
    model: str
    outcome: str
    cost: Decimal


def _cost(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def usage_entries(bodies: Iterable[Mapping[str, Any]], zone: ZoneInfo) -> list[UsageEntry]:
    """Usage documents as entries (a document without a valid ``at`` or ``costUsd`` is skipped,
    like the ledger's sum skips a non-numeric cost)."""
    out: list[UsageEntry] = []
    for body in bodies:
        at: object = body.get("at")
        cost = _cost(body.get("costUsd"))
        if not isinstance(at, str) or cost is None:
            continue
        try:
            moment = parse_iso(at)
        except ValueError:
            continue
        task: object = body.get("task")
        model: object = body.get("model")
        outcome: object = body.get("outcome")
        out.append(
            UsageEntry(
                day=moment.astimezone(zone).date(),
                task=task if isinstance(task, str) else "unknown",
                model=model if isinstance(model, str) else "unknown",
                outcome=outcome if isinstance(outcome, str) else "unknown",
                cost=cost,
            )
        )
    return out


def _money(value: Decimal) -> float:
    return round(float(value), _MONEY_DIGITS)


def fallback_rate(entries: Iterable[UsageEntry], primary_models: Mapping[str, str]) -> float | None:
    """The share of completed calls a non-primary model served (``None`` without calls)."""
    calls = [entry for entry in entries if entry.outcome == "completed"]
    routed = [entry for entry in calls if entry.task in primary_models]
    if not routed:
        return None
    fallbacks = sum(1 for entry in routed if entry.model != primary_models[entry.task])
    return round(fallbacks / len(routed), _RATE_DIGITS)


def spend_view(
    entries: Sequence[UsageEntry],
    *,
    today: date,
    days: int,
    params: BrakeParams,
    review_only: float | None,
) -> SpendView:
    """The view of ``days`` days ending ``today`` (pure; ``entries`` may cover more days)."""
    span = max(1, min(days, MAX_DAYS))
    first = today - timedelta(days=span - 1)
    totals: dict[date, Decimal] = {}
    by_task: dict[date, dict[str, Decimal]] = {}
    by_model: dict[date, dict[str, Decimal]] = {}
    for entry in entries:
        totals[entry.day] = totals.get(entry.day, _ZERO) + entry.cost
        tasks = by_task.setdefault(entry.day, {})
        tasks[entry.task] = tasks.get(entry.task, _ZERO) + entry.cost
        models = by_model.setdefault(entry.day, {})
        models[entry.model] = models.get(entry.model, _ZERO) + entry.cost
    shown = [first + timedelta(days=offset) for offset in range(span)]
    rows = [
        SpendDay(
            date=day.isoformat(),
            cost_usd=_money(totals.get(day, _ZERO)),
            by_task={key: _money(value) for key, value in sorted(by_task.get(day, {}).items())},
            by_model={key: _money(value) for key, value in sorted(by_model.get(day, {}).items())},
        )
        for day in shown
    ]
    window = [today - timedelta(days=offset) for offset in range(1, params.window_days + 1)]
    median = statistics.median([totals.get(day, _ZERO) for day in window])
    threshold = max(params.daily_cap_usd, params.multiplier * median)
    in_view = [entry for entry in entries if first <= entry.day <= today]
    return SpendView(
        days=rows,
        total=_money(sum((totals.get(day, _ZERO) for day in shown), _ZERO)),
        trailing_median=_money(median),
        brake_threshold=_money(threshold),
        fallback_rate=fallback_rate(in_view, params.primary_models),
        review_only_rate=review_only,
        timezone=params.timezone,
    )


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


async def load_usage(store: Store, *, start: datetime, end: datetime) -> list[Mapping[str, Any]]:
    """Usage documents with ``start <= at < end`` (across month partitions)."""
    docs = await store.find(
        USAGE_CONTAINER, [Filter("at", ">=", to_iso(start)), Filter("at", "<", to_iso(end))]
    )
    return [doc.body for doc in docs]


def _drafted_since(doc: Article | Post, since: str) -> bool:
    return any(record.at >= since for record in doc.served_by)


async def review_only_rate(store: Store, *, since: str) -> float | None:
    """The share of flagship articles and posts an AI step wrote since ``since`` that are
    review-only (``None`` when there are none)."""
    articles = await find_docs(
        store,
        Article,
        [Filter("purpose", "==", "flagship"), Filter("updatedAt", ">=", since)],
        order_by=OrderBy("updatedAt", descending=True),
        limit=MAX_REVIEWED_DOCS,
    )
    posts = await find_docs(
        store,
        Post,
        [Filter("updatedAt", ">=", since)],
        order_by=OrderBy("updatedAt", descending=True),
        limit=MAX_REVIEWED_DOCS,
    )
    drafted: list[Article | Post] = [
        stored.doc for stored in articles if _drafted_since(stored.doc, since)
    ]
    drafted.extend(stored.doc for stored in posts if _drafted_since(stored.doc, since))
    if not drafted:
        return None
    flagged = sum(1 for doc in drafted if doc.review_only)
    return round(flagged / len(drafted), _RATE_DIGITS)


async def build_spend(
    store: Store, *, now: datetime, days: int = DEFAULT_DAYS, params: BrakeParams
) -> SpendView:
    """``GET /api/analytics/spend``: the last ``days`` local days up to today."""
    zone = ZoneInfo(params.timezone)
    span = max(1, min(days, MAX_DAYS))
    today = now.astimezone(zone).date()
    first = today - timedelta(days=span - 1)
    earliest = min(first, today - timedelta(days=params.window_days))
    bodies = await load_usage(
        store,
        start=_local_midnight(earliest, zone),
        end=_local_midnight(today + timedelta(days=1), zone),
    )
    since = to_iso(_local_midnight(first, zone))
    return spend_view(
        usage_entries(bodies, zone),
        today=today,
        days=span,
        params=params,
        review_only=await review_only_rate(store, since=since),
    )
