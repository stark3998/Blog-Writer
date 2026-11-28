"""Owner material: the story bank, the POV bank and interviews (container ``stories``).

**Private interview text is never persisted.** Every text field here holds the anonymized,
owner-confirmed wording produced by input-side redaction (blocklist, entity detection, the
anonymizer proposal, owner confirmation). Confidentiality is ``anonymized`` or ``public``;
there is no ``private`` level in storage.

Owners: stories (story and POV entries, intake); pipeline (interviews: questions and the
confirmed answers).
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field

from blogwriter.domain.base import (
    DocId,
    Document,
    DomainModel,
    HttpsUrl,
    Slug,
    Timestamp,
    derived_id,
)
from blogwriter.domain.common import ClassifierCheck, ServedByRecord
from blogwriter.domain.states import InterviewStatus, ReviewStatus

__all__ = [
    "Confidentiality",
    "Interview",
    "InterviewAnswer",
    "InterviewQuestion",
    "PovEntry",
    "PovOrigin",
    "StoryEntry",
    "StoryMetric",
    "StoryOrigin",
]

type Confidentiality = Literal["anonymized", "public"]
type StoryOrigin = Literal["interview", "seed", "quick_capture", "owner"]
type PovOrigin = Literal["interview", "quick_capture", "idea_take", "seed", "owner"]
type PillarId = Annotated[Slug, Field(max_length=40)]


class StoryMetric(DomainModel):
    """A metric, rounded into a bucket unless it is already public."""

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=60)
    """``"2,000+ apps"``, ``"~40%"``."""
    bucketed: bool = True
    public_source: HttpsUrl | None = None


class StoryEntry(Document):
    """One story: problem, actions, decisions, metrics, lessons (plan: "Story bank")."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "story"
    ID_PREFIX: ClassVar[str] = "story"
    OWNER: ClassVar[str] = "stories"

    status: ReviewStatus = ReviewStatus.PROPOSED
    status_changed_at: Timestamp
    origin: StoryOrigin
    title: str = Field(min_length=1, max_length=120)
    problem: str = Field(default="", max_length=2000)
    actions: str = Field(default="", max_length=2000)
    decisions: str = Field(default="", max_length=2000)
    """Decisions and trade-offs."""
    metrics: list[StoryMetric] = Field(default_factory=list[StoryMetric], max_length=10)
    lessons: str = Field(default="", max_length=2000)
    pillars: list[PillarId] = Field(default_factory=list[str], min_length=1, max_length=3)
    confidentiality: Confidentiality = "anonymized"
    tags: list[Annotated[Slug, Field(max_length=40)]] = Field(
        default_factory=list[str], max_length=10
    )
    classifier: ClassifierCheck = Field(default_factory=ClassifierCheck)
    approved_at: Timestamp | None = None
    interview_id: DocId | None = None
    seed_source: Literal["cv", "portfolio"] | None = None
    served_by: ServedByRecord | None = None
    """The model call that proposed the entry (``stories.seed`` extraction); ``None`` when the
    owner typed it."""

    @property
    def usable(self) -> bool:
        """Approved by the owner and passed by the classifier: the only entries drafts use."""
        return self.status is ReviewStatus.APPROVED and self.classifier.passed is True


class PovEntry(Document):
    """One of the owner's positions on a pillar, in their own words."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "pov"
    ID_PREFIX: ClassVar[str] = "pov"
    OWNER: ClassVar[str] = "stories"

    status: ReviewStatus = ReviewStatus.PROPOSED
    status_changed_at: Timestamp
    origin: PovOrigin
    pillar: PillarId
    statement: str = Field(min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=1000)
    confidentiality: Confidentiality = "anonymized"
    idea_id: DocId | None = None
    """For a one-tap "What's your take?" on an idea."""
    interview_id: DocId | None = None
    classifier: ClassifierCheck = Field(default_factory=ClassifierCheck)
    approved_at: Timestamp | None = None
    served_by: ServedByRecord | None = None
    """The model call that proposed the position (``stories.seed``); ``None`` when typed."""

    @property
    def usable(self) -> bool:
        return self.status is ReviewStatus.APPROVED and self.classifier.passed is True


class InterviewQuestion(DomainModel):
    id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=300)
    """What gap in the brief or story bank the question fills (shown to the owner)."""


class InterviewAnswer(DomainModel):
    """An answer as the owner confirmed it after redaction (never the raw dictation)."""

    question_id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=4000)
    confirmed_at: Timestamp
    redaction_findings: int = Field(default=0, ge=0)
    """How many spans input-side redaction changed (a count, never the spans)."""


class Interview(Document):
    """A micro (2 questions) or full (5-8 questions) interview for one piece."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "interview"
    ID_PREFIX: ClassVar[str] = "intv"
    OWNER: ClassVar[str] = "pipeline"

    status: InterviewStatus = InterviewStatus.PENDING
    status_changed_at: Timestamp
    mode: Literal["micro", "full"]
    article_id: DocId | None = None
    post_id: DocId | None = None
    questions: list[InterviewQuestion] = Field(min_length=1, max_length=8)
    answers: list[InterviewAnswer] = Field(default_factory=list[InterviewAnswer], max_length=8)
    due_at: Timestamp | None = None
    job_id: DocId | None = None
    """The pipeline job waiting for the answers (``waiting_input``, key ``interview``)."""
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=5)
    """The INTERVIEW call that wrote the questions (contract 1.8: every AI step is recorded)."""

    def answer(self, question_id: str) -> InterviewAnswer | None:
        return next((a for a in self.answers if a.question_id == question_id), None)

    @staticmethod
    def id_for_pipeline(article_id: str, pipeline_run: int) -> str:
        """The interview of one pipeline run (a resumed ``interview_questions`` step finds it
        instead of creating and pushing a second one)."""
        return derived_id("intv", "pipeline", article_id, pipeline_run)
