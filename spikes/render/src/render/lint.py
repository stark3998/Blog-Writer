"""Editorial carousel rules from the plan ("LinkedIn Studio" > "Carousels"). Issues block approval,
not rendering: a draft that breaks them can still be previewed."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .spec import (
    CoverSlide,
    DeckSpec,
    DiagramSlide,
    FrameworkSlide,
    ListSlide,
    QuoteSlide,
    RecapSlide,
    Slide,
    SourcesSlide,
    StatementSlide,
    StatSlide,
    StepsSlide,
)
from .text import word_count

MAX_TITLE_CHARS = 58
MAX_COVER_WORDS = 10
MAX_SLIDE_WORDS = 35
SLIDE_RANGE = (8, 12)

#: Domain-like tokens: LinkedIn auto-links them, and carousels carry no links at all.
_DOMAIN_LIKE = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|ai|dev|app|co|md|gov|edu|info|"
    r"biz|xyz|cloud|tech|ly|me|us|uk)\b"
)


@dataclass(frozen=True, slots=True)
class LintIssue:
    code: str
    slide: int | None  # 1-based; None for deck-level issues
    message: str


def slide_text(slide: Slide) -> list[str]:
    """Every visible text field of a slide (footer excluded)."""
    match slide:
        case CoverSlide():
            return [t for t in (slide.kicker, slide.hook, slide.subtitle) if t]
        case StatementSlide():
            return [t for t in (slide.statement, slide.note) if t]
        case ListSlide():
            return [slide.title, *slide.items]
        case StepsSlide():
            return [slide.title, *[x for s in slide.steps for x in (s.title, s.detail) if x]]
        case FrameworkSlide():
            cells = [x for c in slide.cells for x in (c.label, c.text)]
            return [slide.title, *cells, *[a for a in (slide.x_axis, slide.y_axis) if a]]
        case DiagramSlide():
            return [t for t in (slide.title, slide.caption) if t]
        case StatSlide():
            return [t for t in (slide.value, slide.label, slide.context, slide.source) if t]
        case QuoteSlide():
            return [t for t in (slide.quote, slide.attribution, slide.role) if t]
        case RecapSlide():
            return [slide.title, *slide.items]
        case SourcesSlide():
            return [slide.title, *[x for s in slide.sources for x in (s.publisher, s.title, s.date) if x]]


def lint_deck(deck: DeckSpec) -> list[LintIssue]:
    issues: list[LintIssue] = []
    if len(deck.title) > MAX_TITLE_CHARS:
        issues.append(
            LintIssue("title_length", None, f"document title is {len(deck.title)} characters (max 58)")
        )
    n = len(deck.slides)
    if not SLIDE_RANGE[0] <= n <= SLIDE_RANGE[1]:
        issues.append(LintIssue("slide_count", None, f"{n} slides; carousels use 8-12"))
    if not isinstance(deck.slides[0], CoverSlide):
        issues.append(LintIssue("first_slide", 1, "the first slide must be the cover"))
    # Plan: "the last slide is a recap" and "carousels get a Sources slide". Reconciled as: the recap
    # closes the argument; an optional sources slide may follow it as an appendix, and only there.
    content = deck.slides[:-1] if isinstance(deck.slides[-1], SourcesSlide) else deck.slides
    if not content or not isinstance(content[-1], RecapSlide):
        issues.append(
            LintIssue("last_slide", len(content), "the last slide must be a recap or 'do this Monday'")
        )
    for i, slide in enumerate(content, start=1):
        if isinstance(slide, SourcesSlide):
            issues.append(
                LintIssue("sources_position", i, "the sources slide goes at the end, after the recap")
            )
    for i, slide in enumerate(deck.slides, start=1):
        if isinstance(slide, CoverSlide) and word_count(slide.hook) > MAX_COVER_WORDS:
            issues.append(
                LintIssue("cover_words", i, f"cover hook is {word_count(slide.hook)} words (max 10)")
            )
        words = sum(word_count(t) for t in slide_text(slide))
        if words > MAX_SLIDE_WORDS and not isinstance(slide, SourcesSlide):
            issues.append(LintIssue("slide_words", i, f"{words} words on the slide (max 35)"))
        for text in slide_text(slide):
            if _DOMAIN_LIKE.search(text):
                issues.append(LintIssue("domain_like", i, "domain-like token; carousels carry no links"))
                break
    return issues
