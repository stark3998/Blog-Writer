"""AI-tell and slop scoring: the linkedin lint recipe exactly, slop merged, fail closed."""

from __future__ import annotations

import logging
import random

import pytest
import regex

from blogwriter.domain.common import Finding, from_linkedin_finding
from blogwriter.domain.testing import FakeSlopChecker
from blogwriter.linkedin.lint import AI_TELLS, lint_post
from blogwriter.linkedin.lint import ai_tell_findings as linkedin_ai_tell_findings
from blogwriter.voice import ai_tell_findings, score_text
from blogwriter.voice.guide import BASELINE_BANNED_PHRASES
from blogwriter.voice.scoring import MAX_FLAGS, sample_weight
from blogwriter.voice.testing import LINKEDIN_POSTS

CURLY = "\u2019"
LEFT = "\u2018"

CORPUS = [
    f"Let{CURLY}s dive in: we leveraged a robust plan.",
    f"It{LEFT}s worth noting that the landscape changed. Landscapes did not.",
    f"I{CURLY}m so excited to share this. I'm excited to share that too.",
    f"Here{CURLY}s why this matters: state of the art beats state-of-the-art.",
    "TL;DR the migration slipped by a month.",
    "  tldr: short answer first.",
    "Game changer, game-changing, GAME CHANGING, gamechanger.",
    "The paradigm shift was seamless, then seamlessly essential and essentially vital.",
    "empowered, empowering, empowers, elevates, harnessing, utilized, supercharged",
    "We deleveraged the debt, unharnessed nothing and kept it unremarkable.",
    f"In today{CURLY}s rapidly evolving world, let{CURLY}s break this down.",
    "In the ever changing landscape, unlock the power of comprehensive, cutting edge tools.",
    "Notably, it's important to note the groundbreaking, revolutionary, crucial part.",
    "Thrilled to announce a 2,000-app migration (leverage!) - leverage, leverages, leveraging.",
    *LINKEDIN_POSTS,
]


def lint_ai_tells(text: str) -> list[tuple[int, int, str, str]]:
    return [
        (finding.start, finding.end, finding.code, finding.message)
        for finding in lint_post(text, approved_hashtags=())
        if finding.code == "ai-tell"
    ]


def voice_ai_tells(text: str) -> list[tuple[int, int, str, str]]:
    out: list[tuple[int, int, str, str]] = []
    for finding in ai_tell_findings(text):
        assert finding.start is not None
        assert finding.end is not None
        assert finding.severity == "warn"
        out.append((finding.start, finding.end, finding.code, finding.message))
    return out


@pytest.mark.parametrize("text", CORPUS)
def test_ai_tells_equal_the_linkedin_lint(text: str) -> None:
    assert voice_ai_tells(text) == lint_ai_tells(text)


@pytest.mark.parametrize("text", CORPUS)
def test_ai_tells_are_the_linkedin_lint_function(text: str) -> None:
    """Contract I-9: one implementation, ``linkedin.lint.ai_tell_findings``."""
    expected = [from_linkedin_finding(finding) for finding in linkedin_ai_tell_findings(text)]
    assert ai_tell_findings(text) == expected


def test_curly_apostrophes_match_like_straight_ones() -> None:
    curly = voice_ai_tells(f"Let{CURLY}s dive in. It{LEFT}s worth noting.")
    straight = voice_ai_tells("Let's dive in. It's worth noting.")
    assert [span[:2] for span in curly] == [span[:2] for span in straight]
    assert len(curly) == 2


def test_phrase_boundaries_never_substring_match() -> None:
    for text in (
        "We deleveraged the debt.",
        "The horse was unharnessed.",
        "Essentially done.",
        "Landscaping crews.",
        "A notablest typo.",
    ):
        assert voice_ai_tells(text) == [], text
    assert voice_ai_tells("leverage") == [(0, 8, "ai-tell", "AI tell: 'leverage'.")]


def test_random_texts_agree_with_the_linkedin_lint() -> None:
    rng = random.Random(20261126)
    vocabulary = [
        "leverage",
        "leveraged",
        "Leveraging",
        "deleverage",
        "landscape",
        "landscapes",
        f"let{CURLY}s",
        "let's",
        f"it{LEFT}s",
        "dive",
        "in",
        "worth",
        "noting",
        "robust",
        "robustness",
        "seamless",
        "seamlessly",
        "game-changer",
        "game",
        "changer",
        "state-of-the-art",
        "tokens",
        "agents",
        "MFA",
        "2,000",
        "apps",
        "the",
        "a",
        "crucial",
        "crucially",
        "TL;DR",
    ]
    separators = [" ", " ", " ", ", ", ". ", "\n", "\n\n", " - ", ": "]
    for _ in range(300):
        size = rng.randint(1, 25)
        pieces = [rng.choice(vocabulary) + rng.choice(separators) for _ in range(size)]
        text = "".join(pieces).strip()
        assert voice_ai_tells(text) == lint_ai_tells(text), text


def test_baseline_banned_phrases_cover_every_ai_tell() -> None:
    for pattern in AI_TELLS:
        compiled = regex.compile(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])")
        assert any(compiled.search(phrase) for phrase in BASELINE_BANNED_PHRASES), pattern


def test_score_is_per_hundred_words_and_merges_slop() -> None:
    text = "We leveraged the plan and kept the robust parts for the next twenty working days here."
    tells = ai_tell_findings(text)
    assert len(tells) == 2
    duplicate = tells[0].model_copy()
    rule_of_three = Finding(
        code="rule-of-three", severity="warn", message="Three in a row.", start=0, end=5
    )
    whole = Finding(code="flat-rhythm", severity="warn", message="Flat rhythm.")
    score = score_text(text, slop=FakeSlopChecker([duplicate, rule_of_three, whole]))
    assert score.checked
    assert [finding.code for finding in score.flags] == [
        "flat-rhythm",
        "rule-of-three",
        "ai-tell",
        "ai-tell",
    ]
    assert score.word_count == 16
    assert score.ai_tell_score == 25.0  # 4 flags per 16 words
    assert not score.exemplar_eligible


def test_without_slop_a_clean_text_is_never_eligible() -> None:
    clean = LINKEDIN_POSTS[0]
    unchecked = score_text(clean, slop=None)
    assert unchecked.flags == []
    assert unchecked.ai_tell_score == 0
    assert not unchecked.checked
    assert not unchecked.exemplar_eligible
    checked = score_text(clean, slop=FakeSlopChecker())
    assert checked.checked
    assert checked.exemplar_eligible


def test_a_failing_slop_checker_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def check(self, text: str) -> list[Finding]:
            raise RuntimeError(text)

    text = "A unique-marker-7731 text that leverages one tell."
    with caplog.at_level(logging.WARNING, logger="blogwriter.voice"):
        score = score_text(text, slop=Broken())
    assert not score.checked
    assert not score.exemplar_eligible
    assert [finding.code for finding in score.flags] == ["ai-tell"]
    assert any(record.message == "voice_slop_failed" for record in caplog.records)
    assert "unique-marker-7731" not in caplog.text


def test_flags_are_capped_but_counted() -> None:
    text = " ".join(["leverage"] * 600)
    score = score_text(text, slop=FakeSlopChecker())
    assert len(score.flags) == MAX_FLAGS
    assert score.ai_tell_score == 100.0


def test_weights_favour_unassisted_words() -> None:
    assert sample_weight("linkedin_post", True) == 4.0
    assert sample_weight("linkedin_post", False) == 2.0
    assert sample_weight("final_post", False) == 0.5
    assert sample_weight("interview_answer", True) == 4.0
    assert sample_weight("portfolio_about", True) == 2.0
