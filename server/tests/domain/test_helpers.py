"""Pure helpers: hashes, URL normalization, slot and week math, blocklist matching, calibration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from blogwriter.db.base import validate_id
from blogwriter.domain.base import derived_id, distinct_stamps, new_id, parse_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.confidentiality import (
    BLOCKLIST_CODE,
    BLOCKLIST_NOT_CONFIGURED_CODE,
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    blocklist_hit,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.hashing import approval_hash, article_sha256, normalize_url, url_sha256
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    BlocklistTerm,
    EmployerPolicy,
    JudgeCalibration,
    auto_publish_decision,
    is_substantive_edit,
    record_correction,
    record_review,
)
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.domain.weeks import (
    add_days,
    local_date_time,
    plan_id,
    slot_utc,
    week_start,
    weekday_of,
)
from blogwriter.jobs.scheduler import daily, next_run_after

NEW_YORK = "America/New_York"

# ---------------------------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------------------------


def ref(alt: str | None = "Alt") -> MediaRef:
    return MediaRef(
        role="image",
        name="posts/p/" + "a" * 64 + ".png",
        sha256="a" * 64,
        content_type="image/png",
        size_bytes=1,
        alt_text=alt,
    )


def test_approval_hash_is_deterministic_and_sensitive() -> None:
    def digest(
        *,
        payload: str = "Hi #AI",
        title: str | None = None,
        media: list[MediaRef] | None = None,
        format: str = "text",
        poll: dict[str, object] | None = None,
        link_url: str | None = None,
    ) -> str:
        items = [ref()] if media is None else media
        return approval_hash(
            display_text="Hi #AI",
            payload=payload,
            document_title=title,
            media=items,
            format=format,
            poll=poll,
            link_url=link_url,
        )

    first = digest()
    assert first == digest()
    assert len(first) == 64
    assert first != digest(media=[])
    assert first != digest(media=[ref("Other")])
    assert first != digest(payload="Hi \\#AI")
    assert first != digest(title="T")
    # v2: everything the post body carries besides the author.
    assert first != digest(format="image_card")
    assert first != digest(poll={"question": "Q?", "options": ["a", "b"]})
    assert digest(poll={"question": "Q?"}) != digest(poll={"question": "Q!"})
    assert first != digest(link_url="https://example.com/x")


def test_approval_hash_golden_value() -> None:
    """Pinned: publisher and posts must compute the same hash forever (v2 material)."""
    value = approval_hash(
        display_text="x",
        payload="x",
        document_title=None,
        media=[],
        format="text",
        poll=None,
        link_url=None,
    )
    material = (
        b'{"displayText":"x","documentTitle":null,"format":"text","linkUrl":null,'
        b'"media":[],"payload":"x","poll":null,"v":2}'
    )
    assert value == hashlib.sha256(material).hexdigest()


def test_article_sha256_covers_meta_and_body() -> None:
    base = article_sha256({"title": "T"}, "Body")
    assert base == article_sha256({"title": "T"}, "Body")
    assert base != article_sha256({"title": "U"}, "Body")
    assert base != article_sha256({"title": "T"}, "Body ")


@pytest.mark.parametrize(
    ("url", "normalized"),
    [
        ("HTTPS://Example.COM", "https://example.com/"),
        ("https://example.com:443/a#frag", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com./a", "https://example.com/a"),
        ("https://u:p@example.com/a", "https://example.com/a"),
        (
            "https://example.com/a?utm_source=x&b=2&a=1&fbclid=y&UTM_Medium=z",
            "https://example.com/a?a=1&b=2",
        ),
        ("https://example.com/a?q=a+b&x=", "https://example.com/a?q=a+b&x="),
    ],
)
def test_normalize_url(url: str, normalized: str) -> None:
    assert normalize_url(url) == normalized
    assert url_sha256(url) == url_sha256(normalized)


@pytest.mark.parametrize("url", ["ftp://example.com/x", "/relative", "https://", "mailto:a@b.c"])
def test_normalize_url_refuses_non_http(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        normalize_url(url)


# ---------------------------------------------------------------------------------------------
# Slots and weeks
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local_date", "local_time", "utc"),
    [
        ("2026-12-01", "10:00", "2026-12-01T15:00:00.000000Z"),
        ("2026-07-01", "10:00", "2026-07-01T14:00:00.000000Z"),
        # Spring forward: 02:30 does not exist; it maps to 03:30 EDT (07:30 UTC).
        ("2026-03-08", "02:30", "2026-03-08T07:30:00.000000Z"),
        # Fall back: 01:30 happens twice; the first (EDT) wins.
        ("2026-11-01", "01:30", "2026-11-01T05:30:00.000000Z"),
    ],
)
def test_slot_utc_uses_the_scheduler_dst_rule(local_date: str, local_time: str, utc: str) -> None:
    assert slot_utc(local_date, local_time, NEW_YORK) == utc
    zone = ZoneInfo(NEW_YORK)
    day_before = datetime.fromisoformat(local_date).replace(tzinfo=zone) - timedelta(hours=12)
    fired = next_run_after(daily(local_time), zone, day_before.astimezone(UTC))
    assert fired == datetime.fromisoformat(utc[:-1]).replace(tzinfo=UTC)


def test_local_date_time_round_trip() -> None:
    assert local_date_time("2026-12-01T15:00:00.000000Z", NEW_YORK) == ("2026-12-01", "10:00")
    assert local_date_time("2026-12-01T03:00:00.000000Z", NEW_YORK) == ("2026-11-30", "22:00")


def test_weeks() -> None:
    assert weekday_of("2026-11-30") == "mon"
    assert weekday_of("2026-11-29") == "sun"
    assert week_start("2026-12-03") == "2026-11-30"
    assert week_start("2026-11-30") == "2026-11-30"
    assert week_start("2026-11-29") == "2026-11-23"
    assert add_days("2026-12-31", 1) == "2027-01-01"
    assert plan_id("2026-11-30") == "plan-2026-11-30"
    with pytest.raises(ValueError, match="Monday"):
        plan_id("2026-12-01")


# ---------------------------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------------------------


def blocklist(*terms: BlocklistTerm) -> Blocklist:
    return Blocklist(
        id="blocklist",
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        terms=list(terms),
        configured=True,
    )


def test_blocklist_word_terms_match_whole_words_case_insensitively() -> None:
    terms = blocklist(
        BlocklistTerm(id="c1", term="Contoso"),
        BlocklistTerm(id="p1", term="Project Falcon", category="project"),
    )
    text = "We helped contoso, then CONTOSO again; contosoft is fine. project falcon shipped."
    findings = blocklist_findings(text, terms)
    assert [(text[f.start or 0 : f.end or 0], f.ref) for f in findings] == [
        ("contoso", "c1"),
        ("CONTOSO", "c1"),
        ("project falcon", "p1"),
    ]
    assert all(f.code == BLOCKLIST_CODE and f.severity == "block" for f in findings)
    assert all(not f.overridable for f in findings)
    assert all("contoso" not in f.message.casefold() for f in findings)
    assert "internal project" in findings[-1].message


def test_blocklist_substring_terms_match_anywhere() -> None:
    terms = blocklist(BlocklistTerm(id="s1", term="falc", match="substring"))
    findings = blocklist_findings("Falcons and falconry", terms)
    assert [f.start for f in findings] == [0, 12]


def test_blocklist_hit_and_empty_list() -> None:
    terms = blocklist(BlocklistTerm(id="c1", term="Contoso"))
    assert blocklist_hit(["mcp security", "contoso agent rollout"], terms)
    assert not blocklist_hit(["mcp security"], terms)
    # An empty list the owner saved is a valid answer.
    assert blocklist_findings("Contoso", blocklist()) == []
    assert not blocklist_hit(["Contoso"], blocklist())


def test_an_unconfigured_blocklist_fails_closed() -> None:
    never_saved = Blocklist.default(SAMPLE_NOW)
    findings = blocklist_findings("anything at all", never_saved)
    assert [(f.code, f.severity, f.overridable) for f in findings] == [
        (BLOCKLIST_NOT_CONFIGURED_CODE, "block", False)
    ]
    assert findings[0].start is None
    assert blocklist_hit(["mcp security"], never_saved)
    with pytest.raises(BlocklistNotConfigured):
        check_texts({"topic": "mcp security"}, never_saved)


def test_check_texts_reports_findings_per_field_without_terms() -> None:
    terms = blocklist(BlocklistTerm(id="c1", term="Contoso"))
    check_texts({"instruction": "Make it shorter.", "audience": None}, terms)
    with pytest.raises(ConfidentialText) as caught:
        check_texts({"instruction": "Mention the Contoso rollout.", "topic": "MCP"}, terms)
    assert list(caught.value.findings) == ["instruction"]
    finding = caught.value.findings["instruction"][0]
    assert (finding.ref, finding.code) == ("c1", BLOCKLIST_CODE)
    assert "contoso" not in str(caught.value).casefold()
    assert "contoso" not in finding.message.casefold()


def test_ai_use_must_be_confirmed() -> None:
    policy = EmployerPolicy.default(SAMPLE_NOW)
    with pytest.raises(AiUseNotConfirmed):
        require_ai_use(policy)
    require_ai_use(policy.model_copy(update={"ai_use_confirmed": True}))


# ---------------------------------------------------------------------------------------------
# Ids and timestamps
# ---------------------------------------------------------------------------------------------


def test_derived_ids_are_stable_valid_and_new_id_shaped() -> None:
    first = derived_id("run", "pipeline", "art-1", 2)
    assert first == derived_id("run", "pipeline", "art-1", 2)
    assert first != derived_id("run", "pipeline", "art-1", 3)
    assert first != derived_id("run", "pipeline", "art-12")  # parts are separated
    assert validate_id(first) == first
    assert len(first) == len(new_id("run"))
    with pytest.raises(ValueError, match="prefix"):
        derived_id("Bad", "x")
    with pytest.raises(ValueError, match="part"):
        derived_id("run")


def test_distinct_stamps_never_tie() -> None:
    stamps = distinct_stamps(NOW, 3)
    assert stamps == sorted(set(stamps))
    assert parse_iso(stamps[-1]) - parse_iso(stamps[0]) == timedelta(microseconds=2)
    assert distinct_stamps(NOW, 0) == []


def test_blocklist_terms_with_regex_characters_are_literal() -> None:
    terms = blocklist(BlocklistTerm(id="x", term="A.B (C)"))
    assert len(blocklist_findings("see A.B (C) here", terms)) == 1
    assert blocklist_findings("see AxB (C) here", terms) == []


# ---------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)


def autonomy(**changes: object) -> AutonomySettings:
    return AutonomySettings.default(SAMPLE_NOW).model_copy(update=changes)


def test_streak_grows_on_clean_reviews_and_resets_otherwise() -> None:
    state = autonomy()
    for index in range(6):
        state = record_review(state, article_id=f"art-{index}", outcome="clean", at=SAMPLE_NOW)
    assert state.calibration.clean_streak == 6
    assert len(state.calibration.events) == 6
    edited = record_review(state, article_id="art-x", outcome="edited", at=SAMPLE_NOW)
    assert edited.calibration.clean_streak == 0
    assert edited.calibration.events[-1].outcome == "edited"


def test_events_are_capped() -> None:
    state = autonomy()
    for index in range(60):
        state = record_review(state, article_id=f"art-{index}", outcome="clean", at=SAMPLE_NOW)
    assert len(state.calibration.events) == 50
    assert state.calibration.events[-1].article_id == "art-59"


def test_correction_turns_auto_publish_off() -> None:
    state = autonomy(auto_publish_enabled=True)
    corrected = record_correction(state, article_id="art-1", at=SAMPLE_NOW)
    assert not corrected.auto_publish_enabled
    assert corrected.calibration.clean_streak == 0
    assert corrected.last_correction_at == SAMPLE_NOW


def test_auto_publish_decision_reasons() -> None:
    assert auto_publish_decision(autonomy(), now=NOW).reasons == (
        "not_enabled",
        "calibrating",
        "judge_uncalibrated",
    )
    judge = JudgeCalibration(as_is_rate=0.95, sample_size=12, checked_at=SAMPLE_NOW)
    ready = autonomy(auto_publish_enabled=True, judge=judge)
    ready = ready.model_copy(
        update={"calibration": ready.calibration.model_copy(update={"clean_streak": 6})}
    )
    assert auto_publish_decision(ready, now=NOW).allowed
    assert auto_publish_decision(ready.model_copy(update={"paused": True}), now=NOW).reasons == (
        "paused",
    )
    stale = auto_publish_decision(ready, now=NOW + timedelta(days=32))
    assert stale.reasons == ("judge_stale",)
    low = ready.model_copy(update={"judge": judge.model_copy(update={"as_is_rate": 0.8})})
    assert auto_publish_decision(low, now=NOW).reasons == ("judge_below_threshold",)


def test_substantive_edits() -> None:
    text = " ".join(f"word{index}" for index in range(200))
    assert not is_substantive_edit(text, text)
    assert not is_substantive_edit(text, text.replace(" ", "\n"))
    assert not is_substantive_edit(text, text.replace("word5 ", "changed ", 1))
    rewritten = text.replace("word1", "other")
    assert is_substantive_edit(text, rewritten)
    assert not is_substantive_edit("", "")
    assert is_substantive_edit("", "new text")
