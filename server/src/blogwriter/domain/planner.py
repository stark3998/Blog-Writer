"""Week plans (container ``ideas``) and reminders (container ``engagement``). Owner: planner.

A week runs Monday to Sunday in the owner's zone. The plan for week N+1 is proposed before
Sunday 18:00 of week N and approved by the owner; the planner never self-adjusts it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import Field, ValidationError, model_validator

from blogwriter.domain.base import (
    DocId,
    Document,
    DomainModel,
    ExpiringDocument,
    LocalDate,
    Slug,
    Timestamp,
    ZoneName,
    derived_id,
)
from blogwriter.domain.common import Finding
from blogwriter.domain.posts import Lane, PostFormat, Slot
from blogwriter.domain.settings import WeekMode
from blogwriter.domain.states import PlanStatus, ReminderStatus
from blogwriter.domain.weeks import plan_id, week_start
from blogwriter.notify.payload import NotificationPayload

__all__ = [
    "MAX_SLOT_REFS",
    "FlagshipPlan",
    "PlannedSlot",
    "Reminder",
    "ReminderKind",
    "Scorecard",
    "WeekPlan",
    "native_avoid_pillar",
]

type PillarId = Annotated[Slug, Field(max_length=40)]

MAX_SLOT_REFS: Final = 5
"""Stories (and POVs) one planned slot draws on at most."""


class FlagshipPlan(DomainModel):
    """The week's flagship article (optional in light weeks, none in away weeks)."""

    title: str = Field(min_length=1, max_length=300)
    pillar: PillarId
    format: Annotated[Slug, Field(max_length=40)]
    idea_id: DocId | None = None
    article_id: DocId | None = None
    publish_at: Timestamp | None = None
    optional: bool = False


class PlannedSlot(DomainModel):
    """A post slot of the week and what fills it.

    ``post_id`` is the post written for the slot (plan approval, or a quick take created for it
    with ``POST /api/posts {planSlot}``; :func:`blogwriter.domain.golive.link_plan_slot`). The
    references say what the post draws on: ``story_ids``, ``pov_ids``, ``idea_id`` and, for a
    derivative, the flagship ``article_id``."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    slot: Slot
    post_id: DocId | None = None
    lane: Lane | None = None
    pillar: PillarId | None = None
    format: PostFormat | None = None
    title: str | None = Field(default=None, max_length=300)
    """Working title until a post exists."""
    story_ids: list[DocId] = Field(default_factory=list[str], max_length=MAX_SLOT_REFS)
    pov_ids: list[DocId] = Field(default_factory=list[str], max_length=MAX_SLOT_REFS)
    idea_id: DocId | None = None
    article_id: DocId | None = None


def native_avoid_pillar(
    lanes: Sequence[tuple[Lane | None, str | None]], flagship_pillar: str | None
) -> str | None:
    """The pillar a week's native posts go around (``lanes.nativeOtherPillar``): the pillar of
    the week's derivatives (Monday's flagship, which they promote) when the week has any, else
    the week's own flagship's. One rule for the planner placing posts and the posts lint."""
    for lane, pillar in lanes:
        if lane == "derivative" and pillar is not None:
            return pillar
    return flagship_pillar


class Scorecard(DomainModel):
    """The weekly quality scorecard (replaces streaks)."""

    posts_published: int = Field(default=0, ge=0)
    posts_with_owner_signal: int = Field(default=0, ge=0)
    replies: int = Field(default=0, ge=0)
    substantive_comments: int = Field(default=0, ge=0)
    on_pillar_share: float | None = Field(default=None, ge=0, le=1)
    computed_at: Timestamp


class WeekPlan(Document):
    """The plan of one week (id ``plan-<Monday>``)."""

    CONTAINER: ClassVar[str] = "ideas"
    KIND: ClassVar[str] = "week_plan"
    OWNER: ClassVar[str] = "planner"

    week_start: LocalDate
    timezone: ZoneName
    mode: WeekMode = "normal"
    status: PlanStatus = PlanStatus.GENERATING
    status_changed_at: Timestamp
    flagship: FlagshipPlan | None = None
    slots: list[PlannedSlot] = Field(default_factory=list[PlannedSlot], max_length=10)
    pillar_balance: dict[str, int] = Field(default_factory=dict[str, int])
    """Posts per pillar across the whole week (at least 80% on-pillar)."""
    warnings: list[Finding] = Field(default_factory=list[Finding], max_length=50)
    """Spacing and mix findings (two posts on one day blocks approval; < 24 h warns)."""
    rationale: str | None = Field(default=None, max_length=2000)
    approved_at: Timestamp | None = None
    scorecard: Scorecard | None = None
    job_id: DocId | None = None

    @model_validator(mode="after")
    def _id_matches_week(self) -> Self:
        if week_start(self.week_start) != self.week_start:
            raise ValueError("week_start must be a Monday")
        if self.id != plan_id(self.week_start):
            raise ValueError("a week plan's id is plan-<week_start>")
        return self


type ReminderKind = Literal["reply", "snapshot", "kit_confirm"]
"""Every kind has a producer: ``reply`` (+15/+60/+180 min) and ``snapshot`` (+48 h/+7 d) from
``planner.post-live``; ``kit_confirm`` (confirm a natively scheduled post went out, with its
final text) from ``planner.post-scheduled``. Other prompts are direct pushes by the job that
knows (plan ready, veto notice, token expiry, eval drafts) or Today items computed from documents
(pending interviews, restore drill, owner inputs)."""


class Reminder(ExpiringDocument):
    """A push reminder delivered by a delayed ``planner.remind`` job at ``due_at``.

    The id is :meth:`id_for` its dedupe ``key``, so creating the same reminder twice is a
    ``Conflict``. The job id is ``remind-<reminder id>``; cancelling a reminder (status
    ``cancelled``) turns the job into a no-op, and the job expires a reminder it finds past
    ``expires_at`` (a reply nudge hours late is noise). Title and URL pass the push payload's
    own checks, so a stored reminder can always be sent. Stored in ``engagement`` with a per-item
    TTL (garbage collection only).
    """

    CONTAINER: ClassVar[str] = "engagement"
    KIND: ClassVar[str] = "reminder"
    ID_PREFIX: ClassVar[str] = "rem"
    OWNER: ClassVar[str] = "planner"

    reminder_kind: ReminderKind
    status: ReminderStatus = ReminderStatus.PENDING
    status_changed_at: Timestamp
    due_at: Timestamp
    target_kind: Literal["post", "article", "week_plan", "interview", "linkedin", "system"]
    target_id: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=80)
    """The push title (title and link only; never draft text)."""
    url: str = Field(min_length=1, max_length=512, pattern=r"^/[^/\\]")
    """Same-origin path the notification opens (a confirmation page)."""
    sent_at: Timestamp | None = None
    delivered: bool | None = None
    key: str = Field(min_length=1, max_length=100)
    """Dedupe key, e.g. ``reply:<postId>:15`` (the id derives from it: one reminder per key)."""

    @model_validator(mode="after")
    def _sendable(self) -> Self:
        try:
            NotificationPayload(title=self.title, url=self.url, tag="reminder")
        except ValidationError:
            raise ValueError(
                "reminder title and url must pass the push payload checks "
                "(no control characters, a same-origin path of URL characters)"
            ) from None
        return self

    @staticmethod
    def id_for(key: str) -> str:
        """``rem-<28 hex>`` of the dedupe key."""
        return derived_id("rem", key)
