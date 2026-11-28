"""Rates per 1K impressions against the owner's trailing median, by dimension.

Every post that is live on LinkedIn (``status == posted``) and went live inside the window
contributes the numbers of **one** snapshot (:func:`usable_snapshot`): its latest ``d7`` snapshot
with impressions, else its latest snapshot with impressions. A post without impressions adds
nothing (a rate needs a denominator).

- A post's rate for a metric is ``metric / impressions * 1000``; ``engagement`` is the sum of the
  metrics the owner entered, per 1K impressions. A metric the owner left empty has no rate (it is
  left out of that metric's median, never counted as zero).
- The owner's median (``ownerMedian``) is the median of the per-post rates over the window.
- A row is one value of one dimension (``pillar=agents``, ``format=carousel``...): the median of
  its posts' rates and ``vsMedian`` = ``row / ownerMedian - 1`` per metric (``0.25`` = 25 % above
  the owner's median; ``None`` when either side is unknown or the median is 0).

Dimensions (snake_case values, the same as ``InsightSuggestion.dimension``): ``pillar``,
``format``, ``hook_type`` (``none`` for posts without one), ``lane``, ``go_live_mode`` and
``owner_signal`` (``with`` when the post draws on a story or POV entry, directly or through the
article it was derived from; else ``without``).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import AliasChoices, Field

from blogwriter.db import Filter, NotFound, OrderBy, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import InvalidStoredDocument, find_docs, read_doc
from blogwriter.domain.posts import MetricsSnapshot, Post
from blogwriter.domain.states import PostStatus

__all__ = [
    "DEFAULT_WEEKS",
    "DIMENSIONS",
    "ENGAGEMENT",
    "MAX_POSTS",
    "MAX_WEEKS",
    "METRICS",
    "NO_HOOK",
    "RATE_KEYS",
    "WITHOUT_SIGNAL",
    "WITH_SIGNAL",
    "InsightDimension",
    "InsightsOverview",
    "InsightsRow",
    "Metric",
    "MetricRates",
    "PostMetrics",
    "RateKey",
    "build_overview",
    "dimension_values",
    "load_post_metrics",
    "median_rates",
    "overview_of",
    "parse_dimension",
    "post_metrics",
    "relative_to",
    "snapshot_rates",
    "usable_snapshot",
]

type InsightDimension = Literal[
    "pillar", "format", "hook_type", "lane", "go_live_mode", "owner_signal"
]
DIMENSIONS: Final[tuple[InsightDimension, ...]] = (
    "pillar",
    "format",
    "hook_type",
    "lane",
    "go_live_mode",
    "owner_signal",
)
_DIMENSION_ALIASES: Final[Mapping[str, InsightDimension]] = {
    "hookType": "hook_type",
    "goLiveMode": "go_live_mode",
    "ownerSignal": "owner_signal",
}

type Metric = Literal["reactions", "comments", "reposts", "saves", "sends"]
METRICS: Final[tuple[Metric, ...]] = ("reactions", "comments", "reposts", "saves", "sends")
type RateKey = Literal["reactions", "comments", "reposts", "saves", "sends", "engagement"]
ENGAGEMENT: Final = "engagement"
RATE_KEYS: Final[tuple[RateKey, ...]] = (*METRICS, "engagement")

DEFAULT_WEEKS: Final = 12
MAX_WEEKS: Final = 52
MAX_POSTS: Final = 500
"""Posts read per window (5 a week for a year is 260)."""
NO_HOOK: Final = "none"
WITH_SIGNAL: Final = "with"
WITHOUT_SIGNAL: Final = "without"
_RATE_DIGITS: Final = 2
_RELATIVE_DIGITS: Final = 3

type RateSet = Mapping[RateKey, float | None]


def parse_dimension(value: str) -> InsightDimension:
    """A dimension name in snake_case (``hook_type``) or camelCase (``hookType``)."""
    alias = _DIMENSION_ALIASES.get(value)
    if alias is not None:
        return alias
    if value in DIMENSIONS:
        return value
    raise ValueError(f"unknown dimension {value!r}")


# --------------------------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------------------------


class MetricRates(ApiModel):
    """Rates per 1K impressions (or, in ``vsMedian``, the relative difference to the median)."""

    reactions: float | None = None
    comments: float | None = None
    reposts: float | None = None
    saves: float | None = None
    sends: float | None = None
    engagement: float | None = None
    """All entered metrics together (reactions + comments + reposts + saves + sends)."""

    @classmethod
    def of(cls, values: RateSet, *, digits: int) -> MetricRates:
        return cls.model_validate({key: _round(values.get(key), digits) for key in RATE_KEYS})


class InsightsRow(ApiModel):
    dimension: InsightDimension
    value: str
    posts: int
    """Posts with impressions behind this row."""
    rates_per_1k: MetricRates = Field(
        serialization_alias="ratesPer1k",
        validation_alias=AliasChoices("ratesPer1k", "rates_per_1k"),
    )
    vs_median: MetricRates
    """``row / ownerMedian - 1`` per metric (``0.25``: 25 % above the owner's median)."""


class InsightsOverview(ApiModel):
    """``GET /api/analytics/overview``."""

    weeks: int
    since: str
    """Start of the window (posts that went live at or after it)."""
    posts: int
    """Posts with impressions in the window."""
    owner_median: MetricRates
    rows: list[InsightsRow]


# --------------------------------------------------------------------------------------------
# Per-post numbers
# --------------------------------------------------------------------------------------------


def usable_snapshot(post: Post) -> MetricsSnapshot | None:
    """The snapshot whose numbers count for ``post``: the latest ``d7`` snapshot with
    impressions, else the latest snapshot with impressions, else ``None``."""
    usable = [
        snapshot
        for snapshot in post.snapshots
        if snapshot.impressions is not None and snapshot.impressions > 0
    ]
    if not usable:
        return None
    week = [snapshot for snapshot in usable if snapshot.offset == "d7"]
    return max(week or usable, key=lambda snapshot: (snapshot.taken_at, snapshot.id))


def snapshot_rates(snapshot: MetricsSnapshot) -> dict[RateKey, float]:
    """Per-1K rates of the metrics the snapshot has (empty without impressions)."""
    impressions = snapshot.impressions
    if impressions is None or impressions <= 0:
        return {}
    rates: dict[RateKey, float] = {}
    entered: list[int] = []
    for metric in METRICS:
        value: int | None = getattr(snapshot, metric)
        if value is None:
            continue
        rates[metric] = value * 1000 / impressions
        entered.append(value)
    if entered:
        rates["engagement"] = sum(entered) * 1000 / impressions
    return rates


def dimension_values(post: Post, *, article_signal: bool = False) -> dict[InsightDimension, str]:
    """The post's value in every dimension (``article_signal``: its article has owner signal)."""
    has_signal = bool(post.story_ids or post.pov_ids) or article_signal
    return {
        "pillar": post.pillar,
        "format": post.format,
        "hook_type": post.hook_type.value if post.hook_type is not None else NO_HOOK,
        "lane": post.lane,
        "go_live_mode": post.go_live_mode,
        "owner_signal": WITH_SIGNAL if has_signal else WITHOUT_SIGNAL,
    }


@dataclass(frozen=True, slots=True)
class PostMetrics:
    """One measured post: its counted impressions, per-1K rates and dimension values."""

    post_id: str
    posted_at: str
    impressions: int
    rates: Mapping[RateKey, float]
    values: Mapping[InsightDimension, str]


def post_metrics(post: Post, *, article_signal: bool = False) -> PostMetrics | None:
    """``post``'s numbers, or ``None`` when it has no snapshot with impressions."""
    snapshot = usable_snapshot(post)
    if snapshot is None or snapshot.impressions is None:
        return None
    return PostMetrics(
        post_id=post.id,
        posted_at=post.posted_at or "",
        impressions=snapshot.impressions,
        rates=snapshot_rates(snapshot),
        values=dimension_values(post, article_signal=article_signal),
    )


# --------------------------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def median_rates(items: Iterable[PostMetrics]) -> dict[RateKey, float | None]:
    """The median per-post rate of every key (``None`` where no post has that metric)."""
    collected: dict[RateKey, list[float]] = {key: [] for key in RATE_KEYS}
    for item in items:
        for key, value in item.rates.items():
            collected[key].append(value)
    return {
        key: (statistics.median(values) if values else None) for key, values in collected.items()
    }


def relative_to(values: RateSet, baseline: RateSet) -> dict[RateKey, float | None]:
    """``values / baseline - 1`` per key (``None`` when either is unknown or the baseline is 0)."""
    out: dict[RateKey, float | None] = {}
    for key in RATE_KEYS:
        value = values.get(key)
        base = baseline.get(key)
        out[key] = None if value is None or base is None or base == 0 else value / base - 1
    return out


def overview_of(
    items: Sequence[PostMetrics],
    *,
    weeks: int,
    since: str,
    dimensions: Sequence[InsightDimension] = DIMENSIONS,
) -> InsightsOverview:
    """The overview of measured posts: the owner's median and one row per dimension value
    (rows of a dimension ordered by post count, then value)."""
    owner = median_rates(items)
    rows: list[InsightsRow] = []
    for dimension in dimensions:
        groups: dict[str, list[PostMetrics]] = {}
        for item in items:
            groups.setdefault(item.values[dimension], []).append(item)
        for value, members in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            medians = median_rates(members)
            rows.append(
                InsightsRow(
                    dimension=dimension,
                    value=value,
                    posts=len(members),
                    rates_per_1k=MetricRates.of(medians, digits=_RATE_DIGITS),
                    vs_median=MetricRates.of(relative_to(medians, owner), digits=_RELATIVE_DIGITS),
                )
            )
    return InsightsOverview(
        weeks=weeks,
        since=since,
        posts=len(items),
        owner_median=MetricRates.of(owner, digits=_RATE_DIGITS),
        rows=rows,
    )


# --------------------------------------------------------------------------------------------
# Reading the window
# --------------------------------------------------------------------------------------------


async def _article_signals(store: Store, article_ids: Iterable[str]) -> dict[str, bool]:
    """Whether each article draws on owner material (unreadable or missing: no)."""
    out: dict[str, bool] = {}
    for article_id in sorted(set(article_ids)):
        try:
            article = (await read_doc(store, Article, article_id)).doc
        except (NotFound, InvalidStoredDocument):
            out[article_id] = False
            continue
        out[article_id] = bool(article.owner_signals)
    return out


async def load_post_metrics(
    store: Store, *, since: str, until: str, limit: int = MAX_POSTS
) -> list[PostMetrics]:
    """The measured posts that went live in ``[since, until]`` (newest first)."""
    found = await find_docs(
        store,
        Post,
        [
            Filter("status", "==", PostStatus.POSTED.value),
            Filter("postedAt", ">=", since),
            Filter("postedAt", "<=", until),
        ],
        order_by=OrderBy("postedAt", descending=True),
        limit=limit,
    )
    posts = [stored.doc for stored in found]
    needs_article = {
        post.article_id
        for post in posts
        if post.article_id is not None and not (post.story_ids or post.pov_ids)
    }
    signals = await _article_signals(store, needs_article)
    out: list[PostMetrics] = []
    for post in posts:
        from_article = signals.get(post.article_id, False) if post.article_id else False
        measured = post_metrics(post, article_signal=from_article)
        if measured is not None:
            out.append(measured)
    return out


async def build_overview(
    store: Store,
    *,
    now: datetime,
    weeks: int = DEFAULT_WEEKS,
    dimension: InsightDimension | None = None,
) -> InsightsOverview:
    """``GET /api/analytics/overview``: the last ``weeks`` weeks up to ``now``."""
    span = max(1, min(weeks, MAX_WEEKS))
    since = to_iso(now - timedelta(weeks=span))
    items = await load_post_metrics(store, since=since, until=to_iso(now))
    dimensions = DIMENSIONS if dimension is None else (dimension,)
    return overview_of(items, weeks=span, since=since, dimensions=dimensions)
