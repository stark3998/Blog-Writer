"""Editorial carousel rules from the plan ("LinkedIn Studio" > "Carousels").

Issues block *approval*, not rendering: a draft that breaks them can still be previewed, and
:func:`~blogwriter.render.service.Renderer.render_carousel` returns them in
``RenderedDeck.warnings``.

Rules: title <= 58 characters, 8-12 slides, the cover comes first with a hook of <= 10 words,
<= 35 words per slide (sources excepted), the recap is the last content slide and an optional
sources slide may follow it (and only there), no domain-like tokens. The remaining carousel rules
are hard limits elsewhere: <= 4 steps and <= 5 sources (spec), a 28px type floor, slide numbers
and overflow checks (templates and renderer), WCAG AA (brand).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from blogwriter.render.spec import (
    CarouselSpec,
    CoverSlide,
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
from blogwriter.render.text import word_count

__all__ = [
    "MAX_COVER_WORDS",
    "MAX_SLIDE_WORDS",
    "MAX_TITLE_CHARS",
    "SLIDE_RANGE",
    "RenderWarning",
    "lint_carousel",
    "slide_text",
]

MAX_TITLE_CHARS: Final = 58
MAX_COVER_WORDS: Final = 10
MAX_SLIDE_WORDS: Final = 35
SLIDE_RANGE: Final = (8, 12)

#: Domain-like tokens: LinkedIn auto-links them, and carousels carry no links at all.
_DOMAIN_LIKE: Final = re.compile(
    r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|ai|dev|app|co|md|"
    r"gov|edu|info|biz|xyz|cloud|tech|ly|me|us|uk)\b"
)


@dataclass(frozen=True, slots=True)
class RenderWarning:
    """A finding that does not stop rendering (lint rule, diagram source change, label size).

    ``code`` is a stable identifier; ``slide`` is 1-based (``None`` for deck-level findings);
    ``message`` is for people and holds no slide text.
    """

    code: str
    slide: int | None
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
            sources = [x for s in slide.sources for x in (s.publisher, s.title, s.date) if x]
            return [slide.title, *sources]


def lint_carousel(spec: CarouselSpec) -> list[RenderWarning]:
    """Every plan rule the deck breaks (empty when it is ready for approval)."""
    issues: list[RenderWarning] = []
    if len(spec.title) > MAX_TITLE_CHARS:
        issues.append(
            RenderWarning(
                "title_length",
                None,
                f"document title is {len(spec.title)} characters (max {MAX_TITLE_CHARS})",
            )
        )
    n = len(spec.slides)
    if not SLIDE_RANGE[0] <= n <= SLIDE_RANGE[1]:
        issues.append(
            RenderWarning(
                "slide_count", None, f"{n} slides; carousels use {SLIDE_RANGE[0]}-{SLIDE_RANGE[1]}"
            )
        )
    if not isinstance(spec.slides[0], CoverSlide):
        issues.append(RenderWarning("first_slide", 1, "the first slide must be the cover"))
    # Plan: "the recap or 'do this Monday' slide is the last content slide; an optional Sources
    # slide may follow it". Anywhere else, a sources slide is an issue.
    last = spec.slides[-1]
    content = spec.slides[:-1] if isinstance(last, SourcesSlide) else spec.slides
    if not content or not isinstance(content[-1], RecapSlide):
        issues.append(
            RenderWarning(
                "last_slide",
                len(content),
                "the last content slide must be a recap or 'do this Monday'",
            )
        )
    for i, slide in enumerate(content, start=1):
        if isinstance(slide, SourcesSlide):
            issues.append(
                RenderWarning(
                    "sources_position", i, "the sources slide goes at the end, after the recap"
                )
            )
    for i, slide in enumerate(spec.slides, start=1):
        if isinstance(slide, CoverSlide) and word_count(slide.hook) > MAX_COVER_WORDS:
            issues.append(
                RenderWarning(
                    "cover_words",
                    i,
                    f"cover hook is {word_count(slide.hook)} words (max {MAX_COVER_WORDS})",
                )
            )
        texts = slide_text(slide)
        words = sum(word_count(t) for t in texts)
        if words > MAX_SLIDE_WORDS and not isinstance(slide, SourcesSlide):
            issues.append(
                RenderWarning(
                    "slide_words", i, f"{words} words on the slide (max {MAX_SLIDE_WORDS})"
                )
            )
        if any(_DOMAIN_LIKE.search(t) for t in texts):
            issues.append(
                RenderWarning("domain_like", i, "domain-like token; carousels carry no links")
            )
    return issues
