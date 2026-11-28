"""Request and response models of the research routes, and the structured outputs of its AI
steps.

Request models are :class:`~blogwriter.domain.api.ApiModel` (camelCase, unknown fields refused):
a client never sends a whole document. The output schemas use plain fields only (no ``dict`` or
``Any``) and carry their size rules as descriptions instead of constraints: a model reply that is
a little long is trimmed in code rather than burning a repair turn.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.base import SLUG_PATTERN, DocId, HttpsUrl
from blogwriter.domain.research import ResearchRun

__all__ = [
    "BriefEditIn",
    "BriefOutput",
    "FindingEdit",
    "FindingOut",
    "PlanOutput",
    "QuickIn",
    "RunAccepted",
    "RunIn",
    "SourceExtraction",
    "StatOut",
    "VerifiedClaimOut",
    "VerifyOutput",
]

type PillarId = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=40)]
type ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$", max_length=64)]

# --------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------


class RunIn(ApiModel):
    """``POST /api/research/runs``."""

    topic: str = Field(min_length=3, max_length=300)
    pillar: PillarId
    audience: str | None = Field(default=None, min_length=1, max_length=300)
    recency_days: int | None = Field(default=None, ge=1, le=3650)
    article_id: DocId | None = None
    idea_id: DocId | None = None
    owner_urls: list[HttpsUrl] = Field(default_factory=list[str], max_length=10)


class RunAccepted(ApiModel):
    """202 answer of ``POST /api/research/runs``: the run and the job that works on it."""

    run: Versioned[ResearchRun]
    job_id: str


class FindingEdit(ApiModel):
    id: ItemId
    text: str = Field(min_length=1, max_length=600)


class BriefEditIn(ApiModel):
    """``PUT /api/research/briefs/{id}``: the owner edits summaries and removes items.

    ``findings`` is the new list of findings: each id must already exist (its citations are
    kept), findings left out are removed. The owner never adds quotes, claims or citations.
    """

    executive_summary: str | None = Field(default=None, min_length=1, max_length=4000)
    findings: list[FindingEdit] | None = Field(default=None, max_length=40)
    counterpoints: list[Annotated[str, Field(min_length=1, max_length=600)]] | None = Field(
        default=None, max_length=20
    )
    open_questions: list[Annotated[str, Field(min_length=1, max_length=300)]] | None = Field(
        default=None, max_length=20
    )
    angles: list[Annotated[str, Field(min_length=1, max_length=300)]] | None = Field(
        default=None, max_length=20
    )
    hook_seeds: list[Annotated[str, Field(min_length=1, max_length=300)]] | None = Field(
        default=None, max_length=20
    )
    remove_claim_ids: list[ItemId] = Field(default_factory=list[str], max_length=100)
    remove_citation_ids: list[ItemId] = Field(default_factory=list[str], max_length=60)


class QuickIn(ApiModel):
    """``POST /api/research/quick``."""

    query: str = Field(min_length=3, max_length=300)
    pillar: PillarId | None = None
    article_id: DocId | None = None
    post_id: DocId | None = None
    idea_id: DocId | None = None


# --------------------------------------------------------------------------------------------
# Structured outputs (all public data)
# --------------------------------------------------------------------------------------------


class PlanOutput(ApiModel):
    """PLAN: what the run searches for."""

    sub_questions: list[str] = Field(
        description="3 to 6 specific, searchable sub-questions, each under 300 characters."
    )
    audience: str | None = Field(
        default=None, description="The most likely reader as a short role phrase, or null."
    )
    recency_days: int = Field(description="How many days back sources may go, 1 to 3650.")


class SourceExtraction(ApiModel):
    """RESEARCH (tool-free): claims and quotes from one page."""

    relevant: bool = Field(description="Whether the page addresses the topic at all.")
    claims: list[str] = Field(
        description="At most 8 claims, one sentence each, under 300 characters, own words."
    )
    quotes: list[str] = Field(
        description="At most 2 exact quotes copied from the page, each under 150 characters."
    )
    publisher: str | None = Field(
        default=None, description="The organization that published the page, or null."
    )
    published: str | None = Field(
        default=None, description="Publication date as YYYY-MM-DD, YYYY-MM or YYYY, or null."
    )


class VerifiedClaimOut(ApiModel):
    text: str = Field(description="One sentence, under 300 characters.")
    sources: list[str] = Field(description="The ids of the input claims this claim merges.")
    status: Literal["verified", "unverified", "conflict"]
    conflict_note: str | None = Field(
        default=None, description="What the sources disagree on, under 300 characters."
    )


class StatOut(ApiModel):
    value: str = Field(description="The number with its unit, under 40 characters.")
    label: str = Field(description="What was measured, by whom and where; under 250 characters.")
    citation_ids: list[str] = Field(description="Citation ids of the sources that report it.")
    as_of: str | None = Field(default=None, description="YYYY, YYYY-MM or YYYY-MM-DD, or null.")
    key: bool = Field(description="True only when two independent sources support it.")


class VerifyOutput(ApiModel):
    """SYNTHESIS: deduplicated, dated and conflict-checked claims, and the statistics."""

    claims: list[VerifiedClaimOut]
    stats: list[StatOut]


class FindingOut(ApiModel):
    text: str = Field(description="One or two sentences, under 500 characters.")
    citation_ids: list[str] = Field(description="Citation ids of the claims it rests on.")


class BriefOutput(ApiModel):
    """SYNTHESIS: the written parts of the brief (claims, quotes and citations come from code)."""

    executive_summary: str = Field(description="4 to 7 plain sentences.")
    findings: list[FindingOut]
    counterpoints: list[str]
    open_questions: list[str]
    angles: list[str]
    hook_seeds: list[str]
