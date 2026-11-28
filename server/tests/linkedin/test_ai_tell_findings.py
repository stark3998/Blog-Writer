"""``linkedin.lint.ai_tell_findings`` (contract I-9): exactly the ``ai-tell`` findings of
``lint_post``, for any text, with the module's phrase boundaries and apostrophe normalization."""

from __future__ import annotations

import random

import pytest

from blogwriter.linkedin.lint import AI_TELLS, Finding, Severity, ai_tell_findings, lint_post

CURLY = "\u2019"
LEFT = "\u2018"

EXAMPLES = (
    "let's dive in",
    "let's explore",
    "let's break this down",
    "in today's rapidly evolving",
    "in the ever-changing landscape",
    "game-changing",
    "revolutionary",
    "groundbreaking",
    "paradigm shift",
    "landscape",
    "leveraging",
    "utilizes",
    "harnessed",
    "it's worth noting",
    "it's important to note",
    "notably",
    "crucial",
    "vital",
    "essential",
    "robust",
    "seamlessly",
    "comprehensive",
    "cutting edge",
    "state-of-the-art",
    "empowering",
    "elevated",
    "unlock the power of",
    "supercharges",
    "here's why this matters",
    "I'm so excited to share",
    "thrilled to announce",
)
"""One phrase per :data:`AI_TELLS` pattern, in the same order."""

CORPUS = (
    f"Let{CURLY}s dive in: we leveraged a robust plan.",
    f"It{LEFT}s worth noting that the landscape changed. Landscapes did not.",
    f"I{CURLY}m so excited to share this. I'm excited to share that too.",
    f"Here{CURLY}s why this matters: state of the art beats state-of-the-art.",
    "TL;DR the migration slipped by a month.",
    "  tldr: short answer first.",
    "The TL;DR comes last here.",
    "Game changer, game-changing, GAME CHANGING, gamechanger.",
    "The paradigm shift was seamless, then seamlessly essential and essentially vital.",
    "empowered, empowering, empowers, elevates, harnessing, utilized, supercharged",
    "We deleveraged the debt, unharnessed nothing and kept it unremarkable.",
    f"In today{CURLY}s rapidly evolving world, let{CURLY}s break this down.",
    "In the ever changing landscape, unlock the power of comprehensive, cutting edge tools.",
    "Notably, it's important to note the groundbreaking, revolutionary, crucial part.",
    "Thrilled to announce a 2,000-app migration (leverage!) - leverage, leverages, leveraging.",
    "robust2 2robust robust_ _robust #robust @leverage",
    "We moved 2,000 apps off our old identity provider in 14 months.",
    "",
)


def lint_tells(text: str) -> list[Finding]:
    return [f for f in lint_post(text, approved_hashtags=()) if f.code == "ai-tell"]


def spans(findings: list[Finding]) -> list[tuple[int, int]]:
    return [(f.start, f.end) for f in findings]


def test_the_examples_cover_every_ai_tell() -> None:
    assert len(EXAMPLES) == len(AI_TELLS)
    for example in EXAMPLES:
        found = ai_tell_findings(example)
        assert (0, len(example)) in spans(found), example


@pytest.mark.parametrize("text", CORPUS)
def test_equals_the_ai_tell_findings_of_lint_post(text: str) -> None:
    assert ai_tell_findings(text) == lint_tells(text)


@pytest.mark.parametrize("text", EXAMPLES)
def test_every_example_agrees_with_lint_post_inside_prose(text: str) -> None:
    prose = f"We shipped it. {text.capitalize()}, then we stopped."
    assert ai_tell_findings(prose) == lint_tells(prose)
    assert ai_tell_findings(prose)


def test_random_texts_agree_with_lint_post() -> None:
    rng = random.Random(20261127)
    words = [
        *EXAMPLES,
        "deleverage",
        "landscapes",
        "robustness",
        "crucially",
        f"let{CURLY}s",
        f"it{LEFT}s",
        "dive",
        "in",
        "worth",
        "noting",
        "game",
        "changer",
        "tokens",
        "agents",
        "2,000",
        "the",
        "TL;DR",
    ]
    separators = [" ", " ", ", ", ". ", "\n", "\n\n", " - ", ": "]
    for _ in range(300):
        pieces = [rng.choice(words) + rng.choice(separators) for _ in range(rng.randint(1, 25))]
        text = "".join(pieces).strip()
        assert ai_tell_findings(text) == lint_tells(text), text


def test_curly_apostrophes_match_like_straight_ones_and_keep_offsets() -> None:
    straight = "Well. Let's dive in. It's worth noting."
    curly = f"Well. Let{CURLY}s dive in. It{LEFT}s worth noting."
    assert spans(ai_tell_findings(curly)) == spans(ai_tell_findings(straight))
    found = ai_tell_findings(curly)
    assert [curly[f.start : f.end] for f in found] == [
        f"Let{CURLY}s dive in",
        f"It{LEFT}s worth noting",
    ]
    assert [f.message for f in found] == [
        'AI tell: "Let\'s dive in".',
        'AI tell: "It\'s worth noting".',
    ]


def test_whole_phrases_only() -> None:
    for text in (
        "We deleveraged the debt.",
        "The horse was unharnessed.",
        "Essentially done.",
        "Landscaping crews.",
        "A notablest typo.",
        "Robustness matters.",
        "Crucially late.",
        "robust2 and 2robust",
    ):
        assert ai_tell_findings(text) == [], text
    assert ai_tell_findings("LEVERAGE") == [
        Finding("ai-tell", Severity.WARN, "AI tell: 'LEVERAGE'.", 0, 8)
    ]


def test_tldr_counts_only_at_the_start() -> None:
    assert ai_tell_findings("  TL;DR: it slipped.") == [
        Finding("ai-tell", Severity.WARN, "Starts with TL;DR.", 0, 5)
    ]
    assert ai_tell_findings("tldr it slipped") == [
        Finding("ai-tell", Severity.WARN, "Starts with TL;DR.", 0, 5)
    ]
    assert ai_tell_findings("It slipped. TL;DR: a month.") == []


def test_any_text_can_be_checked_not_only_valid_display_text() -> None:
    text = "bad\x00text, let's dive in"
    assert [f.code for f in lint_post(text, approved_hashtags=())] == ["invalid-text"]
    assert spans(ai_tell_findings(text)) == [(10, 23)]


def test_findings_are_warnings_sorted_by_position() -> None:
    text = "Robust tooling. Let's dive in. TL;DR elsewhere. Leverage it, landscape and all."
    found = ai_tell_findings(text)
    assert found == sorted(found, key=lambda f: (f.start, f.end, f.code))
    assert {(f.code, f.severity) for f in found} == {("ai-tell", Severity.WARN)}
    assert [text[f.start : f.end] for f in found] == [
        "Robust",
        "Let's dive in",
        "Leverage",
        "landscape",
    ]
    assert ai_tell_findings(text) == found  # deterministic
