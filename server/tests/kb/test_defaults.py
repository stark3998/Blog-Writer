"""The plan's defaults: what every kb GET answers before the owner saves anything (golden), and
the plan values behind them (pillars, format packs, lanes, cadence, autonomy)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import regex

from blogwriter.core.testing import OWNER_OID, principal_headers
from blogwriter.domain.base import parse_iso
from blogwriter.domain.common import blocking
from blogwriter.domain.settings import (
    AutonomySettings,
    BrandKit,
    CadenceSettings,
    FormatPacks,
    LaneSettings,
    PillarSettings,
    auto_publish_decision,
)
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.kb import PLAN_PILLARS, kb_default, validate_brand
from blogwriter.kb.checks import cadence_problems, lane_problems, pillar_problems
from blogwriter.linkedin.lint import AI_TELLS
from blogwriter.linkedin.littletext import Hashtag, parse_display

READ = principal_headers(OWNER_OID)
GOLDEN = Path(__file__).parent / "goldens" / "defaults.json"
SINGLETON_ROUTES = ("pillars", "formats", "lanes", "brand", "blocklist", "policy", "cadence")


async def _defaults(client: httpx.AsyncClient) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in SINGLETON_ROUTES:
        response = await client.get(f"/api/kb/{path}", headers=READ)
        assert response.status_code == 200
        body = response.json()
        assert body["etag"] is None
        assert "etag" not in response.headers
        out[path] = body["item"]
    models = (await client.get("/api/kb/models", headers=READ)).json()
    assert models["preferences"]["etag"] is None
    out["models"] = models["preferences"]["item"]
    autonomy = (await client.get("/api/kb/autonomy", headers=READ)).json()
    assert autonomy["settings"]["etag"] is None
    out["autonomy"] = autonomy["settings"]["item"]
    out["autonomyDecision"] = autonomy["decision"]
    return out


async def test_every_default_matches_the_golden(client: httpx.AsyncClient) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert await _defaults(client) == golden, (
        "kb defaults changed: update tests/kb/goldens/defaults.json deliberately"
    )


def test_plan_pillars() -> None:
    pillars = kb_default(PillarSettings, SAMPLE_NOW)
    assert [p.id for p in pillars.pillars] == ["agents", "identity", "governance"]
    assert [p.name for p in pillars.pillars] == [
        "AI agents & security",
        "Identity modernization",
        "AI governance",
    ]
    for pillar in pillars.pillars:
        assert pillar.topics
        assert pillar.description
        assert pillar.audience
        assert len(pillar.vocabulary) >= 5
        assert 0 <= len(pillar.hashtags) <= 3
        assert pillar.research_domains
    identity = pillars.get("identity")
    assert identity is not None
    assert identity.renames["Azure AD"] == "Microsoft Entra ID"
    assert identity.renames["Azure Active Directory"] == "Microsoft Entra ID"
    assert "Microsoft Entra ID" in identity.vocabulary
    # The plan's research allowlists (Entra docs, NIST SP 800-63-4, Okta docs, ISO 42001).
    domains = {p.id: p.research_domains for p in pillars.pillars}
    assert domains == {
        "agents": [
            "modelcontextprotocol.io",
            "genai.owasp.org",
            "atlas.mitre.org",
            "learn.microsoft.com",
            "arxiv.org",
            "cisa.gov",
        ],
        "identity": [
            "learn.microsoft.com",
            "techcommunity.microsoft.com",
            "openid.net",
            "pages.nist.gov",
            "developer.okta.com",
            "help.okta.com",
        ],
        "governance": ["nist.gov", "owasp.org", "iso.org", "eur-lex.europa.eu", "oecd.ai"],
    }
    assert sum(p.target_share for p in pillars.pillars) <= 1.0
    assert pillar_problems(pillars) == []


def test_plan_pillars_extend_the_domain_default() -> None:
    """kb adds what the domain default leaves empty and changes nothing it sets (so the domain
    default can take these values over without moving anything)."""
    domain = PillarSettings.default(SAMPLE_NOW)
    assert [p.id for p in domain.pillars] == [p.id for p in PLAN_PILLARS]
    for base, plan in zip(domain.pillars, PLAN_PILLARS, strict=True):
        assert (base.name, base.topics, base.target_share) == (
            plan.name,
            plan.topics,
            plan.target_share,
        )
        assert base.research_domains == plan.research_domains
        assert base.renames.items() <= plan.renames.items()
        for field in ("description", "audience", "vocabulary", "hashtags"):
            current = getattr(base, field)
            assert not current or current == getattr(plan, field)


def test_kb_default_returns_fresh_copies() -> None:
    first = kb_default(PillarSettings, SAMPLE_NOW)
    first.pillars[0].vocabulary.append("changed")
    assert "changed" not in kb_default(PillarSettings, SAMPLE_NOW).pillars[0].vocabulary
    assert "changed" not in PLAN_PILLARS[0].vocabulary


def _ai_tells(text: str) -> list[str]:
    """``linkedin.lint``'s recipe: curly apostrophes folded, whole-phrase matches only."""
    folded = text.translate(str.maketrans({"\u2019": "'", "\u2018": "'"}))
    return [
        pattern
        for pattern in AI_TELLS
        if regex.search(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])", folded)
    ]


def test_default_texts_carry_no_ai_tells() -> None:
    texts = [
        text
        for pillar in PLAN_PILLARS
        for text in (pillar.name, pillar.topics, pillar.description, pillar.audience)
    ]
    texts += [
        item
        for pack in FormatPacks.default(SAMPLE_NOW).packs
        for item in (pack.name, *pack.must_contain)
    ]
    assert {text: _ai_tells(text) for text in texts if _ai_tells(text)} == {}


def test_default_hashtags_are_approvable_together() -> None:
    """Every pillar's hashtags form one approved list the LinkedIn serializer accepts."""
    tags = [tag for pillar in PLAN_PILLARS for tag in pillar.hashtags]
    for tag in tags:
        Hashtag(tag)
    tokens = parse_display(" ".join(f"#{tag}" for tag in tags), tags)
    assert [token.tag for token in tokens if isinstance(token, Hashtag)] == tags


def test_plan_format_packs() -> None:
    packs = {pack.id: pack for pack in FormatPacks.default(SAMPLE_NOW).packs}
    assert {
        pack_id: (pack.min_words, pack.max_words, pack.owner_input)
        for pack_id, pack in packs.items()
    } == {
        "case-study": (1500, 2500, "full_interview"),
        "framework": (1200, 2000, "full_interview"),
        "deep-explainer": (1800, 3000, "micro_interview"),
        "news-analysis": (900, 1500, "micro_interview"),
    }
    assert packs["framework"].banned == ["AI-invented frameworks"]


def test_plan_lanes_and_mix_rules() -> None:
    lanes = LaneSettings.default(SAMPLE_NOW)
    assert lanes.derivatives_max_per_week == 2
    assert lanes.native_min_per_week == 1
    assert lanes.native_other_pillar
    assert lanes.on_pillar_min_share == 0.8
    assert (lanes.quick_take.min_chars, lanes.quick_take.max_chars) == (1200, 1500)
    assert lanes.quick_take.max_age_hours == 72
    assert lanes.quick_take.owner_take_required
    assert (lanes.formats.carousel_min_slides, lanes.formats.carousel_max_slides) == (8, 12)
    assert (lanes.formats.text_min_chars, lanes.formats.text_max_chars) == (1200, 2000)
    assert (lanes.repurpose.min_weeks, lanes.repurpose.max_weeks) == (8, 12)
    assert lanes.repurpose.requires_novelty_check
    assert lane_problems(lanes, {p.id for p in PLAN_PILLARS}) == []


def test_plan_cadence() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW)
    assert cadence.timezone == "America/New_York"
    assert (cadence.plan_week.day, cadence.plan_week.time) == ("sun", "18:00")
    assert (cadence.article_publish.day, cadence.article_publish.time) == ("mon", "09:00")
    assert (cadence.veto_notice.day, cadence.veto_notice.time) == ("fri", "12:00")
    assert cadence.min_veto_lead_hours == 48
    slots = [(s.day, s.time, s.kind, s.window_minutes) for s in cadence.post_slots]
    assert slots == [
        ("tue", "10:00", "primary", 120),
        ("wed", "10:00", "primary", 120),
        ("thu", "10:00", "primary", 120),
        ("mon", "12:00", "optional", 120),
        ("fri", "15:00", "optional", 120),
    ]
    per_week = cadence.posts_per_week
    assert (per_week.normal, per_week.light, per_week.away, per_week.max) == (3, 2, 0, 5)
    assert cadence.same_day_block
    assert cadence.missed_slot_grace_minutes == 120
    assert cadence.default_mode == "normal"
    assert cadence_problems(cadence) == []


def test_plan_autonomy() -> None:
    autonomy = AutonomySettings.default(SAMPLE_NOW)
    assert not autonomy.auto_publish_enabled
    assert not autonomy.paused
    assert (autonomy.calibration.clean_streak, autonomy.calibration.required) == (0, 6)
    decision = auto_publish_decision(autonomy, now=parse_iso(SAMPLE_NOW))
    assert not decision.allowed
    assert decision.reasons == ("not_enabled", "calibrating", "judge_uncalibrated")


def test_default_brand_renders_once_named() -> None:
    brand = BrandKit.default(SAMPLE_NOW)
    findings = validate_brand(brand, disclaimer=None)
    assert [f.code for f in findings] == ["brand-incomplete"]
    assert blocking(findings) == []
    named = brand.model_copy(update={"author_name": "A. Author"})
    assert validate_brand(named, disclaimer="Views are my own.") == []
