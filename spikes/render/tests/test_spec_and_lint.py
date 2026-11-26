"""Spec validation (hard limits) and editorial lint (plan rules)."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from render import DeckSpec, lint_deck
from render.text import gate_normalise, normalise_plain_text


def deck(*slides: Mapping[str, object], title: str = "A short title") -> DeckSpec:
    return DeckSpec.model_validate({"title": title, "slides": list(slides)})


COVER = {"type": "cover", "hook": "Short hook here"}
RECAP = {"type": "recap", "items": ["One", "Two"]}
STATEMENT = {"type": "statement", "statement": "A statement."}


@pytest.mark.parametrize(
    "slide",
    [
        {"type": "video", "src": "x"},
        {"type": "cover"},
        {"type": "cover", "hook": "x" * 81},
        {"type": "cover", "hook": "   "},
        {"type": "cover", "hook": "ok", "onclick": "alert(1)"},
        {"type": "list", "title": "t", "items": ["only one"]},
        {"type": "framework", "title": "t", "layout": "2x2", "cells": [{"label": "a", "text": "b"}] * 3},
        {
            "type": "framework",
            "title": "t",
            "layout": "layered",
            "x_axis": "x",
            "cells": [{"label": "a", "text": "b"}] * 3,
        },
        {"type": "diagram", "title": "t", "mermaid": "x" * 3001, "alt": "a"},
        {"type": "diagram", "title": "t", "mermaid": "flowchart TD", "alt": ""},
        {"type": "sources", "sources": []},
    ],
)
def test_invalid_slides_are_rejected(slide: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        deck(slide)


def test_deck_limits() -> None:
    with pytest.raises(ValidationError):
        deck()
    with pytest.raises(ValidationError):
        deck(*([STATEMENT] * 21))
    with pytest.raises(ValidationError):
        DeckSpec.model_validate({"title": "t", "lang": "en; script", "slides": [STATEMENT]})


def test_text_normalisation_and_gate_normalisation() -> None:
    assert normalise_plain_text("  a\n\tb   c ") == "a b c"
    assert normalise_plain_text("Cafe\u0301") == "Caf\u00e9"  # NFC
    assert gate_normalise("Cli\u200bent\u200dName") == "clientname"


def test_sample_style_deck_passes_lint() -> None:
    d = deck(
        COVER, *([STATEMENT] * 7), RECAP, {"type": "sources", "sources": [{"publisher": "P", "title": "T"}]}
    )
    assert lint_deck(d) == []


def test_lint_reports_plan_rules() -> None:
    d = deck(
        STATEMENT,
        {"type": "cover", "hook": "one two three four five six seven eight nine ten eleven"},
        {"type": "sources", "sources": [{"publisher": "P", "title": "T"}]},
        {"type": "statement", "statement": "Read more at example.com today " + "word " * 31},
        title="x" * 59,
    )
    codes = {i.code for i in lint_deck(d)}
    assert codes == {
        "title_length", "slide_count", "first_slide", "last_slide", "sources_position", "cover_words",
        "slide_words", "domain_like",
    }  # fmt: skip


# --- Reviewer additions: default-ignorable code points that are not category Cf. -------------------------
# Each renders with zero width in the slide font ("Cli\u034fent" draws exactly like "Client"), so before
# the fix a blocklisted name could carry one through gate_normalise and into the PDF /Title.
INVISIBLE_SPLITTERS = {
    "combining grapheme joiner": "\u034f",
    "variation selector 1": "\ufe00",
    "variation selector 16": "\ufe0f",
    "variation selector 17": "\U000e0100",
    "Khmer inherent vowel": "\u17b4",
    "Mongolian free variation selector": "\u180b",
    "Hangul filler": "\u3164",
    "halfwidth Hangul filler": "\uffa0",
    "zero-width joiner between letters": "\u200d",
}


@pytest.mark.parametrize("ch", INVISIBLE_SPLITTERS.values(), ids=INVISIBLE_SPLITTERS.keys())
def test_invisible_characters_cannot_split_a_blocklisted_name(ch: str) -> None:
    d = DeckSpec.model_validate(
        {"title": f"Cli{ch}ent onboarding", "slides": [{"type": "statement", "statement": f"Acme{ch} Corp"}]}
    )
    assert d.title == "Client onboarding"  # what the PDF /Title and text layer carry
    assert d.slides[0].statement == "Acme Corp"  # type: ignore[union-attr]
    assert gate_normalise(f"Cli{ch}ent") == "client"


def test_emoji_sequences_keep_their_joiners() -> None:
    technologist = "\U0001f469\u200d\U0001f4bb"  # woman + ZWJ + laptop
    heart = "\u2764\ufe0f"  # heart + VS16 (emoji presentation)
    assert normalise_plain_text(f"Ship it {technologist}") == f"Ship it {technologist}"
    assert normalise_plain_text(f"Love {heart}") == f"Love {heart}"
    assert gate_normalise(f"Ship it {technologist} {heart}") == "ship it \U0001f469\U0001f4bb \u2764"
    assert normalise_plain_text("a \u200d b") == "a b"  # a joiner after a space joins nothing
