"""Derived claims (numbers exact against the material, FACT_CHECK for claims, inline
attribution), near-duplicates against the last 90 days, and the fail-closed paths."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.articles import Article, ArticleMeta, OwnerSignalRef
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MAX_STORED_FINDINGS, Citation, Finding
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.posts import Attribution, Post
from blogwriter.domain.research import Brief, BriefClaim, BriefStat
from blogwriter.domain.settings import EmployerPolicy
from blogwriter.domain.states import ArticleStatus, PostStatus
from blogwriter.domain.testing import (
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    sample_document,
)
from blogwriter.linkedin.posts._post import finding_field
from blogwriter.linkedin.posts.checks import (
    ClaimCheck,
    check_post,
    failing_checks,
    lint_state,
    load_check_settings,
    near_duplicate,
    number_tokens,
    unsupported_numbers,
)
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.testing import (
    CLEAN_BODY,
    T0,
    claims_reply,
    seed_settings,
    usable_story,
)

GOLDENS = Path(__file__).parent / "goldens"
STORY_ID = "story-0193f00000000000000000000001"
ARTICLE_ID = "art-0193f00000000000000000000001"
BRIEF_ID = "brief-run-0193f00000000000000000000001"


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


def post_with(text: str, /, **fields: object) -> Post:
    display, payload = serialize(text, ["AISecurity"])
    values: dict[str, object] = {
        "display_text": display,
        "payload": payload,
        "hashtags": ["AISecurity"],
        "pillar": "agents",
        **fields,
    }
    return sample_document(Post, **values)


# --------------------------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------------------------


def test_number_tokens_golden() -> None:
    text = (
        "1. We moved 2,000 apps (2000 in the plan) in 14 months, about 40% faster.\n"
        "2) Budget: $4.2M, then 4.2 million. Ratio 3x. LLM01 and ASI03 are ids.\n"
        "CVE-2025-1234 on 2026-11-26 at 10:00; version 1.2.3."
    )
    produced = [[t.canonical, t.start, t.end] for t in number_tokens(text)]
    golden = json.loads((GOLDENS / "numbers.json").read_text(encoding="utf-8"))
    assert produced == golden


def test_derived_numbers_must_match_exactly() -> None:
    sources = ["We moved 2,000 apps in 14 months; 40% of them were custom."]
    ok = unsupported_numbers([("displayText", "2000 apps in 14 months, 40% custom.")], sources)
    assert ok == []
    rounded = unsupported_numbers([("displayText", "About 2,100 apps in 14 months.")], sources)
    assert [(f.code, f.severity, finding_field(f)) for f in rounded] == [
        ("unsupported-number", "block", "displayText")
    ]
    unit = unsupported_numbers([("slides[2]", "40 apps were custom.")], sources)
    assert [finding_field(f) for f in unit] == ["slides[2]"]
    assert unit[0].start == 0


# --------------------------------------------------------------------------------------------
# Near-duplicates
# --------------------------------------------------------------------------------------------


def test_near_duplicate_is_word_five_gram_jaccard() -> None:
    text = "we moved two thousand apps in fourteen months and the last ones set the date"
    assert near_duplicate(text, [("post-a", text)]) == (1.0, "post-a")
    score, other = near_duplicate(text, [("post-a", "an entirely different post about tokens")])
    assert (score, other) == (0.0, None)
    half = "we moved two thousand apps in fourteen months but nothing else matches here at all"
    score, other = near_duplicate(text, [("post-b", half), ("post-c", "unrelated words only")])
    assert other == "post-b"
    assert 0 < score < 1
    assert near_duplicate("", [("post-a", text)]) == (0.0, None)


async def test_near_duplicates_compare_with_the_last_90_days(services: FakeServices) -> None:
    await seed_settings(services)
    stamp_old = to_iso(T0 - timedelta(days=91))
    stamp_recent = to_iso(T0 - timedelta(days=10))
    original = post_with(
        CLEAN_BODY,
        id="post-0193f00000000000000000000010",
        created_at=stamp_old,
        updated_at=stamp_old,
    )
    await upsert_doc(services.store, original)
    candidate = post_with(CLEAN_BODY)
    runner(services).add(Task.FACT_CHECK, claims_reply())
    checked = await check_post(services, candidate, origin="owner", job_id=None)
    assert checked.near_duplicate is not None
    assert checked.near_duplicate.passed
    recent = post_with(
        CLEAN_BODY,
        id="post-0193f00000000000000000000011",
        created_at=stamp_recent,
        updated_at=stamp_recent,
    )
    await upsert_doc(services.store, recent)
    runner(services).add(Task.FACT_CHECK, claims_reply())
    again = await check_post(services, candidate, origin="owner", job_id=None)
    near = again.near_duplicate
    assert near is not None
    assert not near.passed
    assert near.findings[0].code == "near-duplicate"
    assert near.findings[0].ref == recent.id
    archived = recent.model_copy(update={"status": PostStatus.ARCHIVED})
    await upsert_doc(services.store, archived)
    runner(services).add(Task.FACT_CHECK, claims_reply())
    third = await check_post(services, candidate, origin="owner", job_id=None)
    assert third.near_duplicate is not None
    assert third.near_duplicate.passed


# --------------------------------------------------------------------------------------------
# Derived claims
# --------------------------------------------------------------------------------------------


async def seed_article(services: FakeServices) -> tuple[Article, Brief]:
    citation = Citation(
        id="src-nist",
        url="https://pages.nist.gov/800-63-4/",
        title="Digital Identity Guidelines",
        publisher="NIST",
        domain="pages.nist.gov",
    )
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
        stats=[
            BriefStat(
                id="stat-1",
                value="73%",
                label="of apps used legacy auth",
                citation_ids=["src-nist"],
            )
        ],
        citations=[citation],
    )
    await upsert_doc(services.store, brief)
    await usable_story(services, STORY_ID)
    article = sample_document(
        Article,
        id=ARTICLE_ID,
        status=ArticleStatus.APPROVED,
        version=3,
        brief_id=BRIEF_ID,
        meta=ArticleMeta(
            title="Moving 2,000 apps",
            slug="moving-apps",
            excerpt="How.",
            pillar="identity",
            format="case-study",
        ),
        markdown=(
            "We moved [2,000 apps](https://example.org/apps) in 14 months.[^src-nist]\n\n"
            "[^src-nist]: https://pages.nist.gov/800-63-4/"
        ),
        owner_signals=[OwnerSignalRef(kind="story", id=STORY_ID)],
    )
    await upsert_doc(services.store, article)
    return article, brief


async def test_claims_are_checked_against_the_material(services: FakeServices) -> None:
    await seed_settings(services)
    await seed_article(services)
    text = (
        "We moved 2,000 apps in 14 months.\n\n"
        "NIST's Digital Identity Guidelines say phishing-resistant MFA is required at AAL3.\n\n"
        "73% of apps used legacy auth."
    )
    post = post_with(
        text,
        lane="derivative",
        article_id=ARTICLE_ID,
        attributions=[
            Attribution(
                publisher="NIST", title="Digital Identity Guidelines", citation_id="src-nist"
            )
        ],
    )
    runner(services).add(
        Task.FACT_CHECK,
        claims_reply(
            ClaimCheck(
                quote="We moved 2,000 apps in 14 months.",
                supported=True,
                source=f"story:{STORY_ID}",
            ),
            ClaimCheck(
                quote="phishing-resistant MFA is required at AAL3",
                supported=True,
                source="brief:claim-1",
                research=True,
                citation_id="src-nist",
            ),
            ClaimCheck(
                quote="73% of apps used legacy auth.",
                supported=True,
                source="brief:stat-1",
                research=True,
                citation_id="src-nist",
            ),
        ),
    )
    checked = await check_post(services, post, origin="owner", job_id=None)
    claims = checked.derived_claims
    assert claims is not None
    assert claims.passed, claims.findings
    call = runner(services).calls_for(Task.FACT_CHECK)[0]
    assert call.request.sensitivity is Sensitivity.ANONYMIZED
    assert not call.request.tools
    sent = call.request.system + "".join(m.text for m in call.request.messages)
    assert "https://" not in sent
    assert "http://" not in sent
    assert checked.served_by[-1].step == "derived_claims"


async def test_unsupported_claims_and_missing_attribution_block(services: FakeServices) -> None:
    await seed_settings(services)
    await seed_article(services)
    text = "Phishing-resistant MFA is required at AAL3.\n\nEvery bank will adopt it by June."
    post = post_with(text, lane="derivative", article_id=ARTICLE_ID)
    runner(services).add(
        Task.FACT_CHECK,
        claims_reply(
            ClaimCheck(
                quote="Phishing-resistant MFA is required at AAL3.",
                supported=True,
                research=True,
                citation_id="src-nist",
            ),
            ClaimCheck(quote="Every bank will adopt it by June.", supported=False),
        ),
    )
    checked = await check_post(services, post, origin="owner", job_id=None)
    claims = checked.derived_claims
    assert claims is not None
    assert not claims.passed
    found = {(f.code, f.start) for f in claims.findings}
    assert ("attribution-missing", 0) in found
    assert ("unsupported-claim", text.index("Every")) in found


async def test_attribution_must_be_inline(services: FakeServices) -> None:
    await seed_settings(services)
    await seed_article(services)
    post = post_with(
        "We moved 2,000 apps in 14 months.",
        lane="derivative",
        article_id=ARTICLE_ID,
        attributions=[
            Attribution(
                publisher="NIST", title="Digital Identity Guidelines", citation_id="src-nist"
            )
        ],
    )
    runner(services).add(Task.FACT_CHECK, claims_reply())
    checked = await check_post(services, post, origin="owner", job_id=None)
    claims = checked.derived_claims
    assert claims is not None
    assert "attribution-not-inline" in {f.code for f in claims.findings}


async def test_the_claims_check_fails_closed(services: FakeServices) -> None:
    await seed_settings(services, ai_use_confirmed=False)
    await seed_article(services)
    post = post_with("We moved 2,000 apps in 14 months.", lane="derivative", article_id=ARTICLE_ID)
    checked = await check_post(services, post, origin="owner", job_id=None)
    assert checked.derived_claims is not None
    assert "ai-use-not-confirmed" in {f.code for f in checked.derived_claims.findings}
    assert runner(services).calls_for(Task.FACT_CHECK) == []

    await seed_settings(services, terms=["Fabrikam"])
    blocked = post_with(
        "Fabrikam moved 2,000 apps in 14 months.", lane="derivative", article_id=ARTICLE_ID
    )
    checked = await check_post(services, blocked, origin="owner", job_id=None)
    assert checked.derived_claims is not None
    assert "claims-blocklist-hit" in {f.code for f in checked.derived_claims.findings}
    assert runner(services).calls_for(Task.FACT_CHECK) == []

    await seed_settings(services, blocklist_configured=False)
    checked = await check_post(services, post, origin="owner", job_id=None)
    assert checked.derived_claims is not None
    assert "blocklist-not-configured" in {f.code for f in checked.derived_claims.findings}
    assert runner(services).calls_for(Task.FACT_CHECK) == []

    await seed_settings(services)
    services.ai = None
    checked = await check_post(services, post, origin="owner", job_id=None)
    assert checked.derived_claims is not None
    assert "claims-unavailable" in {f.code for f in checked.derived_claims.findings}


async def test_a_refused_check_and_a_missing_source_fail(services: FakeServices) -> None:
    await seed_settings(services)
    post = post_with("We moved 2,000 apps in 14 months.", lane="native", story_ids=[STORY_ID])
    runner(services).add(Task.FACT_CHECK, ScriptedReply(refused=True))
    checked = await check_post(services, post, origin="owner", job_id=None)
    assert checked.derived_claims is not None
    found = {f.code for f in checked.derived_claims.findings}
    assert {"source-missing", "claims-check-failed"} <= found
    assert checked.served_by[-1].refused


async def test_failing_checks_need_current_passed_results(services: FakeServices) -> None:
    await seed_settings(services)
    await usable_story(services, STORY_ID)
    post = post_with(CLEAN_BODY, lane="native", story_ids=[STORY_ID])
    assert failing_checks(post) == ["lint", "derived_claims", "near_duplicate"]
    runner(services).add(Task.FACT_CHECK, claims_reply())
    checked = await check_post(services, post, origin="owner", job_id=None)
    assert failing_checks(checked) == []
    edited_text, payload = serialize(CLEAN_BODY + " More.", ["AISecurity"])
    edited = checked.model_copy(update={"display_text": edited_text, "payload": payload})
    assert failing_checks(edited) == ["lint", "derived_claims", "near_duplicate"]


async def test_native_posts_on_the_flagship_pillar_warn(services: FakeServices) -> None:
    from blogwriter.domain.planner import FlagshipPlan, WeekPlan
    from blogwriter.domain.settings import LaneSettings
    from blogwriter.linkedin.posts.checks import lane_findings

    lanes = LaneSettings.default(to_iso(T0))
    plan = sample_document(
        WeekPlan,
        id="plan-2026-11-23",
        week_start="2026-11-23",
        flagship=FlagshipPlan(title="Moving apps", pillar="identity", format="case-study"),
    )
    await upsert_doc(services.store, plan)
    same = post_with(CLEAN_BODY, lane="native", pillar="identity")
    found = await lane_findings(services.store, same, lanes=lanes, now=T0)
    assert [(f.code, f.severity) for f in found] == [("native-same-pillar", "warn")]
    other = post_with(CLEAN_BODY, lane="native", pillar="agents")
    assert await lane_findings(services.store, other, lanes=lanes, now=T0) == []
    derivative = post_with(CLEAN_BODY, lane="derivative", pillar="identity")
    assert await lane_findings(services.store, derivative, lanes=lanes, now=T0) == []
    relaxed = lanes.model_copy(update={"native_other_pillar": False})
    assert await lane_findings(services.store, same, lanes=relaxed, now=T0) == []


async def test_with_derivatives_natives_avoid_the_pillar_they_promote(
    services: FakeServices,
) -> None:
    """The planner keeps natives off the derivatives' pillar (Monday's flagship) when the week
    has derivatives; the lint warns about the same pillar, not this week's flagship's."""
    from blogwriter.domain.planner import FlagshipPlan, PlannedSlot, WeekPlan
    from blogwriter.domain.posts import Slot
    from blogwriter.domain.settings import LaneSettings
    from blogwriter.linkedin.posts.checks import lane_findings

    lanes = LaneSettings.default(to_iso(T0))
    plan = sample_document(
        WeekPlan,
        id="plan-2026-11-23",
        week_start="2026-11-23",
        flagship=FlagshipPlan(title="Next Monday", pillar="identity", format="case-study"),
        slots=[
            PlannedSlot(
                id="s1",
                slot=Slot.at("2026-11-24", "10:00", "America/New_York"),
                lane="derivative",
                pillar="agents",
            )
        ],
    )
    await upsert_doc(services.store, plan)
    on_flagship = post_with(CLEAN_BODY, lane="native", pillar="identity")
    assert await lane_findings(services.store, on_flagship, lanes=lanes, now=T0) == []
    on_derivatives = post_with(CLEAN_BODY, lane="native", pillar="agents")
    found = await lane_findings(services.store, on_derivatives, lanes=lanes, now=T0)
    assert [(f.code, f.severity) for f in found] == [("native-same-pillar", "warn")]


# --------------------------------------------------------------------------------------------
# Employer forbidden topics
# --------------------------------------------------------------------------------------------


async def test_a_forbidden_topic_blocks_a_post(services: FakeServices) -> None:
    """Native posts and quick takes never pass gate 3: the post lint applies the employer
    policy's forbidden topics itself (named by number, never quoted)."""
    await seed_settings(services)
    stamp = to_iso(T0)
    policy = EmployerPolicy(
        id=EmployerPolicy.SINGLETON_ID,
        created_at=stamp,
        updated_at=stamp,
        ai_use_confirmed=True,
        ai_use_confirmed_at=stamp,
        forbidden_topics=["pending acquisition"],
        configured=True,
    )
    await upsert_doc(services.store, policy)
    settings = await load_check_settings(services.store, now=T0)
    post = post_with("Our Pending  Acquisition changes how we scope agent tokens.")
    state = lint_state(post, settings, slop=services.slop, now=T0)
    forbidden = [f for f in state.findings if f.code == "forbidden-topic"]
    assert [(f.severity, f.ref, finding_field(f)) for f in forbidden] == [
        ("block", "topic-1", "displayText")
    ]
    assert "acquisition" not in forbidden[0].message.lower()
    assert not state.passed
    clean = lint_state(post_with(CLEAN_BODY), settings, slop=services.slop, now=T0)
    assert all(f.code != "forbidden-topic" for f in clean.findings)


async def test_a_stored_check_counts_the_findings_it_leaves_out(services: FakeServices) -> None:
    """``CheckState.truncated``: the composer says "and N more" for a capped result."""
    await seed_settings(services)
    settings = await load_check_settings(services.store, now=T0)
    clean = lint_state(post_with(CLEAN_BODY), settings, slop=services.slop, now=T0)
    assert clean.truncated == 0
    extra = [
        Finding(code="extra-note", severity="warn", message=f"Note {n}.", field="displayText")
        for n in range(MAX_STORED_FINDINGS + 10)
    ]
    state = lint_state(post_with(CLEAN_BODY), settings, slop=services.slop, now=T0, extra=extra)
    assert len(state.findings) == MAX_STORED_FINDINGS
    assert state.truncated == len(clean.findings) + len(extra) - MAX_STORED_FINDINGS
    assert state.model_dump(by_alias=True)["truncated"] == state.truncated
    assert {finding_field(f) for f in state.findings if f.code == "extra-note"} == {"displayText"}
