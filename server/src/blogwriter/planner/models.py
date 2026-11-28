"""Request and response models of the planner routes (camelCase on the wire, unknown input
refused). Documents themselves are ``blogwriter.domain.planner`` models: clients never send a
whole document."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.articles import VetoNotice
from blogwriter.domain.base import DocId, LocalDate, LocalTime
from blogwriter.domain.planner import Scorecard, WeekPlan
from blogwriter.domain.posts import Lane, PostFormat
from blogwriter.domain.settings import WeekMode
from blogwriter.domain.states import PlanStatus

__all__ = [
    "MAX_TODAY_TITLE",
    "PRIORITY_RANK",
    "SLOT_ID_PATTERN",
    "ApproveOut",
    "FlagshipIn",
    "ModeIn",
    "PlanEditIn",
    "PlanSlotIn",
    "Priority",
    "ReslotIn",
    "SlotIn",
    "Today",
    "TodayItem",
    "TodayKind",
    "TodayRefs",
    "TodayWeek",
    "VetoView",
    "WeekScorecard",
]

SLOT_ID_PATTERN: Final = r"^[a-z0-9-]+$"
MAX_TODAY_TITLE: Final = 80

type TodayKind = Literal[
    "approve_plan",
    "answer_interview",
    "review_article",
    "veto_window",
    "approve_post",
    "take_needed",
    "publish_post",
    "kit_confirm",
    "schedule_kit",
    "missed_slot",
    "reply",
    "snapshot",
    "needs_attention",
    "schedule_confirm",
    "reconnect_linkedin",
    "owner_input",
    "restore_drill",
]
type Priority = Literal["urgent", "high", "normal", "low"]
PRIORITY_RANK: Final[dict[str, int]] = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
type PillarRef = Annotated[str, Field(min_length=1, max_length=40)]


# --------------------------------------------------------------------------------------------
# Week plans
# --------------------------------------------------------------------------------------------


class PlanSlotIn(ApiModel):
    """One slot of a plan edit (drag and drop): where it goes and what fills it."""

    id: str = Field(min_length=1, max_length=40, pattern=SLOT_ID_PATTERN)
    local_date: LocalDate
    local_time: LocalTime
    post_id: DocId | None = None
    """Link an existing post to the slot (its slot and ``weekPlanId`` follow the plan)."""
    lane: Lane | None = None
    pillar: PillarRef | None = None
    format: PostFormat | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class FlagshipIn(ApiModel):
    title: str = Field(min_length=1, max_length=150)
    pillar: PillarRef
    format: Annotated[str, Field(min_length=1, max_length=40)]
    idea_id: DocId | None = None
    optional: bool = False


class PlanEditIn(ApiModel):
    """``PUT /api/plan/weeks/{weekStart}``. ``flagship`` set to ``null`` removes it; leaving
    it out keeps it."""

    mode: WeekMode | None = None
    flagship: FlagshipIn | None = None
    slots: list[PlanSlotIn] = Field(max_length=10)


class ModeIn(ApiModel):
    mode: WeekMode


class ApproveOut(ApiModel):
    plan: Versioned[WeekPlan]
    article_id: str | None = None
    """The flagship article (created planned, its pipeline enqueued for research start)."""
    job_ids: list[str] = Field(default_factory=list[str])
    """Every job the approval started (the flagship's pipeline start, the posts' writing)."""
    post_ids: list[str] = Field(default_factory=list[str])
    """The posts the approval started for the plan's native slots (``generating``)."""


# --------------------------------------------------------------------------------------------
# Re-slotting, veto, scorecard
# --------------------------------------------------------------------------------------------


class SlotIn(ApiModel):
    """A local date and time in the cadence's zone."""

    local_date: LocalDate
    local_time: LocalTime


class ReslotIn(ApiModel):
    """``snooze`` (30 minutes later), ``next_slot`` (the next free template slot) or ``set``
    (``slot`` required)."""

    action: Literal["snooze", "next_slot", "set"]
    slot: SlotIn | None = None


class VetoView(ApiModel):
    publish_at: str | None = None
    auto: bool = False
    notice: VetoNotice | None = None


class WeekScorecard(ApiModel):
    week_start: str
    scorecard: Scorecard


# --------------------------------------------------------------------------------------------
# Today
# --------------------------------------------------------------------------------------------


class TodayRefs(ApiModel):
    """Ids the web needs to act on an item (only the relevant ones are set)."""

    article_id: str | None = None
    post_id: str | None = None
    interview_id: str | None = None
    schedule_id: str | None = None
    reminder_id: str | None = None
    """Dismiss with ``POST /api/reminders/{reminderId}/dismiss``."""
    job_id: str | None = None
    idea_id: str | None = None
    week_start: str | None = None


class TodayItem(ApiModel):
    id: str
    """Stable per item: ``<kind>:<document id>``."""
    kind: TodayKind
    title: str = Field(min_length=1, max_length=MAX_TODAY_TITLE)
    href: str
    """Same-origin path of the page that acts on it."""
    due_at: str | None = None
    priority: Priority = "normal"
    refs: TodayRefs = Field(default_factory=TodayRefs)


class TodayWeek(ApiModel):
    week_start: str
    mode: WeekMode
    plan_status: PlanStatus | None = None
    scorecard: Scorecard | None = None
    """This week so far (posts, owner signal, replies, substantive comments, on-pillar share)."""


class Today(ApiModel):
    generated_at: str
    week: TodayWeek
    items: list[TodayItem]
