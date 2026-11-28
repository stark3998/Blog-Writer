"""Spec validation (hard limits), plain-text normalisation and editorial lint (plan rules)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from blogwriter.render import CarouselSpec, OgImageSpec, lint_carousel
from blogwriter.render.lint import slide_text
from blogwriter.render.spec import StatementSlide
from blogwriter.render.text import gate_normalise, normalise_plain_text, word_count


def deck(*slides: Mapping[str, object], title: str = "A short title") -> CarouselSpec:
    return CarouselSpec.model_validate({"title": title, "slides": list(slides)})


COVER = {"type": "cover", "hook": "Short hook here"}
RECAP = {"type": "recap", "items": ["One", "Two"]}
STATEMENT = {"type": "statement", "statement": "A statement."}
SOURCES = {"type": "sources", "sources": [{"publisher": "P", "title": "T"}]}


@pytest.mark.parametrize(
    "slide",
    [
        {"type": "video", "src": "x"},
        {"type": "cover"},
        {"type": "cover", "hook": "x" * 81},
        {"type": "cover", "hook": "   "},
        {"type": "cover", "hook": "ok", "onclick": "alert(1)"},
        {"type": "list", "title": "t", "items": ["only one"]},
        {"type": "framework", "title": "t", "layout": "2x2",
         "cells": [{"label": "a", "text": "b"}] * 3},
        {
            "type": "framework",
            "title": "t",
            "layout": "layered",
            "x_axis": "x",
            "cells": [{"label": "a", "text": "b"}] * 3,
        },
        {"type": "steps", "title": "t", "steps": [{"title": "s"}] * 5},
        {"type": "sources", "sources": [{"publisher": "P", "title": "T"}] * 6},
        {"type": "diagram", "title": "t", "mermaid": "x" * 3001, "alt": "a"},
        {"type": "diagram", "title": "t", "mermaid": "flowchart TD", "alt": ""},
        {"type": "diagram", "title": "t", "mermaid": "flowchart TD\x00", "alt": "a"},
        {"type": "sources", "sources": []},
    ],
)  # fmt: skip
def test_invalid_slides_are_rejected(slide: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        deck(slide)


def test_carousel_limits() -> None:
    with pytest.raises(ValidationError):
        deck()
    with pytest.raises(ValidationError):
        deck(*([STATEMENT] * 21))
    with pytest.raises(ValidationError):
        CarouselSpec.model_validate({"title": "t", "lang": "en; script", "slides": [STATEMENT]})
    with pytest.raises(ValidationError):
        CarouselSpec.model_validate({"title": "t", "brand": {}, "slides": [STATEMENT]})


def test_og_spec_limits() -> None:
    OgImageSpec(title="ok", subtitle="s" * 140, pillar_label="k" * 40)
    with pytest.raises(ValidationError):
        OgImageSpec(title="x" * 111)
    with pytest.raises(ValidationError):
        OgImageSpec(title="ok", pillar_label="k" * 41)
    with pytest.raises(ValidationError):
        OgImageSpec(title="ok", subtitle="s" * 141)


def test_text_normalisation_and_gate_normalisation() -> None:
    assert normalise_plain_text("  a\n\tb   c ") == "a b c"
    assert normalise_plain_text("Cafe\u0301") == "Caf\u00e9"  # NFC
    assert gate_normalise("Cli\u200bent\u200dName") == "clientname"
    assert word_count("one two  three") == 3


def test_plain_text_strips_invisible_spoofing_characters() -> None:
    d = CarouselSpec.model_validate(
        {
            "title": "Cli\u200bent \u202eevil\u202c name\ufeff",
            "slides": [{"type": "statement", "statement": "a\u2066b\u2069c\tline\nbreak"}],
        }
    )
    assert d.title == "Client evil name"
    slide = d.slides[0]
    assert isinstance(slide, StatementSlide)
    assert slide.statement == "abc line break"


@pytest.mark.parametrize("bad", ["nul\x00byte", "bell\x07", "esc\x1b[31m", "pua\ue000", "x\ud800"])
def test_plain_text_rejects_control_surrogate_and_private_use(bad: str) -> None:
    with pytest.raises(ValidationError):
        CarouselSpec.model_validate({"title": bad, "slides": [STATEMENT]})


#: Default-ignorable code points outside category Cf: each renders with zero width in the slide
#: font ("Cli\u034fent" draws exactly like "Client").
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
    d = CarouselSpec.model_validate(
        {
            "title": f"Cli{ch}ent onboarding",
            "slides": [{"type": "statement", "statement": f"Acme{ch} Corp"}],
        }
    )
    assert d.title == "Client onboarding"  # what the PDF /Title and the text layer carry
    slide = d.slides[0]
    assert isinstance(slide, StatementSlide)
    assert slide.statement == "Acme Corp"
    assert gate_normalise(f"Cli{ch}ent") == "client"


def test_emoji_sequences_keep_their_joiners() -> None:
    technologist = "\U0001f469\u200d\U0001f4bb"  # woman + ZWJ + laptop
    heart = "\u2764\ufe0f"  # heart + VS16 (emoji presentation)
    assert normalise_plain_text(f"Ship it {technologist}") == f"Ship it {technologist}"
    assert normalise_plain_text(f"Love {heart}") == f"Love {heart}"
    assert (
        gate_normalise(f"Ship it {technologist} {heart}") == "ship it \U0001f469\U0001f4bb \u2764"
    )
    assert normalise_plain_text("a \u200d b") == "a b"  # a joiner after a space joins nothing


def test_sample_carousel_is_lint_clean(sample_carousel: CarouselSpec) -> None:
    assert lint_carousel(sample_carousel) == []
    assert len(sample_carousel.slides) == 12
    assert {s.type for s in sample_carousel.slides} == {
        "cover", "statement", "list", "steps", "framework", "diagram", "stat", "quote", "recap",
        "sources",
    }  # fmt: skip


def test_recap_closes_the_deck_and_sources_may_follow() -> None:
    assert lint_carousel(deck(COVER, *([STATEMENT] * 7), RECAP, SOURCES)) == []
    assert lint_carousel(deck(COVER, *([STATEMENT] * 7), RECAP)) == []
    codes = {i.code for i in lint_carousel(deck(COVER, *([STATEMENT] * 7), SOURCES, RECAP))}
    assert codes == {"sources_position"}
    codes = {i.code for i in lint_carousel(deck(COVER, *([STATEMENT] * 8), SOURCES))}
    assert codes == {"last_slide"}


def test_lint_reports_every_plan_rule() -> None:
    d = deck(
        STATEMENT,
        {"type": "cover", "hook": "one two three four five six seven eight nine ten eleven"},
        SOURCES,
        {"type": "statement", "statement": "Read more at example.com today " + "word " * 31},
        title="x" * 59,
    )
    issues = lint_carousel(d)
    assert {i.code for i in issues} == {
        "title_length", "slide_count", "first_slide", "last_slide", "sources_position",
        "cover_words", "slide_words", "domain_like",
    }  # fmt: skip
    for issue in issues:
        assert "example.com" not in issue.message  # messages never quote slide text


def test_lint_counts_words_per_slide_but_not_on_the_sources_slide() -> None:
    many = {"type": "sources", "sources": [{"publisher": "Pub " * 9, "title": "T " * 40}] * 5}
    d = deck(COVER, *([STATEMENT] * 6), RECAP, many)
    assert sum(len(t.split()) for t in slide_text(d.slides[-1])) > 35
    assert [i.code for i in lint_carousel(d)] == []


def _spec_with(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"title": "t", "slides": [STATEMENT]}
    base.update(overrides)
    return base


def test_lang_is_a_simple_tag() -> None:
    assert CarouselSpec.model_validate(_spec_with(lang="en-GB")).lang == "en-GB"
    with pytest.raises(ValidationError):
        CarouselSpec.model_validate(_spec_with(lang='en" onload="x'))
