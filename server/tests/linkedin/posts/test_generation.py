"""Generation on a real runner: derivatives (at most the lane cap, derived ids, siblings),
native posts from the bank, quick takes, regeneration, refusals, and the "0 links" invariant
(no URL ever reaches the drafter; scripted output with a link is blocked by lint)."""

from __future__ import annotations

import json

from blogwriter.ai.providers.base import ProviderRequestError
from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.articles import Article, ArticleMeta, OwnerSignalRef
from blogwriter.domain.common import Citation
from blogwriter.domain.docstore import find_docs, read_doc, upsert_doc
from blogwriter.domain.jobtypes import (
    POSTS_DERIVE,
    POSTS_GENERATE,
    ArticleJobInput,
    derive_job_id,
)
from blogwriter.domain.posts import HookType, Post
from blogwriter.domain.research import Brief, BriefClaim, QuickSearch
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ArticleStatus, PostStatus
from blogwriter.domain.testing import (
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    sample_document,
)
from blogwriter.linkedin.littletext import api_commentary, parse_display, unescape
from blogwriter.linkedin.posts._post import with_extra
from blogwriter.linkedin.posts.jobs import GenerateInput
from blogwriter.linkedin.posts.testing import (
    T0,
    carousel_slides,
    claims_reply,
    draft_reply,
    run_job,
    seed_settings,
    usable_pov,
    usable_story,
)

STORY_ID = "story-0193f00000000000000000000001"
POV_ID = "pov-0193f00000000000000000000001"
ARTICLE_ID = "art-0193f00000000000000000000001"
BRIEF_ID = "brief-run-0193f00000000000000000000001"


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


def sent_text(services: FakeServices, task: Task) -> str:
    return "\n".join(
        call.request.system + "".join(m.text for m in call.request.messages)
        for call in runner(services).calls_for(task)
    )


def assert_saved_invariants(post: Post) -> None:
    """Every saved post: payload == the escaped display text; kit text == display text."""
    assert post.payload == api_commentary(parse_display(post.display_text, post.hashtags))
    assert unescape(post.payload) == post.display_text


async def seed_article(
    services: FakeServices, *, status: ArticleStatus = ArticleStatus.PUBLISHED
) -> Article:
    brief = sample_document(
        Brief,
        id=BRIEF_ID,
        topic="Identity migrations",
        pillar="identity",
        claims=[
            BriefClaim(
                id="claim-1",
                text="Phishing-resistant MFA is required at AAL3.",
                citation_id="src-nist",
            )
        ],
        citations=[
            Citation(
                id="src-nist",
                url="https://pages.nist.gov/800-63-4/",
                title="Digital Identity Guidelines",
                publisher="NIST",
                domain="pages.nist.gov",
            )
        ],
        hook_seeds=["The last 600 apps set the date"],
    )
    await upsert_doc(services.store, brief)
    await usable_story(services, STORY_ID)
    article = sample_document(
        Article,
        id=ARTICLE_ID,
        status=status,
        version=3,
        brief_id=BRIEF_ID,
        meta=ArticleMeta(
            title="Moving 2,000 apps",
            slug="moving-apps",
            excerpt="How we did it.",
            pillar="identity",
            format="case-study",
        ),
        markdown=(
            "We moved [2,000 apps](https://example.org/apps) in 14 months. See "
            "<https://example.org/more> or www.example.org.[^src-nist]\n\n"
            "[^src-nist]: https://pages.nist.gov/800-63-4/ Digital Identity Guidelines"
        ),
        owner_signals=[OwnerSignalRef(kind="story", id=STORY_ID)],
    )
    await upsert_doc(services.store, article)
    return article


async def test_derive_drafts_at_most_the_cap_with_derived_ids(services: FakeServices) -> None:
    await seed_settings(services, hashtags=("AISecurity",))
    article = await seed_article(services)
    slides = carousel_slides(8)
    runner(services).add(
        Task.DRAFT,
        draft_reply(),
        draft_reply(
            fmt="carousel",
            body="We moved 2,000 apps in 14 months. Swipe for the order.",
            slides=slides,
            document_title="The order that moved 2,000 apps",
        ),
    )
    runner(services).add(Task.FACT_CHECK, claims_reply(), claims_reply())
    job_input = ArticleJobInput(article_id=ARTICLE_ID, origin="owner").to_json()
    job = await run_job(services, POSTS_DERIVE, job_input, job_id=derive_job_id(ARTICLE_ID, 3))
    assert job.status == "succeeded", job.error
    first = Post.id_for_derivative(ARTICLE_ID, article.version, 1)
    second = Post.id_for_derivative(ARTICLE_ID, article.version, 2)
    posts = {p.doc.id: p.doc for p in await find_docs(services.store, Post)}
    assert set(posts) == {first, second}
    text_post, deck = posts[first], posts[second]
    for post in (text_post, deck):
        assert post.status is PostStatus.DRAFT
        assert post.lane == "derivative"
        assert post.pillar == "identity"
        assert post.article_id == ARTICLE_ID
        assert post.brief_id == BRIEF_ID
        assert STORY_ID in post.story_ids
        assert len(post.hook_variants) == 3
        assert post.selected_hook_id == "h1"
        assert post.hook_type is HookType.RESULT_METRIC
        assert post.ai_draft_text == post.display_text
        assert post.lint is not None
        assert post.derived_claims is not None
        assert_saved_invariants(post)
    assert any(v.uses_owner_wording for v in text_post.hook_variants)
    assert deck.format == "carousel"
    assert deck.document_title == "The order that moved 2,000 apps"
    assert deck.carousel_spec is not None
    # The second draft saw the first one's opening (different angle).
    second_call = runner(services).calls_for(Task.DRAFT)[1]
    assert "<other_posts>" in "".join(m.text for m in second_call.request.messages)
    # Default prompts carry zero links: no URL of the article or the brief reaches the model.
    sent = sent_text(services, Task.DRAFT)
    assert "http://" not in sent
    assert "https://" not in sent
    assert "www." not in sent
    assert all(c.request.sensitivity is Sensitivity.ANONYMIZED for c in runner(services).calls)
    assert all(c.origin == "owner" for c in runner(services).calls)
    # Job documents hold references, never post text.
    assert "The last 600" not in json.dumps(job.to_body())

    # A second run of the same version creates nothing new (the cap is reached).
    again = await run_job(
        services, POSTS_DERIVE, job_input, job_id=derive_job_id(ARTICLE_ID, 3) + "-r1"
    )
    assert again.status == "succeeded"
    assert len(await find_docs(services.store, Post)) == 2


async def test_derive_refuses_an_article_not_yet_in_review(services: FakeServices) -> None:
    await seed_settings(services)
    await seed_article(services, status=ArticleStatus.DRAFTING)
    job = await run_job(
        services,
        POSTS_DERIVE,
        ArticleJobInput(article_id=ARTICLE_ID, origin="owner").to_json(),
        job_id="derive-x",
    )
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "article_not_ready"
    assert runner(services).calls == []


async def generating_post(services: FakeServices, **fields: object) -> Post:
    values: dict[str, object] = {"status": PostStatus.GENERATING, "pillar": "identity", **fields}
    post = sample_document(Post, **values)
    await upsert_doc(services.store, post)
    return post


async def test_native_post_from_the_bank(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    await usable_pov(services, POV_ID)
    post = await generating_post(services, lane="native", format="text")
    runner(services).add(Task.DRAFT, draft_reply(hashtags=("AISecurity", "NotOnTheList")))
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-1",
    )
    assert job.status == "succeeded", job.error
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.status is PostStatus.DRAFT
    assert saved.story_ids == [STORY_ID]
    assert saved.pov_ids == [POV_ID]
    assert saved.hashtags == []  # identity has no approved hashtags in the seed
    assert saved.hook_variants[1].uses_owner_wording  # the POV's own words, verified
    assert saved.hook_variants[2].uses_owner_wording  # the story's own words
    assert not saved.hook_variants[0].uses_owner_wording
    assert saved.derived_claims is not None
    assert saved.derived_claims.passed, saved.derived_claims
    assert_saved_invariants(saved)
    request = runner(services).calls_for(Task.DRAFT)[0].request
    material = request.messages[0].parts[0]
    assert "<story" in getattr(material, "text", "")
    assert "<pov" in getattr(material, "text", "")


async def test_scripted_output_with_a_link_is_blocked_by_lint(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    body = "We moved 2,000 apps in 14 months.\n\nDetails: https://example.org/apps and README.md"
    runner(services).add(Task.DRAFT, draft_reply(body=body))
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-2",
    )
    assert job.status == "succeeded"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.lint is not None
    assert not saved.lint.passed
    codes = {f.code for f in saved.lint.findings if f.severity == "block"}
    assert {"external-link", "domain-like"} <= codes
    assert_saved_invariants(saved)


async def test_quick_take_needs_and_uses_the_owner_take(services: FakeServices) -> None:
    await seed_settings(services, hashtags=("AISecurity",))
    idea = sample_document(
        Idea,
        title="A new MCP authorization draft",
        time_sensitive=True,
        expires_at="2026-11-29T12:00:00.000000Z",
    )
    await upsert_doc(services.store, idea)
    search = sample_document(
        QuickSearch,
        query="MCP authorization draft",
        answer="The draft requires resource indicators.[^src-mcp]",
        claims=[
            BriefClaim(
                id="c1", text="The draft requires resource indicators.", citation_id="src-mcp"
            )
        ],
        citations=[
            Citation(
                id="src-mcp",
                url="https://modelcontextprotocol.io/spec",
                title="Authorization",
                publisher=None,
                domain="modelcontextprotocol.io",
            )
        ],
    )
    await upsert_doc(services.store, search)
    take = "Resource indicators fix the token confusion I keep finding in agent pilots."
    post = with_extra(
        await generating_post(
            services,
            lane="quick_take",
            format="text",
            pillar="agents",
            idea_id=idea.id,
            quick_search_id=search.id,
        ),
        ownerTake=take,
    )
    await upsert_doc(services.store, post)
    runner(services).add(
        Task.DRAFT,
        draft_reply(
            body="Resource indicators fix the token confusion I keep finding in agent pilots.",
            hooks=(
                (
                    "Resource indicators fix the token confusion I keep finding",
                    HookType.DECISION_TRADEOFF,
                ),
                ("The draft requires resource indicators.", HookType.COUNTER_EVIDENCE),
                ("Check your agent tokens this week.", HookType.CHECKLIST_PROMISE),
            ),
            citation_ids=("src-mcp", "src-unknown"),
        ),
    )
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-3",
    )
    assert job.status == "succeeded", job.error
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert [a.citation_id for a in saved.attributions] == ["src-mcp"]
    assert saved.attributions[0].publisher == "Modelcontextprotocol"
    assert saved.hook_variants[0].uses_owner_wording
    sent = sent_text(services, Task.DRAFT)
    assert "<owner_take" in sent
    assert "https://" not in sent

    # Without a take the job fails and the post leaves `generating`.
    bare = await generating_post(
        services, lane="quick_take", format="text", pillar="agents", idea_id=idea.id
    )
    failed = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=bare.id, origin="owner").to_json(),
        job_id="gen-4",
    )
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "owner_take_required"
    stuck = (await read_doc(services.store, Post, bare.id)).doc
    assert stuck.status is PostStatus.NEEDS_ATTENTION
    assert stuck.last_error == "owner_take_required"


async def test_a_refusal_needs_attention_and_is_review_only(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    runner(services).add(Task.DRAFT, ScriptedReply(refused=True))
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-5",
    )
    assert job.status == "failed"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.status is PostStatus.NEEDS_ATTENTION
    assert saved.review_only
    assert "refusal" in saved.review_only_reasons
    assert saved.served_by[-1].refused


async def test_a_rejected_request_needs_attention_instead_of_stranding_the_post(
    services: FakeServices,
) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    rejected = ProviderRequestError(
        "anthropic rejected the request (HTTP 400)", provider="anthropic", retryable=False
    )
    runner(services).add(Task.DRAFT, ScriptedReply(error=rejected))
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-rejected",
    )
    assert job.status == "failed"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.status is PostStatus.NEEDS_ATTENTION
    assert saved.last_error == "generation_failed"


async def test_a_fallback_draft_is_review_only(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    runner(services).add(
        Task.DRAFT, ScriptedReply(value=draft_reply(), fallback_reason="rate_limited")
    )
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-6",
    )
    assert job.status == "succeeded"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.review_only
    assert saved.review_only_reasons == ["fallback_model"]


async def test_blocklisted_material_never_reaches_a_model(services: FakeServices) -> None:
    await seed_settings(services, terms=["Fabrikam"])
    await usable_story(services, STORY_ID, problem="Fabrikam had to move 2,000 apps.")
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-7",
    )
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert runner(services).calls == []
    events = json.dumps([e.model_dump(mode="json") for e in job.events])
    assert "Fabrikam" not in events


async def test_ai_use_not_confirmed_makes_no_call(services: FakeServices) -> None:
    await seed_settings(services, ai_use_confirmed=False)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-8",
    )
    assert job.error is not None
    assert job.error.code == "ai_use_not_confirmed"
    assert runner(services).calls == []


async def test_regenerating_hooks_keeps_the_body(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = await generating_post(services, lane="native", format="text", story_ids=[STORY_ID])
    runner(services).add(Task.DRAFT, draft_reply())
    runner(services).add(Task.FACT_CHECK, claims_reply())
    await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post.id, origin="owner").to_json(),
        job_id="gen-9",
    )
    drafted = (await read_doc(services.store, Post, post.id)).doc
    body = drafted.display_text.partition("\n\n")[2]
    await upsert_doc(services.store, drafted.model_copy(update={"status": PostStatus.GENERATING}))
    from blogwriter.linkedin.posts.derive import HookOut, HooksOut

    runner(services).add(
        Task.REVISE,
        HooksOut(
            hooks=[
                HookOut(
                    text="The last 600 apps set the date.",
                    hook_type=HookType.MISTAKE_LESSON,
                    concrete_detail="600 apps",
                ),
                HookOut(
                    text="We moved 2,000 apps in 14 months.",
                    hook_type=HookType.RESULT_METRIC,
                    concrete_detail="2,000",
                ),
                HookOut(
                    text="Count the odd apps first.",
                    hook_type=HookType.CHECKLIST_PROMISE,
                    concrete_detail="odd apps",
                ),
            ]
        ),
    )
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(
            post_id=post.id, origin="owner", what="hooks", instruction="Lead with the mistake."
        ).to_json(),
        job_id="gen-10",
    )
    assert job.status == "succeeded", job.error
    again = (await read_doc(services.store, Post, post.id)).doc
    assert again.display_text.startswith("The last 600 apps set the date.")
    assert again.display_text.partition("\n\n")[2] == body
    assert again.hook_type is HookType.MISTAKE_LESSON
    assert_saved_invariants(again)
    revise = runner(services).calls_for(Task.REVISE)[0]
    assert "Lead with the mistake." in "".join(m.text for m in revise.request.messages)


async def test_a_planned_slot_post_is_created_by_generate(services: FakeServices) -> None:
    from blogwriter.domain.planner import PlannedSlot, WeekPlan
    from blogwriter.domain.posts import Slot
    from blogwriter.domain.states import PlanStatus

    await seed_settings(services)
    await usable_story(services, STORY_ID)
    slot = Slot.at("2026-12-01", "10:00", "America/New_York", "primary")
    plan = sample_document(
        WeekPlan,
        status=PlanStatus.APPROVED,
        slots=[PlannedSlot(id="tue", slot=slot, lane="native", pillar="identity", format="text")],
    )
    await upsert_doc(services.store, plan)
    post_id = Post.id_for_plan_slot(plan.id, "tue")
    runner(services).add(Task.DRAFT, draft_reply())
    runner(services).add(Task.FACT_CHECK, claims_reply())
    job = await run_job(
        services,
        POSTS_GENERATE,
        GenerateInput(post_id=post_id, origin="owner").to_json(),
        job_id="gen-11",
    )
    assert job.status == "succeeded", job.error
    created = (await read_doc(services.store, Post, post_id)).doc
    assert created.week_plan_id == plan.id
    assert created.slot == slot
    assert created.status is PostStatus.DRAFT
    assert created.go_live_mode == "kit"  # LinkedIn is not connected
    assert T0  # the fixed clock
