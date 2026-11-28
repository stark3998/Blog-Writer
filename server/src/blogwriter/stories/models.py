"""Request and response models of the stories routes (camelCase, unknown fields refused).

Requests never carry a whole document: the server sets ids, statuses, origins, timestamps and
the classifier verdict. Text fields hold **owner-confirmed** wording (after ``redact`` and
``check``); raw intake text appears only in :class:`RedactIn`, which is never stored.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import DocId, HttpsUrl, Slug
from blogwriter.domain.common import Finding
from blogwriter.domain.stories import Confidentiality
from blogwriter.stories.intake import IntakePurpose
from blogwriter.stories.settings import INTAKE_HARD_MAX_CHARS

__all__ = [
    "CheckIn",
    "CheckOut",
    "MetricIn",
    "PillarId",
    "PovIn",
    "RedactIn",
    "ReviewDecision",
    "ReviewIn",
    "SeedIn",
    "SeedSource",
    "StoryIn",
    "TagSlug",
]

type PillarId = Annotated[Slug, Field(max_length=40)]
type TagSlug = Annotated[Slug, Field(max_length=40)]
type ReviewDecision = Literal["approve", "reject", "archive", "restore"]
type SeedSource = Literal["cv", "portfolio"]


class RedactIn(ApiModel):
    """Raw owner text to redact (the only place raw text is accepted; never stored).

    The length limit is ``BW_STORIES_INTAKE_MAX_CHARS`` (8000 by default; the route checks it).
    """

    text: str = Field(min_length=1, max_length=INTAKE_HARD_MAX_CHARS)
    purpose: IntakePurpose
    pillar: PillarId | None = None


class CheckIn(ApiModel):
    """Owner-confirmed text to check locally before saving (no model call)."""

    text: str = Field(min_length=1, max_length=INTAKE_HARD_MAX_CHARS)


class CheckOut(ApiModel):
    """``ok`` is false when any finding blocks a save."""

    ok: bool
    findings: list[Finding]


class MetricIn(ApiModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=60)
    bucketed: bool = True
    public_source: HttpsUrl | None = None


class StoryIn(ApiModel):
    """A story in the owner's confirmed wording."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    problem: str = Field(default="", max_length=2000)
    actions: str = Field(default="", max_length=2000)
    decisions: str = Field(default="", max_length=2000)
    metrics: list[MetricIn] = Field(default_factory=list[MetricIn], max_length=10)
    lessons: str = Field(default="", max_length=2000)
    pillars: list[PillarId] = Field(min_length=1, max_length=3)
    confidentiality: Confidentiality = "anonymized"
    tags: list[TagSlug] = Field(default_factory=list[str], max_length=10)

    @model_validator(mode="after")
    def _dedupe(self) -> Self:
        self.pillars = list(dict.fromkeys(self.pillars))
        self.tags = list(dict.fromkeys(self.tags))
        return self


class PovIn(ApiModel):
    """A POV entry in the owner's confirmed wording. ``origin`` is kept from the first save."""

    model_config = ConfigDict(str_strip_whitespace=True)

    pillar: PillarId
    statement: str = Field(min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=1000)
    confidentiality: Confidentiality = "anonymized"
    origin: Literal["owner", "quick_capture", "idea_take"] = "owner"
    idea_id: DocId | None = None

    @model_validator(mode="after")
    def _idea_take_needs_an_idea(self) -> Self:
        if self.context is not None and not self.context:
            self.context = None
        if self.origin == "idea_take" and self.idea_id is None:
            raise ValueError("an idea_take POV needs ideaId")
        return self


class ReviewIn(ApiModel):
    decision: ReviewDecision


class SeedIn(ApiModel):
    """Public resume or portfolio text to propose entries from (JSON form of the seed route)."""

    text: str = Field(min_length=1)
    source: Literal["cv", "portfolio"]
    """Inline, not the ``SeedSource`` alias: the seed route's OpenAPI body is this model's JSON
    schema, where a ``#/$defs/...`` reference would not resolve."""
