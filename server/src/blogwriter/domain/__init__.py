"""blogwriter.domain: the stored documents of Phases 2-7 and the build contract's shared code.

The binding description is ``docs/v2/build/contract.md``. Modules:

- :mod:`.base`: ``DomainModel``/``Document`` bases, field types, ids and timestamps.
- :mod:`.states`: every status machine as data, ``transition()``, the who-writes rule.
- :mod:`.common`: findings, ``served_by`` records, media references, citations, quotes.
- Documents by area: :mod:`.settings` (kb singletons, LinkedIn connection, autonomy),
  :mod:`.stories`, :mod:`.voice`, :mod:`.research`, :mod:`.signals` (sources, signals, ideas),
  :mod:`.articles`, :mod:`.posts`, :mod:`.planner` (week plans, reminders),
  :mod:`.engagement`, :mod:`.analytics`, :mod:`.ephemeral`, :mod:`.backups`, :mod:`.seeds`
  (the v1 migration's proposals for kb and voice).
- Pure helpers: :mod:`.hashing` (approval hash, article hash, URL normalization),
  :mod:`.weeks` (DST, weeks), :mod:`.confidentiality` (blocklist matching, fail closed, the
  AI-use gate), and the calibration functions in :mod:`.settings`.
- Shared rules (import explicitly; not re-exported here): :mod:`.compose` (the canonical
  article document and word counts), :mod:`.slots` (week slots, spacing, next free slot, next
  article publish time), :mod:`.jobflow` (enqueue once, parent and child jobs, deferral,
  cancelling a child), :mod:`.golive` (the go-live times posts hold, the default go-live mode,
  the slot jobs).
- Integration (import explicitly; not re-exported here): :mod:`.docstore` (typed store access),
  :mod:`.api` (response envelopes, cursors), :mod:`.ports` (service protocols, the services
  registry, schedule entries), :mod:`.deps` (FastAPI dependencies, problem mapping) and
  :mod:`.testing` (fakes for every service).
"""

from blogwriter.domain.analytics import (
    AnalyticsImport,
    EvalResult,
    EvalTopic,
    Experiment,
    InsightSuggestion,
)
from blogwriter.domain.articles import (
    GATE_ORDER,
    Article,
    ArticleMeta,
    ArticleVersion,
    GateName,
    GateReport,
    GateResult,
    GateVerdict,
    check_article_transition,
)
from blogwriter.domain.backups import BACKUP_CONTAINERS, BackupManifest, BackupStatus
from blogwriter.domain.base import (
    Document,
    DomainModel,
    ExpiringDocument,
    SingletonDocument,
    WrongKind,
    derived_id,
    distinct_stamps,
    new_id,
    now_iso,
    parse_iso,
    sha256_hex,
    to_iso,
    utc_now,
)
from blogwriter.domain.common import Citation, Finding, MediaRef, Quote, ServedByRecord
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    blocklist_hit,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.ephemeral import OAuthState, SeedText
from blogwriter.domain.hashing import approval_hash, article_sha256, normalize_url, url_sha256
from blogwriter.domain.planner import Reminder, WeekPlan
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.research import Brief, QuickSearch, ResearchRun
from blogwriter.domain.seeds import KbSeed, VoiceSeed
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    FormatPacks,
    LaneSettings,
    LinkedInConnection,
    ModelPreferences,
    PillarSettings,
    ProfileChecklist,
    StandardsCatalog,
    VoicePreferences,
)
from blogwriter.domain.signals import Idea, Signal, Source
from blogwriter.domain.states import (
    MACHINES,
    ArticleStatus,
    IdeaStatus,
    KitState,
    PostStatus,
    StateMachine,
    TransitionError,
    TransitionRefused,
    WriterConflict,
    check_article_writer,
    transition,
)
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide, VoiceDigest, VoiceSample

DOCUMENT_TYPES: tuple[type[Document], ...] = (
    PillarSettings,
    FormatPacks,
    LaneSettings,
    BrandKit,
    Blocklist,
    EmployerPolicy,
    CadenceSettings,
    ModelPreferences,
    AutonomySettings,
    LinkedInConnection,
    VoicePreferences,
    StandardsCatalog,
    ProfileChecklist,
    BackupStatus,
    KbSeed,
    Experiment,
    InsightSuggestion,
    EvalTopic,
    EvalResult,
    AnalyticsImport,
    StoryEntry,
    PovEntry,
    Interview,
    VoiceSample,
    StyleGuide,
    VoiceDigest,
    VoiceSeed,
    Idea,
    WeekPlan,
    ResearchRun,
    Brief,
    QuickSearch,
    Article,
    ArticleVersion,
    Post,
    Source,
    Signal,
    Reminder,
    EngagementItem,
    EngagementRecord,
    WatchEntry,
    OAuthState,
    SeedText,
)
"""Every stored document class (a test checks containers, kinds and partition keys)."""

__all__ = [
    "BACKUP_CONTAINERS",
    "DOCUMENT_TYPES",
    "GATE_ORDER",
    "MACHINES",
    "AiUseNotConfirmed",
    "AnalyticsImport",
    "Article",
    "ArticleMeta",
    "ArticleStatus",
    "ArticleVersion",
    "AutonomySettings",
    "BackupManifest",
    "BackupStatus",
    "Blocklist",
    "BlocklistNotConfigured",
    "BrandKit",
    "Brief",
    "CadenceSettings",
    "Citation",
    "ConfidentialText",
    "Document",
    "DomainModel",
    "EmployerPolicy",
    "EngagementItem",
    "EngagementRecord",
    "EvalResult",
    "EvalTopic",
    "Experiment",
    "ExpiringDocument",
    "Finding",
    "FormatPacks",
    "GateName",
    "GateReport",
    "GateResult",
    "GateVerdict",
    "Idea",
    "IdeaStatus",
    "InsightSuggestion",
    "Interview",
    "KbSeed",
    "KitState",
    "LaneSettings",
    "LinkedInConnection",
    "MediaRef",
    "ModelPreferences",
    "OAuthState",
    "PillarSettings",
    "Post",
    "PostStatus",
    "PovEntry",
    "ProfileChecklist",
    "QuickSearch",
    "Quote",
    "Reminder",
    "ResearchRun",
    "SeedText",
    "ServedByRecord",
    "Signal",
    "SingletonDocument",
    "Slot",
    "Source",
    "StandardsCatalog",
    "StateMachine",
    "StoryEntry",
    "StyleGuide",
    "TransitionError",
    "TransitionRefused",
    "VoiceDigest",
    "VoicePreferences",
    "VoiceSample",
    "VoiceSeed",
    "WatchEntry",
    "WeekPlan",
    "WriterConflict",
    "WrongKind",
    "approval_hash",
    "article_sha256",
    "blocklist_findings",
    "blocklist_hit",
    "check_article_transition",
    "check_article_writer",
    "check_texts",
    "derived_id",
    "distinct_stamps",
    "new_id",
    "normalize_url",
    "now_iso",
    "parse_iso",
    "require_ai_use",
    "sha256_hex",
    "to_iso",
    "transition",
    "url_sha256",
    "utc_now",
]
