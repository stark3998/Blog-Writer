"""Request and response models of the kb routes (contract W-A2).

Requests subclass :class:`~blogwriter.domain.api.ApiModel` (camelCase, unknown top-level fields
refused) and list only the fields the owner edits: ids, timestamps, ``configured``, the AI-use
confirmation time, calibration and judge fields are set by the server. Nested values reuse the
domain sub-models (``Pillar``, ``FormatPack``, ``SlotTemplate``...), so the constraints live in
one place and a field added by a newer revision survives a round trip through the UI. The
ranges and cross-field rules the domain models leave open are checked by
:mod:`blogwriter.kb.checks`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.base import LocalTime, Slug, ZoneName
from blogwriter.domain.common import MediaRef
from blogwriter.domain.settings import (
    AutonomySettings,
    BrandColors,
    FormatPack,
    ModelPreferences,
    Pillar,
    PostFormatRules,
    PostsPerWeek,
    QuickTakeRules,
    ReminderOffsets,
    RepurposeRules,
    SeriesDef,
    SlotTemplate,
    WeeklyTime,
    WeekMode,
)

__all__ = [
    "AutonomyIn",
    "AutonomyView",
    "BlocklistIn",
    "BlocklistTermIn",
    "BrandIn",
    "CadenceIn",
    "DecisionView",
    "FormatPacksIn",
    "KbSeedApproveIn",
    "LanesIn",
    "ModelPreferencesIn",
    "ModelsView",
    "PillarsIn",
    "PolicyIn",
    "PriceView",
    "RouteModelView",
    "RouteView",
]

MAX_FORMAT_PACKS = 20
MAX_BLOCKLIST_TERMS = 2000


class PillarsIn(ApiModel):
    """``PUT /api/kb/pillars``: the whole list (1-5). Removing or renaming an id that anything
    references is refused (409 ``pillar_in_use``)."""

    pillars: list[Pillar] = Field(min_length=1, max_length=5)


class FormatPacksIn(ApiModel):
    packs: list[FormatPack] = Field(min_length=1, max_length=MAX_FORMAT_PACKS)


class LanesIn(ApiModel):
    """Every :class:`~blogwriter.domain.settings.LaneSettings` field."""

    derivatives_max_per_week: int
    native_min_per_week: int
    native_other_pillar: bool
    on_pillar_min_share: float
    hook_variants: int
    quick_take: QuickTakeRules
    formats: PostFormatRules
    repurpose: RepurposeRules
    series: list[SeriesDef] = Field(max_length=50)


class BrandIn(ApiModel):
    """The brand kit. ``headshot`` and ``nameMark`` are references returned by
    ``POST /api/media`` (role ``headshot`` / ``name_mark``)."""

    author_name: str | None
    author_headline: str | None
    site_name: str | None
    colors: BrandColors
    font: Literal["inter"] = "inter"
    headshot: MediaRef | None
    name_mark: MediaRef | None
    art_briefs: dict[str, str] = Field(default_factory=dict[str, str], max_length=5)
    """Pillar id -> art brief (at most 500 characters each)."""


class BlocklistTermIn(ApiModel):
    """One term. Leave ``id`` out for a new term; the server assigns ``t-<n>``."""

    id: str | None = Field(default=None, max_length=40)
    term: str = Field(min_length=1, max_length=200)
    category: Literal["client", "project", "person", "internal", "vendor", "other"] = "client"
    match: Literal["word", "substring"] = "word"


class BlocklistIn(ApiModel):
    """The whole blocklist. Saving it, even empty, marks it ``configured``."""

    terms: list[BlocklistTermIn] = Field(max_length=MAX_BLOCKLIST_TERMS)


class PolicyIn(ApiModel):
    """The employer policy. Empty disclaimers mean "none"."""

    disclaimer_short: str | None
    disclaimer_long: str | None
    forbidden_topics: list[Annotated[str, Field(max_length=200)]] = Field(max_length=100)
    no_employer_branding: bool
    ai_use_confirmed: bool
    clearance_statement: str = Field(max_length=1000)


class CadenceIn(ApiModel):
    """Every :class:`~blogwriter.domain.settings.CadenceSettings` field (local times in
    ``timezone``). Saving reseeds the schedule items."""

    timezone: ZoneName
    plan_week: WeeklyTime
    plan_lead_minutes: int
    research_start: WeeklyTime
    interview_due: WeeklyTime
    draft_day: WeeklyTime
    veto_notice: WeeklyTime
    article_publish: WeeklyTime
    min_veto_lead_hours: int
    business_hours_start: LocalTime
    business_hours_end: LocalTime
    post_slots: list[SlotTemplate] = Field(max_length=14)
    posts_per_week: PostsPerWeek
    same_day_block: bool
    min_gap_hours_warn: int
    missed_slot_grace_minutes: int
    stage_lead_minutes: int
    reminders: ReminderOffsets
    crawl_interval_hours: int
    scout: WeeklyTime
    digest: WeeklyTime
    backup_time: LocalTime
    default_mode: WeekMode


class ModelPreferencesIn(ApiModel):
    images_enabled: bool
    novelty_max_similarity: float
    near_duplicate_max_similarity: float


class AutonomyIn(ApiModel):
    """Only the owner's switch: calibration and judge fields are read-only here."""

    auto_publish_enabled: bool


class KbSeedApproveIn(ApiModel):
    """``POST /api/kb/seeds/{id}/approve`` (the body may be left out): which pillar's vocabulary
    takes the keywords (default: the seed's suggested pillar) and which keywords, each spelled
    as on the seed (default: all of them)."""

    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(
        default=None, min_length=1, max_length=200
    )


# --------------------------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------------------------


class DecisionView(ApiModel):
    """``domain.settings.auto_publish_decision``: reasons are ``paused``, ``not_enabled``,
    ``calibrating``, ``judge_uncalibrated``, ``judge_below_threshold``, ``judge_stale``."""

    allowed: bool
    reasons: list[str]


class AutonomyView(ApiModel):
    settings: Versioned[AutonomySettings]
    decision: DecisionView


class RouteModelView(ApiModel):
    model: str
    deployment: str
    provider: str
    effort: str | None


class RouteView(ApiModel):
    """One task's route, primary first (read-only: ``BW_AI_*``)."""

    task: str
    max_class: str
    tools: bool
    chain: list[RouteModelView]


def _is_none(value: object) -> bool:
    return value is None


class PriceView(ApiModel):
    """USD per 1M tokens (``BW_AI_PRICE_OVERRIDES`` over the defaults); image models also
    list their price per image by size (``imageUsd``, ``BW_AI_IMAGE_USD_*``)."""

    model: str
    input: float
    output: float
    cached_input: float | None
    cache_write: float | None
    image_usd: dict[str, float] | None = Field(default=None, exclude_if=_is_none)
    """USD per generated image by size (``1536x1024``); left out for text models."""


class ModelsView(ApiModel):
    preferences: Versioned[ModelPreferences]
    routes: list[RouteView]
    """Empty when no model router is running."""
    prices: list[PriceView]
    """The running router's prices (``AIRouter.prices``: what every call is costed with); while
    no router runs, the ones ``BW_AI_*`` configures."""
    web_search_usd_per_1k: float | None = Field(serialization_alias="webSearchUsdPer1k")
    """USD per 1,000 hosted ``web_search`` requests (``null`` when ``BW_AI_*`` is unreadable)."""
