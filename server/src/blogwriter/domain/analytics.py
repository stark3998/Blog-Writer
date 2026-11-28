"""Insights: experiments, suggestions, evals and export imports (container ``settings``).

Owner: analytics. Snapshots live on the post (:class:`blogwriter.domain.posts.MetricsSnapshot`).
Suggestions only: the owner approves, the planner never self-adjusts.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, model_validator

from blogwriter.domain.base import Code, DocId, Document, DomainModel, Slug, Timestamp, derived_id
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.states import ExperimentStatus, SuggestionStatus

__all__ = [
    "AnalyticsImport",
    "EvalLabel",
    "EvalResult",
    "EvalTopic",
    "Experiment",
    "ExperimentArm",
    "InsightSuggestion",
]

type PillarId = Annotated[Slug, Field(max_length=40)]


class ExperimentArm(DomainModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    label: str = Field(min_length=1, max_length=120)
    post_ids: list[DocId] = Field(default_factory=list[str], max_length=100)


class Experiment(Document):
    """A pre-registered experiment (one running at a time, at least 8 posts per arm)."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "experiment"
    ID_PREFIX: ClassVar[str] = "exp"
    OWNER: ClassVar[str] = "analytics"

    status: ExperimentStatus = ExperimentStatus.DRAFT
    status_changed_at: Timestamp
    question: Literal["links_vs_none", "slot_timing", "carousel_vs_image_card", "custom"]
    hypothesis: str = Field(min_length=1, max_length=500)
    metric: Literal["impressions", "reactions", "comments", "saves", "sends", "engagement_rate"]
    min_posts_per_arm: int = Field(default=8, ge=8, le=100)
    arms: list[ExperimentArm] = Field(min_length=2, max_length=4)
    started_at: Timestamp | None = None
    concluded_at: Timestamp | None = None
    conclusion: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _unique_arms(self) -> Self:
        ids = [arm.id for arm in self.arms]
        if len(ids) != len(set(ids)):
            raise ValueError("arm ids must be unique")
        return self


class InsightSuggestion(Document):
    """A suggestion from the weekly insights job, decided by the owner."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "insight_suggestion"
    ID_PREFIX: ClassVar[str] = "sug"
    OWNER: ClassVar[str] = "analytics"

    status: SuggestionStatus = SuggestionStatus.PROPOSED
    status_changed_at: Timestamp
    week_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    dimension: Literal["pillar", "format", "hook_type", "lane", "go_live_mode", "owner_signal"]
    value: str | None = Field(default=None, min_length=1, max_length=200)
    """The dimension's value the suggestion is about (a pillar id, a format, a hook type...)."""
    text: str = Field(min_length=1, max_length=500)
    evidence: str | None = Field(default=None, max_length=1000)
    """Rates per 1K impressions vs the trailing median that support it (numbers only)."""
    served_by: ServedByRecord | None = None
    decided_at: Timestamp | None = None

    @staticmethod
    def id_for(week_start: str, dimension: str, ordinal: int) -> str:
        """The ``ordinal``-th suggestion of one week and dimension (``analytics.suggest``)."""
        return derived_id("sug", week_start, dimension, ordinal)


type EvalLabel = Literal["as_is", "needs_edits", "reject"]


class EvalTopic(Document):
    """One of the 12 eval topics (4 formats x 3 pillars). ``brief_id``: the brief the eval
    pipeline drafts from (``analytics.eval`` makes it with one ``research.run`` per topic)."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "eval_topic"
    ID_PREFIX: ClassVar[str] = "evt"
    OWNER: ClassVar[str] = "analytics"

    pillar: PillarId
    format: Annotated[Slug, Field(max_length=40)]
    topic: str = Field(min_length=3, max_length=300)
    brief_id: DocId | None = None
    active: bool = True


class EvalResult(Document):
    """A generated eval article (in ``articles``, ``purpose=eval``, drafted by the real
    ``pipeline.article``), the judge's verdict (gate 1 alone) and the owner's label."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "eval_result"
    ID_PREFIX: ClassVar[str] = "evr"
    OWNER: ClassVar[str] = "analytics"

    topic_id: DocId
    run_id: str = Field(min_length=1, max_length=100)
    """The eval run (job id) this result belongs to."""
    article_id: DocId | None = None
    judge_passed: bool | None = None
    judge_score: float | None = Field(default=None, ge=0, le=10)
    owner_label: EvalLabel | None = None
    labelled_at: Timestamp | None = None
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=10)
    error_code: Code | None = None
    """Why the topic has no judged draft (``not_drafted``, ``pipeline_failed``, ``judge_missing``,
    ``cancelled``...); ``judge_passed`` is ``None`` then."""

    @staticmethod
    def id_for(topic_id: str, eval_job_id: str) -> str:
        """The result of one topic in one eval run (retry-safe)."""
        return derived_id("evr", topic_id, eval_job_id)


class AnalyticsImport(Document):
    """One upload of LinkedIn's native analytics export (matched by date, then URN)."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "analytics_import"
    ID_PREFIX: ClassVar[str] = "imp"
    OWNER: ClassVar[str] = "analytics"

    filename: str | None = Field(default=None, max_length=120)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: int = Field(default=0, ge=0)
    matched: int = Field(default=0, ge=0)
    unmatched: int = Field(default=0, ge=0)
    error_code: Code | None = None
    imported_at: Timestamp
