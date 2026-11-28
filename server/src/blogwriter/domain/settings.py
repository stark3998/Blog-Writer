"""Singleton documents in the ``settings`` container (owner: kb, unless noted).

Each class is one document with a fixed id. A document that was never saved reads as its
:meth:`~blogwriter.domain.base.SingletonDocument.default` (the plan's defaults), so every package
can read settings without waiting for the owner, and the API reports it with ``etag: null``.

Security: :class:`Blocklist` is confidential. Its terms never go to a model, a log, a job
event, a push notification or a research query; findings say "matches a blocklisted name" and
point at the term by id.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from blogwriter.domain.base import (
    DocId,
    DomainModel,
    HostName,
    LocalTime,
    Sha256Hex,
    SingletonDocument,
    Slug,
    Timestamp,
    Weekday,
    ZoneName,
    parse_iso,
)
from blogwriter.domain.common import MediaRef
from blogwriter.domain.states import ConnectionStatus
from blogwriter.domain.weeks import DEFAULT_TIMEZONE

__all__ = [
    "LINKEDIN_API_VERSION",
    "LINKEDIN_SCOPES",
    "TOKEN_ALERT_DAYS",
    "TOKEN_BANNER_DAY",
    "TOKEN_LIFETIME_DAYS",
    "AutoPublishDecision",
    "AutonomySettings",
    "Blocklist",
    "BlocklistTerm",
    "BrandColors",
    "BrandKit",
    "CadenceSettings",
    "Calibration",
    "CalibrationEvent",
    "CalibrationOutcome",
    "ChecklistItem",
    "EmployerPolicy",
    "FormatPack",
    "FormatPacks",
    "JudgeCalibration",
    "LaneSettings",
    "LinkedInConnection",
    "ModelPreferences",
    "Pillar",
    "PillarSettings",
    "PostFormatRules",
    "PostsPerWeek",
    "ProfileChecklist",
    "QuickTakeRules",
    "ReminderOffsets",
    "RepurposeRules",
    "SeriesDef",
    "SlotTemplate",
    "StandardsCatalog",
    "StandardsFamily",
    "VoicePreferences",
    "WeekMode",
    "WeeklyTime",
    "auto_publish_decision",
    "default_pillars",
    "is_substantive_edit",
    "record_correction",
    "record_review",
]

# --------------------------------------------------------------------------------------------
# Pillars
# --------------------------------------------------------------------------------------------

_HASHTAG = r"^[A-Za-z][A-Za-z0-9]{0,49}$"


class Pillar(DomainModel):
    """One content pillar (plan: "Pillars, format packs, post lanes")."""

    id: Annotated[Slug, Field(max_length=40)]
    name: str = Field(min_length=1, max_length=80)
    topics: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=2000)
    audience: str = Field(default="", max_length=1000)
    vocabulary: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list[str], max_length=200
    )
    """Canonical terms the writer must use."""
    renames: dict[str, str] = Field(default_factory=dict[str, str], max_length=200)
    """Deprecated name -> current name (``Azure AD`` -> ``Microsoft Entra ID``)."""
    hashtags: list[Annotated[str, Field(pattern=_HASHTAG)]] = Field(
        default_factory=list[str], max_length=3
    )
    """Approved hashtags, without ``#`` (0-3)."""
    research_domains: list[HostName] = Field(default_factory=list[str], max_length=50)
    """The research allowlist (``web_search`` ``allowed_domains`` on the allowlist pass)."""
    target_share: float = Field(default=0.33, ge=0, le=1)


def default_pillars() -> list[Pillar]:
    """The plan's three pillars ("Pillars, format packs, post lanes"), complete: topics,
    description, audience, canonical vocabulary, deprecated -> current names, 0-3 hashtags,
    the research allowlists (Entra docs = ``learn.microsoft.com``, NIST SP 800-63-4 =
    ``pages.nist.gov``, Okta docs = ``developer.okta.com`` and ``help.okta.com``, ISO 42001 =
    ``iso.org``) and the target shares. A fresh list per call."""
    return [
        Pillar(
            id="agents",
            name="AI agents & security",
            topics="MCP, multi-agent SecOps, prompt injection, agent identity",
            description=(
                "Running AI agents against real systems without opening a new way in for "
                "attackers. Covers Model Context Protocol (MCP) servers and the tool permissions "
                "they expose, prompt injection and tool poisoning, agent identity and least "
                "privilege, and multi-agent SecOps workflows a security team can audit."
            ),
            audience=(
                "Security architects, AppSec and SecOps leads, and platform engineers connecting "
                "agents and MCP servers to production data, identities and tools."
            ),
            vocabulary=[
                "Model Context Protocol (MCP)",
                "MCP server",
                "MCP client",
                "prompt injection",
                "indirect prompt injection",
                "tool poisoning",
                "agent identity",
                "least privilege",
                "human-in-the-loop approval",
                "OWASP Top 10 for LLM Applications",
                "MITRE ATLAS",
                "Microsoft Sentinel",
            ],
            renames={
                "Azure Sentinel": "Microsoft Sentinel",
                "Microsoft 365 Defender": "Microsoft Defender XDR",
                "Microsoft Copilot for Security": "Microsoft Security Copilot",
                "Azure AI Studio": "Microsoft Foundry",
                "Azure AI Foundry": "Microsoft Foundry",
            },
            hashtags=["AISecurity", "AIAgents", "MCP"],
            research_domains=[
                "modelcontextprotocol.io",
                "genai.owasp.org",
                "atlas.mitre.org",
                "learn.microsoft.com",
                "arxiv.org",
                "cisa.gov",
            ],
            target_share=0.33,
        ),
        Pillar(
            id="identity",
            name="Identity modernization",
            topics="Okta to Entra, a 2,000-app migration, CIAM",
            description=(
                "Moving a large enterprise from Okta to Microsoft Entra ID without breaking "
                "sign-in for anyone. Covers app inventory and migration waves for a 2,000-app "
                "estate, SSO and SCIM provisioning cutovers, Conditional Access design, "
                "phishing-resistant MFA, and customer identity (CIAM) on Microsoft Entra External "
                "ID."
            ),
            audience=(
                "IAM architects, identity engineers and IT leaders planning or running a workforce "
                "identity migration, and teams building customer sign-in."
            ),
            vocabulary=[
                "Microsoft Entra ID",
                "Microsoft Entra External ID",
                "Conditional Access",
                "single sign-on (SSO)",
                "SCIM provisioning",
                "OpenID Connect (OIDC)",
                "SAML",
                "phishing-resistant MFA",
                "passkeys",
                "customer identity and access management (CIAM)",
                "workload identities",
                "NIST SP 800-63-4",
            ],
            renames={
                "Azure AD": "Microsoft Entra ID",
                "Azure Active Directory": "Microsoft Entra ID",
                "AAD": "Microsoft Entra ID",
                "Azure AD Connect": "Microsoft Entra Connect",
                "Azure AD Connect cloud sync": "Microsoft Entra Cloud Sync",
                "Azure AD Conditional Access": "Microsoft Entra Conditional Access",
                "Azure AD Identity Protection": "Microsoft Entra ID Protection",
                "Azure AD Privileged Identity Management": (
                    "Microsoft Entra Privileged Identity Management"
                ),
                "Azure AD Identity Governance": "Microsoft Entra ID Governance",
                "Azure AD External Identities": "Microsoft Entra External ID",
                "Azure AD Domain Services": "Microsoft Entra Domain Services",
                "Azure AD Application Proxy": "Microsoft Entra application proxy",
                "Azure AD Workload Identities": "Microsoft Entra Workload ID",
                "Azure AD Verifiable Credentials": "Microsoft Entra Verified ID",
                "Hybrid Azure AD joined": "Microsoft Entra hybrid joined",
                "Azure AD joined": "Microsoft Entra joined",
            },
            hashtags=["IAM", "MicrosoftEntra", "CIAM"],
            research_domains=[
                "learn.microsoft.com",
                "techcommunity.microsoft.com",
                "openid.net",
                "pages.nist.gov",
                "developer.okta.com",
                "help.okta.com",
            ],
            target_share=0.33,
        ),
        Pillar(
            id="governance",
            name="AI governance",
            topics="Risk-tiered agent lifecycles, NIST AI RMF, OWASP, MITRE ATLAS",
            description=(
                "Turning AI governance frameworks into controls a team can operate and test. "
                "Covers risk-tiered lifecycles for agents and models, the NIST AI RMF and its "
                "Generative AI Profile in practice, ISO/IEC 42001, the EU AI Act, and mapping "
                "OWASP and MITRE ATLAS threats to specific controls."
            ),
            audience=(
                "CISOs, risk and compliance leads, and AI platform owners who must show an "
                "inventory, a risk tier and working controls for every AI system in production."
            ),
            vocabulary=[
                "NIST AI RMF",
                "NIST AI 600-1 (Generative AI Profile)",
                "ISO/IEC 42001",
                "EU AI Act",
                "AI system inventory",
                "risk tier",
                "model card",
                "AI red teaming",
                "OWASP Top 10 for LLM Applications",
                "MITRE ATLAS",
            ],
            renames={"Azure Purview": "Microsoft Purview"},
            hashtags=["AIGovernance", "ResponsibleAI", "AIRisk"],
            research_domains=["nist.gov", "owasp.org", "iso.org", "eur-lex.europa.eu", "oecd.ai"],
            target_share=0.33,
        ),
    ]


class PillarSettings(SingletonDocument):
    """The pillars (1-5). Ids are stable: articles, posts and ideas reference them."""

    KIND: ClassVar[str] = "pillars"
    SINGLETON_ID: ClassVar[str] = "pillars"
    OWNER: ClassVar[str] = "kb"

    pillars: list[Pillar] = Field(default_factory=default_pillars, min_length=1, max_length=5)

    @model_validator(mode="after")
    def _check_pillars(self) -> Self:
        ids = [pillar.id for pillar in self.pillars]
        if len(ids) != len(set(ids)):
            raise ValueError("pillar ids must be unique")
        if sum(pillar.target_share for pillar in self.pillars) > 1.0 + 1e-9:
            raise ValueError("pillar target shares must add up to at most 1")
        return self

    def get(self, pillar_id: str) -> Pillar | None:
        return next((pillar for pillar in self.pillars if pillar.id == pillar_id), None)


# --------------------------------------------------------------------------------------------
# Format packs and lanes
# --------------------------------------------------------------------------------------------


class FormatPack(DomainModel):
    """An article format: length, required parts and the owner input it needs."""

    id: Annotated[Slug, Field(max_length=40)]
    name: str = Field(min_length=1, max_length=80)
    min_words: int = Field(ge=100, le=20_000)
    max_words: int = Field(ge=100, le=20_000)
    must_contain: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list[str], max_length=20
    )
    owner_input: Literal["full_interview", "micro_interview"]
    min_questions: int = Field(ge=1, le=12)
    max_questions: int = Field(ge=1, le=12)
    banned: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list[str], max_length=20
    )

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.min_words > self.max_words or self.min_questions > self.max_questions:
            raise ValueError("minimums must not exceed maximums")
        return self


def _default_format_packs() -> list[FormatPack]:
    return [
        FormatPack(
            id="case-study",
            name="Case study / how I did it",
            min_words=1500,
            max_words=2500,
            must_contain=[
                "anonymized context",
                "constraints",
                "decisions and trade-offs",
                "metrics",
                "what I would change",
            ],
            owner_input="full_interview",
            min_questions=5,
            max_questions=8,
        ),
        FormatPack(
            id="framework",
            name="Framework / checklist",
            min_words=1200,
            max_words=2000,
            must_contain=[
                "an owner-originated named framework",
                "a diagram",
                "maturity levels",
                "a checklist table",
            ],
            owner_input="full_interview",
            min_questions=5,
            max_questions=8,
            banned=["AI-invented frameworks"],
        ),
        FormatPack(
            id="deep-explainer",
            name="Deep explainer",
            min_words=1800,
            max_words=3000,
            must_contain=["a Mermaid flow", "a threat model", "config or code", "pitfalls"],
            owner_input="micro_interview",
            min_questions=2,
            max_questions=2,
        ),
        FormatPack(
            id="news-analysis",
            name="News analysis",
            min_words=900,
            max_words=1500,
            must_contain=[
                "what changed (cited)",
                "why it matters per role",
                "what to do Monday",
            ],
            owner_input="micro_interview",
            min_questions=2,
            max_questions=2,
        ),
    ]


class FormatPacks(SingletonDocument):
    KIND: ClassVar[str] = "format_packs"
    SINGLETON_ID: ClassVar[str] = "format-packs"
    OWNER: ClassVar[str] = "kb"

    packs: list[FormatPack] = Field(default_factory=_default_format_packs, min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [pack.id for pack in self.packs]
        if len(ids) != len(set(ids)):
            raise ValueError("format pack ids must be unique")
        return self

    def get(self, format_id: str) -> FormatPack | None:
        return next((pack for pack in self.packs if pack.id == format_id), None)


class QuickTakeRules(DomainModel):
    min_chars: int = Field(default=1200, ge=1)
    max_chars: int = Field(default=1500, ge=1)
    max_age_hours: int = Field(default=72, ge=1)
    owner_take_required: bool = True


class PostFormatRules(DomainModel):
    carousel_min_slides: int = Field(default=8, ge=2, le=20)
    carousel_max_slides: int = Field(default=12, ge=2, le=20)
    text_min_chars: int = Field(default=1200, ge=1)
    text_max_chars: int = Field(default=2000, ge=1)
    image_card_enabled: bool = True
    poll_max_per_month: int = Field(default=1, ge=0)


class RepurposeRules(DomainModel):
    min_weeks: int = Field(default=8, ge=1)
    max_weeks: int = Field(default=12, ge=1)
    requires_novelty_check: bool = True


class SeriesDef(DomainModel):
    id: Slug
    pillar: Annotated[Slug, Field(max_length=40)]
    title: str = Field(min_length=1, max_length=150)
    active: bool = True


class LaneSettings(SingletonDocument):
    """Post lanes and the weekly mix rules."""

    KIND: ClassVar[str] = "lanes"
    SINGLETON_ID: ClassVar[str] = "lanes"
    OWNER: ClassVar[str] = "kb"

    derivatives_max_per_week: int = Field(default=2, ge=0)
    native_min_per_week: int = Field(default=1, ge=0)
    native_other_pillar: bool = True
    """Native posts come from the story/POV bank on a pillar other than the flagship's."""
    on_pillar_min_share: float = Field(default=0.8, ge=0, le=1)
    hook_variants: int = Field(default=3, ge=1, le=5)
    quick_take: QuickTakeRules = Field(default_factory=QuickTakeRules)
    formats: PostFormatRules = Field(default_factory=PostFormatRules)
    repurpose: RepurposeRules = Field(default_factory=RepurposeRules)
    series: list[SeriesDef] = Field(default_factory=list[SeriesDef], max_length=50)


# --------------------------------------------------------------------------------------------
# Brand kit, blocklist, employer policy
# --------------------------------------------------------------------------------------------

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _hex_color(value: str) -> str:
    if not _HEX.fullmatch(value):
        raise ValueError("colours are #RRGGBB")
    return value.upper()


type HexColor = Annotated[str, AfterValidator(_hex_color)]


class BrandColors(DomainModel):
    """Semantic colour tokens of the carousel and card templates.

    The defaults are the render spike's placeholder palette, which passes WCAG AA. The render
    package checks every text pair again before rendering (kb validates on save through it).
    """

    ink: HexColor = "#0E2A24"
    paper: HexColor = "#F3F5F2"
    muted: HexColor = "#4A5D56"
    muted_on_ink: HexColor = "#A8C1B8"
    signal: HexColor = "#F2A93B"
    signal_text: HexColor = "#8A4F00"
    rule: HexColor = "#C5D1CB"
    rule_on_ink: HexColor = "#2E4A43"


class BrandKit(SingletonDocument):
    """Owner identity on carousels, image cards and OG images. No employer branding."""

    KIND: ClassVar[str] = "brand_kit"
    SINGLETON_ID: ClassVar[str] = "brand-kit"
    OWNER: ClassVar[str] = "kb"

    author_name: str | None = Field(default=None, min_length=1, max_length=48)
    author_headline: str | None = Field(default=None, min_length=1, max_length=64)
    site_name: str | None = Field(default=None, min_length=1, max_length=40)
    """Shown on OG images only (carousels carry no domain-like tokens)."""
    colors: BrandColors = Field(default_factory=BrandColors)
    font: Literal["inter"] = "inter"
    headshot: MediaRef | None = None
    name_mark: MediaRef | None = None
    art_briefs: dict[str, Annotated[str, Field(max_length=500)]] = Field(
        default_factory=dict[str, str]
    )
    """Per-pillar art brief for opt-in illustrations."""

    @property
    def complete(self) -> bool:
        """Enough to render owner-branded media (the author name is set)."""
        return self.author_name is not None


class BlocklistTerm(DomainModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*$")
    term: str = Field(min_length=2, max_length=100)
    category: Literal["client", "project", "person", "internal", "vendor", "other"] = "client"
    match: Literal["word", "substring"] = "word"


class Blocklist(SingletonDocument):
    """Confidential names (clients, internal projects). Never leaves the app (module docs).

    ``configured`` is false until the owner saves the list once; gates fail closed until then.
    """

    KIND: ClassVar[str] = "blocklist"
    SINGLETON_ID: ClassVar[str] = "blocklist"
    OWNER: ClassVar[str] = "kb"

    terms: list[BlocklistTerm] = Field(default_factory=list[BlocklistTerm], max_length=2000)
    configured: bool = False

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [term.id for term in self.terms]
        folded = [term.term.casefold() for term in self.terms]
        if len(ids) != len(set(ids)) or len(folded) != len(set(folded)):
            raise ValueError("blocklist term ids and terms must be unique")
        return self


class EmployerPolicy(SingletonDocument):
    """The employer social-media and AI-use policy profile."""

    KIND: ClassVar[str] = "employer_policy"
    SINGLETON_ID: ClassVar[str] = "employer-policy"
    OWNER: ClassVar[str] = "kb"

    disclaimer_short: str | None = Field(default=None, min_length=1, max_length=40)
    """Footer on every carousel slide and image card ("Views are my own.")."""
    disclaimer_long: str | None = Field(default=None, min_length=1, max_length=300)
    """Article footer disclaimer."""
    forbidden_topics: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list[str], max_length=100
    )
    no_employer_branding: bool = True
    ai_use_confirmed: bool = False
    """The owner confirmed the employer's AI-use policy covers anonymized work details in Azure
    OpenAI, Claude (Anthropic as processor) and Web IQ. Until then no anonymized owner material
    goes to any model."""
    ai_use_confirmed_at: Timestamp | None = None
    clearance_statement: str = Field(
        default="I confirm this piece is anonymized and cleared for publication.",
        min_length=10,
        max_length=500,
    )
    configured: bool = False


# --------------------------------------------------------------------------------------------
# Cadence, slots and modes (owner: kb; read by planner, scheduler seeding, linkedin)
# --------------------------------------------------------------------------------------------

type WeekMode = Literal["normal", "light", "away"]


class WeeklyTime(DomainModel):
    day: Weekday
    time: LocalTime


class SlotTemplate(DomainModel):
    """A recurring post slot. ``optional`` slots are used only in weeks with more than 3 posts."""

    day: Weekday
    time: LocalTime
    kind: Literal["primary", "optional"] = "primary"
    window_minutes: int = Field(default=120, ge=0, le=240)


class PostsPerWeek(DomainModel):
    normal: int = Field(default=3, ge=0, le=7)
    light: int = Field(default=2, ge=0, le=7)
    away: int = Field(default=0, ge=0, le=0)
    max: int = Field(default=5, ge=1, le=7)
    """Up to 5 only when every post passes the gates."""


class ReminderOffsets(DomainModel):
    reply_minutes: list[int] = Field(default_factory=lambda: [15, 60, 180], max_length=6)
    snapshot_hours: list[int] = Field(default_factory=lambda: [48, 168], max_length=4)


def _default_slots() -> list[SlotTemplate]:
    return [
        SlotTemplate(day="tue", time="10:00"),
        SlotTemplate(day="wed", time="10:00"),
        SlotTemplate(day="thu", time="10:00"),
        SlotTemplate(day="mon", time="12:00", kind="optional"),
        SlotTemplate(day="fri", time="15:00", kind="optional"),
    ]


class CadenceSettings(SingletonDocument):
    """The weekly production calendar (all times local to ``timezone``)."""

    KIND: ClassVar[str] = "cadence"
    SINGLETON_ID: ClassVar[str] = "cadence"
    OWNER: ClassVar[str] = "kb"

    timezone: ZoneName = DEFAULT_TIMEZONE
    plan_week: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="sun", time="18:00"))
    plan_lead_minutes: int = Field(default=60, ge=0, le=720)
    """The plan job runs this long before ``plan_week`` so the plan is ready for the owner."""
    research_start: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="mon", time="08:00"))
    """Approving a week's plan enqueues the flagship's pipeline to start at this time of that
    week (research Mon-Tue, interview due Wed, draft Thu)."""
    interview_due: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="wed", time="18:00"))
    draft_day: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="thu", time="09:00"))
    veto_notice: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="fri", time="12:00"))
    article_publish: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="mon", time="09:00"))
    min_veto_lead_hours: int = Field(default=48, ge=24, le=168)
    business_hours_start: LocalTime = "09:00"
    business_hours_end: LocalTime = "17:00"
    post_slots: list[SlotTemplate] = Field(default_factory=_default_slots, max_length=14)
    posts_per_week: PostsPerWeek = Field(default_factory=PostsPerWeek)
    same_day_block: bool = True
    """Two posts on the same local day is a hard block."""
    min_gap_hours_warn: int = Field(default=24, ge=0, le=72)
    missed_slot_grace_minutes: int = Field(default=120, ge=0, le=720)
    stage_lead_minutes: int = Field(default=60, ge=15, le=240)
    reminders: ReminderOffsets = Field(default_factory=ReminderOffsets)
    crawl_interval_hours: int = Field(default=6, ge=1, le=48)
    scout: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="sun", time="15:00"))
    digest: WeeklyTime = Field(default_factory=lambda: WeeklyTime(day="mon", time="08:00"))
    backup_time: LocalTime = "03:30"
    default_mode: WeekMode = "normal"


# --------------------------------------------------------------------------------------------
# Models, autonomy and calibration
# --------------------------------------------------------------------------------------------


class ModelPreferences(SingletonDocument):
    """Owner-tunable AI preferences. Routing and prices come from ``BW_AI_*`` (read-only here)."""

    KIND: ClassVar[str] = "model_preferences"
    SINGLETON_ID: ClassVar[str] = "model-preferences"
    OWNER: ClassVar[str] = "kb"

    images_enabled: bool = False
    """Opt-in ``gpt-image-2`` illustrations (needs an image route; deferred)."""
    novelty_max_similarity: float = Field(default=0.92, gt=0, le=1)
    """Gate 4: an article this similar (cosine) to an earlier one fails the novelty check."""
    near_duplicate_max_similarity: float = Field(default=0.5, gt=0, le=1)
    """Posts: word-shingle Jaccard similarity to any post of the last 90 days that blocks."""


type CalibrationOutcome = Literal[
    "clean", "edited", "gates_failed", "review_only", "correction", "vetoed"
]


class CalibrationEvent(DomainModel):
    article_id: DocId
    at: Timestamp
    outcome: CalibrationOutcome
    content_sha256: Sha256Hex | None = None


class Calibration(DomainModel):
    """Auto-publish switches on after ``required`` consecutive clean reviewed articles."""

    clean_streak: int = Field(default=0, ge=0)
    required: int = Field(default=6, ge=1, le=50)
    events: list[CalibrationEvent] = Field(default_factory=list[CalibrationEvent], max_length=50)


class JudgeCalibration(DomainModel):
    """The rubric judge against the owner's labels on the eval topics (re-checked monthly)."""

    as_is_rate: float | None = Field(default=None, ge=0, le=1)
    """Of the articles the judge passed, the share the owner labelled "as-is"."""
    sample_size: int = Field(default=0, ge=0)
    checked_at: Timestamp | None = None
    threshold: float = Field(default=0.9, ge=0, le=1)
    max_age_days: int = Field(default=31, ge=1)


class AutonomySettings(SingletonDocument):
    """Autonomy switches and the auto-publish calibration (owner: kb; pipeline and publish
    update ``calibration`` through :func:`record_review` / :func:`record_correction`)."""

    KIND: ClassVar[str] = "autonomy"
    SINGLETON_ID: ClassVar[str] = "autonomy"
    OWNER: ClassVar[str] = "kb"

    auto_publish_enabled: bool = False
    """The owner's switch. It has no effect until calibration and the judge allow it."""
    paused: bool = False
    """One-tap "pause all autonomy": no auto-publish, no automatic generation."""
    paused_at: Timestamp | None = None
    calibration: Calibration = Field(default_factory=Calibration)
    judge: JudgeCalibration = Field(default_factory=JudgeCalibration)
    last_correction_at: Timestamp | None = None


@dataclass(frozen=True, slots=True)
class AutoPublishDecision:
    allowed: bool
    reasons: tuple[str, ...]
    """Codes: ``paused``, ``not_enabled``, ``calibrating``, ``judge_uncalibrated``,
    ``judge_below_threshold``, ``judge_stale``."""


def auto_publish_decision(autonomy: AutonomySettings, *, now: datetime) -> AutoPublishDecision:
    """Whether a gated article may auto-publish at all (per-article gates are checked apart)."""
    reasons: list[str] = []
    if autonomy.paused:
        reasons.append("paused")
    if not autonomy.auto_publish_enabled:
        reasons.append("not_enabled")
    if autonomy.calibration.clean_streak < autonomy.calibration.required:
        reasons.append("calibrating")
    judge = autonomy.judge
    if judge.as_is_rate is None or judge.checked_at is None:
        reasons.append("judge_uncalibrated")
    else:
        if judge.as_is_rate < judge.threshold:
            reasons.append("judge_below_threshold")
        if now - parse_iso(judge.checked_at) > timedelta(days=judge.max_age_days):
            reasons.append("judge_stale")
    return AutoPublishDecision(allowed=not reasons, reasons=tuple(reasons))


def record_review(
    autonomy: AutonomySettings,
    *,
    article_id: str,
    outcome: CalibrationOutcome,
    at: str,
    content_sha256: str | None = None,
) -> AutonomySettings:
    """A copy after one reviewed article: ``clean`` extends the streak, anything else resets it."""
    event = CalibrationEvent(
        article_id=article_id, at=at, outcome=outcome, content_sha256=content_sha256
    )
    streak = autonomy.calibration.clean_streak + 1 if outcome == "clean" else 0
    events = [*autonomy.calibration.events, event][-50:]
    calibration = autonomy.calibration.model_copy(update={"clean_streak": streak, "events": events})
    return autonomy.model_copy(update={"calibration": calibration, "updated_at": at})


def record_correction(autonomy: AutonomySettings, *, article_id: str, at: str) -> AutonomySettings:
    """A post-publish correction: the streak resets and auto-publish turns itself off."""
    reviewed = record_review(autonomy, article_id=article_id, outcome="correction", at=at)
    return reviewed.model_copy(update={"auto_publish_enabled": False, "last_correction_at": at})


_WORD = re.compile(r"\S+")


def is_substantive_edit(before: str, after: str, *, max_changed: float = 0.05) -> bool:
    """Whether the owner's edits change more than ``max_changed`` of the words (calibration).

    Word-level similarity (:class:`difflib.SequenceMatcher`, no junk heuristic); pure
    whitespace and line-wrapping changes do not count. Run it in a thread for long texts.
    """
    old = _WORD.findall(before)
    new = _WORD.findall(after)
    if not old and not new:
        return False
    ratio = difflib.SequenceMatcher(None, old, new, autojunk=False).ratio()
    return (1.0 - ratio) > max_changed


# --------------------------------------------------------------------------------------------
# LinkedIn connection (owner: linkedin transport)
# --------------------------------------------------------------------------------------------

LINKEDIN_SCOPES: Final = ("openid", "profile", "w_member_social")
"""The OAuth scopes. A constant: an invariant test checks nothing else is ever requested."""
LINKEDIN_API_VERSION: Final = "202611"
TOKEN_LIFETIME_DAYS: Final = 60
TOKEN_BANNER_DAY: Final = 50
TOKEN_ALERT_DAYS: Final = (53, 57, 59)

_VAULT_TOKEN = r"^v1\.[A-Za-z0-9_-]{1,32}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"  # noqa: S105 - a pattern


class LinkedInConnection(SingletonDocument):
    """The encrypted LinkedIn member token (``TokenVault``, context = this document's id)."""

    KIND: ClassVar[str] = "linkedin_connection"
    SINGLETON_ID: ClassVar[str] = "linkedin-connection"
    OWNER: ClassVar[str] = "linkedin"

    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    status_changed_at: Timestamp | None = None
    member_urn: str | None = Field(default=None, pattern=r"^urn:li:person:[A-Za-z0-9_-]{1,100}$")
    scopes: list[str] = Field(default_factory=lambda: list(LINKEDIN_SCOPES))
    access_token: str | None = Field(default=None, pattern=_VAULT_TOKEN, max_length=8192)
    """Vault ciphertext ``v1.<kid>.<nonce>.<ct>``; never the plain token."""
    connected_at: Timestamp | None = None
    expires_at: Timestamp | None = None
    last_introspected_at: Timestamp | None = None
    introspection_active: bool | None = None
    alerts_sent: dict[str, Timestamp] = Field(default_factory=dict[str, str])
    """Day thresholds already alerted (``"53"``, ``"57"``, ``"59"``)."""
    api_version: str = LINKEDIN_API_VERSION
    last_error: str | None = Field(default=None, max_length=64)

    @field_validator("scopes")
    @classmethod
    def _constant_scopes(cls, value: list[str]) -> list[str]:
        if tuple(value) != LINKEDIN_SCOPES:
            raise ValueError("LinkedIn scopes are a constant")
        return value

    def token_day(self, now: datetime) -> int | None:
        """Days since the token was issued (``None`` when not connected)."""
        if self.connected_at is None:
            return None
        return (now - parse_iso(self.connected_at)).days

    def banner_due(self, now: datetime) -> bool:
        day = self.token_day(now)
        return day is not None and day >= TOKEN_BANNER_DAY

    def alerts_due(self, now: datetime) -> list[int]:
        """Alert days reached and not yet alerted."""
        day = self.token_day(now)
        if day is None:
            return []
        return [d for d in TOKEN_ALERT_DAYS if day >= d and str(d) not in self.alerts_sent]


# --------------------------------------------------------------------------------------------
# Voice preferences (owner: voice), standards catalog (owner: gates), profile checklist
# (owner: engagement)
# --------------------------------------------------------------------------------------------


class VoicePreferences(SingletonDocument):
    KIND: ClassVar[str] = "voice_preferences"
    SINGLETON_ID: ClassVar[str] = "voice-preferences"
    OWNER: ClassVar[str] = "voice"

    active_guide_id: DocId | None = None
    digest_cadence: Literal["weekly", "monthly"] = "weekly"
    weekly_until: Timestamp | None = None
    """End of the first quarter (set when the first guide is activated); monthly after it."""
    min_linkedin_samples: int = Field(default=5, ge=1, le=10)


type StandardsFamily = Literal[
    "owasp_llm", "owasp_agentic", "mitre_atlas", "nist_ai_rmf", "nist_sp_800_63_4"
]


class StandardsCatalog(SingletonDocument):
    """Canonical standards ids for the standards-ID validator (refreshed weekly by gates).

    CVE ids are checked by pattern only.
    """

    KIND: ClassVar[str] = "standards_catalog"
    SINGLETON_ID: ClassVar[str] = "standards-catalog"
    OWNER: ClassVar[str] = "gates"

    families: dict[StandardsFamily, list[Annotated[str, Field(min_length=1, max_length=40)]]] = (
        Field(default_factory=dict[StandardsFamily, list[str]])
    )
    refreshed_at: Timestamp | None = None
    source_versions: dict[str, Annotated[str, Field(max_length=100)]] = Field(
        default_factory=dict[str, str]
    )


class ChecklistItem(DomainModel):
    id: Annotated[Slug, Field(max_length=40)]
    label: str = Field(min_length=1, max_length=200)
    done: bool = False
    done_at: Timestamp | None = None
    note: str | None = Field(default=None, max_length=500)


def _default_checklist() -> list[ChecklistItem]:
    return [
        ChecklistItem(id="headline", label="Headline names your three pillars"),
        ChecklistItem(id="about", label="About section matches the pillars and your voice"),
        ChecklistItem(id="featured", label="Featured section shows your best flagship pieces"),
    ]


class ProfileChecklist(SingletonDocument):
    KIND: ClassVar[str] = "profile_checklist"
    SINGLETON_ID: ClassVar[str] = "profile-checklist"
    OWNER: ClassVar[str] = "engagement"

    items: list[ChecklistItem] = Field(default_factory=_default_checklist, max_length=30)
