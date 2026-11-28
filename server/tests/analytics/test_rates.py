"""Rates per 1K impressions and medians on fixed data (golden), snapshot choice, dimensions and
the overview route."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from blogwriter.analytics.rates import (
    DIMENSIONS,
    PostMetrics,
    RateKey,
    build_overview,
    dimension_values,
    median_rates,
    overview_of,
    parse_dimension,
    relative_to,
    snapshot_rates,
    usable_snapshot,
)
from blogwriter.analytics.testing import TEST_NOW, posted_post, snapshot
from blogwriter.domain.articles import Article, OwnerSignalRef
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.posts import HookType, Post
from blogwriter.domain.testing import FakeServices, sample_document

GOLDENS = Path(__file__).parent / "goldens"
STORY = "story-0000000000000000000000000001"
POV = "pov-0000000000000000000000000000001"


async def seed_posts(services: FakeServices) -> dict[str, str]:
    """Five measured posts in the window, plus three that must not count."""
    article = sample_document(
        Article,
        owner_signals=[OwnerSignalRef(kind="story", id=STORY)],
    )
    await create_doc(services.store, article)
    p1 = await posted_post(
        services,
        posted_at="2026-11-02T15:00:00.000000Z",
        pillar="agents",
        format="carousel",
        lane="derivative",
        hook_type=HookType.RESULT_METRIC,
        article_id=article.id,
        snapshots=[
            snapshot(
                taken_at="2026-11-09T15:00:00.000000Z",
                impressions=2000,
                reactions=40,
                comments=10,
                reposts=2,
                saves=6,
                sends=2,
            )
        ],
    )
    p2 = await posted_post(
        services,
        posted_at="2026-11-04T15:00:00.000000Z",
        pillar="agents",
        format="text",
        lane="native",
        hook_type=HookType.MISTAKE_LESSON,
        go_live_mode="kit",
        story_ids=[STORY],
        snapshots=[
            snapshot(
                taken_at="2026-11-11T15:00:00.000000Z",
                impressions=1000,
                reactions=10,
                comments=4,
                reposts=0,
                saves=1,
                sends=0,
            )
        ],
    )
    p3 = await posted_post(
        services,
        posted_at="2026-11-10T15:00:00.000000Z",
        pillar="identity",
        format="carousel",
        lane="native",
        snapshots=[
            snapshot(
                taken_at="2026-11-12T15:00:00.000000Z",
                offset="h48",
                snapshot_id="form-h48",
                impressions=4000,
                reactions=48,
                comments=8,
                reposts=4,
                saves=8,
                sends=4,
            )
        ],
    )
    p4 = await posted_post(
        services,
        posted_at="2026-11-12T15:00:00.000000Z",
        pillar="identity",
        format="image_card",
        lane="quick_take",
        hook_type=HookType.QUESTION,
        snapshots=[
            snapshot(
                taken_at="2026-11-19T15:00:00.000000Z", impressions=500, reactions=5, comments=1
            )
        ],
    )
    p5 = await posted_post(
        services,
        posted_at="2026-11-16T15:00:00.000000Z",
        pillar="governance",
        format="text",
        lane="native",
        hook_type=HookType.RESULT_METRIC,
        pov_ids=[POV],
        snapshots=[
            snapshot(
                taken_at="2026-11-18T15:00:00.000000Z",
                offset="h48",
                snapshot_id="form-h48",
                impressions=800,
                reactions=8,
            ),
            snapshot(
                taken_at="2026-11-23T15:00:00.000000Z",
                impressions=1500,
                reactions=30,
                comments=3,
                reposts=3,
                saves=3,
                sends=3,
            ),
        ],
    )
    # Not counted: no snapshot, out of the window, not live.
    await posted_post(services, posted_at="2026-11-20T15:00:00.000000Z", pillar="agents")
    await posted_post(
        services,
        posted_at="2026-08-20T15:00:00.000000Z",
        snapshots=[snapshot(taken_at="2026-08-27T15:00:00.000000Z", impressions=10, reactions=9)],
    )
    await posted_post(
        services,
        posted_at="2026-11-20T15:00:00.000000Z",
        status="approved",
        snapshots=[snapshot(taken_at="2026-11-21T15:00:00.000000Z", impressions=10, reactions=9)],
    )
    return {"p1": p1.id, "p2": p2.id, "p3": p3.id, "p4": p4.id, "p5": p5.id}


# --------------------------------------------------------------------------------------------
# Pure pieces
# --------------------------------------------------------------------------------------------


def test_the_snapshot_that_counts_prefers_the_latest_seven_day_one() -> None:
    early = snapshot(taken_at="2026-11-03T00:00:00.000000Z", offset="h48", impressions=100)
    week = snapshot(
        taken_at="2026-11-08T00:00:00.000000Z", snapshot_id="d7-a", impressions=500, reactions=5
    )
    later = snapshot(
        taken_at="2026-11-20T00:00:00.000000Z", offset="other", snapshot_id="x", impressions=900
    )
    empty = snapshot(taken_at="2026-11-21T00:00:00.000000Z", snapshot_id="y", impressions=0)
    post = sample_document(Post, snapshots=[early, week, later, empty])
    assert usable_snapshot(post) == week
    without_week = sample_document(Post, snapshots=[early, later, empty])
    assert usable_snapshot(without_week) == later
    assert usable_snapshot(sample_document(Post, snapshots=[empty])) is None


def test_rates_are_per_thousand_impressions_and_skip_empty_metrics() -> None:
    rates = snapshot_rates(
        snapshot(taken_at="2026-11-03T00:00:00.000000Z", impressions=2500, reactions=50, saves=5)
    )
    assert rates == {"reactions": 20.0, "saves": 2.0, "engagement": 22.0}
    assert snapshot_rates(snapshot(taken_at="2026-11-03T00:00:00.000000Z", impressions=None)) == {}


def test_dimension_values_and_owner_signal() -> None:
    bare = sample_document(Post, lane="native", pillar="agents", format="text")
    assert dimension_values(bare) == {
        "pillar": "agents",
        "format": "text",
        "hook_type": "none",
        "lane": "native",
        "go_live_mode": "one_click",
        "owner_signal": "without",
    }
    assert dimension_values(bare, article_signal=True)["owner_signal"] == "with"
    told = sample_document(Post, pov_ids=[POV], hook_type="story_in_motion")
    assert dimension_values(told)["owner_signal"] == "with"
    assert dimension_values(told)["hook_type"] == "story_in_motion"


def test_medians_and_relative_differences() -> None:
    def item(rates: Mapping[RateKey, float]) -> PostMetrics:
        return PostMetrics(
            post_id="p",
            posted_at="2026-11-01T00:00:00.000000Z",
            impressions=1,
            rates=rates,
            values=dict.fromkeys(DIMENSIONS, "x"),
        )

    medians = median_rates(
        [item({"reactions": 10, "saves": 1}), item({"reactions": 20}), item({"reactions": 40})]
    )
    assert medians["reactions"] == 20
    assert medians["saves"] == 1
    assert medians["comments"] is None
    relative = relative_to({"reactions": 25.0, "saves": 0.0}, {"reactions": 20.0, "saves": 0.0})
    assert relative["reactions"] == pytest.approx(0.25)
    assert relative["saves"] is None  # a zero median has no relative difference
    assert relative["comments"] is None


def test_dimension_names_accept_both_spellings() -> None:
    assert parse_dimension("hookType") == "hook_type"
    assert parse_dimension("go_live_mode") == "go_live_mode"
    assert parse_dimension("ownerSignal") == "owner_signal"
    with pytest.raises(ValueError, match="unknown dimension"):
        parse_dimension("weekday")


def test_an_empty_window_has_no_medians() -> None:
    overview = overview_of([], weeks=12, since="2026-09-03T17:00:00.000000Z")
    assert overview.posts == 0
    assert overview.rows == []
    assert overview.owner_median.reactions is None


# --------------------------------------------------------------------------------------------
# Fixed data (golden)
# --------------------------------------------------------------------------------------------


async def test_rates_and_medians_on_fixed_data(services: FakeServices) -> None:
    await seed_posts(services)
    overview = await build_overview(services.store, now=TEST_NOW, weeks=12)
    assert overview.posts == 5
    # By hand: per-post reactions/1K are 20, 10, 12, 10, 20 -> median 12; engagement/1K are
    # 30, 15, 18, 12, 28 -> 18; reposts 1, 0, 1, 2 (one post has none) -> 1.
    owner = overview.owner_median
    assert (owner.reactions, owner.comments, owner.reposts, owner.saves, owner.sends) == (
        12.0,
        2.0,
        1.0,
        2.0,
        1.0,
    )
    assert owner.engagement == 18.0
    agents = next(
        row for row in overview.rows if (row.dimension, row.value) == ("pillar", "agents")
    )
    assert agents.posts == 2
    assert agents.rates_per_1k.reactions == 15.0
    assert agents.vs_median.reactions == 0.25
    assert agents.vs_median.comments == 1.25
    assert agents.vs_median.reposts == -0.5
    signal = {row.value: row.posts for row in overview.rows if row.dimension == "owner_signal"}
    assert signal == {"with": 3, "without": 2}  # p1 through its article, p2 story, p5 POV
    rendered = overview.model_dump(mode="json", by_alias=True)
    golden = json.loads((GOLDENS / "overview.json").read_text(encoding="utf-8"))
    assert rendered == golden


async def test_one_dimension_only(services: FakeServices) -> None:
    await seed_posts(services)
    overview = await build_overview(services.store, now=TEST_NOW, weeks=12, dimension="hook_type")
    assert {row.dimension for row in overview.rows} == {"hook_type"}
    assert [(row.value, row.posts) for row in overview.rows] == [
        ("result_metric", 2),
        ("mistake_lesson", 1),
        ("none", 1),
        ("question", 1),
    ]


# --------------------------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------------------------


async def test_overview_route(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await seed_posts(services)
    response = await client.get(
        "/api/analytics/overview",
        params={"weeks": 12, "dimension": "goLiveMode"},
        headers=read_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["posts"] == 5
    assert set(body["ownerMedian"]) == {
        "reactions",
        "comments",
        "reposts",
        "saves",
        "sends",
        "engagement",
    }
    assert [(row["value"], row["posts"]) for row in body["rows"]] == [("one_click", 4), ("kit", 1)]
    assert set(body["rows"][0]) == {"dimension", "value", "posts", "ratesPer1k", "vsMedian"}
    short = await client.get("/api/analytics/overview", params={"weeks": 1}, headers=read_headers)
    assert short.json()["posts"] == 0
    bad = await client.get(
        "/api/analytics/overview", params={"dimension": "weekday"}, headers=read_headers
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "unknown_dimension"
    too_long = await client.get(
        "/api/analytics/overview", params={"weeks": 53}, headers=read_headers
    )
    assert too_long.status_code == 422
