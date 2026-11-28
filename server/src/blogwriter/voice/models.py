"""Request and response models of the voice routes (camelCase, unknown input refused)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.base import HttpsUrl, Slug, Timestamp
from blogwriter.domain.states import GuideStatus, SampleStatus
from blogwriter.domain.voice import GuideRules, StyleGuide
from blogwriter.voice.text import MAX_SAMPLE_CHARS

__all__ = [
    "GuideEditIn",
    "GuideRulesIn",
    "GuideVersionSummary",
    "GuideView",
    "OwnerSourceType",
    "SampleIn",
    "SampleStatusIn",
    "SampleUpdateIn",
    "SuggestionDecisionIn",
    "VoiceSeedApproveIn",
]

type OwnerSourceType = Literal[
    "portfolio_post", "portfolio_about", "linkedin_post", "linkedin_comment"
]
"""Sources the owner adds by hand. Interview answers come from the pipeline (``add_sample``)
and final post texts from the digest."""

type Pillar = Annotated[Slug, Field(max_length=40)]
type Title = Annotated[str, Field(max_length=200)]
type RuleText = Annotated[str, Field(min_length=1, max_length=300)]
type PhraseText = Annotated[str, Field(min_length=1, max_length=100)]
type TermText = Annotated[str, Field(min_length=1, max_length=100)]
type NoteText = Annotated[str, Field(max_length=300)]


class SampleIn(ApiModel):
    """A new sample: ``text``, or an owner-typed ``url`` the research fetcher turns into text."""

    source_type: OwnerSourceType
    text: Annotated[str, Field(max_length=MAX_SAMPLE_CHARS)] | None = None
    url: HttpsUrl | None = None
    title: Title | None = None
    posted_at: Timestamp | None = None
    pillar: Pillar | None = None
    owner_unassisted: bool = False


class SampleUpdateIn(ApiModel):
    """Fields to change; a field left out stays as it is, ``null`` clears ``title``/``pillar``."""

    text: Annotated[str, Field(min_length=1, max_length=MAX_SAMPLE_CHARS)] | None = None
    title: Title | None = None
    pillar: Pillar | None = None
    owner_unassisted: bool | None = None


class SampleStatusIn(ApiModel):
    status: SampleStatus


class GuideRulesIn(ApiModel):
    prefer: list[RuleText] = Field(default_factory=list[str], max_length=50)
    avoid: list[RuleText] = Field(default_factory=list[str], max_length=50)
    banned_phrases: list[PhraseText] = Field(default_factory=list[str], max_length=200)
    vocabulary: dict[TermText, NoteText] = Field(default_factory=dict[str, str], max_length=200)

    def to_rules(self) -> GuideRules:
        return GuideRules(
            prefer=list(self.prefer),
            avoid=list(self.avoid),
            banned_phrases=list(self.banned_phrases),
            vocabulary=dict(self.vocabulary),
        )


class GuideEditIn(ApiModel):
    """The owner's edit of a version: a new ``proposed`` version with this body and rules."""

    body: Annotated[str, Field(min_length=1, max_length=50_000)]
    rules: GuideRulesIn = Field(default_factory=GuideRulesIn)


class SuggestionDecisionIn(ApiModel):
    decision: Literal["accept", "dismiss"]


class VoiceSeedApproveIn(ApiModel):
    """``POST /api/voice/seeds/{id}/approve`` (the body may be left out): how the v1 profile's
    sample text joins the corpus. ``title`` defaults to the profile's name (``null``: no title);
    ``sourceType`` to ``portfolio_post`` (v1 wrote blog posts)."""

    source_type: OwnerSourceType = "portfolio_post"
    title: Title | None = None
    pillar: Pillar | None = None
    owner_unassisted: bool = False


class GuideVersionSummary(ApiModel):
    id: str
    version: int
    status: GuideStatus
    created_at: str
    approved_at: str | None = None


class GuideView(ApiModel):
    """``GET /api/voice/guide``: the active guide, every version and whether one can be built."""

    active: Versioned[StyleGuide] | None
    versions: list[GuideVersionSummary]
    linkedin_samples: int
    required: int
    can_build: bool
