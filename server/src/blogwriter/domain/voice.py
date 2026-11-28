"""The voice corpus, the versioned style guide and the weekly diff digest (container ``stories``).

Owner: voice. Every sample is scored with the AI-tell lint and the structural slop detectors;
flagged passages inform topics and terminology only and are never used as exemplars.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field

from blogwriter.domain.base import (
    DocId,
    Document,
    DomainModel,
    HttpsUrl,
    Sha256Hex,
    Slug,
    Timestamp,
)
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.states import GuideStatus, SampleStatus, SuggestionStatus

__all__ = [
    "DraftComparison",
    "Exemplar",
    "GuideRules",
    "GuideSuggestion",
    "StyleGuide",
    "VoiceDigest",
    "VoiceSample",
    "VoiceSourceType",
]

type VoiceSourceType = Literal[
    "portfolio_post",
    "portfolio_about",
    "linkedin_post",
    "linkedin_comment",
    "interview_answer",
    "final_post",
]
"""``linkedin_post``: 5-10 past posts the owner wrote by hand (required). ``final_post``: the
owner-edited final text of a post published through the app."""


class VoiceSample(Document):
    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "voice_sample"
    ID_PREFIX: ClassVar[str] = "sample"
    OWNER: ClassVar[str] = "voice"

    status: SampleStatus = SampleStatus.ACTIVE
    status_changed_at: Timestamp
    source_type: VoiceSourceType
    owner_unassisted: bool = False
    """Written by the owner without AI help (highest weight)."""
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    url: HttpsUrl | None = None
    posted_at: Timestamp | None = None
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    word_count: int = Field(default=0, ge=0)
    weight: float = Field(default=1.0, ge=0, le=5)
    ai_tell_score: float = Field(default=0, ge=0)
    """AI tells and slop findings per 100 words."""
    flags: list[Finding] = Field(default_factory=list[Finding], max_length=500)
    exemplar_eligible: bool = False
    """No flags: the sample may be quoted in the guide as an exemplar."""
    content_sha256: Sha256Hex | None = None
    source_ref: DocId | None = None
    """The interview or post the sample came from."""


class Exemplar(DomainModel):
    sample_id: DocId
    excerpt: str = Field(min_length=1, max_length=1200)


class GuideRules(DomainModel):
    prefer: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=50
    )
    avoid: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=50
    )
    banned_phrases: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list[str], max_length=200
    )
    vocabulary: dict[str, str] = Field(default_factory=dict[str, str], max_length=200)
    """Preferred term -> note (pillar renames live in the pillar settings)."""


class StyleGuide(Document):
    """One version of the Voice & Style Guide. Exactly one version is ``active``."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "style_guide"
    OWNER: ClassVar[str] = "voice"

    version: int = Field(ge=1)
    status: GuideStatus = GuideStatus.PROPOSED
    status_changed_at: Timestamp
    body: str = Field(min_length=1, max_length=50_000)
    """The guide as Markdown (prompt-cached as the drafting prefix, 1 h TTL)."""
    rules: GuideRules = Field(default_factory=GuideRules)
    exemplars: list[Exemplar] = Field(default_factory=list[Exemplar], max_length=20)
    source_sample_ids: list[DocId] = Field(default_factory=list[str], max_length=200)
    based_on_version: int | None = Field(default=None, ge=1)
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=5)
    approved_at: Timestamp | None = None
    edited_by_owner: bool = False
    provisional: bool = False
    """Built at the owner's explicit request with fewer LinkedIn posts than the guide normally
    needs (``POST /api/voice/guide/build {"provisional": true}``); the UI labels it until a full
    build replaces it."""

    @staticmethod
    def id_for(version: int) -> str:
        """``style-guide-0003``: deterministic, so a retried build cannot duplicate a version."""
        if version < 1:
            raise ValueError("guide versions start at 1")
        return f"style-guide-{version:04d}"


class DraftComparison(DomainModel):
    """AI draft vs. final posted text of one piece (kit text included)."""

    target_kind: Literal["post", "article"]
    target_id: DocId
    edit_ratio: float = Field(ge=0, le=1)
    """Share of words the owner changed."""
    notable_edits: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list[str], max_length=10
    )


class GuideSuggestion(DomainModel):
    id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=500)
    rationale: str | None = Field(default=None, max_length=500)
    kind: Literal["prefer", "avoid", "banned_phrase", "vocabulary"] = "prefer"
    status: SuggestionStatus = SuggestionStatus.PROPOSED
    decided_at: Timestamp | None = None


class VoiceDigest(Document):
    """The weekly (first quarter) then monthly digest of AI draft vs final text."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "voice_digest"
    OWNER: ClassVar[str] = "voice"

    period_start: Timestamp
    period_end: Timestamp
    guide_version: int | None = Field(default=None, ge=1)
    comparisons: list[DraftComparison] = Field(default_factory=list[DraftComparison], max_length=50)
    suggestions: list[GuideSuggestion] = Field(default_factory=list[GuideSuggestion], max_length=20)
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=5)

    @staticmethod
    def id_for(period_end_date: str) -> str:
        """``voice-digest-2026-11-30`` (the local date the period ends)."""
        return f"voice-digest-{period_end_date}"
