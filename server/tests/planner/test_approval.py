"""Plan approval: the flagship pipeline starts for Monday research, native posts are created and
generated, quick takes wait for the owner's take, derivatives wait for the flagship, away weeks
generate nothing, and nothing is ever created twice."""

from __future__ import annotations

import logging

import httpx
import pytest

from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import find_docs, read_doc, update_doc
from blogwriter.domain.golive import link_plan_slot
from blogwriter.domain.planner import FlagshipPlan, WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ArticleStatus, IdeaStatus, PlanStatus, PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, StoreJobQueue
from blogwriter.domain.weeks import plan_id
from blogwriter.planner import flagship_article_id, post_generate_job_id
from blogwriter.planner.plan import SlotRefs
from blogwriter.planner.slots import slot_at
from blogwriter.planner.testing import (
    T0,
    idea,
    planned_slot,
    post,
    save_plan,
    seed_policy,
    story,
)

WEEK = "2026-11-30"
PLAN_ID = plan_id(WEEK)
CADENCE = CadenceSettings.default(SAMPLE_NOW)


async def proposed(services: FakeServices, *, mode: str = "normal") -> tuple[str, Idea, Idea, str]:
    """A proposed plan: derivative Tue, native Wed (with a story), quick take Thu; flagship."""
    await seed_policy(services)
    flagship_idea = await idea(services, "NIST AI RMF profile for agents", pillar="governance")
    news = await idea(
        services, "Exploited IdP session flaw", time_sensitive=True, pillar="identity"
    )
    used = await story(services, "600 straggler apps", ["identity"])
    slots = [
        planned_slot("s1", "2026-12-01", lane="derivative", pillar="agents", format="carousel"),
        planned_slot("s2", "2026-12-02", refs=SlotRefs(story_ids=[used.id])),
        planned_slot("s3", "2026-12-03", lane="quick_take", refs=SlotRefs(idea_id=news.id)),
    ]
    flagship = FlagshipPlan(
        title="An AI RMF profile for agent sponsors",
        pillar="governance",
        format="framework",
        idea_id=flagship_idea.id,
    )
    stored = await save_plan(services, WEEK, slots, mode=mode, flagship=flagship)
    return stored.etag, flagship_idea, news, used.id


async def approve(client: httpx.AsyncClient, headers: dict[str, str], etag: str) -> httpx.Response:
    return await client.post(
        f"/api/plan/weeks/{WEEK}/approve", headers={**headers, "If-Match": etag}
    )


async def test_approval_starts_the_flagship_for_monday_research(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, flagship_idea, _news, story_id = await proposed(services)
    response = await approve(client, write_headers, etag)
    assert response.status_code == 200, response.text
    body = response.json()
    article_id = flagship_article_id(PLAN_ID)
    native_post = Post.id_for_plan_slot(PLAN_ID, "s2")
    assert body["articleId"] == article_id
    assert body["jobIds"] == [f"pipeline-{article_id}-1", post_generate_job_id(native_post)]
    assert body["postIds"] == [native_post]  # the web counts the posts started from these
    assert response.headers["ETag"] == body["plan"]["etag"]

    article = (await read_doc(services.store, Article, article_id)).doc
    assert article.status is ArticleStatus.PLANNED
    assert (article.meta.title, article.meta.pillar, article.meta.format) == (
        "An AI RMF profile for agent sponsors",
        "governance",
        "framework",
    )
    assert article.week_plan_id == PLAN_ID
    assert article.idea_id == flagship_idea.id
    assert article.pipeline_job_id == f"pipeline-{article_id}-1"
    assert article.pipeline_runs == 1

    pipeline = await services.jobs.get(f"pipeline-{article_id}-1")
    assert pipeline.type == "pipeline.article"
    assert pipeline.run_after == "2026-11-30T13:00:00.000000Z"  # Monday 08:00 EST
    assert isinstance(pipeline.input, dict)
    assert pipeline.input["origin"] == "owner"
    assert pipeline.input["articleId"] == article_id

    created = (await read_doc(services.store, Post, native_post)).doc
    assert created.status is PostStatus.GENERATING
    assert (created.lane, created.pillar, created.week_plan_id) == ("native", "identity", PLAN_ID)
    assert created.plan_slot_id == "s2"
    assert created.slot is not None
    assert created.slot.utc == "2026-12-02T15:00:00.000000Z"
    assert created.story_ids == [story_id]
    generate = await services.jobs.get(post_generate_job_id(native_post))
    assert generate.type == "posts.generate"
    assert isinstance(generate.input, dict)
    assert generate.input == {"origin": "owner", "postId": native_post}

    posts = await find_docs(services.store, Post)
    assert [p.doc.id for p in posts] == [native_post]  # no derivative, no quick take
    plan = (await read_doc(services.store, WeekPlan, PLAN_ID)).doc
    assert plan.status is PlanStatus.APPROVED
    assert plan.approved_at is not None
    assert plan.flagship is not None
    assert plan.flagship.article_id == article_id
    assert [s.post_id for s in plan.slots] == [None, native_post, None]
    used = (await read_doc(services.store, Idea, flagship_idea.id)).doc
    assert used.status is IdeaStatus.USED
    assert used.article_id == article_id
    assert services.jobs.types() == ["pipeline.article", "posts.generate"]


async def test_quick_takes_wait_for_the_owners_take(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    etag, _flagship, news, _story = await proposed(services)
    await approve(client, write_headers, etag)
    today = (await client.get("/api/today", headers=read_headers)).json()
    takes = [item for item in today["items"] if item["kind"] == "take_needed"]
    assert len(takes) == 1
    assert takes[0]["title"] == "What's your take? Quick-take slot Thu 10:00"
    assert takes[0]["refs"]["ideaId"] == news.id
    assert takes[0]["href"] == f"/calendar/ideas?idea={news.id}&take=s3&week={WEEK}"
    # The owner's take starts the post: the slot is filled and the item leaves Today.
    await post(
        services,
        status=PostStatus.GENERATING,
        lane="quick_take",
        pillar="identity",
        idea_id=news.id,
        slot=slot_at(CADENCE, "2026-12-03", "10:00"),
    )
    later = (await client.get("/api/today", headers=read_headers)).json()
    assert [item for item in later["items"] if item["kind"] == "take_needed"] == []


async def test_a_take_fills_its_slot_by_its_link_never_by_its_day(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    """Calendar report: a quick take written for another idea on the same day used to hide the
    slot's "What's your take?". Only a take that names the slot (or was written from the slot's
    idea before posts named their slot) fills it."""
    etag, _flagship, news, _story = await proposed(services)
    await approve(client, write_headers, etag)
    other = await idea(services, "A different headline", pillar="identity")
    await post(
        services,
        status=PostStatus.GENERATING,
        lane="quick_take",
        pillar="identity",
        idea_id=other.id,
        slot=slot_at(CADENCE, "2026-12-03", "10:00"),
    )
    same_day = (await client.get("/api/today", headers=read_headers)).json()
    takes = [item for item in same_day["items"] if item["kind"] == "take_needed"]
    assert [item["id"] for item in takes] == [f"take_needed:{PLAN_ID}:s3"]
    await post(
        services,
        status=PostStatus.GENERATING,
        lane="quick_take",
        pillar="identity",
        week_plan_id=PLAN_ID,
        plan_slot_id="s3",
        slot=slot_at(CADENCE, "2026-12-03", "10:00"),
    )
    linked = (await client.get("/api/today", headers=read_headers)).json()
    assert [item for item in linked["items"] if item["kind"] == "take_needed"] == []
    assert news.id  # the slot's own idea never had a post: the link alone filled it


async def take_ids(client: httpx.AsyncClient, headers: dict[str, str]) -> list[str]:
    today = (await client.get("/api/today", headers=headers)).json()
    return [item["id"] for item in today["items"] if item["kind"] == "take_needed"]


@pytest.mark.parametrize("gone", [PostStatus.ARCHIVED, PostStatus.EXPIRED])
@pytest.mark.parametrize("seen", [True, False], ids=["recent", "older"])
async def test_a_slot_whose_take_is_gone_asks_for_the_take_again(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
    gone: PostStatus,
    seen: bool,
) -> None:
    """Quick takes expire by design (and the owner may archive one). The plan's slot keeps
    naming the take, but a gone take no longer fills it: "What's your take?" comes back. A take
    older than Today's take query is read on its own (``older``)."""
    etag, _flagship, news, _story = await proposed(services)
    await approve(client, write_headers, etag)
    created = "2026-11-25T12:00:00.000000Z" if seen else "2026-10-01T12:00:00.000000Z"
    take = await post(
        services,
        status=PostStatus.DRAFT,
        lane="quick_take",
        pillar="identity",
        idea_id=news.id,
        week_plan_id=PLAN_ID,
        plan_slot_id="s3",
        slot=slot_at(CADENCE, "2026-12-03", "10:00"),
        created_at=created,
    )
    assert await link_plan_slot(services.store, PLAN_ID, "s3", take.id, now=T0) is not None
    assert await take_ids(client, read_headers) == []
    await update_doc(
        services.store, Post, take.id, lambda doc: doc.model_copy(update={"status": gone})
    )
    assert await take_ids(client, read_headers) == [f"take_needed:{PLAN_ID}:s3"]
    plan = (await read_doc(services.store, WeekPlan, PLAN_ID)).doc
    assert plan.status is PlanStatus.APPROVED  # the owner re-takes it without a plan edit
    assert [s.post_id for s in plan.slots][2] == take.id


async def test_a_take_that_cannot_be_found_keeps_its_slot(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    """Only a take seen archived, expired or deleted frees its slot (fail closed)."""
    etag, *_ = await proposed(services)
    await approve(client, write_headers, etag)
    assert await take_ids(client, read_headers) == [f"take_needed:{PLAN_ID}:s3"]
    missing = "post-0193f0000000000000000000dead"
    assert await link_plan_slot(services.store, PLAN_ID, "s3", missing, now=T0) is not None
    assert await take_ids(client, read_headers) == []


async def test_a_double_approval_creates_nothing_twice(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, *_ = await proposed(services)
    first = await approve(client, write_headers, etag)
    assert first.status_code == 200
    stale = await approve(client, write_headers, etag)
    assert stale.status_code == 412
    assert stale.json()["code"] == "etag_mismatch"
    again = await approve(client, write_headers, first.json()["plan"]["etag"])
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    assert len(await find_docs(services.store, Article)) == 1
    assert len(await find_docs(services.store, Post)) == 1
    assert len(services.jobs.enqueued) == 2


async def test_reapproval_after_an_edit_adds_only_what_is_new(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, flagship_idea, news, story_id = await proposed(services)
    first = (await approve(client, write_headers, etag)).json()
    edit = {
        "flagship": {
            "title": "Agent sponsors under the AI RMF",
            "pillar": "governance",
            "format": "framework",
            "ideaId": flagship_idea.id,
        },
        "slots": [
            {
                "id": "s1",
                "localDate": "2026-12-01",
                "localTime": "10:00",
                "lane": "derivative",
                "pillar": "agents",
                "format": "carousel",
            },
            {"id": "s2", "localDate": "2026-12-02", "localTime": "10:00"},
            {
                "id": "s3",
                "localDate": "2026-12-03",
                "localTime": "10:00",
                "lane": "quick_take",
                "pillar": "identity",
                "format": "text",
            },
            {
                "id": "s4",
                "localDate": "2026-12-04",
                "localTime": "15:00",
                "lane": "native",
                "pillar": "agents",
                "format": "text",
            },
        ],
    }
    edited = await client.put(
        f"/api/plan/weeks/{WEEK}",
        json=edit,
        headers={**write_headers, "If-Match": first["plan"]["etag"]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["item"]["status"] == "proposed"
    second = await approve(client, write_headers, edited.json()["etag"])
    assert second.status_code == 200, second.text
    new_post = Post.id_for_plan_slot(PLAN_ID, "s4")
    assert second.json()["articleId"] == first["articleId"]
    assert second.json()["jobIds"] == [
        f"pipeline-{first['articleId']}-1",
        post_generate_job_id(new_post),
    ]
    article = (await read_doc(services.store, Article, first["articleId"])).doc
    assert article.meta.title == "Agent sponsors under the AI RMF"  # a planned flagship follows
    assert len(await find_docs(services.store, Article)) == 1
    assert sorted(p.doc.id for p in await find_docs(services.store, Post)) == sorted(
        [Post.id_for_plan_slot(PLAN_ID, "s2"), new_post]
    )
    assert services.jobs.types().count("pipeline.article") == 1
    del news, story_id


async def test_removing_the_flagship_retires_its_planned_article(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, *_ = await proposed(services)
    first = (await approve(client, write_headers, etag)).json()
    slots = [
        {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00"},
        {"id": "s2", "localDate": "2026-12-02", "localTime": "10:00"},
        {"id": "s3", "localDate": "2026-12-03", "localTime": "10:00"},
    ]
    edited = await client.put(
        f"/api/plan/weeks/{WEEK}",
        json={"flagship": None, "slots": slots},
        headers={**write_headers, "If-Match": first["plan"]["etag"]},
    )
    assert edited.status_code == 200, edited.text
    again = await approve(client, write_headers, edited.json()["etag"])
    assert again.status_code == 200, again.text
    assert again.json()["articleId"] is None
    article = (await read_doc(services.store, Article, first["articleId"])).doc
    assert article.status is ArticleStatus.ARCHIVED
    pipeline = await services.jobs.get(f"pipeline-{first['articleId']}-1")
    assert pipeline.status == "cancelled"
    # Planned again: the article comes back and its research start is queued anew.
    flagship = {"title": "Agent sponsors, take two", "pillar": "governance", "format": "framework"}
    readded = await client.put(
        f"/api/plan/weeks/{WEEK}",
        json={"flagship": flagship, "slots": slots},
        headers={**write_headers, "If-Match": again.json()["plan"]["etag"]},
    )
    assert readded.status_code == 200, readded.text
    third = await approve(client, write_headers, readded.json()["etag"])
    assert third.status_code == 200, third.text
    assert third.json()["articleId"] == first["articleId"]
    revived = (await read_doc(services.store, Article, first["articleId"])).doc
    assert revived.status is ArticleStatus.PLANNED
    assert revived.meta.title == "Agent sponsors, take two"
    assert revived.pipeline_job_id == f"pipeline-{first['articleId']}-1-r1"
    restarted = await services.jobs.get(f"pipeline-{first['articleId']}-1-r1")
    assert restarted.status == "queued"
    assert restarted.run_after == "2026-11-30T13:00:00.000000Z"


async def test_an_away_week_generates_nothing(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, *_ = await proposed(services, mode="away")
    response = await approve(client, write_headers, etag)
    assert response.status_code == 200, response.text
    assert response.json()["articleId"] is None
    assert response.json()["jobIds"] == []
    assert await find_docs(services.store, Article) == []
    assert await find_docs(services.store, Post) == []
    assert services.jobs.enqueued == []


async def test_a_light_week_generates_two_posts_and_its_optional_flagship(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    slots = [
        planned_slot("s1", "2026-12-01", pillar="identity"),
        planned_slot("s2", "2026-12-02", pillar="governance"),
    ]
    flagship = FlagshipPlan(
        title="Okta to Entra field notes", pillar="identity", format="case-study", optional=True
    )
    stored = await save_plan(services, WEEK, slots, mode="light", flagship=flagship)
    response = await approve(client, write_headers, stored.etag)
    assert response.status_code == 200, response.text
    assert response.json()["articleId"] == flagship_article_id(PLAN_ID)
    assert len(await find_docs(services.store, Post)) == 2


async def test_two_posts_on_one_day_block_approval(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    etag, *_ = await proposed(services)
    await post(services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-12-02", "15:00"))
    response = await approve(client, write_headers, etag)
    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "slot_conflict"
    assert {f["code"] for f in problem["findings"]} == {"slot-same-day"}
    assert await find_docs(services.store, Article) == []
    assert services.jobs.enqueued == []


async def test_mix_rules_block_approval(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    slots = [
        planned_slot(f"s{n}", f"2026-12-0{n}", lane="derivative", pillar="agents")
        for n in (1, 2, 3)
    ]
    stored = await save_plan(services, WEEK, slots)
    response = await approve(client, write_headers, stored.etag)
    assert response.status_code == 409
    assert response.json()["code"] == "mix_rules"
    assert "mix-derivatives" in {f["code"] for f in response.json()["findings"]}


async def test_unregistered_job_types_do_not_fail_approval(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    services.jobs = StoreJobQueue(services.store, clock=services.clock, registered=set())
    etag, *_ = await proposed(services)
    with caplog.at_level(logging.WARNING, logger="blogwriter.planner"):
        response = await approve(client, write_headers, etag)
    assert response.status_code == 200, response.text
    assert response.json()["jobIds"] == []
    article = (await read_doc(services.store, Article, flagship_article_id(PLAN_ID))).doc
    assert article.pipeline_job_id is None
    hooks = [r for r in caplog.records if r.getMessage() == "job_hook_unregistered"]
    assert {r.__dict__.get("job_type") for r in hooks} == {"pipeline.article", "posts.generate"}


async def test_approval_needs_if_match_and_a_monday(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await proposed(services)
    missing = await client.post(f"/api/plan/weeks/{WEEK}/approve", headers=write_headers)
    assert missing.status_code == 428
    tuesday = await client.post(
        "/api/plan/weeks/2026-12-01/approve", headers={**write_headers, "If-Match": '"x"'}
    )
    assert tuesday.status_code == 404
    nothing = await client.post(
        "/api/plan/weeks/2026-12-07/approve", headers={**write_headers, "If-Match": '"x"'}
    )
    assert nothing.status_code == 404
