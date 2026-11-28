"""The posts routes: create, derive, edit (approval invalidated), live lint, slides, preview,
render, regenerate, approve (checks, media, slot conflicts, payload, blocklist), unapprove,
archive, list."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from blogwriter.ai.types import Task
from blogwriter.core.testing import make_settings
from blogwriter.domain.articles import Article, ArticleMeta
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import find_docs, read_doc, update_doc, upsert_doc
from blogwriter.domain.jobtypes import (
    PLANNER_SLOT_CHECK,
    POSTS_DERIVE,
    POSTS_EXPIRE,
    POSTS_GENERATE,
    POSTS_STAGE,
    derive_job_id,
    stage_job_id,
)
from blogwriter.domain.planner import PlannedSlot, WeekPlan
from blogwriter.domain.posts import KitInfo, Lane, Post, PublishIntent, Slot
from blogwriter.domain.research import QuickSearch
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import (
    OWNER_WRITES,
    POST_MACHINE,
    ArticleStatus,
    IdeaStatus,
    KitState,
    PlanStatus,
    PostStatus,
)
from blogwriter.domain.stories import StoryEntry
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    StoreJobQueue,
    make_app,
    make_services,
    sample_document,
)
from blogwriter.linkedin.littletext import api_commentary, parse_display, unescape
from blogwriter.linkedin.posts import get_router
from blogwriter.linkedin.posts.carousel import spec_json
from blogwriter.linkedin.posts.jobs import check_and_save
from blogwriter.linkedin.posts.routes import EDITABLE
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.staging import expire_post
from blogwriter.linkedin.posts.testing import (
    CLEAN_BODY,
    T0,
    carousel_slides,
    claims_reply,
    seed_settings,
    usable_story,
)
from blogwriter.render.spec import CarouselSpec
from blogwriter.signals import propose_repurpose

STORY_ID = "story-0193f00000000000000000000001"
TAGS = ["AISecurity"]
TUESDAY = {"localDate": "2026-12-01", "localTime": "10:00"}


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


def jobs(services: FakeServices) -> StoreJobQueue:
    return services.jobs


async def store_post(services: FakeServices, text: str = CLEAN_BODY, /, **fields: object) -> Post:
    display, payload = serialize(text, TAGS)
    values: dict[str, object] = {
        "display_text": display,
        "payload": payload,
        "hashtags": TAGS,
        "pillar": "agents",
        "lane": "native",
        "story_ids": [STORY_ID],
        **fields,
    }
    post = sample_document(Post, **values)
    await upsert_doc(services.store, post)
    return post


async def etag_of(client: httpx.AsyncClient, post_id: str, headers: dict[str, str]) -> str:
    response = await client.get(f"/api/posts/{post_id}", headers=headers)
    assert response.status_code == 200, response.text
    return cast(str, response.json()["etag"])


async def checked_post(services: FakeServices, **fields: object) -> Post:
    """A draft whose three checks passed on its current content."""
    await usable_story(services, STORY_ID)
    post = await store_post(services, **fields)
    runner(services).add(Task.FACT_CHECK, claims_reply())
    assert await check_and_save(services, post.id, origin="owner", job_id="job-test")
    stored = (await read_doc(services.store, Post, post.id)).doc
    assert stored.derived_claims is not None
    assert stored.derived_claims.passed, stored.derived_claims
    return stored


def problem(response: httpx.Response) -> dict[str, Any]:
    return cast(dict[str, Any], response.json())


# --------------------------------------------------------------------------------------------
# Create and derive
# --------------------------------------------------------------------------------------------


async def test_create_a_quick_take(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_settings(services)
    idea = sample_document(
        Idea, title="MCP draft", time_sensitive=True, expires_at="2026-11-29T12:00:00.000000Z"
    )
    await upsert_doc(services.store, idea)
    search = sample_document(QuickSearch, query="MCP authorization draft")
    await upsert_doc(services.store, search)
    body = {
        "lane": "quick_take",
        "pillar": "agents",
        "format": "text",
        "ideaId": idea.id,
        "quickSearchId": search.id,
        "takeText": "Resource indicators fix the token confusion I keep finding.",
        "slot": TUESDAY,
    }
    response = await client.post("/api/posts", json=body, headers=write_headers)
    assert response.status_code == 202, response.text
    data = response.json()
    item = data["post"]["item"]
    assert item["status"] == "generating"
    assert item["ownerTake"] == body["takeText"]
    assert item["timeSensitive"] is True
    assert item["expiresAt"] == "2026-11-29T12:00:00.000000Z"
    assert item["slot"]["utc"] == "2026-12-01T15:00:00.000000Z"
    assert item["goLiveMode"] == "kit"
    assert jobs(services).types() == [POSTS_GENERATE, POSTS_EXPIRE]
    assert data["jobId"] == jobs(services).enqueued[0].id
    used = (await read_doc(services.store, Idea, idea.id)).doc
    assert used.status is IdeaStatus.USED
    assert used.post_id == item["id"]


def planned_week(*slots: PlannedSlot, status: PlanStatus = PlanStatus.APPROVED) -> WeekPlan:
    return sample_document(
        WeekPlan,
        status=status,
        approved_at=SAMPLE_NOW if status is PlanStatus.APPROVED else None,
        slots=list(slots),
    )


def take_slot(slot_id: str, *, lane: Lane = "quick_take", **fields: object) -> PlannedSlot:
    return PlannedSlot.model_validate(
        {
            "id": slot_id,
            "slot": Slot.at("2026-12-01", "10:00", "America/New_York", "primary"),
            "lane": lane,
            "pillar": "agents",
            **fields,
        }
    )


async def test_a_quick_take_for_a_plan_slot_is_linked_without_reopening_the_plan(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    """The "What's your take?" slot: the post takes the slot's time, names the slot, and the
    plan's slot names the post. The approved plan stays approved (no second approval)."""
    await seed_settings(services)
    idea = sample_document(Idea, title="MCP draft")
    await upsert_doc(services.store, idea)
    plan = planned_week(
        take_slot("s1", idea_id=idea.id), take_slot("s2", lane="native", pillar="identity")
    )
    await upsert_doc(services.store, plan)
    body = {
        "lane": "quick_take",
        "pillar": "agents",
        "format": "text",
        "ideaId": idea.id,
        "takeText": "Resource indicators fix the token confusion I keep finding.",
        "planSlot": {"weekStart": plan.week_start, "slotId": "s1"},
    }
    response = await client.post("/api/posts", json=body, headers=write_headers)
    assert response.status_code == 202, response.text
    item = response.json()["post"]["item"]
    assert (item["weekPlanId"], item["planSlotId"]) == (plan.id, "s1")
    assert item["slot"] == plan.slots[0].slot.model_dump(mode="json", by_alias=True)
    after = (await read_doc(services.store, WeekPlan, plan.id)).doc
    assert after.status is PlanStatus.APPROVED
    assert after.approved_at == plan.approved_at
    assert [slot.post_id for slot in after.slots] == [item["id"], None]
    assert after.slots[0].idea_id == idea.id
    listed = await client.get("/api/posts?lane=quick_take", headers=read_headers)
    (entry,) = listed.json()["items"]
    assert (entry["item"]["weekPlanId"], entry["item"]["planSlotId"]) == (plan.id, "s1")

    again = await client.post("/api/posts", json=body, headers=write_headers)
    assert again.status_code == 409
    assert problem(again)["code"] == "plan_slot_taken"


@pytest.mark.parametrize("gone", ["archived", "expired"])
async def test_a_slot_whose_take_is_gone_takes_a_new_take(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
    gone: str,
) -> None:
    """Quick takes expire by design, and the owner may archive one: either way its slot is open
    again. A new take fills it in the gone one's place (the plan's slot names the new post), the
    approved plan stays approved (no plan edit, no second approval), and a live take still
    holds the slot (409)."""
    await seed_settings(services)
    news = sample_document(
        Idea, title="MCP draft", time_sensitive=True, expires_at="2026-11-29T12:00:00.000000Z"
    )
    fresh = sample_document(Idea, title="Token exchange for agents")
    for idea in (news, fresh):
        await upsert_doc(services.store, idea)
    plan = planned_week(take_slot("s1", idea_id=news.id))
    await upsert_doc(services.store, plan)
    body = {
        "lane": "quick_take",
        "pillar": "agents",
        "format": "text",
        "ideaId": news.id,
        "takeText": "Resource indicators fix the token confusion I keep finding.",
        "planSlot": {"weekStart": plan.week_start, "slotId": "s1"},
    }
    first = await client.post("/api/posts", json=body, headers=write_headers)
    assert first.status_code == 202, first.text
    first_id = first.json()["post"]["item"]["id"]
    taken = await client.post("/api/posts", json=body, headers=write_headers)
    assert (taken.status_code, problem(taken)["code"]) == (409, "plan_slot_taken")
    if gone == "archived":
        etag = await etag_of(client, first_id, read_headers)
        archived = await client.post(
            f"/api/posts/{first_id}/archive", headers={**write_headers, "If-Match": etag}
        )
        assert archived.status_code == 200, archived.text
        retake = body  # the same idea, a new take
    else:
        # Written, then past the news' expiry: ``posts.expire`` expires the take.
        await update_doc(
            services.store,
            Post,
            first_id,
            lambda post: post.model_copy(update={"status": PostStatus.DRAFT}),
        )
        services.clock.set(datetime(2026, 11, 29, 13, 0, tzinfo=UTC))
        assert await expire_post(services.store, first_id, now=services.clock.now()) == "expired"
        retake = {**body, "ideaId": fresh.id}  # the news is stale: a take on another idea
    second = await client.post("/api/posts", json=retake, headers=write_headers)
    assert second.status_code == 202, second.text
    item = second.json()["post"]["item"]
    assert item["id"] != first_id
    assert (item["weekPlanId"], item["planSlotId"]) == (plan.id, "s1")
    assert item["slot"] == plan.slots[0].slot.model_dump(mode="json", by_alias=True)
    after = (await read_doc(services.store, WeekPlan, plan.id)).doc
    assert [slot.post_id for slot in after.slots] == [item["id"]]
    assert (after.status, after.approved_at) == (PlanStatus.APPROVED, plan.approved_at)
    assert (await read_doc(services.store, Post, first_id)).doc.status.value == gone
    again = await client.post("/api/posts", json=retake, headers=write_headers)
    assert (again.status_code, problem(again)["code"]) == (409, "plan_slot_taken")


@pytest.mark.parametrize(
    ("plan_slot", "status", "code"),
    [
        ({"weekStart": "2026-11-30", "slotId": "nope"}, 422, "plan_slot_unknown"),
        ({"weekStart": "2026-12-07", "slotId": "s1"}, 422, "plan_slot_unknown"),
        ({"weekStart": "2026-11-30", "slotId": "s2"}, 409, "plan_slot_lane"),
    ],
)
async def test_a_plan_slot_that_cannot_take_the_post_is_refused(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    plan_slot: dict[str, str],
    status: int,
    code: str,
) -> None:
    await seed_settings(services)
    idea = sample_document(Idea, title="MCP draft")
    await upsert_doc(services.store, idea)
    await upsert_doc(services.store, planned_week(take_slot("s1"), take_slot("s2", lane="native")))
    body = {
        "lane": "quick_take",
        "pillar": "agents",
        "format": "text",
        "ideaId": idea.id,
        "takeText": "Resource indicators fix the token confusion I keep finding.",
        "planSlot": plan_slot,
    }
    response = await client.post("/api/posts", json=body, headers=write_headers)
    assert response.status_code == status, response.text
    assert problem(response)["code"] == code
    assert await find_docs(services.store, Post) == []


async def test_a_closed_week_or_a_slot_and_a_time_at_once_are_refused(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_settings(services)
    idea = sample_document(Idea, title="MCP draft")
    await upsert_doc(services.store, idea)
    await upsert_doc(services.store, planned_week(take_slot("s1"), status=PlanStatus.CLOSED))
    body = {
        "lane": "quick_take",
        "pillar": "agents",
        "format": "text",
        "ideaId": idea.id,
        "takeText": "Resource indicators fix the token confusion I keep finding.",
        "planSlot": {"weekStart": "2026-11-30", "slotId": "s1"},
    }
    closed = await client.post("/api/posts", json=body, headers=write_headers)
    assert (closed.status_code, problem(closed)["code"]) == (409, "plan_closed")
    both = await client.post("/api/posts", json={**body, "slot": TUESDAY}, headers=write_headers)
    assert (both.status_code, problem(both)["code"]) == (422, "invalid_slot")


async def test_create_refuses_what_it_cannot_generate(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    base = {"lane": "native", "pillar": "agents", "format": "text"}
    await seed_settings(services, ai_use_confirmed=False)
    response = await client.post("/api/posts", json=base, headers=write_headers)
    assert (response.status_code, problem(response)["code"]) == (409, "ai_use_not_confirmed")
    await seed_settings(services, blocklist_configured=False)
    response = await client.post("/api/posts", json=base, headers=write_headers)
    assert (response.status_code, problem(response)["code"]) == (409, "blocklist_not_configured")
    await seed_settings(services, terms=["Fabrikam"])
    take = {**base, "lane": "quick_take", "takeText": "Fabrikam got this wrong."}
    response = await client.post("/api/posts", json=take, headers=write_headers)
    assert response.status_code == 422
    assert problem(response)["code"] == "confidential_text"
    assert "Fabrikam" not in response.text
    for leaky in ("Call 425-555-0199 for it.", "Ask @someone, who ran it."):
        response = await client.post(
            "/api/posts", json={**take, "takeText": leaky}, headers=write_headers
        )
        assert (response.status_code, problem(response)["code"]) == (422, "confidential_text")
        assert {f["field"] for f in problem(response)["findings"]} == {"takeText"}
    response = await client.post(
        "/api/posts", json={**base, "pillar": "nope"}, headers=write_headers
    )
    assert problem(response)["code"] == "unknown_pillar"
    response = await client.post(
        "/api/posts", json={**base, "lane": "quick_take"}, headers=write_headers
    )
    assert problem(response)["code"] == "idea_required"
    proposed = sample_document(StoryEntry, id=STORY_ID)
    await upsert_doc(services.store, proposed)
    response = await client.post(
        "/api/posts", json={**base, "storyIds": [STORY_ID]}, headers=write_headers
    )
    assert (response.status_code, problem(response)["code"]) == (422, "unusable_material")
    assert problem(response)["ids"] == [STORY_ID]
    services.ai = None
    response = await client.post("/api/posts", json=base, headers=write_headers)
    assert (response.status_code, problem(response)["code"]) == (503, "ai_unavailable")
    assert jobs(services).enqueued == []


async def test_derive_enqueues_once_per_version(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_settings(services)
    article = sample_document(
        Article,
        status=ArticleStatus.DRAFTING,
        version=4,
        meta=ArticleMeta(pillar="agents"),
    )
    await upsert_doc(services.store, article)
    url = f"/api/articles/{article.id}/posts/derive"
    response = await client.post(url, headers=write_headers)
    assert (response.status_code, problem(response)["code"]) == (409, "article_not_ready")
    await upsert_doc(services.store, article.model_copy(update={"status": ArticleStatus.IN_REVIEW}))
    response = await client.post(url, headers=write_headers)
    assert response.status_code == 202, response.text
    assert response.json()["jobId"] == derive_job_id(article.id, 4)
    again = await client.post(url, headers=write_headers)
    assert again.json()["jobId"] == derive_job_id(article.id, 4)
    assert jobs(services).types() == [POSTS_DERIVE]


# --------------------------------------------------------------------------------------------
# Edit, lint, slides
# --------------------------------------------------------------------------------------------


async def test_an_edit_invalidates_the_approval(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await checked_post(services)
    etag = await etag_of(client, post.id, read_headers)
    approved = await client.post(
        f"/api/posts/{post.id}/approve",
        json={"slot": TUESDAY},
        headers={**write_headers, "If-Match": etag},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["item"]["status"] == "approved"
    assert jobs(services).types() == [POSTS_STAGE, PLANNER_SLOT_CHECK]
    stage_id = stage_job_id(post.id, "2026-12-01T15:00:00.000000Z")
    edit = {
        "displayText": CLEAN_BODY + "\n\nOne more line #aisecurity",
        "hashtags": ["#aisecurity"],
    }
    stale = await client.put(
        f"/api/posts/{post.id}", json=edit, headers={**write_headers, "If-Match": etag}
    )
    assert stale.status_code == 412
    assert problem(stale)["current"]["status"] == "approved"
    response = await client.put(
        f"/api/posts/{post.id}",
        json=edit,
        headers={**write_headers, "If-Match": approved.json()["etag"]},
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "draft"
    assert item["approval"] is None
    assert item["displayText"].endswith("#AISecurity")
    assert item["payload"].endswith("#AISecurity")
    assert item["lint"]["contentSha256"]
    assert (await jobs(services).get(stage_id)).status == "cancelled"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.payload == api_commentary(parse_display(saved.display_text, saved.hashtags))
    assert unescape(saved.payload) == saved.display_text


async def test_edit_rules(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await store_post(services)
    etag = await etag_of(client, post.id, read_headers)
    headers = {**write_headers, "If-Match": etag}
    response = await client.put(
        f"/api/posts/{post.id}", json={"displayText": "x", "hashtags": ["Nope"]}, headers=headers
    )
    assert (response.status_code, problem(response)["code"]) == (422, "hashtag_not_approved")
    response = await client.put(
        f"/api/posts/{post.id}", json={"displayText": "bell\u0007"}, headers=headers
    )
    assert problem(response)["code"] == "invalid_text"
    response = await client.put(
        f"/api/posts/{post.id}", json={"displayText": "x", "selectedHookId": "h9"}, headers=headers
    )
    assert problem(response)["code"] == "unknown_hook"
    response = await client.put(
        f"/api/posts/{post.id}", json={"displayText": "x"}, headers=write_headers
    )
    assert (response.status_code, problem(response)["code"]) == (428, "if_match_required")
    link = {
        "displayText": f"{CLEAN_BODY}\n\nhttps://example.org/report",
        "linkException": {"url": "https://example.org/report", "reason": "Link experiment arm B"},
    }
    response = await client.put(f"/api/posts/{post.id}", json=link, headers=headers)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["linkException"]["url"] == "https://example.org/report"
    assert item["lint"]["passed"] is True
    posted = await store_post(services, status=PostStatus.KIT)
    response = await client.put(
        f"/api/posts/{posted.id}",
        json={"displayText": "x"},
        headers={**write_headers, "If-Match": await etag_of(client, posted.id, read_headers)},
    )
    assert (response.status_code, problem(response)["code"]) == (409, "read_only_state")
    assert problem(response)["state"] == "kit"


async def test_live_lint(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_settings(services, terms=["Fabrikam"])
    post = await store_post(services)
    response = await client.post(
        f"/api/posts/{post.id}/lint",
        json={"displayText": "Agree? Fabrikam said #AISecurity"},
        headers=write_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    found = {(f["code"], f["severity"]) for f in data["findings"]}
    assert ("engagement-bait", "block") in found
    assert ("blocklisted-term", "block") in found
    assert "Fabrikam" not in response.text.replace("Agree? Fabrikam said", "")
    assert data["payloadUnits"] == len("Agree? Fabrikam said #AISecurity")
    assert data["foldIndex"] == len("Agree? Fabrikam said #AISecurity")
    # Nothing was stored.
    assert (await read_doc(services.store, Post, post.id)).doc.display_text == post.display_text


async def test_slides_are_validated_and_invalidate_media(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    media = MediaRef(
        role="carousel_pdf",
        name="posts/x/a.pdf",
        sha256="a" * 64,
        content_type="application/pdf",
        size_bytes=10,
    )
    post = await store_post(services, "Caption.", format="carousel", media=[media])
    etag = await etag_of(client, post.id, read_headers)
    headers = {**write_headers, "If-Match": etag}
    bad = await client.put(
        f"/api/posts/{post.id}/carousel",
        json={"spec": {"title": "T", "slides": [{"type": "cover"}]}},
        headers=headers,
    )
    assert (bad.status_code, problem(bad)["code"]) == (422, "invalid_spec")
    assert problem(bad)["errors"]
    spec = spec_json(
        CarouselSpec(title="The order that moved 2,000 apps", slides=carousel_slides(8))
    )
    response = await client.put(
        f"/api/posts/{post.id}/carousel", json={"spec": spec}, headers=headers
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["media"] == []
    assert item["documentTitle"] == "The order that moved 2,000 apps"
    assert item["carouselSpec"]["slides"][0]["type"] == "cover"
    text = await store_post(services)
    response = await client.put(
        f"/api/posts/{text.id}/carousel",
        json={"spec": spec},
        headers={**write_headers, "If-Match": await etag_of(client, text.id, read_headers)},
    )
    assert (response.status_code, problem(response)["code"]) == (409, "not_a_carousel")


async def test_preview_and_render(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    spec = spec_json(CarouselSpec(title="Deck", slides=carousel_slides(8)))
    post = await store_post(
        services, "Caption.", format="carousel", carousel_spec=spec, document_title="Deck"
    )
    response = await client.get(f"/api/posts/{post.id}/carousel/preview", headers=read_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    text = await store_post(services)
    missing = await client.get(f"/api/posts/{text.id}/carousel/preview", headers=read_headers)
    assert missing.status_code == 404
    rendered = await client.post(f"/api/posts/{post.id}/render", headers=write_headers)
    assert rendered.status_code == 202
    nothing = await client.post(f"/api/posts/{text.id}/render", headers=write_headers)
    assert (nothing.status_code, problem(nothing)["code"]) == (409, "nothing_to_render")
    services.render = None
    unavailable = await client.post(f"/api/posts/{post.id}/render", headers=write_headers)
    assert (unavailable.status_code, problem(unavailable)["code"]) == (503, "render_unavailable")


async def test_regenerate_checks_the_instruction(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services, terms=["Fabrikam"])
    post = await store_post(services)
    headers = {**write_headers, "If-Match": await etag_of(client, post.id, read_headers)}
    refused = await client.post(
        f"/api/posts/{post.id}/regenerate",
        json={"what": "text", "instruction": "Mention Fabrikam"},
        headers=headers,
    )
    assert (refused.status_code, problem(refused)["code"]) == (422, "confidential_text")
    assert "Fabrikam" not in refused.text
    ok = await client.post(
        f"/api/posts/{post.id}/regenerate",
        json={"what": "hooks", "instruction": "Shorter."},
        headers=headers,
    )
    assert ok.status_code == 202, ok.text
    job = jobs(services).enqueued[-1]
    assert job.type == POSTS_GENERATE
    assert job.input == {
        "origin": "owner",
        "postId": post.id,
        "what": "hooks",
        "instruction": "Shorter.",
        "weekPlanId": None,
        "slotId": None,
    }
    assert (await read_doc(services.store, Post, post.id)).doc.status is PostStatus.GENERATING
    carousel = await client.post(
        f"/api/posts/{post.id}/regenerate",
        json={"what": "carousel"},
        headers={**write_headers, "If-Match": await etag_of(client, post.id, read_headers)},
    )
    assert (carousel.status_code, problem(carousel)["code"]) == (409, "read_only_state")


# --------------------------------------------------------------------------------------------
# Approve
# --------------------------------------------------------------------------------------------


async def approve(
    client: httpx.AsyncClient,
    post_id: str,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
    body: object = None,
) -> httpx.Response:
    etag = await etag_of(client, post_id, read_headers)
    return await client.post(
        f"/api/posts/{post_id}/approve", json=body, headers={**write_headers, "If-Match": etag}
    )


async def test_approval_binds_the_hash_and_kit_posts_are_not_staged(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await checked_post(services, go_live_mode="kit")
    response = await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    assert response.status_code == 200, response.text
    saved = (await read_doc(services.store, Post, post.id)).doc
    # Kit mode has no staging or tap: approving puts it straight into the kit, prepared.
    assert saved.status is PostStatus.KIT
    assert saved.kit is not None
    assert saved.kit.state is KitState.PREPARED
    assert saved.approval is not None
    assert saved.approval.sha256 == saved.compute_approval_hash()
    assert saved.approval_current
    assert jobs(services).enqueued == []
    again = await approve(client, post.id, read_headers, write_headers)
    assert (again.status_code, problem(again)["code"]) == (409, "invalid_transition")


async def test_approval_refusals(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await checked_post(services)
    # Stale derived claims after an edit.
    display, payload = serialize(CLEAN_BODY + "\n\nEdited.", TAGS)
    await upsert_doc(
        services.store, post.model_copy(update={"display_text": display, "payload": payload})
    )
    response = await approve(client, post.id, read_headers, write_headers)
    assert (response.status_code, problem(response)["code"]) == (409, "checks_failing")
    assert problem(response)["failing"] == ["derived_claims"]
    # A payload that drifted from the text.
    await upsert_doc(services.store, post.model_copy(update={"payload": "tampered"}))
    response = await approve(client, post.id, read_headers, write_headers)
    assert problem(response)["code"] == "payload_mismatch"
    # A carousel without its render.
    spec = spec_json(CarouselSpec(title="Deck", slides=carousel_slides(8)))
    deck = await store_post(
        services, "Caption.", format="carousel", carousel_spec=spec, document_title="Deck"
    )
    response = await approve(client, deck.id, read_headers, write_headers)
    assert problem(response)["code"] == "media_missing"
    # Slots: in the past, and on a day another post already has.
    await upsert_doc(services.store, post)
    past = await approve(
        client,
        post.id,
        read_headers,
        write_headers,
        {"slot": {"localDate": "2026-11-20", "localTime": "10:00"}},
    )
    assert (past.status_code, problem(past)["code"]) == (422, "slot_in_past")
    other = await store_post(
        services,
        status=PostStatus.APPROVED,
        slot=Slot.at("2026-12-01", "15:00", "America/New_York"),
    )
    del other
    conflict = await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    assert (conflict.status_code, problem(conflict)["code"]) == (409, "slot_conflict")
    assert problem(conflict)["conflicts"] == ["2026-12-01T20:00:00.000000Z"]
    # An unconfigured blocklist.
    await seed_settings(services, blocklist_configured=False)
    response = await approve(client, post.id, read_headers, write_headers)
    assert (response.status_code, problem(response)["code"]) == (409, "blocklist_not_configured")


async def test_posts_derived_in_review_wait_for_the_article_approval(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    article = sample_document(
        Article, status=ArticleStatus.IN_REVIEW, meta=ArticleMeta(pillar="agents")
    )
    await upsert_doc(services.store, article)
    post = await checked_post(services)
    await upsert_doc(services.store, post.model_copy(update={"article_id": article.id}))
    refused = await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    assert (refused.status_code, problem(refused)["code"]) == (409, "article_not_approved")
    assert problem(refused)["articleId"] == article.id
    await upsert_doc(services.store, article.model_copy(update={"status": ArticleStatus.APPROVED}))
    after = await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    # Past the article gate, the usual checks apply (here the derived claims must be re-run
    # against the article the post now cites).
    assert problem(after).get("code") != "article_not_approved", after.text


async def test_unapprove_and_archive(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await checked_post(services)
    assert (
        await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    ).status_code == 200
    stage_id = stage_job_id(post.id, "2026-12-01T15:00:00.000000Z")
    etag = await etag_of(client, post.id, read_headers)
    response = await client.post(
        f"/api/posts/{post.id}/unapprove", headers={**write_headers, "If-Match": etag}
    )
    assert response.status_code == 200, response.text
    assert response.json()["item"]["status"] == "draft"
    assert response.json()["item"]["approval"] is None
    assert (await jobs(services).get(stage_id)).status == "cancelled"
    # Re-approving the same slot after the unapprove gets a fresh stage job (rerun).
    assert (
        await approve(client, post.id, read_headers, write_headers, {"slot": TUESDAY})
    ).status_code == 200
    assert (await jobs(services).get(stage_id + "-r1")).status == "queued"
    etag = await etag_of(client, post.id, read_headers)
    archived = await client.post(
        f"/api/posts/{post.id}/archive", headers={**write_headers, "If-Match": etag}
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["item"]["status"] == "archived"
    assert (await jobs(services).get(stage_id + "-r1")).status == "cancelled"
    etag = await etag_of(client, post.id, read_headers)
    again = await client.post(
        f"/api/posts/{post.id}/archive", headers={**write_headers, "If-Match": etag}
    )
    assert (again.status_code, problem(again)["code"]) == (409, "invalid_transition")


# --------------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------------


async def test_list_filters_and_pages(
    client: httpx.AsyncClient, services: FakeServices, read_headers: dict[str, str]
) -> None:
    await seed_settings(services)
    stamps = [f"2026-11-2{index}T12:00:00.000000Z" for index in range(5)]
    for index, stamp in enumerate(stamps):
        await store_post(
            services,
            id=f"post-0193f0000000000000000000002{index}",
            updated_at=stamp,
            lane="native" if index % 2 == 0 else "quick_take",
            article_id="art-0193f00000000000000000000009" if index == 4 else None,
        )
    first = await client.get("/api/posts?limit=2", headers=read_headers)
    assert first.status_code == 200, first.text
    page = first.json()
    assert [i["item"]["id"][-1] for i in page["items"]] == ["4", "3"]
    assert page["items"][0]["item"]["hook"].startswith("The first 1,400 apps")
    assert "displayText" not in page["items"][0]["item"]
    second = await client.get(
        f"/api/posts?limit=2&cursor={page['nextCursor']}", headers=read_headers
    )
    assert [i["item"]["id"][-1] for i in second.json()["items"]] == ["2", "1"]
    drafts = await client.get("/api/posts?status=draft&status=approved", headers=read_headers)
    assert len(drafts.json()["items"]) == 5
    none = await client.get("/api/posts?status=posted", headers=read_headers)
    assert none.json()["items"] == []
    lanes = await client.get("/api/posts?lane=quick_take", headers=read_headers)
    assert {i["item"]["lane"] for i in lanes.json()["items"]} == {"quick_take"}
    by_article = await client.get(
        "/api/posts?articleId=art-0193f00000000000000000000009", headers=read_headers
    )
    assert [i["item"]["id"][-1] for i in by_article.json()["items"]] == ["4"]
    bad = await client.get("/api/posts?cursor=%25%25", headers=read_headers)
    assert (bad.status_code, problem(bad)["code"]) == (422, "invalid_cursor")
    bad_range = await client.get("/api/posts?from=yesterday", headers=read_headers)
    assert bad_range.status_code == 422


async def test_every_saved_post_keeps_the_serializer_invariant(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    await seed_settings(services)
    post = await store_post(services)
    for text in (
        "#aisecurity first",
        "Back\\slash (and) [brackets] *star* _u_ ~t~ <a> {b} @c |d|",
        "C# and #Other",
    ):
        etag = await etag_of(client, post.id, read_headers)
        response = await client.put(
            f"/api/posts/{post.id}",
            json={"displayText": text},
            headers={**write_headers, "If-Match": etag},
        )
        assert response.status_code == 200, response.text
    for stored in await find_docs(services.store, Post):
        saved = stored.doc
        assert saved.payload == api_commentary(parse_display(saved.display_text, saved.hashtags))
        assert unescape(saved.payload) == saved.display_text


@pytest.mark.parametrize("held_by", ["posted", "publishing", "needs_attention", "kit"])
async def test_approval_never_stacks_a_post_on_a_day_another_post_holds(
    client: httpx.AsyncClient,
    services: FakeServices,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
    held_by: str,
) -> None:
    """Two posts on one local day is a hard block, whatever state the other post is in (the
    planner's taken-slot rule, shared through ``domain.golive``)."""
    await seed_settings(services)
    post = await checked_post(services)
    services.clock.set(datetime(2026, 12, 1, 16, 0, tzinfo=UTC))  # Tue 11:00 in New York
    morning = Slot.at("2026-12-01", "10:00", "America/New_York")
    stamp = "2026-12-01T15:00:00.000000Z"
    fields: dict[str, object] = {"slot": morning}
    match held_by:
        case "posted":
            fields.update(status=PostStatus.POSTED, posted_at=stamp)
        case "publishing":
            fields.update(
                status=PostStatus.PUBLISHING,
                intent=PublishIntent(
                    id="intent-1", created_at=stamp, approval_sha256="0" * 64, status="pending"
                ),
            )
        case "needs_attention":
            fields.update(status=PostStatus.NEEDS_ATTENTION)
        case _:
            fields.update(
                status=PostStatus.KIT,
                go_live_mode="kit",
                kit=KitInfo(state=KitState.PREPARED, prepared_at=stamp),
            )
    other = await store_post(services, **fields)
    afternoon = {"localDate": "2026-12-01", "localTime": "15:00"}
    response = await approve(client, post.id, read_headers, write_headers, {"slot": afternoon})
    assert (response.status_code, problem(response)["code"]) == (409, "slot_conflict")
    assert (await read_doc(services.store, Post, post.id)).doc.status is PostStatus.DRAFT
    del other


async def test_the_repurpose_lane_starts_from_a_repurpose_idea(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    """A post that went out -> a repurpose idea (signals) -> a ``repurpose`` post."""
    await seed_settings(services)
    earlier = await store_post(
        services, status=PostStatus.POSTED, posted_at="2026-09-21T15:00:00.000000Z"
    )
    base = {"lane": "repurpose", "pillar": "agents", "format": "text"}
    refused = await client.post("/api/posts", json=base, headers=write_headers)
    assert problem(refused)["code"] == "repurpose_source_missing"
    idea = (await propose_repurpose(services.store, earlier, kind="recut", now=T0)).stored.doc
    assert idea.repurpose_of == earlier.id
    response = await client.post(
        "/api/posts", json={**base, "ideaId": idea.id}, headers=write_headers
    )
    assert response.status_code == 202, response.text
    item = response.json()["post"]["item"]
    assert (item["lane"], item["ideaId"], item["status"]) == ("repurpose", idea.id, "generating")
    assert jobs(services).types() == [POSTS_GENERATE]


def test_the_owner_edits_posts_in_the_states_the_machines_route_names() -> None:
    """``GET /api/meta/machines`` serves ``OWNER_WRITES``: the web's read-only rule."""
    assert {status.value for status in EDITABLE} == OWNER_WRITES[POST_MACHINE.name]


def test_the_openapi_document_types_the_carousel_preview_as_html() -> None:
    """The web shows it only in a sandboxed iframe (``src``): the document says it is HTML."""
    app = make_app(make_services(), routers=[get_router()], settings=make_settings())
    preview = app.openapi()["paths"]["/api/posts/{post_id}/carousel/preview"]["get"]
    content = preview["responses"]["200"]["content"]
    assert set(content) == {"text/html"}
    assert content["text/html"]["schema"] == {"type": "string"}
