"""The weekly scorecard: posts with owner signal, replies, substantive comments, on-pillar
share; the Monday job stores it and closes the approved plan."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from blogwriter.domain.articles import OwnerSignalRef
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.engagement import EngagementRecord
from blogwriter.domain.planner import WeekPlan
from blogwriter.domain.states import ArticleStatus, PlanStatus, PostStatus
from blogwriter.domain.testing import FakeServices, sample_document
from blogwriter.domain.weeks import plan_id
from blogwriter.planner.scorecard import compute_scorecard
from blogwriter.planner.testing import article, post, run_job, save_plan

LAST_WEEK = "2026-11-23"
MONDAY_SEVEN = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)  # Monday 07:00 EST


async def seed_week(services: FakeServices) -> None:
    flagship = await article(
        services,
        status=ArticleStatus.PUBLISHED,
        owner_signals=[OwnerSignalRef(kind="story", id="story-0000000000000000000000000001")],
    )
    plain = await article(services, status=ArticleStatus.PUBLISHED, title="No owner material")
    stamps = [
        "2026-11-24T15:00:00.000000Z",
        "2026-11-25T15:00:00.000000Z",
        "2026-11-26T15:00:00.000000Z",
        "2026-11-27T20:00:00.000000Z",
        "2026-11-29T23:00:00.000000Z",
    ]
    await post(services, status=PostStatus.POSTED, posted_at=stamps[0], story_ids=["story-1"])
    await post(
        services,
        status=PostStatus.POSTED,
        posted_at=stamps[1],
        lane="derivative",
        article_id=flagship.id,
    )
    await post(
        services,
        status=PostStatus.DELETED,
        posted_at=stamps[2],
        lane="derivative",
        article_id=plain.id,
    )
    await post(
        services, status=PostStatus.POSTED, posted_at=stamps[3], lane="quick_take", pillar="cooking"
    )
    await post(services, status=PostStatus.POSTED, posted_at=stamps[4], pillar="identity")
    await post(
        services, status=PostStatus.POSTED, posted_at="2026-11-30T15:00:00.000000Z"
    )  # this week
    for kind, substantive, at in (
        ("reply", True, "2026-11-24T16:00:00.000000Z"),
        ("reply", True, "2026-11-24T17:00:00.000000Z"),
        ("comment", True, "2026-11-25T12:00:00.000000Z"),
        ("comment", False, "2026-11-26T12:00:00.000000Z"),
        ("comment", True, "2026-11-30T13:00:00.000000Z"),  # this week
    ):
        record = sample_document(
            EngagementRecord, item_kind=kind, substantive=substantive, posted_at=at
        )
        await create_doc(services.store, record)


async def test_the_scorecard_of_a_week(services: FakeServices) -> None:
    await seed_week(services)
    card = await compute_scorecard(
        services.store,
        week_start=LAST_WEEK,
        zone="America/New_York",
        pillar_ids=("agents", "identity", "governance"),
        now=MONDAY_SEVEN,
    )
    assert card.posts_published == 5
    assert card.posts_with_owner_signal == 3  # a story, a flagship with owner material, a take
    assert card.replies == 2
    assert card.substantive_comments == 1
    assert card.on_pillar_share == 0.8
    assert card.computed_at == "2026-11-30T12:00:00.000000Z"


async def test_the_monday_job_stores_it_and_closes_the_week(services: FakeServices) -> None:
    await seed_week(services)
    await save_plan(services, LAST_WEEK, status=PlanStatus.APPROVED)
    services.clock.set(MONDAY_SEVEN)
    job = await run_job(
        services,
        "planner.scorecard",
        {},
        job_id="sched-planner-scorecard-20261130T120000Z",
        scheduled_for=MONDAY_SEVEN,
        schedule_id="sched-planner-scorecard",
    )
    assert job.result == {
        "weekStart": LAST_WEEK,
        "planId": plan_id(LAST_WEEK),
        "closed": True,
        "postsPublished": 5,
    }
    plan = (await read_doc(services.store, WeekPlan, plan_id(LAST_WEEK))).doc
    assert plan.status is PlanStatus.CLOSED
    assert plan.scorecard is not None
    assert plan.scorecard.replies == 2


async def test_an_unapproved_or_missing_plan_is_not_closed(services: FakeServices) -> None:
    services.clock.set(MONDAY_SEVEN)
    missing = await run_job(services, "planner.scorecard", {}, job_id="score-1")
    assert isinstance(missing.result, dict)
    assert missing.result["planId"] is None
    await save_plan(services, LAST_WEEK, status=PlanStatus.PROPOSED)
    proposed = await run_job(services, "planner.scorecard", {}, job_id="score-2")
    assert isinstance(proposed.result, dict)
    assert proposed.result["closed"] is False
    plan = (await read_doc(services.store, WeekPlan, plan_id(LAST_WEEK))).doc
    assert plan.status is PlanStatus.PROPOSED
    assert plan.scorecard is not None


async def test_the_scorecard_route(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await seed_week(services)
    services.clock.set(datetime(2026, 12, 2, 12, 0, tzinfo=UTC))
    response = await client.get("/api/plan/scorecard?weeks=2", headers=read_headers)
    assert response.status_code == 200
    rows = response.json()
    # Completed weeks only, newest first (Today shows the week in progress).
    assert [row["weekStart"] for row in rows] == ["2026-11-23", "2026-11-16"]
    assert rows[0]["scorecard"]["postsPublished"] == 5
    assert rows[1]["scorecard"]["postsPublished"] == 0
    capped = await client.get("/api/plan/scorecard?weeks=500", headers=read_headers)
    assert len(capped.json()) == 52
