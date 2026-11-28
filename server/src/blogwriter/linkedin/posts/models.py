"""Request and response models of the posts routes (camelCase, unknown input refused).

The list answers :class:`PostSummary` items built from :class:`PostRow` projections
(``find_docs(..., fields=PostRow)``), so it never reads hook variants, checks, served-by
records, snapshots, the carousel spec or the final text.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.base import (
    DocId,
    DomainModel,
    HttpsUrl,
    LocalDate,
    LocalTime,
    Slug,
    Timestamp,
    ZoneName,
)
from blogwriter.domain.common import Finding, MediaRef
from blogwriter.domain.hashing import approval_hash
from blogwriter.domain.posts import (
    Approval,
    GoLiveMode,
    KitInfo,
    Lane,
    LinkException,
    PlanSlotId,
    Post,
    PostFormat,
    Slot,
)
from blogwriter.domain.states import KitState, PostStatus

__all__ = [
    "ApproveIn",
    "AttributionIn",
    "CarouselIn",
    "LinkExceptionIn",
    "LintIn",
    "LintOut",
    "PlanSlotRef",
    "PostCreateIn",
    "PostCreated",
    "PostEditIn",
    "PostRow",
    "PostSummary",
    "RegenerateIn",
    "RegenerateWhat",
    "SlotIn",
]

type PillarId = Annotated[Slug, Field(max_length=40)]
type RegenerateWhat = Literal["hooks", "text", "carousel"]


class SlotIn(ApiModel):
    """A slot as the owner picks it: local date and time (UTC is computed, DST-safe)."""

    local_date: LocalDate
    local_time: LocalTime
    timezone: ZoneName | None = None
    """Defaults to the cadence's zone."""
    kind: Literal["primary", "optional", "custom"] = "custom"

    def to_slot(self, default_zone: str) -> Slot:
        return Slot.at(self.local_date, self.local_time, self.timezone or default_zone, self.kind)


class PlanSlotRef(ApiModel):
    """A slot of a week plan (``WeekPlan.weekStart`` and ``PlannedSlot.id``)."""

    week_start: LocalDate
    slot_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")


class PostCreateIn(ApiModel):
    """``POST /api/posts``: a standalone post (native, quick take or repurpose)."""

    lane: Literal["native", "quick_take", "repurpose"]
    pillar: PillarId
    format: PostFormat
    story_ids: list[DocId] = Field(default_factory=list[str], max_length=10)
    pov_ids: list[DocId] = Field(default_factory=list[str], max_length=10)
    idea_id: DocId | None = None
    quick_search_id: DocId | None = None
    take_text: str | None = Field(default=None, min_length=1, max_length=2000)
    """Quick take: the owner's take, already confirmed through intake (redact + check)."""
    slot: SlotIn | None = None
    plan_slot: PlanSlotRef | None = None
    """The week plan's slot the post is written for (a quick take for its "What's your
    take?" slot): the post takes that slot's time and the plan's slot names the post, without
    changing the plan's status. A slot whose post was archived or expired takes the new one.
    Give ``slot`` or ``planSlot``, not both."""
    go_live_mode: GoLiveMode | None = None


class PostCreated(ApiModel):
    """``202``: the post (``generating``) and the ``posts.generate`` job."""

    post: Versioned[Post]
    job_id: str


class LinkExceptionIn(ApiModel):
    url: HttpsUrl
    reason: str = Field(min_length=3, max_length=300)


class AttributionIn(ApiModel):
    publisher: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    citation_id: Annotated[Slug, Field(max_length=64)] | None = None


class PostEditIn(ApiModel):
    """``PUT /api/posts/{id}``: the owner's edit (an approved or staged post goes back to
    draft; the approval is invalidated)."""

    display_text: str = Field(max_length=6000)
    selected_hook_id: str | None = Field(default=None, max_length=20)
    document_title: str | None = Field(default=None, min_length=1, max_length=100)
    hashtags: list[Annotated[str, Field(min_length=1, max_length=51)]] | None = Field(
        default=None, max_length=3
    )
    format: PostFormat | None = None
    pillar: PillarId | None = None
    attributions: list[AttributionIn] | None = Field(default=None, max_length=10)
    link_exception: LinkExceptionIn | None = None
    """Send ``null`` to remove an exception; leave it out to keep the current one."""


class LintIn(ApiModel):
    display_text: str = Field(max_length=6000)


class LintOut(ApiModel):
    """The live composer's lint: deterministic, nothing stored."""

    findings: list[Finding]
    payload_units: int
    fold_index: int


class CarouselIn(ApiModel):
    spec: dict[str, JsonValue]
    """A ``blogwriter.render.CarouselSpec`` as JSON (an image card: exactly one slide)."""


class RegenerateIn(ApiModel):
    what: RegenerateWhat
    instruction: str | None = Field(default=None, min_length=1, max_length=1000)


class ApproveIn(ApiModel):
    slot: SlotIn | None = None


class PostRow(DomainModel):
    """What a post list item reads (a projection of ``Post``: same names, types and defaults;
    never written back): the summary fields plus everything the approval hash covers."""

    id: DocId
    status: PostStatus = PostStatus.DRAFT
    status_changed_at: Timestamp
    lane: Lane
    pillar: Annotated[Slug, Field(max_length=40)]
    format: PostFormat
    display_text: str = Field(default="", max_length=6000)
    payload: str = Field(default="", max_length=12000)
    document_title: str | None = Field(default=None, min_length=1, max_length=100)
    media: list[MediaRef] = Field(default_factory=list[MediaRef], max_length=21)
    poll: dict[str, Any] | None = None
    link_exception: LinkException | None = None
    article_id: DocId | None = None
    week_plan_id: DocId | None = None
    plan_slot_id: PlanSlotId | None = None
    slot: Slot | None = None
    go_live_mode: GoLiveMode = "one_click"
    approval: Approval | None = None
    kit: KitInfo | None = None
    posted_at: Timestamp | None = None
    review_only: bool = False
    updated_at: Timestamp

    def compute_approval_hash(self) -> str:
        """``Post.compute_approval_hash`` on the row (the same inputs)."""
        return approval_hash(
            display_text=self.display_text,
            payload=self.payload,
            document_title=self.document_title,
            media=self.media,
            format=self.format,
            poll=self.poll,
            link_url=self.link_exception.url if self.link_exception is not None else None,
        )

    @property
    def approval_current(self) -> bool:
        """``Post.approval_current``: an approval exists and still matches the content."""
        return self.approval is not None and self.approval.sha256 == self.compute_approval_hash()


class PostSummary(ApiModel):
    """A post in a list (no text, payload or carousel spec)."""

    id: str
    status: PostStatus
    status_changed_at: Timestamp
    lane: Lane
    pillar: str
    format: PostFormat
    hook: str
    """The first line of the display text."""
    slot: Slot | None
    go_live_mode: GoLiveMode
    kit_state: KitState | None
    approval_current: bool
    posted_at: Timestamp | None
    review_only: bool
    article_id: str | None
    week_plan_id: str | None = None
    """The week plan the post belongs to, and its slot there (``planSlotId``): Calendar and
    Today match posts to planned slots by these, never by day."""
    plan_slot_id: str | None = None
    updated_at: Timestamp
