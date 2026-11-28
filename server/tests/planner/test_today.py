"""``GET /api/today``: items derived from documents, ordered by priority then due time."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from blogwriter.domain.articles import Article
from blogwriter.domain.backups import BackupStatus
from blogwriter.domain.docstore import create_doc, upsert_doc
from blogwriter.domain.posts import KitInfo, PublishIntent
from blogwriter.domain.settings import CadenceSettings, LinkedInConnection
from blogwriter.domain.states import (
    ArticleStatus,
    ConnectionStatus,
    KitState,
    PlanStatus,
    PostStatus,
)
from blogwriter.domain.stories import Interview
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.jobs import Job
from blogwriter.jobs.scheduler import ScheduleItem, daily
from blogwriter.planner.slots import slot_at
from blogwriter.planner.testing import article, planned_slot, post, save_plan

CADENCE = CadenceSettings.default(SAMPLE_NOW)


async def seed_everything(services: FakeServices) -> dict[str, str]:
    ids: dict[str, str] = {}
    await save_plan(services, "2026-11-30", [planned_slot("s1", "2026-12-01")])
    review = await article(
        services, status=ArticleStatus.IN_REVIEW, title="Okta to Entra field notes"
    )
    late = await article(
        services, status=ArticleStatus.IN_REVIEW, title="Late one", last_error="publish_late"
    )
    stuck = await article(
        services, status=ArticleStatus.NEEDS_ATTENTION, title="Stuck draft", last_error="refused"
    )
    auto = await article(
        services,
        status=ArticleStatus.APPROVED,
        publish_at="2026-11-30T14:00:00.000000Z",
        auto=True,
    )
    interview = sample_document(
        Interview, article_id=review.id, due_at="2026-11-27T23:00:00.000000Z"
    )
    await create_doc(services.store, interview)
    soon = await post(
        services, status=PostStatus.STAGED, slot=slot_at(CADENCE, "2026-11-26", "08:30")
    )
    draft = await post(
        services, status=PostStatus.DRAFT, slot=slot_at(CADENCE, "2026-11-27", "10:00")
    )
    intent = PublishIntent(id="intent-0002", created_at=SAMPLE_NOW, approval_sha256="0" * 64)
    unsure = await post(
        services,
        status=PostStatus.NEEDS_ATTENTION,
        intent=intent.model_copy(update={"status": "unknown"}),
    )
    job = Job.model_validate(
        {
            "id": "job-stuck-1",
            "type": "signals.crawl",
            "status": "quarantined",
            "runAfter": SAMPLE_NOW,
            "createdAt": SAMPLE_NOW,
            "updatedAt": SAMPLE_NOW,
        }
    )
    await services.store.create("jobs", job.to_body())
    item = ScheduleItem(
        id="sched-signals-scout",
        name="Weekly scouting per pillar",
        job_type="signals.scout",
        timezone="America/New_York",
        rule=daily("15:00"),
        status="needs_confirmation",
        next_run_at=SAMPLE_NOW,
        pending_run_at="2026-11-26T08:00:00.000000Z",
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
    )
    await services.store.create("jobs", item.to_body())
    connection = LinkedInConnection.default(SAMPLE_NOW).model_copy(
        update={"status": ConnectionStatus.NEEDS_RECONNECT}
    )
    await upsert_doc(services.store, connection)
    ids.update(
        review=review.id,
        late=late.id,
        stuck=stuck.id,
        auto=auto.id,
        interview=interview.id,
        soon=soon.id,
        draft=draft.id,
        unsure=unsure.id,
    )
    return ids


async def test_today_collects_what_needs_the_owner(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    ids = await seed_everything(services)
    response = await client.get("/api/today", headers=read_headers)
    assert response.status_code == 200, response.text
    today = response.json()
    assert today["generatedAt"] == "2026-11-26T12:00:00.000000Z"
    assert today["week"]["weekStart"] == "2026-11-23"
    assert today["week"]["mode"] == "normal"
    assert today["week"]["planStatus"] is None
    assert today["week"]["scorecard"]["postsPublished"] == 0
    items = {item["id"]: item for item in today["items"]}
    assert len(items) == len(today["items"])  # ids are unique
    assert all(len(item["title"]) <= 80 for item in today["items"])
    expected = {
        f"publish_post:{ids['soon']}": ("Post at Thu 08:30: tap to publish", "urgent"),
        "reconnect_linkedin:linkedin-connection": (
            "Reconnect LinkedIn: one-click publishing is off",
            "urgent",
        ),
        "approve_plan:plan-2026-11-30": ("Approve next week's plan", "high"),
        f"answer_interview:{ids['interview']}": ("Answer 1 quick question", "high"),
        f"review_article:{ids['late']}": ("Publish time passed: approve again", "high"),
        f"needs_attention:{ids['stuck']}": ("Needs you: Stuck draft", "high"),
        f"veto_window:{ids['auto']}": ("Auto-publishes Mon 09:00: review or veto", "normal"),
        f"review_article:{ids['review']}": ("Review: Okta to Entra field notes", "normal"),
        f"approve_post:{ids['draft']}": ("Approve the native post for Fri 10:00", "normal"),
        f"needs_attention:{ids['unsure']}": ("Check LinkedIn: did your post go out?", "high"),
        "needs_attention:job-stuck-1": ("A signals.crawl job is stuck: retry it", "high"),
        "schedule_confirm:sched-signals-scout": (
            "Confirm a missed run: Weekly scouting per pillar",
            "high",
        ),
        "restore_drill:backup-status": ("Run the monthly restore drill", "low"),
    }
    for item_id, (title, priority) in expected.items():
        assert item_id in items, item_id
        assert (items[item_id]["title"], items[item_id]["priority"]) == (title, priority)
    assert items[f"publish_post:{ids['soon']}"]["href"] == (
        f"/studio/{ids['soon']}/go-live?post={ids['soon']}"
    )
    assert items[f"answer_interview:{ids['interview']}"]["href"] == (
        f"/studio/{ids['review']}/interview"
    )
    assert items["approve_plan:plan-2026-11-30"]["dueAt"] == "2026-11-30T13:00:00.000000Z"
    owner_inputs = [i for i in today["items"] if i["kind"] == "owner_input"]
    assert {i["id"] for i in owner_inputs} >= {
        "owner_input:blocklist",
        "owner_input:linkedin_samples",
    }
    assert all(i["priority"] == "low" for i in owner_inputs)
    ranks = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    order = [ranks[i["priority"]] for i in today["items"]]
    assert order == sorted(order)


async def test_today_week_shows_the_plan_and_this_weeks_scorecard(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await save_plan(services, "2026-11-23", status=PlanStatus.APPROVED, mode="light")
    await post(
        services,
        status=PostStatus.POSTED,
        slot=slot_at(CADENCE, "2026-11-24", "10:00"),
        posted_at="2026-11-24T15:00:00.000000Z",
        story_ids=["story-1"],
    )
    done = BackupStatus.default(SAMPLE_NOW).model_copy(
        update={"last_restore_drill_at": "2026-11-20T12:00:00.000000Z"}
    )
    await upsert_doc(services.store, done)
    today = (await client.get("/api/today", headers=read_headers)).json()
    assert today["week"]["mode"] == "light"
    assert today["week"]["planStatus"] == "approved"
    card = today["week"]["scorecard"]
    assert (card["postsPublished"], card["postsWithOwnerSignal"], card["onPillarShare"]) == (
        1,
        1,
        1.0,
    )
    assert all(item["kind"] != "restore_drill" for item in today["items"])


async def test_publish_now_inside_the_grace_and_not_before_two_hours(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    open_slot = await post(
        services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-11-26", "06:00")
    )
    far = await post(
        services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-11-26", "11:00")
    )
    kit_mode = await post(
        services,
        status=PostStatus.APPROVED,
        slot=slot_at(CADENCE, "2026-11-26", "08:00"),
        go_live_mode="kit",
    )
    by_hand = await post(
        services,
        status=PostStatus.KIT,
        slot=slot_at(CADENCE, "2026-11-26", "09:00"),
        go_live_mode="kit",
        kit=KitInfo(state=KitState.NATIVE_DRAFT_SAVED, prepared_at=SAMPLE_NOW),
    )
    scheduled_natively = await post(
        services,
        status=PostStatus.KIT,
        slot=slot_at(CADENCE, "2026-11-26", "08:15"),
        go_live_mode="kit",
        kit=KitInfo(
            state=KitState.NATIVELY_SCHEDULED,
            prepared_at=SAMPLE_NOW,
            scheduled_at=slot_at(CADENCE, "2026-11-26", "08:15").utc,
        ),
    )
    services.clock.set(datetime(2026, 11, 26, 12, 30, tzinfo=UTC))  # 07:30
    items = (await client.get("/api/today", headers=read_headers)).json()["items"]
    publish = {i["refs"]["postId"]: i["title"] for i in items if i["kind"] == "publish_post"}
    assert publish == {
        open_slot.id: "Your Thu 06:00 slot is open: tap to publish",
        by_hand.id: "Post at Thu 09:00: post it from the kit",
    }
    assert scheduled_natively.id not in publish
    assert far.id not in publish
    assert kit_mode.id not in publish


async def test_eval_articles_stay_out_of_today(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    evaluation = await article(services, status=ArticleStatus.IN_REVIEW, purpose="eval")
    items = (await client.get("/api/today", headers=read_headers)).json()["items"]
    assert all(i["refs"].get("articleId") != evaluation.id for i in items)
    assert isinstance(evaluation, Article)


async def test_a_failed_generation_is_not_a_linkedin_check(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    failed = await post(services, status=PostStatus.NEEDS_ATTENTION, last_error="refused")
    items = (await client.get("/api/today", headers=read_headers)).json()["items"]
    [item] = [i for i in items if i["refs"].get("postId") == failed.id]
    assert item["title"] == "Post generation failed: regenerate or archive it"
    assert item["href"].startswith(f"/studio/{failed.id}/posts")


async def test_an_away_week_asks_once_to_schedule_its_kit_posts_in_linkedin(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    """Away weeks: their approved posts went to the kit, so the owner schedules them natively
    before leaving. One item per week names how many are left and opens the first one."""
    await save_plan(services, "2026-11-30", status=PlanStatus.APPROVED, mode="away")
    await save_plan(services, "2026-12-07", status=PlanStatus.APPROVED)  # a normal week

    def kit(state: KitState) -> KitInfo:
        return KitInfo(state=state, prepared_at=SAMPLE_NOW)

    first = await post(
        services,
        status=PostStatus.KIT,
        go_live_mode="kit",
        slot=slot_at(CADENCE, "2026-12-01", "10:00"),
        kit=kit(KitState.PREPARED),
    )
    await post(
        services,
        status=PostStatus.KIT,
        go_live_mode="kit",
        slot=slot_at(CADENCE, "2026-12-03", "10:00"),
        kit=kit(KitState.NATIVE_DRAFT_SAVED),
    )
    await post(  # scheduled in LinkedIn already: LinkedIn posts it
        services,
        status=PostStatus.KIT,
        go_live_mode="kit",
        slot=slot_at(CADENCE, "2026-12-02", "10:00"),
        kit=KitInfo(
            state=KitState.NATIVELY_SCHEDULED,
            prepared_at=SAMPLE_NOW,
            scheduled_at=slot_at(CADENCE, "2026-12-02", "10:00").utc,
        ),
    )
    await post(  # a kit post of the normal week: its slot's own "post it" item covers it
        services,
        status=PostStatus.KIT,
        go_live_mode="kit",
        slot=slot_at(CADENCE, "2026-12-08", "10:00"),
        kit=kit(KitState.PREPARED),
    )
    items = (await client.get("/api/today", headers=read_headers)).json()["items"]
    batch = [item for item in items if item["kind"] == "schedule_kit"]
    assert batch == [
        {
            "id": "schedule_kit:plan-2026-11-30",
            "kind": "schedule_kit",
            "title": "Away week of Nov 30: schedule 2 posts in LinkedIn",
            "href": f"/studio/{first.id}/go-live?post={first.id}",
            "dueAt": first.slot.utc if first.slot is not None else None,
            "priority": "high",
            "refs": {
                "articleId": None,
                "postId": first.id,
                "interviewId": None,
                "scheduleId": None,
                "reminderId": None,
                "jobId": None,
                "ideaId": None,
                "weekStart": "2026-11-30",
            },
        }
    ]


async def test_an_away_week_with_every_kit_post_scheduled_asks_nothing(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await save_plan(services, "2026-11-30", status=PlanStatus.APPROVED, mode="away")
    await post(
        services,
        status=PostStatus.KIT,
        go_live_mode="kit",
        slot=slot_at(CADENCE, "2026-12-01", "10:00"),
        kit=KitInfo(
            state=KitState.NATIVELY_SCHEDULED,
            prepared_at=SAMPLE_NOW,
            scheduled_at=slot_at(CADENCE, "2026-12-01", "10:00").utc,
        ),
    )
    items = (await client.get("/api/today", headers=read_headers)).json()["items"]
    assert [item for item in items if item["kind"] == "schedule_kit"] == []
