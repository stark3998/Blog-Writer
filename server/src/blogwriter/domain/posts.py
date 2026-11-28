"""LinkedIn posts (container ``posts``). Owner: linkedin.

The linkedin package has two parts with separate write rights on a post:

- **posts** (wave B: derivation, hooks, lint, checks, approval, carousel spec, staging) writes
  the content fields and moves ``generating -> draft -> approved -> staged``;
- **transport** (wave A: publisher, kit, edit/delete) moves ``approved|staged -> publishing ->
  posted|approved|needs_attention`` (:func:`settle_publish_intent`), runs the kit states and
  writes ``intent``, ``linkedin_urn``, ``posted_at``, ``post_url``, ``final_text`` and media
  upload URNs. A post is ``publishing`` exactly while its intent is ``pending``.

Planner writes ``slot`` and ``week_plan_id`` (re-slotting), analytics appends ``snapshots``.
Every write is an If-Match CAS.

``display_text`` is what readers see (the kit copies it; counts use it). ``payload`` is the
escaped little-text commentary sent to ``/rest/posts``; the invariant
``payload == api_commentary(parse_display(display_text, approved_hashtags))`` is checked by
linkedin before approval. ``payload`` is excluded from indexing (Terraform ``/payload/*``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Final, Literal, Self, cast

from pydantic import Field, computed_field, model_validator

from blogwriter.domain.base import (
    Code,
    DocId,
    Document,
    DomainModel,
    HttpsUrl,
    LocalDate,
    LocalTime,
    Sha256Hex,
    Slug,
    Timestamp,
    ZoneName,
    derived_id,
)
from blogwriter.domain.common import Finding, MediaRef, ServedByRecord
from blogwriter.domain.hashing import approval_hash
from blogwriter.domain.states import POST_MACHINE, KitState, PostStatus, TransitionError
from blogwriter.domain.weeks import slot_utc

__all__ = [
    "Approval",
    "Attribution",
    "CheckState",
    "GoLiveMode",
    "HookType",
    "HookVariant",
    "KitInfo",
    "Lane",
    "LinkException",
    "MetricsSnapshot",
    "PlanSlotId",
    "Post",
    "PostFormat",
    "PublishIntent",
    "PublishOutcome",
    "Slot",
    "StagingState",
    "into_kit",
    "outgoing_texts",
    "settle_publish_intent",
]

type PlanSlotId = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")]
"""A ``PlannedSlot.id`` (unique within its week plan)."""
type Lane = Literal["derivative", "native", "quick_take", "repurpose"]
"""derivative: from the week's flagship (at most 2/week); native: from the story/POV bank on
another pillar (at least 1/week); quick_take: from an idea, the owner's take required, cited from
quick search, same week; repurpose: re-cut of a post that beat the owner's median."""
type PostFormat = Literal["carousel", "text", "image_card", "poll"]
type GoLiveMode = Literal["one_click", "kit"]


class HookType(StrEnum):
    """The hook taxonomy (plan: "LinkedIn Studio / Hooks")."""

    RESULT_METRIC = "result_metric"
    MISTAKE_LESSON = "mistake_lesson"
    DECISION_TRADEOFF = "decision_tradeoff"
    COUNTER_EVIDENCE = "counter_evidence"
    CHECKLIST_PROMISE = "checklist_promise"
    STORY_IN_MOTION = "story_in_motion"
    QUESTION = "question"
    """Allowed only when the question is genuine."""


class HookVariant(DomainModel):
    id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=400)
    hook_type: HookType
    concrete_detail: str = Field(min_length=1, max_length=300)
    """The specific detail the hook carries (every variant needs one)."""
    uses_owner_wording: bool = False
    """At least one variant reuses the owner's own words."""


class Slot(DomainModel):
    """A post slot: local date and time in an IANA zone, plus the UTC instant (checked)."""

    local_date: LocalDate
    local_time: LocalTime
    timezone: ZoneName
    utc: Timestamp
    kind: Literal["primary", "optional", "custom"] = "primary"

    @model_validator(mode="after")
    def _utc_matches_local(self) -> Self:
        if slot_utc(self.local_date, self.local_time, self.timezone) != self.utc:
            raise ValueError("slot utc does not match its local date, time and zone")
        return self

    @classmethod
    def at(
        cls,
        local_date: str,
        local_time: str,
        timezone: str,
        kind: Literal["primary", "optional", "custom"] = "primary",
    ) -> Slot:
        """A slot with ``utc`` computed (DST rule of :mod:`blogwriter.domain.weeks`)."""
        return cls(
            local_date=local_date,
            local_time=local_time,
            timezone=timezone,
            utc=slot_utc(local_date, local_time, timezone),
            kind=kind,
        )


class Approval(DomainModel):
    """Approval bound to :func:`blogwriter.domain.hashing.approval_hash` of text plus media."""

    sha256: Sha256Hex
    approved_at: Timestamp


class PublishIntent(DomainModel):
    """Written by the tap before the single ``POST /rest/posts`` (never retried).

    On a restart with an intent but no URN, or a 5xx or timeout, the post goes to
    ``needs_attention`` and the owner checks LinkedIn.
    """

    id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    created_at: Timestamp
    approval_sha256: Sha256Hex
    status: Literal["pending", "succeeded", "unknown", "failed"] = "pending"
    resolved_at: Timestamp | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_code: Code | None = None


class KitInfo(DomainModel):
    """Ready-to-post kit progress. ``scheduled_at`` is the native schedule time T the owner
    entered; reminders and spacing key on it."""

    state: KitState = KitState.PREPARED
    prepared_at: Timestamp
    draft_saved_at: Timestamp | None = None
    scheduled_at: Timestamp | None = None
    posted_at: Timestamp | None = None


class MetricsSnapshot(DomainModel):
    """Metrics the owner typed (+48 h, +7 d form) or imported from LinkedIn's export."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    taken_at: Timestamp
    offset: Literal["h48", "d7", "other"]
    impressions: int | None = Field(default=None, ge=0)
    reactions: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    sends: int | None = Field(default=None, ge=0)
    source: Literal["form", "import"] = "form"
    import_id: DocId | None = None


class CheckState(DomainModel):
    """The result of one check (lint, derived claims, near-duplicate) on exact content.

    ``findings`` keeps at most :data:`~blogwriter.domain.common.MAX_STORED_FINDINGS`
    (:func:`~blogwriter.domain.common.cap_findings`: blockers first); ``truncated`` counts the
    rest, so a screen can say "and N more"."""

    ran_at: Timestamp
    content_sha256: Sha256Hex
    passed: bool
    findings: list[Finding] = Field(default_factory=list[Finding], max_length=200)
    truncated: int = Field(default=0, ge=0)


class Attribution(DomainModel):
    """Inline attribution for a research-derived claim: publisher and title, never a URL."""

    publisher: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    citation_id: Annotated[Slug, Field(max_length=64)] | None = None


class LinkException(DomainModel):
    """A per-post exception to the zero-links rule (logged for the link experiment)."""

    url: HttpsUrl
    reason: str = Field(min_length=3, max_length=300)
    experiment_id: DocId | None = None
    approved_at: Timestamp


class StagingState(DomainModel):
    """T-60 staging (owner: linkedin posts): rendered, uploaded, documents AVAILABLE, token ok."""

    slot_utc: Timestamp
    job_id: str | None = None
    staged_at: Timestamp | None = None
    media_ready: bool = False
    token_ok: bool = False
    approval_sha256: Sha256Hex | None = None
    error_code: Code | None = None


class Post(Document):
    """One LinkedIn post through approval, go-live and insights."""

    CONTAINER: ClassVar[str] = "posts"
    KIND: ClassVar[str] = "post"
    ID_PREFIX: ClassVar[str] = "post"
    OWNER: ClassVar[str] = "linkedin"

    status: PostStatus = PostStatus.DRAFT
    status_changed_at: Timestamp
    lane: Lane
    pillar: Annotated[Slug, Field(max_length=40)]
    format: PostFormat
    series_id: Slug | None = None
    hook_type: HookType | None = None
    hook_variants: list[HookVariant] = Field(default_factory=list[HookVariant], max_length=5)
    selected_hook_id: str | None = None

    display_text: str = Field(default="", max_length=6000)
    payload: str = Field(default="", max_length=12000)
    ai_draft_text: str | None = Field(default=None, max_length=6000)
    """The AI's first display text, kept for the voice digest (draft vs final)."""
    document_title: str | None = Field(default=None, min_length=1, max_length=100)
    """LinkedIn document title (carousels; lint wants at most 58 characters)."""
    hashtags: list[Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,49}$")]] = Field(
        default_factory=list[str], max_length=3
    )
    media: list[MediaRef] = Field(default_factory=list[MediaRef], max_length=21)
    carousel_spec: dict[str, Any] | None = None
    """The slide spec, validated by ``blogwriter.render`` (stored opaque here)."""
    poll: dict[str, Any] | None = None
    """Poll question and options (rare format), validated by linkedin."""
    attributions: list[Attribution] = Field(default_factory=list[Attribution], max_length=10)
    link_exception: LinkException | None = None

    article_id: DocId | None = None
    story_ids: list[DocId] = Field(default_factory=list[str], max_length=10)
    pov_ids: list[DocId] = Field(default_factory=list[str], max_length=10)
    idea_id: DocId | None = None
    brief_id: DocId | None = None
    quick_search_id: DocId | None = None
    week_plan_id: DocId | None = None
    plan_slot_id: PlanSlotId | None = None
    """The slot of ``week_plan_id`` the post fills (``PlannedSlot.id``): set when a post is
    created for a planned slot (a quick take written for its slot, a native post from plan
    approval), so screens never match a post to a slot by its day."""
    experiment_id: DocId | None = None
    experiment_arm: str | None = Field(default=None, max_length=40)

    slot: Slot | None = None
    go_live_mode: GoLiveMode = "one_click"
    time_sensitive: bool = False
    expires_at: Timestamp | None = None

    lint: CheckState | None = None
    derived_claims: CheckState | None = None
    near_duplicate: CheckState | None = None
    approval: Approval | None = None
    staging: StagingState | None = None
    intent: PublishIntent | None = None
    kit: KitInfo | None = None
    linkedin_urn: str | None = Field(
        default=None, pattern=r"^urn:li:(?:share|ugcPost):[0-9]{1,30}$"
    )
    post_url: HttpsUrl | None = None
    posted_at: Timestamp | None = None
    final_text: str | None = Field(default=None, max_length=6000)
    """What actually went live: the published display text, or (kit) the text the owner
    confirmed when marking the post as posted."""
    snapshots: list[MetricsSnapshot] = Field(default_factory=list[MetricsSnapshot], max_length=20)

    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=50)
    review_only: bool = False
    review_only_reasons: list[str] = Field(default_factory=list[str], max_length=20)
    last_error: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _intent_matches_status(self) -> Self:
        pending = self.intent is not None and self.intent.status == "pending"
        if (self.status is PostStatus.PUBLISHING) != pending:
            raise ValueError("a post is publishing exactly while its publish intent is pending")
        return self

    def compute_approval_hash(self) -> str:
        """:func:`~blogwriter.domain.hashing.approval_hash` of the current content."""
        return approval_hash(
            display_text=self.display_text,
            payload=self.payload,
            document_title=self.document_title,
            media=self.media,
            format=self.format,
            poll=self.poll,
            link_url=self.link_exception.url if self.link_exception is not None else None,
        )

    @staticmethod
    def id_for_derivative(article_id: str, version: int, ordinal: int) -> str:
        """The ``ordinal``-th post ``posts.derive`` drafts from an article version (retry-safe)."""
        return derived_id("post", "derive", article_id, version, ordinal)

    @staticmethod
    def id_for_plan_slot(plan_id: str, slot_id: str) -> str:
        """The post that fills a planned slot (plan approval; a double approve cannot duplicate)."""
        return derived_id("post", "plan", plan_id, slot_id)

    @computed_field
    @property
    def approval_current(self) -> bool:
        """An approval exists and still matches the content (nothing changed since). A read-only
        view for the web (``approvalCurrent``): never stored, never taken from input."""
        return self.approval is not None and self.approval.sha256 == self.compute_approval_hash()

    @property
    def go_live_at(self) -> str | None:
        """When the post went (or will go) live: posted time, else the kit's native T,
        else the slot."""
        if self.posted_at is not None:
            return self.posted_at
        if self.kit is not None and self.kit.scheduled_at is not None:
            return self.kit.scheduled_at
        return self.slot.utc if self.slot is not None else None


def into_kit(post: Post, *, stamp: str) -> Post:
    """``post`` moved to the ready-to-post kit, ``prepared`` (approved or staged -> kit, go-live
    mode ``kit``; ``TransitionError`` from any other status). One rule for the transport's mode
    switch, approving a kit-mode post and a week switched to away (validate before writing)."""
    status = POST_MACHINE.transition(post.status, PostStatus.KIT)
    return post.model_copy(
        update={
            "status": status,
            "status_changed_at": stamp,
            "updated_at": stamp,
            "go_live_mode": "kit",
            "kit": KitInfo(state=KitState.PREPARED, prepared_at=stamp),
        }
    )


_STRUCTURAL_KEYS: Final = frozenset({"type", "layout", "duration"})
"""Keys of the stored carousel spec and poll whose values are not text readers see."""


def _strings(value: object, path: str, out: dict[str, str]) -> None:
    if isinstance(value, str):
        if value.strip():
            out[path] = value
    elif isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            if key not in _STRUCTURAL_KEYS:
                _strings(item, f"{path}.{key}", out)
    elif isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            _strings(item, f"{path}[{index}]", out)


def outgoing_texts(post: Post) -> dict[str, str]:
    """Every text of ``post`` that leaves the app when it goes to LinkedIn, by field: the display
    text and its escaped payload, the document title, every text of the stored slides (the PDF
    and image text layer) and poll, the media alt texts and file names, and the attributions.
    What the blocklist screens again at staging and at the owner's tap (security rule 1.9.5)."""
    texts: dict[str, str] = {}
    for name, value in (
        ("displayText", post.display_text),
        ("payload", post.payload),
        ("documentTitle", post.document_title),
    ):
        if value and value.strip():
            texts[name] = value
    if post.carousel_spec is not None:
        _strings(post.carousel_spec, "carouselSpec", texts)
    if post.poll is not None:
        _strings(post.poll, "poll", texts)
    for index, media in enumerate(post.media):
        for name, value in (("altText", media.alt_text), ("filename", media.filename)):
            if value and value.strip():
                texts[f"media[{index}].{name}"] = value
    for index, attribution in enumerate(post.attributions):
        texts[f"attributions[{index}]"] = f"{attribution.publisher}\n{attribution.title}"
    return texts


type PublishOutcome = Literal["posted", "rejected", "unknown"]
"""How the single ``POST /rest/posts`` ended: ``posted`` (201 with a URN), ``rejected`` (a
definite 4xx: LinkedIn created nothing), ``unknown`` (5xx, timeout, network error, or a restart
that found a pending intent)."""

_SETTLED: Final[dict[str, tuple[PostStatus, Literal["succeeded", "failed", "unknown"]]]] = {
    "posted": (PostStatus.POSTED, "succeeded"),
    "rejected": (PostStatus.APPROVED, "failed"),
    "unknown": (PostStatus.NEEDS_ATTENTION, "unknown"),
}


def settle_publish_intent(
    post: Post,
    outcome: PublishOutcome,
    *,
    now: str,
    http_status: int | None = None,
    error_code: str | None = None,
) -> Post:
    """The post after its one publish attempt: status and intent settled together.

    ``posted`` -> ``posted`` (intent ``succeeded``; the caller also sets ``linkedin_urn``,
    ``posted_at``, ``post_url`` and ``final_text``), ``rejected`` -> ``approved`` (intent
    ``failed``; the approval still holds, the owner may tap again), ``unknown`` ->
    ``needs_attention`` (intent ``unknown``; the owner checks LinkedIn). Raises
    :class:`~blogwriter.domain.states.TransitionError` unless the post is ``publishing`` (an
    attempt settles once).
    """
    status, intent_status = _SETTLED[outcome]
    intent = post.intent
    if post.status is not PostStatus.PUBLISHING or intent is None:
        # Only the attempt in flight settles (needs_attention -> posted is the owner's resolve).
        raise TransitionError(POST_MACHINE.name, post.status.value, status.value, ())
    target = POST_MACHINE.transition(post.status, status)
    settled = intent.model_copy(
        update={
            "status": intent_status,
            "resolved_at": now,
            "http_status": http_status,
            "error_code": error_code,
        }
    )
    changes: dict[str, object] = {
        "status": target,
        "status_changed_at": now,
        "updated_at": now,
        "intent": settled,
    }
    if outcome != "posted":
        fallback = "publish_rejected" if outcome == "rejected" else "publish_unknown"
        changes["last_error"] = error_code or fallback
    # Re-validate: model_copy skips validators, and the intent invariant must hold.
    return Post.model_validate(post.model_copy(update=changes).model_dump())
