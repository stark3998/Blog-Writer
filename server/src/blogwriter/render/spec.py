"""Slide-spec models (the JSON the AI layer and the Studio editor produce).

Hard limits here are *layout* limits: the templates are designed so that the longest allowed value
of every field fits its box at the minimum type size (checked at render time and by the layout
tests). Editorial rules from the plan (cover <= 10 words, <= 35 words per slide, title <= 58
characters, 8-12 slides, the recap closes the deck) live in :mod:`blogwriter.render.lint`, so a
non-compliant draft can still be previewed.

The brand is not part of the spec: every render call takes a
:class:`~blogwriter.render.brand.RenderBrand` next to it.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from blogwriter.render.text import PlainText

__all__ = [
    "MAX_MERMAID_SOURCE",
    "MAX_SLIDES",
    "SLIDE_TYPES",
    "CarouselSpec",
    "Cell",
    "CoverSlide",
    "DiagramSlide",
    "FrameworkSlide",
    "Lang",
    "ListSlide",
    "OgImageSpec",
    "QuoteSlide",
    "RecapSlide",
    "Slide",
    "Source",
    "SourcesSlide",
    "StatSlide",
    "StatementSlide",
    "Step",
    "StepsSlide",
]

MAX_MERMAID_SOURCE: Final = 3000
MAX_SLIDES: Final = 20

Lang = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CoverSlide(_Frozen):
    type: Literal["cover"] = "cover"
    kicker: Annotated[PlainText, Field(max_length=40)] | None = None
    """Series or pillar name."""
    hook: Annotated[PlainText, Field(max_length=80)]
    subtitle: Annotated[PlainText, Field(max_length=110)] | None = None


class StatementSlide(_Frozen):
    type: Literal["statement"] = "statement"
    statement: Annotated[PlainText, Field(max_length=200)]
    note: Annotated[PlainText, Field(max_length=140)] | None = None


class ListSlide(_Frozen):
    type: Literal["list"] = "list"
    title: Annotated[PlainText, Field(max_length=70)]
    items: Annotated[
        list[Annotated[PlainText, Field(max_length=100)]], Field(min_length=2, max_length=5)
    ]


class Step(_Frozen):
    title: Annotated[PlainText, Field(max_length=48)]
    detail: Annotated[PlainText, Field(max_length=90)] | None = None


class StepsSlide(_Frozen):
    type: Literal["steps"] = "steps"
    title: Annotated[PlainText, Field(max_length=70)]
    steps: Annotated[list[Step], Field(min_length=2, max_length=4)]


class Cell(_Frozen):
    label: Annotated[PlainText, Field(max_length=28)]
    text: Annotated[PlainText, Field(max_length=64)]


class FrameworkSlide(_Frozen):
    """``2x2``: exactly four cells, row-major (top-left, top-right, bottom-left, bottom-right)
    with optional axis labels. ``layered``: 3-5 cells, outermost layer first."""

    type: Literal["framework"] = "framework"
    title: Annotated[PlainText, Field(max_length=70)]
    layout: Literal["2x2", "layered"]
    cells: Annotated[list[Cell], Field(min_length=3, max_length=5)]
    x_axis: Annotated[PlainText, Field(max_length=28)] | None = None
    y_axis: Annotated[PlainText, Field(max_length=28)] | None = None

    @model_validator(mode="after")
    def _shape(self) -> FrameworkSlide:
        if self.layout == "2x2":
            if len(self.cells) != 4:
                raise ValueError("a 2x2 framework needs exactly 4 cells")
        elif self.x_axis or self.y_axis:
            raise ValueError("axes are only valid for the 2x2 layout")
        return self


class DiagramSlide(_Frozen):
    type: Literal["diagram"] = "diagram"
    title: Annotated[PlainText, Field(max_length=70)]
    mermaid: Annotated[str, Field(min_length=1, max_length=MAX_MERMAID_SOURCE)]
    """Mermaid source. Not plain text: it is neutralised and rendered in an isolated context."""
    alt: Annotated[PlainText, Field(max_length=250)]
    caption: Annotated[PlainText, Field(max_length=120)] | None = None

    @field_validator("mermaid")
    @classmethod
    def _no_nul(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("NUL is not allowed in diagram source")
        return v


class StatSlide(_Frozen):
    type: Literal["stat"] = "stat"
    value: Annotated[PlainText, Field(max_length=10)]
    """For example "2,000", "73%" or "$4.2M"."""
    label: Annotated[PlainText, Field(max_length=90)]
    context: Annotated[PlainText, Field(max_length=140)] | None = None
    source: Annotated[PlainText, Field(max_length=90)] | None = None
    """Publisher and title, never a URL."""


class QuoteSlide(_Frozen):
    type: Literal["quote"] = "quote"
    quote: Annotated[PlainText, Field(max_length=200)]
    attribution: Annotated[PlainText, Field(max_length=48)]
    role: Annotated[PlainText, Field(max_length=64)] | None = None


class RecapSlide(_Frozen):
    type: Literal["recap"] = "recap"
    title: Annotated[PlainText, Field(max_length=60)] = "Do this Monday"
    items: Annotated[
        list[Annotated[PlainText, Field(max_length=90)]], Field(min_length=2, max_length=5)
    ]


class Source(_Frozen):
    publisher: Annotated[PlainText, Field(max_length=40)]
    title: Annotated[PlainText, Field(max_length=100)]
    date: Annotated[PlainText, Field(max_length=16)] | None = None


class SourcesSlide(_Frozen):
    type: Literal["sources"] = "sources"
    title: Annotated[PlainText, Field(max_length=40)] = "Sources"
    sources: Annotated[list[Source], Field(min_length=1, max_length=5)]


Slide = Annotated[
    CoverSlide
    | StatementSlide
    | ListSlide
    | StepsSlide
    | FrameworkSlide
    | DiagramSlide
    | StatSlide
    | QuoteSlide
    | RecapSlide
    | SourcesSlide,
    Field(discriminator="type"),
]

SLIDE_TYPES: Final[tuple[str, ...]] = (
    "cover",
    "statement",
    "list",
    "steps",
    "framework",
    "diagram",
    "stat",
    "quote",
    "recap",
    "sources",
)


class CarouselSpec(_Frozen):
    """A carousel (LinkedIn document post). Rendered as one PDF page per slide."""

    title: Annotated[PlainText, Field(max_length=100)]
    """LinkedIn document title and PDF ``/Title``. Lint wants <= 58 characters."""
    lang: Lang = "en"
    slides: Annotated[list[Slide], Field(min_length=1, max_length=MAX_SLIDES)]


class OgImageSpec(_Frozen):
    """A 1200x630 Open Graph image for an article."""

    title: Annotated[PlainText, Field(max_length=110)]
    subtitle: Annotated[PlainText, Field(max_length=140)] | None = None
    pillar_label: Annotated[PlainText, Field(max_length=40)] | None = None
    lang: Lang = "en"
