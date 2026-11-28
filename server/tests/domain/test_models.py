"""Model-specific validation: the rules each document enforces on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from blogwriter.db.base import MAX_DOCUMENT_BYTES, prepare_write
from blogwriter.domain.articles import (
    GATE_ORDER,
    MAX_ARTICLE_MARKDOWN_BYTES,
    MAX_EMBEDDING_DIMENSIONS,
    PUBLISHED_MARKER_NAME,
    Article,
    ArticleMeta,
    ArticleVersion,
    CalibrationFlags,
    Clearance,
    Correction,
    GateName,
    GateOverride,
    GateReport,
    GateResult,
    GateVerdict,
    OwnerSignalRef,
    PublishState,
    autosave_snapshot_due,
    check_article_transition,
    has_published_marker,
    versions_to_prune,
)
from blogwriter.domain.common import (
    MAX_STORED_FINDINGS,
    Citation,
    ClassifierCheck,
    Finding,
    MediaRef,
    Quote,
    ServedByRecord,
    cap_findings,
)
from blogwriter.domain.engagement import EngagementItem, third_party_expiry
from blogwriter.domain.ephemeral import OAuthState, SeedText
from blogwriter.domain.planner import Reminder, WeekPlan
from blogwriter.domain.posts import (
    Approval,
    KitInfo,
    Post,
    PublishIntent,
    Slot,
    settle_publish_intent,
)
from blogwriter.domain.research import (
    Brief,
    BriefClaim,
    BriefStat,
    QuickSearch,
    ResearchRun,
    strip_verbatim,
    verbatim_spans,
)
from blogwriter.domain.settings import (
    LINKEDIN_SCOPES,
    Blocklist,
    BlocklistTerm,
    BrandColors,
    BrandKit,
    CadenceSettings,
    FormatPacks,
    LaneSettings,
    LinkedInConnection,
    PillarSettings,
)
from blogwriter.domain.signals import Idea, Source
from blogwriter.domain.states import (
    ArticleStatus,
    KitState,
    PostStatus,
    ReviewStatus,
    TransitionError,
    TransitionRefused,
)
from blogwriter.domain.stories import Interview, StoryEntry
from blogwriter.domain.testing import SAMPLE_NOW, fake_vault, png_bytes, sample_document
from blogwriter.publish.contract import MAX_MARKDOWN_BYTES

NOW = SAMPLE_NOW


def sha(char: str) -> str:
    return char * 64


def finding(severity: str = "block", *, overridable: bool = False) -> Finding:
    return Finding.model_validate(
        {"code": "x", "severity": severity, "message": "m", "overridable": overridable}
    )


# ---------------------------------------------------------------------------------------------
# Shared parts
# ---------------------------------------------------------------------------------------------


def test_finding_spans() -> None:
    Finding(code="ai-tell", severity="warn", message="m", start=1, end=3)
    with pytest.raises(ValidationError):
        Finding(code="x", severity="warn", message="m", start=1)
    with pytest.raises(ValidationError):
        Finding(code="x", severity="warn", message="m", start=3, end=1)
    with pytest.raises(ValidationError):
        Finding(code="Bad Code", severity="warn", message="m")


def test_served_by_record_from_the_router() -> None:
    from blogwriter.ai.router import ServedBy
    from blogwriter.ai.types import Task

    served = ServedBy(
        task=Task.DRAFT,
        model="claude-opus-5-5",
        deployment="opus",
        provider="anthropic",
        effort="high",
        fallback_reason="refusal",
        review_only=True,
    )
    record = ServedByRecord.from_served_by(served, step="draft", at=NOW, refused=True)
    assert record.task == "draft"
    assert record.fallback
    assert record.review_only
    assert record.refused
    assert record.model_dump()["fallbackReason"] == "refusal"


def test_media_ref_url_only_for_media() -> None:
    ref = MediaRef(
        role="image",
        name="uploads/2026-11/" + sha("a") + ".png",
        sha256=sha("a"),
        content_type="image/png",
        size_bytes=10,
    )
    assert ref.url == "/api/media/uploads/2026-11/" + sha("a") + ".png"
    with pytest.raises(ValueError, match="media-container"):
        _ = ref.model_copy(update={"container": "published"}).url
    with pytest.raises(ValidationError):
        MediaRef(role="image", name="../x", sha256=sha("a"), content_type="image/png", size_bytes=1)


def citation(cid: str) -> Citation:
    return Citation(id=cid, url=f"https://example.com/{cid}", title="T", domain="example.com")


def test_brief_quote_budget_is_200_chars_per_source() -> None:
    base = {
        "id": "brief-1",
        "createdAt": NOW,
        "updatedAt": NOW,
        "topic": "MCP auth",
        "pillar": "agents",
        "executiveSummary": "S",
        "citations": [citation("src-a"), citation("src-b")],
    }
    ok = [
        Quote(id="q1", citation_id="src-a", text="x" * 120),
        Quote(id="q2", citation_id="src-a", text="y" * 80),
    ]
    Brief.model_validate({**base, "quotes": ok})
    over = [*ok, Quote(id="q3", citation_id="src-a", text="z")]
    with pytest.raises(ValidationError, match="200"):
        Brief.model_validate({**base, "quotes": over})
    with pytest.raises(ValidationError):
        Quote(id="q", citation_id="src-a", text="x" * 201)


def test_brief_references_must_resolve() -> None:
    base = {
        "id": "brief-1",
        "createdAt": NOW,
        "updatedAt": NOW,
        "topic": "MCP auth",
        "pillar": "agents",
        "executiveSummary": "S",
        "citations": [citation("src-a")],
    }
    with pytest.raises(ValidationError, match="unknown citation"):
        Brief.model_validate(
            {**base, "claims": [BriefClaim(id="c1", text="t", citation_id="src-zzz")]}
        )
    with pytest.raises(ValidationError, match="unknown quote"):
        Brief.model_validate(
            {
                **base,
                "claims": [BriefClaim(id="c1", text="t", citation_id="src-a", quote_ids=["q9"])],
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        Brief.model_validate({**base, "citations": [citation("src-a"), citation("src-a")]})


def test_key_stats_need_two_sources() -> None:
    BriefStat(id="s1", value="73%", label="l", key=False, citation_ids=["src-a"])
    BriefStat(id="s1", value="73%", label="l", key=True, citation_ids=["src-a", "src-b"])
    with pytest.raises(ValidationError, match="two sources"):
        BriefStat(id="s1", value="73%", label="l", key=True, citation_ids=["src-a", "src-a"])


def test_quick_search_claims_cite_its_sources() -> None:
    with pytest.raises(ValidationError):
        sample_document(QuickSearch, claims=[BriefClaim(id="c", text="t", citation_id="src-x")])


# ---------------------------------------------------------------------------------------------
# Articles and gates
# ---------------------------------------------------------------------------------------------


def test_markdown_cap_matches_the_publish_contract() -> None:
    assert MAX_ARTICLE_MARKDOWN_BYTES == MAX_MARKDOWN_BYTES
    sample_document(Article, markdown="é" * (MAX_ARTICLE_MARKDOWN_BYTES // 2))
    with pytest.raises(ValidationError, match="larger"):
        sample_document(Article, markdown="é" * (MAX_ARTICLE_MARKDOWN_BYTES // 2 + 1))


def test_meta_missing_for_publish_and_tags() -> None:
    assert ArticleMeta().missing_for_publish() == ["title", "slug", "excerpt", "pillar", "format"]
    meta = ArticleMeta(
        title="T", slug="t", excerpt="E", pillar="agents", format="deep-explainer", tags=["mcp"]
    )
    assert meta.missing_for_publish() == []
    with pytest.raises(ValidationError):
        ArticleMeta(tags=["a", "a"])
    with pytest.raises(ValidationError):
        ArticleMeta(slug="Not A Slug")
    with pytest.raises(ValidationError):
        ArticleMeta.model_validate({"series": {"id": "s", "title": "S", "part": 3, "total": 2}})


def test_article_hash_and_writer() -> None:
    article = sample_document(Article, markdown="Body", meta=ArticleMeta(title="T"))
    first = article.compute_sha256()
    assert article.model_copy(update={"markdown": "Body!"}).compute_sha256() != first
    assert article.writer == "owner"
    assert article.model_copy(update={"status": ArticleStatus.DRAFTING}).writer == "pipeline"


def test_version_ids_are_deterministic() -> None:
    assert ArticleVersion.id_for("art-1", 42) == "art-1-v00042"
    with pytest.raises(ValueError, match="start at 1"):
        ArticleVersion.id_for("art-1", 0)
    version = sample_document(ArticleVersion)
    assert version.partition_key == version.article_id


def result(gate: GateName, verdict: GateVerdict, *findings: Finding) -> GateResult:
    return GateResult(gate=gate, verdict=verdict, findings=list(findings), ran_at=NOW)


def report(*results: GateResult) -> GateReport:
    return GateReport(content_sha256=sha("c"), version=1, ran_at=NOW, results=list(results))


def test_gate_report_readiness() -> None:
    passing = [result(gate, GateVerdict.PASS) for gate in GATE_ORDER]
    assert report(*passing).auto_publish_ready
    assert report(*passing).manual_publish_ready
    assert not report(*passing[:-1]).auto_publish_ready
    assert report(*passing[:-1]).blocking() == [GateName.OUTBOUND]

    warned = [*passing[:-1], result(GateName.OUTBOUND, GateVerdict.WARN)]
    assert report(*warned).auto_publish_ready

    rubric_failed = result(GateName.RUBRIC, GateVerdict.FAIL, finding(overridable=True))
    failing = [rubric_failed, *passing[1:]]
    assert not report(*failing).manual_publish_ready
    assert report(*failing).blocking() == [GateName.RUBRIC]
    overridden = rubric_failed.model_copy(
        update={"override": GateOverride(reason="Owner reviewed", at=NOW)}
    )
    assert report(overridden, *passing[1:]).manual_publish_ready
    assert not report(overridden, *passing[1:]).auto_publish_ready


def test_hard_gates_and_hard_findings_cannot_be_overridden() -> None:
    override = GateOverride(reason="Please", at=NOW)
    blocklisted = result(GateName.CONFIDENTIALITY, GateVerdict.FAIL, finding(overridable=False))
    assert not blocklisted.overridable
    assert not blocklisted.model_copy(update={"override": override}).cleared_for_manual_publish
    skipped_lint = result(GateName.LINT, GateVerdict.SKIPPED)
    assert not skipped_lint.overridable
    skipped_originality = result(GateName.ORIGINALITY, GateVerdict.SKIPPED)
    assert skipped_originality.overridable
    mixed = result(GateName.FACT_CHECK, GateVerdict.FAIL, finding(), finding(overridable=True))
    assert not mixed.overridable


def test_one_result_per_gate() -> None:
    with pytest.raises(ValidationError):
        report(result(GateName.LINT, GateVerdict.PASS), result(GateName.LINT, GateVerdict.FAIL))


def test_clearance_binds_to_a_version() -> None:
    assert Clearance().valid_for(sha("a"))
    clearance = Clearance(required=True, content_sha256=sha("a"), attested_at=NOW)
    assert clearance.valid_for(sha("a"))
    assert not clearance.valid_for(sha("b"))
    assert not Clearance(required=True).valid_for(sha("a"))


# ---------------------------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------------------------


def media(char: str, alt: str | None = "Slide") -> MediaRef:
    return MediaRef(
        role="carousel_pdf",
        name=f"posts/post-1/{sha(char)}.pdf",
        sha256=sha(char),
        content_type="application/pdf",
        size_bytes=100,
        alt_text=alt,
    )


def test_approval_hash_binds_text_and_media() -> None:
    post = sample_document(Post, display_text="Hello", payload="Hello", media=[media("a")])
    approved_hash = post.compute_approval_hash()
    approved = post.model_copy(update={"approval": Approval(sha256=approved_hash, approved_at=NOW)})
    assert approved.approval_current
    for change in (
        {"display_text": "Hello!"},
        {"payload": "Hello\\!"},
        {"document_title": "New title"},
        {"media": [media("b")]},
        {"media": [media("a", alt="Other alt")]},
    ):
        assert not approved.model_copy(update=change).approval_current, change


def test_slot_utc_must_match_local_time() -> None:
    slot = Slot.at("2026-12-01", "10:00", "America/New_York")
    assert slot.utc == "2026-12-01T15:00:00.000000Z"
    with pytest.raises(ValidationError, match="does not match"):
        Slot(
            local_date="2026-12-01",
            local_time="10:00",
            timezone="America/New_York",
            utc="2026-12-01T14:00:00.000000Z",
        )
    with pytest.raises(ValueError, match="hour"):
        Slot.at("2026-12-01", "25:00", "America/New_York")
    with pytest.raises(ValueError, match="IANA"):
        Slot.at("2026-12-01", "10:00", "Mars/Olympus")


def test_go_live_at_prefers_posted_then_kit_then_slot() -> None:
    slot = Slot.at("2026-12-01", "10:00", "America/New_York")
    post = sample_document(Post, slot=slot)
    assert post.go_live_at == slot.utc
    info = KitInfo(state=KitState.NATIVELY_SCHEDULED, prepared_at=NOW, scheduled_at=NOW)
    kit = post.model_copy(update={"kit": info})
    assert kit.go_live_at == NOW
    posted = kit.model_copy(update={"posted_at": "2026-12-01T15:01:00.000000Z"})
    assert posted.go_live_at == "2026-12-01T15:01:00.000000Z"


def test_post_field_rules() -> None:
    with pytest.raises(ValidationError):
        sample_document(Post, hashtags=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        sample_document(Post, hashtags=["#AI"])
    with pytest.raises(ValidationError):
        sample_document(Post, linkedin_urn="urn:li:person:abc")
    sample_document(Post, linkedin_urn="urn:li:share:7158000000000000000")
    assert sample_document(Post).status is PostStatus.DRAFT


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------


def test_default_settings_follow_the_plan() -> None:
    pillars = PillarSettings.default(NOW)
    assert [p.id for p in pillars.pillars] == ["agents", "identity", "governance"]
    agents = pillars.get("agents")
    assert agents is not None
    assert "atlas.mitre.org" in agents.research_domains
    assert pillars.get("nope") is None
    packs = FormatPacks.default(NOW)
    case = packs.get("case-study")
    assert case is not None
    assert (case.min_words, case.max_words) == (1500, 2500)
    assert case.owner_input == "full_interview"
    lanes = LaneSettings.default(NOW)
    assert lanes.derivatives_max_per_week == 2
    assert lanes.on_pillar_min_share == 0.8
    cadence = CadenceSettings.default(NOW)
    assert cadence.timezone == "America/New_York"
    assert (cadence.plan_week.day, cadence.plan_week.time) == ("sun", "18:00")
    assert (cadence.veto_notice.day, cadence.veto_notice.time) == ("fri", "12:00")
    assert (cadence.article_publish.day, cadence.article_publish.time) == ("mon", "09:00")
    assert [(s.day, s.time, s.kind) for s in cadence.post_slots] == [
        ("tue", "10:00", "primary"),
        ("wed", "10:00", "primary"),
        ("thu", "10:00", "primary"),
        ("mon", "12:00", "optional"),
        ("fri", "15:00", "optional"),
    ]
    assert cadence.reminders.reply_minutes == [15, 60, 180]
    assert cadence.missed_slot_grace_minutes == 120


def test_pillar_rules() -> None:
    pillars = PillarSettings.default(NOW)
    duplicate = [*pillars.pillars, pillars.pillars[0]]
    with pytest.raises(ValidationError, match="unique"):
        PillarSettings.model_validate({**pillars.model_dump(), "pillars": duplicate[:4]})
    heavy = [p.model_copy(update={"target_share": 0.5}) for p in pillars.pillars]
    with pytest.raises(ValidationError, match="shares"):
        PillarSettings.model_validate({**pillars.model_dump(), "pillars": heavy})
    with pytest.raises(ValidationError):
        PillarSettings.model_validate(
            {
                **pillars.model_dump(),
                "pillars": [
                    {**pillars.pillars[0].model_dump(), "researchDomains": ["https://x.io"]}
                ],
            }
        )


def test_brand_colors_are_normalized_and_pass_wcag_aa() -> None:
    assert BrandColors(ink="#0e2a24").ink == "#0E2A24"
    with pytest.raises(ValidationError):
        BrandColors(ink="red;background:url(x)")

    def luminance(color: str) -> float:
        def channel(value: int) -> float:
            s = value / 255
            return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

        r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    def contrast(fg: str, bg: str) -> float:
        high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    c = BrandColors()
    pairs = [
        (c.ink, c.paper),
        (c.muted, c.paper),
        (c.signal_text, c.paper),
        (c.paper, c.ink),
        (c.muted_on_ink, c.ink),
        (c.signal, c.ink),
        (c.ink, c.signal),
    ]
    assert all(contrast(fg, bg) >= 4.5 for fg, bg in pairs)
    assert not BrandKit.default(NOW).complete


def test_blocklist_terms_are_unique() -> None:
    term = BlocklistTerm(id="t1", term="Contoso")
    with pytest.raises(ValidationError, match="unique"):
        Blocklist.model_validate(
            {
                **Blocklist.default(NOW).model_dump(),
                "terms": [term, BlocklistTerm(id="t2", term="contoso")],
            }
        )
    assert not Blocklist.default(NOW).configured


def test_linkedin_connection_scopes_token_and_alerts() -> None:
    connection = LinkedInConnection.default(NOW)
    assert tuple(connection.scopes) == LINKEDIN_SCOPES == ("openid", "profile", "w_member_social")
    with pytest.raises(ValidationError, match="constant"):
        LinkedInConnection.model_validate({**connection.model_dump(), "scopes": ["openid"]})
    token = fake_vault().encrypt("secret-token", context=connection.id)
    connected = LinkedInConnection.model_validate(
        {**connection.model_dump(), "accessToken": token, "connectedAt": NOW}
    )
    with pytest.raises(ValidationError):
        LinkedInConnection.model_validate({**connection.model_dump(), "accessToken": "plain"})
    start = datetime(2026, 11, 26, 12, tzinfo=UTC)
    assert not connected.banner_due(start + timedelta(days=49))
    assert connected.banner_due(start + timedelta(days=50))
    assert connected.alerts_due(start + timedelta(days=52)) == []
    assert connected.alerts_due(start + timedelta(days=57)) == [53, 57]
    alerted = connected.model_copy(update={"alerts_sent": {"53": NOW}})
    assert alerted.alerts_due(start + timedelta(days=59)) == [57, 59]
    assert connection.alerts_due(start) == []
    assert connection.token_day(start) is None


# ---------------------------------------------------------------------------------------------
# Other documents
# ---------------------------------------------------------------------------------------------


def test_week_plan_needs_a_monday_and_matching_id() -> None:
    sample_document(WeekPlan)
    with pytest.raises(ValidationError, match="Monday"):
        sample_document(WeekPlan, id="plan-2026-12-01", week_start="2026-12-01")
    with pytest.raises(ValidationError, match="id"):
        sample_document(WeekPlan, id="plan-2026-12-07")


def test_source_shape() -> None:
    with pytest.raises(ValidationError, match="url"):
        sample_document(Source, url=None)
    with pytest.raises(ValidationError, match="query"):
        sample_document(Source, type="scout_query", url=None)
    sample_document(Source, type="scout_query", url=None, query="MCP security news")


def test_story_usable_needs_approval_and_classifier() -> None:
    story = sample_document(StoryEntry)
    assert not story.usable
    approved = story.model_copy(update={"status": ReviewStatus.APPROVED})
    assert not approved.usable
    check = ClassifierCheck(passed=True, checked_at=NOW)
    assert approved.model_copy(update={"classifier": check}).usable
    with pytest.raises(ValidationError):
        sample_document(StoryEntry, pillars=[])


def test_interview_answers() -> None:
    interview = sample_document(
        Interview, answers=[{"questionId": "q1", "text": "Anonymized answer.", "confirmedAt": NOW}]
    )
    answer = interview.answer("q1")
    assert answer is not None
    assert answer.text == "Anonymized answer."
    assert interview.answer("q2") is None


def test_third_party_text_expires_in_48_hours() -> None:
    now = datetime(2026, 11, 26, 12, tzinfo=UTC)
    expires_at, ttl = third_party_expiry(now)
    assert (expires_at, ttl) == ("2026-11-28T12:00:00.000000Z", 172_800)
    item = sample_document(EngagementItem, expires_at=expires_at, ttl=ttl)
    assert item.to_document()["ttl"] == 172_800
    assert item.expired(now + timedelta(hours=48))


def test_oauth_state_ids_hide_the_state() -> None:
    now = datetime(2026, 11, 26, 12, tzinfo=UTC)
    state = OAuthState.issue("a-secret-state", now=now)
    assert "a-secret-state" not in state.to_document().values()
    assert state.id == OAuthState.id_for("a-secret-state")
    assert not state.expired(now + timedelta(minutes=9))
    assert state.expired(now + timedelta(minutes=10))
    assert state.return_path == "/settings/integrations"


def test_png_helper_is_a_real_png() -> None:
    assert png_bytes().startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------------------------
# Review fixes: findings, reports and document size
# ---------------------------------------------------------------------------------------------


def test_finding_refs_never_need_json_escapes() -> None:
    Finding(code="x", severity="warn", message="m", ref="https://example.com/a?b=1#c")
    Finding(code="x", severity="warn", message="m", ref="src-nist-ai-rmf")
    for bad in ('say "hi"', "back\\slash", "line\nbreak", "", "x" * 201):
        with pytest.raises(ValidationError):
            Finding(code="x", severity="warn", message="m", ref=bad)


def test_cap_findings_never_cuts_a_hard_blocker_for_a_softer_one() -> None:
    soft = [
        Finding(code="slop", severity="block", message="m", start=i, end=i, overridable=True)
        for i in range(60)
    ]
    hard = Finding(code="blocklisted-term", severity="block", message="m", start=999, end=999)
    warn = Finding(code="ai-tell", severity="warn", message="m", start=0, end=1)
    kept, dropped = cap_findings([warn, *soft, hard])
    assert len(kept) == MAX_STORED_FINDINGS
    assert dropped == 12
    assert kept[0] is hard
    assert warn not in kept
    result = GateResult(
        gate=GateName.ORIGINALITY,
        verdict=GateVerdict.FAIL,
        findings=kept,
        truncated=dropped,
        ran_at=NOW,
    )
    assert not result.overridable
    with pytest.raises(ValidationError):
        GateResult(gate=GateName.LINT, verdict=GateVerdict.FAIL, findings=[*soft], ran_at=NOW)
    with pytest.raises(ValueError, match="limit"):
        cap_findings(soft, limit=0)


def test_an_article_at_every_maximum_still_fits_in_cosmos() -> None:
    """Probe from the review: a full gate report used to push an article past 2 MB."""
    message = "m" * 500
    ref = "r" * 200
    findings = [
        Finding(code="x", severity="block", message=message, ref=ref, start=1, end=2)
        for _ in range(MAX_STORED_FINDINGS)
    ]
    served = ServedByRecord(
        step="draft",
        at=NOW,
        task="draft",
        model="m" * 100,
        deployment="d" * 100,
        provider="anthropic",
        effort="xhigh",
        fallback_reason="f" * 40,
        prompt_version="p" * 40,
        cost_usd=1.5,
    )
    results = [
        GateResult(
            gate=gate,
            verdict=GateVerdict.FAIL,
            score=9.5,
            dimensions={f"dimension_{i}": 9.0 for i in range(8)},
            findings=findings,
            truncated=10_000,
            ran_at=NOW,
            served_by=[served] * 20,
        )
        for gate in GATE_ORDER
    ]
    report = GateReport(content_sha256=sha("c"), version=99, ran_at=NOW, results=results)
    media_item = MediaRef(
        role="hero_image",
        name="articles/" + "a" * 280 + ".png",
        sha256=sha("a"),
        content_type="image/png",
        size_bytes=10,
        alt_text="a" * 1000,
        filename="f" * 120,
        linkedin_urn="u" * 200,
    )
    article = sample_document(
        Article,
        # Newlines are the worst case JSON can do to normalized Markdown: two bytes each.
        markdown="\n" * MAX_ARTICLE_MARKDOWN_BYTES,
        gates=report,
        embedding=[0.123456789012345678] * MAX_EMBEDDING_DIMENSIONS,
        served_by=[served] * 50,
        media=[media_item] * 50,
        corrections=[Correction(date="2026-11-26", note="n" * 1000, created_at=NOW)] * 100,
        owner_signals=[OwnerSignalRef(kind="interview", id="intv-1", quote="q" * 1000)] * 50,
        review_only_reasons=["fallback_model"] * 20,
    )
    size = len(prepare_write("articles", article.to_document()).text.encode("utf-8"))
    assert size < MAX_DOCUMENT_BYTES, size


# ---------------------------------------------------------------------------------------------
# Review fixes: article rules
# ---------------------------------------------------------------------------------------------


def test_live_and_eval_articles_have_extra_rules() -> None:
    live = sample_document(
        Article, status=ArticleStatus.IN_REVIEW, publish=PublishState(live=True, slug="x")
    )
    with pytest.raises(TransitionRefused) as caught:
        check_article_transition(live, ArticleStatus.ARCHIVED)
    assert caught.value.code == "live_article"
    assert check_article_transition(live, ArticleStatus.RESEARCHING) is ArticleStatus.RESEARCHING
    evaluation = sample_document(Article, status=ArticleStatus.IN_REVIEW, purpose="eval")
    with pytest.raises(TransitionRefused) as refused:
        check_article_transition(evaluation, ArticleStatus.APPROVED)
    assert refused.value.code == "eval_article"
    assert check_article_transition(evaluation, ArticleStatus.ARCHIVED) is ArticleStatus.ARCHIVED
    with pytest.raises(TransitionError):
        check_article_transition(evaluation, ArticleStatus.PUBLISHED)
    failed = sample_document(Article, status=ArticleStatus.PUBLISH_FAILED)
    assert check_article_transition(failed, ArticleStatus.UNPUBLISHING) is (
        ArticleStatus.UNPUBLISHING
    )


def test_versions_referenced_elsewhere_are_never_pruned() -> None:
    article = sample_document(
        Article,
        calibration=CalibrationFlags(gated_version=3),
        gates=GateReport(content_sha256=sha("c"), version=40, ran_at=NOW),
        publish=PublishState(last_published_version=7),
    )
    assert article.pinned_versions() == {3, 7, 40}
    assert sample_document(Article).pinned_versions() == frozenset()
    pruned = versions_to_prune(range(1, 61), keep=50, pinned=article.pinned_versions())
    assert pruned == [1, 2, 4, 5, 6, 8, 9, 10]
    assert versions_to_prune([1, 2], keep=50) == []
    with pytest.raises(ValueError, match="at least one"):
        versions_to_prune([1], keep=0)


def test_autosaves_coalesce_into_versions() -> None:
    now = datetime(2026, 11, 26, 12, 30, tzinfo=UTC)
    assert autosave_snapshot_due(None, None, now=now)
    assert autosave_snapshot_due("pipeline", NOW, now=now)
    assert not autosave_snapshot_due("autosave", "2026-11-26T12:25:00.000000Z", now=now)
    assert autosave_snapshot_due("autosave", "2026-11-26T12:20:00.000000Z", now=now)


def test_slugs_owned_by_the_article() -> None:
    article = sample_document(
        Article, publish=PublishState(slug="agent-identity", previous_slugs=["agent-ids"])
    )
    assert article.owns_slug("agent-identity")
    assert article.owns_slug("agent-ids")
    assert not article.owns_slug("someone-elses")
    assert not sample_document(Article).owns_slug("agent-identity")


def test_published_marker_proves_the_new_version_is_live() -> None:
    stamp = "2026-12-07T14:00:05Z"
    page = f'<head><meta charset="utf-8"><meta content="{stamp}" name="{PUBLISHED_MARKER_NAME}">'
    assert has_published_marker(page, stamp)
    assert has_published_marker(page.encode(), stamp)
    assert not has_published_marker(page, "2026-11-30T14:00:05Z")
    single = f"<META NAME='{PUBLISHED_MARKER_NAME}' CONTENT='{stamp}'/>"
    assert has_published_marker(single, stamp)
    assert not has_published_marker("<html><body>old page</body></html>", stamp)


def test_derived_document_ids_are_retry_safe() -> None:
    assert ResearchRun.id_for_pipeline("art-1", 2) == ResearchRun.id_for_pipeline("art-1", 2)
    assert ResearchRun.id_for_pipeline("art-1", 2) != ResearchRun.id_for_pipeline("art-1", 3)
    assert ResearchRun.id_for_eval("evt-1", "job-1").startswith("run-")
    assert Interview.id_for_pipeline("art-1", 1).startswith("intv-")
    assert Article.id_for_eval("evt-1", "job-1").startswith("art-")
    assert Post.id_for_derivative("art-1", 3, 0) != Post.id_for_derivative("art-1", 3, 1)
    assert Post.id_for_plan_slot("plan-2026-11-30", "tue").startswith("post-")
    assert Brief.id_for("run-1") == "brief-run-1"


# ---------------------------------------------------------------------------------------------
# Review fixes: posts, reminders, seeds, research
# ---------------------------------------------------------------------------------------------


def publishing_post() -> Post:
    intent = PublishIntent(id="intent-0001", created_at=NOW, approval_sha256=sha("a"))
    return sample_document(Post, status=PostStatus.PUBLISHING, intent=intent)


def test_a_post_is_publishing_exactly_while_its_intent_is_pending() -> None:
    publishing_post()
    with pytest.raises(ValidationError, match="pending"):
        sample_document(Post, status=PostStatus.PUBLISHING)
    pending = PublishIntent(id="intent-0001", created_at=NOW, approval_sha256=sha("a"))
    with pytest.raises(ValidationError, match="pending"):
        sample_document(Post, status=PostStatus.APPROVED, intent=pending)
    settled = pending.model_copy(update={"status": "failed"})
    sample_document(Post, status=PostStatus.APPROVED, intent=settled)


@pytest.mark.parametrize(
    ("outcome", "status", "intent_status", "last_error"),
    [
        ("posted", PostStatus.POSTED, "succeeded", None),
        ("rejected", PostStatus.APPROVED, "failed", "publish_rejected"),
        ("unknown", PostStatus.NEEDS_ATTENTION, "unknown", "publish_unknown"),
    ],
)
def test_settle_publish_intent(
    outcome: str, status: PostStatus, intent_status: str, last_error: str | None
) -> None:
    later = "2026-11-26T12:00:30.000000Z"
    settled = settle_publish_intent(
        publishing_post(),
        outcome,  # type: ignore[arg-type]
        now=later,
        http_status=201 if outcome == "posted" else 422,
    )
    assert settled.status is status
    assert settled.intent is not None
    assert (settled.intent.status, settled.intent.resolved_at) == (intent_status, later)
    assert settled.status_changed_at == later
    assert settled.last_error == last_error
    with pytest.raises(TransitionError):
        settle_publish_intent(settled, "posted", now=later)


def test_reminders_are_sendable_and_keyed() -> None:
    reminder = sample_document(Reminder)
    assert reminder.id == Reminder.id_for(reminder.key)
    with pytest.raises(ValidationError, match="push payload"):
        sample_document(Reminder, title="Reply \u202e now")
    with pytest.raises(ValidationError, match="push payload"):
        sample_document(Reminder, url="/today?x=a b")
    with pytest.raises(ValidationError):
        sample_document(Reminder, reminder_kind="plan_ready")


def test_seed_text_lives_in_ephemeral_with_a_ttl() -> None:
    now = datetime(2026, 11, 26, 12, tzinfo=UTC)
    seed = SeedText.issue("Led a 2,000-app identity migration.", "cv", now=now)
    assert seed.id == SeedText.id_for(seed.text)
    assert seed.expired(now + timedelta(days=1))
    assert seed.to_document()["ttl"] == 86_400
    assert seed.CONTAINER == "ephemeral"


def test_claims_lose_verbatim_runs_of_the_passage() -> None:
    passage = (
        "The Model Context Protocol specification requires that authorization servers "
        "implement PKCE for all clients, including confidential ones."
    )
    echoed = (
        "MCP: the model context protocol SPECIFICATION requires that authorization servers "
        "implement PKCE. Plan for it."
    )
    spans = verbatim_spans(echoed, passage)
    assert len(spans) == 1
    assert "specification" in echoed[spans[0][0] : spans[0][1]].casefold()
    stripped = strip_verbatim(echoed, passage)
    assert "authorization servers" not in stripped
    assert stripped.startswith("MCP:")
    assert stripped.endswith("Plan for it.")
    assert strip_verbatim(passage, passage) == ""
    summary = "Authorization servers must use PKCE for every client."
    assert verbatim_spans(summary, passage) == []
    assert strip_verbatim(summary, passage) == summary
    with pytest.raises(ValueError, match="min_chars"):
        verbatim_spans(summary, passage, min_chars=0)


def test_idea_suggested_format_takes_format_packs_and_post_formats() -> None:
    for value in ("deep-dive", "image_card", "carousel"):
        assert sample_document(Idea, suggested_format=value).suggested_format == value
    for bad in ("Image_Card", "image__card", "-x", "x" * 41):
        with pytest.raises(ValidationError):
            sample_document(Idea, suggested_format=bad)


def test_engagement_drafts_have_the_final_reply_budget() -> None:
    assert sample_document(EngagementItem, draft_text="d" * 1250).draft_text == "d" * 1250
    with pytest.raises(ValidationError):
        sample_document(EngagementItem, draft_text="d" * 1251)
