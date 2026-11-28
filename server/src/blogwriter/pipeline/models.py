"""Request and response models of the pipeline routes, and the structured outputs of its AI steps.

Request models are :class:`~blogwriter.domain.api.ApiModel` (camelCase, unknown fields refused):
a client never sends a whole document. The output schemas use plain fields only (no ``dict`` or
``Any``) and state their size rules as descriptions rather than constraints, so a reply that is
a little long is trimmed in code instead of burning a repair turn. Scores keep their 0-10 range
as a constraint: a score outside it is a wrong answer, not a long one.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.articles import Article

__all__ = [
    "DIMENSIONS",
    "AnswerIn",
    "AnswersIn",
    "ArticleJobAccepted",
    "CritiqueNote",
    "CritiqueOut",
    "Dimension",
    "InterviewOut",
    "OutlineOut",
    "PipelineStartIn",
    "QuestionOut",
    "ReviseIn",
    "SectionOut",
    "SelectionIn",
    "SelectionOut",
    "StartFrom",
]

type StartFrom = Literal["research", "interview", "draft"]

type Dimension = Literal[
    "clarity",
    "structure",
    "accuracy",
    "depth",
    "first_hand_specificity",
    "voice",
    "originality",
    "actionability",
]
DIMENSIONS: tuple[Dimension, ...] = (
    "clarity",
    "structure",
    "accuracy",
    "depth",
    "first_hand_specificity",
    "voice",
    "originality",
    "actionability",
)
"""The rubric dimensions (the same names and order as gate 1, ``gates.RUBRIC_DIMENSIONS``)."""

type Score = Annotated[int, Field(ge=0, le=10)]

# --------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------


class PipelineStartIn(ApiModel):
    """``POST /api/articles/{id}/pipeline``."""

    start_from: StartFrom = "research"


class ArticleJobAccepted(ApiModel):
    """202 answer of the pipeline and revise routes: the moved article and the job."""

    article: Versioned[Article]
    job_id: str


class ReviseIn(ApiModel):
    """``POST /api/articles/{id}/revise``: the owner's revision instruction."""

    instruction: str = Field(min_length=1, max_length=1000)


class SelectionIn(ApiModel):
    """``POST /api/articles/{id}/selection``: a passage of the draft and what to do with it."""

    text: str = Field(min_length=1, max_length=8000)
    instruction: str = Field(min_length=1, max_length=500)


class SelectionOut(ApiModel):
    """The proposal for the selected passage (nothing is saved; the editor shows a diff and
    applies it through autosave). ``proposal`` is ``None`` with the ``reason`` when there is no
    usable proposal."""

    proposal: str | None
    reason: Literal["refused", "empty", "blocklist_hit"] | None = None
    review_only: bool = False
    """Served by a fallback model or after a refusal (the owner reviews it anyway)."""


class AnswerIn(ApiModel):
    """One answer as the owner confirmed it through stories intake (never raw dictation)."""

    question_id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=4000)
    redaction_findings: int = Field(default=0, ge=0, le=1000)
    """How many spans input-side redaction changed (a count, never the spans)."""


class AnswersIn(ApiModel):
    """``POST /api/interviews/{id}/answers``."""

    answers: list[AnswerIn] = Field(min_length=1, max_length=8)


# --------------------------------------------------------------------------------------------
# Structured outputs (anonymized, tool-free)
# --------------------------------------------------------------------------------------------


class QuestionOut(ApiModel):
    text: str = Field(description="One question in plain words, under 300 characters.")
    purpose: str = Field(
        description="Which gap in the brief or the story bank it fills, under 200 characters."
    )


class InterviewOut(ApiModel):
    """INTERVIEW: the questions, in the order the owner answers them."""

    questions: list[QuestionOut] = Field(
        description="Exactly the number of questions the request asks for, most important first."
    )


class SectionOut(ApiModel):
    heading: str = Field(description="The section heading as the reader sees it, under 80 chars.")
    purpose: str = Field(description="What this section proves or teaches, under 200 chars.")
    points: list[str] = Field(
        description="2 to 6 concrete points in order, each under 240 characters."
    )
    citation_ids: list[str] = Field(
        description="Brief citation ids this section relies on (only ids from the brief)."
    )
    story_ids: list[str] = Field(description="Story ids from the author's material used here.")
    pov_ids: list[str] = Field(description="POV ids from the author's material used here.")
    interview_question_ids: list[str] = Field(
        description="Interview question ids whose answers this section is built on."
    )


class OutlineOut(ApiModel):
    """OUTLINE: the plan of the article the drafter follows."""

    title: str = Field(description="A specific title under 100 characters, no clickbait.")
    excerpt: str = Field(description="One or two plain sentences under 280 characters.")
    slug: str = Field(description="Lowercase kebab-case slug under 60 characters.")
    tags: list[str] = Field(description="3 to 6 lowercase kebab-case tags.")
    thesis: str = Field(
        description="The one claim the article argues, in the author's terms, under 300 chars."
    )
    opening: str = Field(
        description="How the article opens: the concrete first-hand moment or fact, under 400."
    )
    sections: list[SectionOut] = Field(description="3 to 8 body sections in order.")
    closing: str = Field(
        description="What the reader does on Monday after reading, under 300 characters."
    )
    story_ids: list[str] = Field(description="Every story id the article uses.")
    pov_ids: list[str] = Field(description="Every POV id the article uses.")
    diagram: str | None = Field(
        default=None,
        description="What the one Mermaid diagram shows, under 200 characters, or null.",
    )


class CritiqueNote(ApiModel):
    dimension: Dimension
    where: str = Field(description="The section heading or the first words of the sentence.")
    problem: str = Field(description="The exact problem, under 200 characters.")
    fix: str = Field(description="The concrete change to make, under 240 characters.")


class CritiqueOut(ApiModel):
    """CRITIQUE: rubric scores (gate 1's dimensions and scale) and revision notes."""

    clarity: Score
    structure: Score
    accuracy: Score
    depth: Score
    first_hand_specificity: Score
    voice: Score
    originality: Score
    actionability: Score
    overall: Score
    notes: list[CritiqueNote] = Field(
        description="At most 10 notes, worst problem first; only for problems worth a rewrite."
    )
    keep: list[str] = Field(
        description="At most 5 things that work and must survive the revision, under 160 chars."
    )

    def scores(self) -> dict[str, int]:
        """The eight dimension scores by name."""
        return {name: int(getattr(self, name)) for name in DIMENSIONS}
