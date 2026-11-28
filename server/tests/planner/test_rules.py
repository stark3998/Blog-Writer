"""Lane and mix rules, modes, pillar balance, spacing findings and slot assignment (pure)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from blogwriter.domain.articles import Article, ArticleMeta
from blogwriter.domain.common import Finding
from blogwriter.domain.planner import FlagshipPlan, PlannedSlot, WeekPlan
from blogwriter.domain.posts import Slot
from blogwriter.domain.settings import (
    CadenceSettings,
    FormatPacks,
    LaneSettings,
    PillarSettings,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.slots import SAME_DAY_CODE, TOO_CLOSE_CODE, week_slots
from blogwriter.domain.states import IdeaStatus, PlanStatus, PostStatus, ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.planner.plan import (
    FLAGSHIP_MISSING,
    MIX_DERIVATIVES,
    MIX_NATIVE,
    MIX_NATIVE_PILLAR,
    MIX_OFF_PILLAR,
    MODE_AWAY,
    MODE_POSTS,
    MODE_POSTS_MAX,
    SLOT_INCOMPLETE,
    SLOT_PAST,
    FlagshipPick,
    Material,
    PlanRules,
    PostPick,
    Proposal,
    SlotRefs,
    assign_slots,
    default_proposal,
    normalize,
    pillar_balance,
    plan_findings,
    slot_refs,
    title_is_plain,
    with_refs,
)
from blogwriter.planner.slots import TakenSlot, free_week_slots, slot_at

NY = "America/New_York"
RULES = PlanRules(
    cadence=CadenceSettings.default(SAMPLE_NOW),
    lanes=LaneSettings.default(SAMPLE_NOW),
    pillars=PillarSettings.default(SAMPLE_NOW),
    packs=FormatPacks.default(SAMPLE_NOW),
)
WEEK = "2026-11-30"
CHECKED = {"passed": True, "checkedAt": SAMPLE_NOW}


def story(title: str, pillar: str) -> StoryEntry:
    return sample_document(
        StoryEntry, title=title, pillars=[pillar], status=ReviewStatus.APPROVED, classifier=CHECKED
    )


def pov(statement: str, pillar: str) -> PovEntry:
    return sample_document(
        PovEntry,
        statement=statement,
        pillar=pillar,
        status=ReviewStatus.APPROVED,
        classifier=CHECKED,
    )


def idea(title: str, **fields: object) -> Idea:
    return sample_document(Idea, title=title, status=IdeaStatus.ACCEPTED, **fields)


def source_article() -> Article:
    meta = ArticleMeta(
        title="Scoping agent tokens per tool call", pillar="agents", format="deep-explainer"
    )
    return sample_document(Article, meta=meta)


def material(*, with_source: bool = True, ideas: tuple[Idea, ...] = ()) -> Material:
    stories = [
        story("The 2,000-app cut-over that slipped on 600 stragglers", "identity"),
        story("Risk tiers for 37 agents", "governance"),
        story("A token that outlived its agent", "agents"),
    ]
    povs = [pov("Agents need their own identities, not borrowed ones.", "agents")]
    source = source_article() if with_source else None
    return Material(
        ideas={i.id: i for i in ideas},
        stories={s.id: s for s in stories},
        povs={p.id: p for p in povs},
        source=source,
        source_pillar="agents" if source is not None else None,
    )


# --------------------------------------------------------------------------------------------
# Modes and the deterministic default
# --------------------------------------------------------------------------------------------


def test_normal_week_with_a_source_recuts_it_twice_and_adds_one_native_elsewhere() -> None:
    mat = material(
        ideas=(
            idea(
                "NIST AI RMF profile update",
                suggested_lane="flagship",
                pillar="governance",
                suggested_format="news-analysis",
            ),
        )
    )
    proposal = default_proposal(mat, RULES, mode="normal", posts=3)
    lanes = [pick.lane for pick in proposal.posts]
    assert lanes == ["derivative", "derivative", "native"]
    assert all(p.pillar == "agents" for p in proposal.posts if p.lane == "derivative")
    native = proposal.posts[2]
    assert native.pillar != "agents"
    assert native.story_ids  # a story from the bank
    assert proposal.flagship is not None
    assert (proposal.flagship.pillar, proposal.flagship.format) == ("governance", "news-analysis")
    assert proposal.flagship.optional is False


def test_without_a_source_a_time_sensitive_idea_becomes_a_quick_take() -> None:
    news = idea("Patch for an actively exploited IdP flaw", time_sensitive=True, pillar="identity")
    proposal = default_proposal(
        material(with_source=False, ideas=(news,)), RULES, mode="normal", posts=3
    )
    lanes = [pick.lane for pick in proposal.posts]
    assert lanes.count("derivative") == 0
    assert lanes.count("quick_take") == 1
    assert lanes.count("native") == 2
    quick = next(p for p in proposal.posts if p.lane == "quick_take")
    assert quick.idea_id == news.id
    natives = [p for p in proposal.posts if p.lane == "native"]
    assert proposal.flagship is not None
    assert all(p.pillar != proposal.flagship.pillar for p in natives)
    assert len({p.pillar for p in natives}) == 2  # balance spreads them


def test_light_week_has_two_posts_and_an_optional_flagship() -> None:
    proposal = default_proposal(material(), RULES, mode="light", posts=2)
    assert len(proposal.posts) == 2
    assert [p.lane for p in proposal.posts] == ["derivative", "native"]
    assert proposal.flagship is not None
    assert proposal.flagship.optional is True
    normal = normalize(proposal, material(), RULES, mode="light", posts=2)
    assert normal.flagship is not None
    assert normal.flagship.optional is True
    # The model may leave the flagship out in a light week.
    without = normalize(
        Proposal(flagship=None, posts=proposal.posts), material(), RULES, mode="light", posts=2
    )
    assert without.flagship is None


def test_away_week_has_nothing() -> None:
    assert default_proposal(material(), RULES, mode="away", posts=0) == Proposal(None, ())
    busy = default_proposal(material(), RULES, mode="normal", posts=3)
    away = normalize(busy, material(), RULES, mode="away", posts=3)
    assert away.flagship is None
    assert away.posts == ()


# --------------------------------------------------------------------------------------------
# Normalization of a proposal
# --------------------------------------------------------------------------------------------


def test_derivatives_are_capped_and_need_a_source() -> None:
    mat = material()
    three = tuple(PostPick("derivative", "agents", "carousel", f"Angle {n}") for n in range(3))
    normal = normalize(Proposal(None, three), mat, RULES, mode="normal", posts=3)
    lanes = [p.lane for p in normal.posts]
    assert lanes.count("derivative") == 2
    assert lanes.count("native") == 1
    no_source = normalize(
        Proposal(None, three), material(with_source=False), RULES, mode="normal", posts=3
    )
    assert [p.lane for p in no_source.posts] == ["native", "native", "native"]


def test_native_minimum_replaces_a_quick_take() -> None:
    news = idea("Exploited flaw in a popular IdP", time_sensitive=True)
    mat = material(ideas=(news,))
    picks = (
        PostPick("derivative", "agents", "carousel", "Per-call token scopes"),
        PostPick("derivative", "agents", "text", "What the audit log showed"),
        PostPick("quick_take", "identity", "text", "The patch window", idea_id=news.id),
    )
    normal = normalize(Proposal(None, picks), mat, RULES, mode="normal", posts=3)
    assert [p.lane for p in normal.posts] == ["derivative", "derivative", "native"]
    assert normal.posts[2].pillar != "agents"


def test_unknown_references_and_values_are_dropped() -> None:
    mat = material()
    picks = (
        PostPick(
            "native",
            "not-a-pillar",
            "poll",
            "Okta to Entra: the redirect list",
            story_ids=("story-0000000000000000000000000000",),
        ),
        PostPick(
            "quick_take",
            "identity",
            "text",
            "Reaction",
            idea_id="idea-0000000000000000000000000000",
        ),
        PostPick("native", "governance", "image_card", "Three lines per AI pilot"),
    )
    flagship = FlagshipPick(
        title="Agent sponsors", pillar="unknown", format="memo", idea_id="idea-x"
    )
    normal = normalize(Proposal(flagship, picks), mat, RULES, mode="normal", posts=3)
    assert normal.flagship is not None
    assert normal.flagship.pillar in RULES.pillar_ids
    assert normal.flagship.format in {pack.id for pack in RULES.packs.packs}
    assert normal.flagship.idea_id is None
    assert all(p.pillar in RULES.pillar_ids for p in normal.posts)
    assert all(p.format in ("carousel", "text", "image_card") for p in normal.posts)
    assert all(not p.story_ids or p.story_ids[0] in mat.stories for p in normal.posts)
    assert "quick_take" not in [p.lane for p in normal.posts]
    disabled = RULES.lanes.model_copy(
        update={"formats": RULES.lanes.formats.model_copy(update={"image_card_enabled": False})}
    )
    rules = PlanRules(RULES.cadence, disabled, RULES.pillars, RULES.packs)
    no_cards = normalize(Proposal(None, picks[2:]), mat, rules, mode="normal", posts=1)
    assert no_cards.posts[0].format == "text"


def test_native_on_the_flagship_pillar_moves() -> None:
    mat = material()
    picks = (
        PostPick("derivative", "agents", "carousel", "Per-call token scopes"),
        PostPick("derivative", "agents", "text", "Where refresh tokens hide"),
        PostPick("native", "agents", "text", "Agents need their own identities"),
    )
    normal = normalize(Proposal(None, picks), mat, RULES, mode="normal", posts=3)
    assert normal.posts[2].lane == "native"
    assert normal.posts[2].pillar != "agents"


def test_natives_stay_where_the_bank_can_draft_them() -> None:
    """With stories and POVs on the flagship's pillar only, a native post elsewhere could only
    fail to generate (``no_owner_material``): natives stay on that pillar (a warning) with a
    bank entry each, and the model's own pick on it is kept."""
    taken = story("A token that outlived its agent", "agents")
    take = pov("Agents need their own identities, not borrowed ones.", "agents")
    mat = Material(stories={taken.id: taken}, povs={take.id: take})
    flagship = FlagshipPick(title="Agent sponsors", pillar="agents", format="deep-explainer")
    proposal = default_proposal(mat, RULES, mode="normal", posts=3)
    natives = [p for p in proposal.posts if p.lane == "native"]
    assert len(natives) == 3
    assert {p.pillar for p in natives} == {"agents"}
    assert [*natives[0].story_ids, *natives[1].pov_ids] == [taken.id, take.id]
    model = PostPick("native", "agents", "text", "A token that outlived it", (taken.id,))
    normal = normalize(Proposal(flagship, (model,)), mat, RULES, mode="normal", posts=3)
    assert normal.posts[0] == model
    assert {p.pillar for p in normal.posts} == {"agents"}
    slots, short = assign_slots(normal, RULES, week_start=WEEK, suffix="abc123")
    assert short == 0
    plan = sample_document(
        WeekPlan,
        id="plan-2026-11-30",
        week_start=WEEK,
        slots=slots,
        flagship=normal.flagship.to_plan() if normal.flagship is not None else None,
    )
    findings = plan_findings(plan, RULES)
    assert {f.code for f in findings if f.severity == "block"} == set()
    assert [f.code for f in findings].count(MIX_NATIVE_PILLAR) == 3  # warned, not blocked
    # Material on another pillar: the natives move there, as before.
    other = story("The 2,000-app cut-over that slipped", "identity")
    elsewhere = Material(stories={taken.id: taken, other.id: other}, povs={take.id: take})
    moved = normalize(Proposal(flagship, (model,)), elsewhere, RULES, mode="normal", posts=1)
    assert moved.posts[0].pillar == "identity"
    assert moved.posts[0].story_ids == (other.id,)


@pytest.mark.parametrize(
    "title",
    [
        "Unpopular opinion: MFA prompts are fine",
        "Here's the thing about agent tokens",
        "Most people get conditional access wrong",
        "SAML is dead.",
        "Nobody talks about service accounts",
        "Let that sink in: 37 agents",
        "A game-changer for identity teams",
        "Leverage the NIST AI RMF",
        "A deep dive into MCP auth",
        "Is your agent inventory real?",
        "Read https://example.com first",
        "Token scopes #security",
    ],
)
def test_slop_titles_are_refused(title: str) -> None:
    assert not title_is_plain(title)


def test_plain_titles_pass_and_replace_slop() -> None:
    assert title_is_plain("Scoping agent tokens to one tool call, five minutes")
    assert title_is_plain("What 600 straggler apps cost the Okta to Entra move")
    mat = material()
    picks = (PostPick("native", "identity", "text", "Unpopular opinion: redirects are easy"),)
    normal = normalize(Proposal(None, picks), mat, RULES, mode="normal", posts=1)
    assert normal.posts[0].title
    assert title_is_plain(normal.posts[0].title)


# --------------------------------------------------------------------------------------------
# Slots, balance and findings
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("monday", ["2026-03-02", "2026-03-09", "2026-10-26", "2026-11-02", WEEK])
@pytest.mark.parametrize(("mode", "posts"), [("normal", 3), ("light", 2), ("normal", 5)])
def test_free_week_slots_match_the_domain_week_slots(monday: str, mode: str, posts: int) -> None:
    expected = week_slots(RULES.cadence, monday, "normal" if mode == "normal" else "light", posts)
    assert free_week_slots(RULES.cadence, monday, posts) == expected


def test_assignment_alternates_pillars_and_keeps_references() -> None:
    picks = (
        PostPick("native", "identity", "text", "The 600 stragglers", story_ids=("story-1",)),
        PostPick("derivative", "agents", "carousel", "Per-call scopes", article_id="art-1"),
        PostPick("derivative", "agents", "text", "Refresh tokens"),
    )
    slots, short = assign_slots(Proposal(None, picks), RULES, week_start=WEEK, suffix="abc123")
    assert short == 0
    assert [(s.slot.local_date, s.lane, s.pillar) for s in slots] == [
        ("2026-12-01", "derivative", "agents"),
        ("2026-12-02", "native", "identity"),
        ("2026-12-03", "derivative", "agents"),
    ]
    assert [s.id for s in slots] == ["s1-abc123", "s2-abc123", "s3-abc123"]
    assert slot_refs(slots[1]) == SlotRefs(story_ids=["story-1"])
    assert slot_refs(slots[0]).article_id == "art-1"


def test_assignment_skips_taken_and_past_days() -> None:
    picks = tuple(
        PostPick("native", p, "text", f"Post {p}") for p in ("agents", "identity", "governance")
    )
    slots, short = assign_slots(
        Proposal(None, picks), RULES, week_start=WEEK, taken_days={"2026-12-02"}, suffix="t"
    )
    assert short == 0
    assert [(s.slot.local_date, s.slot.local_time, s.slot.kind) for s in slots] == [
        ("2026-11-30", "12:00", "optional"),
        ("2026-12-01", "10:00", "primary"),
        ("2026-12-03", "10:00", "primary"),
    ]
    after = datetime(2026, 12, 2, 16, tzinfo=UTC)  # Wednesday 11:00 in New York
    late, short = assign_slots(
        Proposal(None, picks), RULES, week_start=WEEK, after=after, suffix="t"
    )
    assert [s.slot.local_date for s in late] == ["2026-12-03", "2026-12-04"]
    assert short == 1


def test_refs_survive_a_store_round_trip() -> None:
    slot = with_refs(
        PlannedSlot(id="s1", slot=slot_at(RULES.cadence, "2026-12-01", "10:00")),
        SlotRefs(story_ids=["story-1"], idea_id="idea-1"),
    )
    plan = sample_document(WeekPlan, slots=[slot])
    again = WeekPlan.from_document(plan.to_document())
    assert slot_refs(again.slots[0]) == SlotRefs(story_ids=["story-1"], idea_id="idea-1")


def planned(
    day: str, time: str, *, lane: str = "native", pillar: str | None = "identity", sid: str = ""
) -> PlannedSlot:
    return PlannedSlot.model_validate(
        {
            "id": sid or f"s-{day[-2:]}-{time[:2]}",
            "slot": slot_at(RULES.cadence, day, time),
            "lane": lane,
            "pillar": pillar,
            "format": "text",
        }
    )


def plan(
    *slots: PlannedSlot,
    mode: str = "normal",
    flagship: bool = True,
    status: PlanStatus = PlanStatus.PROPOSED,
) -> WeekPlan:
    head = (
        FlagshipPlan(title="Flagship", pillar="governance", format="framework")
        if flagship
        else None
    )
    return sample_document(WeekPlan, slots=list(slots), mode=mode, flagship=head, status=status)


def codes(findings: Sequence[Finding]) -> list[tuple[str, str]]:
    return [(finding.code, finding.severity) for finding in findings]


def test_clean_plan_has_no_findings() -> None:
    clean = plan(
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="native", pillar="identity"),
        planned("2026-12-03", "10:00", lane="derivative", pillar="agents"),
    )
    assert plan_findings(clean, RULES) == []
    assert pillar_balance(clean.slots, RULES) == {"agents": 2, "identity": 1, "governance": 0}


def test_spacing_same_day_blocks_and_close_slots_warn() -> None:
    crowded = plan(
        planned("2026-12-01", "10:00", sid="a"),
        planned("2026-12-01", "15:00", sid="b", pillar="agents"),
        planned("2026-12-02", "09:00", sid="c", pillar="governance"),
    )
    found = codes(plan_findings(crowded, RULES))
    assert (SAME_DAY_CODE, "block") in found
    assert (TOO_CLOSE_CODE, "warn") in found  # Tue 15:00 -> Wed 09:00 is 18 h
    relaxed = RULES.cadence.model_copy(update={"same_day_block": False})
    soft = codes(
        list(plan_findings(crowded, PlanRules(relaxed, RULES.lanes, RULES.pillars, RULES.packs)))
    )
    assert (SAME_DAY_CODE, "warn") in soft


def test_a_slot_on_a_day_another_post_holds_blocks() -> None:
    one = plan(
        planned("2026-12-01", "10:00"),
        planned("2026-12-02", "10:00", pillar="agents"),
        planned("2026-12-03", "10:00", pillar="governance"),
    )
    taken = [
        TakenSlot("post-other", slot_at(RULES.cadence, "2026-12-02", "16:00"), PostStatus.APPROVED)
    ]
    found = codes(plan_findings(one, RULES, taken=taken))
    assert (SAME_DAY_CODE, "block") in found


def test_mix_rules() -> None:
    too_many = plan(
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-03", "10:00", lane="derivative", pillar="agents"),
    )
    found = codes(plan_findings(too_many, RULES))
    assert (MIX_DERIVATIVES, "block") in found
    assert (MIX_NATIVE, "warn") in found
    off_pillar = plan(
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="quick_take", pillar=None),
        planned("2026-12-03", "10:00", lane="native", pillar="identity"),
    )
    assert (MIX_OFF_PILLAR, "block") in codes(plan_findings(off_pillar, RULES))
    same_pillar = plan(
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="native", pillar="agents"),
        planned("2026-12-03", "10:00", lane="native", pillar="identity"),
    )
    assert (MIX_NATIVE_PILLAR, "warn") in codes(plan_findings(same_pillar, RULES))
    no_pillar = plan(planned("2026-12-01", "10:00", lane="native", pillar=None))
    assert (SLOT_INCOMPLETE, "block") in codes(plan_findings(no_pillar, RULES))


def test_four_of_five_on_pillar_is_enough() -> None:
    five = plan(
        planned("2026-11-30", "12:00", lane="native", pillar="identity"),
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="quick_take", pillar=None),
        planned("2026-12-03", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-04", "15:00", lane="native", pillar="governance"),
    )
    found = codes(plan_findings(five, RULES))
    assert all(code != MIX_OFF_PILLAR for code, _ in found)
    assert (MODE_POSTS, "warn") in found  # a normal week has 3


def test_mode_findings() -> None:
    light = plan(
        planned("2026-12-01", "10:00", lane="derivative", pillar="agents"),
        planned("2026-12-02", "10:00", lane="native", pillar="identity"),
        planned("2026-12-03", "10:00", lane="derivative", pillar="agents"),
        mode="light",
        flagship=False,
    )
    found = codes(plan_findings(light, RULES))
    assert (MODE_POSTS, "warn") in found
    assert all(code != FLAGSHIP_MISSING for code, _ in found)  # optional in light weeks
    away = plan(planned("2026-12-01", "10:00"), mode="away", flagship=False)
    assert (MODE_AWAY, "warn") in codes(plan_findings(away, RULES))
    assert (FLAGSHIP_MISSING, "warn") in codes(plan_findings(plan(flagship=False), RULES))
    six = plan(*(planned(f"2026-12-0{d}", "10:00", pillar="agents") for d in range(1, 7)))
    assert (MODE_POSTS_MAX, "block") in codes(plan_findings(six, RULES))


def test_past_slots_warn_until_approved() -> None:
    now = datetime(2026, 12, 2, 18, tzinfo=UTC)
    proposed = plan(planned("2026-12-01", "10:00"), planned("2026-12-03", "10:00", pillar="agents"))
    assert (SLOT_PAST, "warn") in codes(plan_findings(proposed, RULES, now=now))
    approved = proposed.model_copy(update={"status": PlanStatus.APPROVED})
    assert all(code != SLOT_PAST for code, _ in codes(plan_findings(approved, RULES, now=now)))


def test_spring_forward_week_slots_are_an_hour_earlier_in_utc() -> None:
    before = free_week_slots(RULES.cadence, "2026-03-02", 3)
    after = free_week_slots(RULES.cadence, "2026-03-09", 3)
    assert [s.utc[11:16] for s in before] == ["15:00", "15:00", "15:00"]
    assert [s.utc[11:16] for s in after] == ["14:00", "14:00", "14:00"]
    assert all(isinstance(s, Slot) and s.local_time == "10:00" for s in [*before, *after])
