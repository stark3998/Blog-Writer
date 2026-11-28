"""Articles, their versions and gate reports (containers ``articles``, ``article-versions``).

Owner: articles (the document, autosave, versions, owner transitions). Other writers touch only
their fields, always with an If-Match CAS: pipeline (Markdown and ``served_by`` in pipeline
states, through the articles Python API), gates (``gates``), publish (``publish``, ``veto``
status and ``corrections``), planner (``veto``) and the illustrations job (``illustrations``,
``illustrationJobId``, through the articles Python API).

``markdown`` is the article-dialect **body** (``docs/v2/article-dialect.md``); the frontmatter
lives in :class:`ArticleMeta` and :func:`blogwriter.domain.compose.compose_document` builds the
canonical document (gates, publish and export use that one function). The server caps the body
at 512 KiB of UTF-8.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from blogwriter.domain.base import (
    DocId,
    Document,
    DomainModel,
    HttpsUrl,
    LocalDate,
    Sha256Hex,
    Slug,
    Timestamp,
    derived_id,
    parse_iso,
)
from blogwriter.domain.common import MAX_STORED_FINDINGS, Finding, MediaRef, ServedByRecord
from blogwriter.domain.hashing import article_sha256
from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ARTICLE_WRITER,
    ArticleStatus,
    TransitionRefused,
    Writer,
)

__all__ = [
    "AUTOSAVE_SNAPSHOT_EVERY",
    "GATE_ORDER",
    "ILLUSTRATION_ID_PATTERN",
    "MAX_ARTICLE_MARKDOWN_BYTES",
    "MAX_EMBEDDING_DIMENSIONS",
    "MAX_ILLUSTRATIONS",
    "MAX_ILLUSTRATION_ALT",
    "PUBLISHED_MARKER_NAME",
    "Article",
    "ArticleMeta",
    "ArticleVersion",
    "CalibrationFlags",
    "Clearance",
    "Correction",
    "GateName",
    "GateOverride",
    "GateReport",
    "GateResult",
    "GateVerdict",
    "Illustration",
    "IllustrationRole",
    "OwnerSignalRef",
    "PublishState",
    "PublishedAsset",
    "SeriesRef",
    "VetoNotice",
    "autosave_snapshot_due",
    "check_article_transition",
    "has_published_marker",
    "versions_to_prune",
]

MAX_ARTICLE_MARKDOWN_BYTES: Final = 512 * 1024
"""Same cap as ``blogwriter.publish.contract.MAX_MARKDOWN_BYTES``."""
MAX_EMBEDDING_DIMENSIONS: Final = 3072
"""``text-embedding-3-large`` is used at 1024 dimensions; anything up to its native size fits."""

type TagSlug = Annotated[Slug, Field(max_length=40)]


class SeriesRef(DomainModel):
    id: Slug
    title: str = Field(min_length=1, max_length=150)
    part: int = Field(ge=1, le=9999)
    total: int | None = Field(default=None, ge=1, le=9999)

    @model_validator(mode="after")
    def _total_covers_part(self) -> Self:
        if self.total is not None and self.total < self.part:
            raise ValueError("series total must be at least the part number")
        return self


class ArticleMeta(DomainModel):
    """The frontmatter fields (dialect section 2). Optional while drafting; all but ``series``
    are required to publish (:meth:`missing_for_publish`)."""

    title: str | None = Field(default=None, min_length=1, max_length=150)
    slug: Slug | None = None
    excerpt: str | None = Field(default=None, min_length=1, max_length=400)
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    format: Annotated[Slug, Field(max_length=40)] | None = None
    tags: list[TagSlug] = Field(default_factory=list[str], max_length=10)
    series: SeriesRef | None = None

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    def missing_for_publish(self) -> list[str]:
        """Names (camelCase) of the frontmatter fields a published article needs."""
        required = {
            "title": self.title,
            "slug": self.slug,
            "excerpt": self.excerpt,
            "pillar": self.pillar,
            "format": self.format,
        }
        return [name for name, value in required.items() if value is None]


# --------------------------------------------------------------------------------------------
# Gates (owner: gates)
# --------------------------------------------------------------------------------------------


class GateName(StrEnum):
    """The seven gates, in the plan's order."""

    RUBRIC = "rubric"
    FACT_CHECK = "fact_check"
    CONFIDENTIALITY = "confidentiality"
    ORIGINALITY = "originality"
    LINT = "lint"
    OWNER_SIGNAL = "owner_signal"
    OUTBOUND = "outbound"


GATE_ORDER: Final = tuple(GateName)


class GateVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 - a verdict, not a password
    WARN = "warn"
    """Passed with warnings."""
    FAIL = "fail"
    SKIPPED = "skipped"
    """Could not run (a dependency is unavailable). Counts as not passed: gates fail closed."""
    ERROR = "error"


class GateOverride(DomainModel):
    """The owner overrode a failing gate for a manual publish (never for auto-publish)."""

    reason: str = Field(min_length=3, max_length=500)
    at: Timestamp


class GateResult(DomainModel):
    gate: GateName
    verdict: GateVerdict
    score: float | None = None
    dimensions: dict[str, float] = Field(default_factory=dict[str, float])
    """Rubric dimensions (``first_hand_specificity`` among them), 0-10."""
    findings: list[Finding] = Field(default_factory=list[Finding], max_length=MAX_STORED_FINDINGS)
    """At most :data:`~blogwriter.domain.common.MAX_STORED_FINDINGS`, cut with
    :func:`~blogwriter.domain.common.cap_findings` (hard blockers are never the ones cut)."""
    truncated: int = Field(default=0, ge=0)
    """Findings left out by the cut (the gates panel says "and N more"; the live lint has all)."""
    ran_at: Timestamp
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=20)
    override: GateOverride | None = None

    @property
    def passed(self) -> bool:
        """Passed on its own (``pass`` or ``warn``); an override does not count."""
        return self.verdict in (GateVerdict.PASS, GateVerdict.WARN)

    @property
    def overridable(self) -> bool:
        """The owner may override it: it failed only on overridable findings."""
        if self.passed:
            return False
        if self.verdict in (GateVerdict.SKIPPED, GateVerdict.ERROR):
            return self.gate not in _HARD_GATES
        blockers = [f for f in self.findings if f.severity == "block"]
        return bool(blockers) and all(f.overridable for f in blockers)

    @property
    def cleared_for_manual_publish(self) -> bool:
        return self.passed or (self.override is not None and self.overridable)


_HARD_GATES: Final = frozenset({GateName.CONFIDENTIALITY, GateName.LINT, GateName.OUTBOUND})
"""Gates that are never skippable by override: a skipped or errored run must be re-run."""


class GateReport(DomainModel):
    """One run of the seven gates over one exact article version."""

    content_sha256: Sha256Hex
    """:func:`blogwriter.domain.hashing.article_sha256` of the gated version."""
    version: int = Field(ge=1)
    ran_at: Timestamp
    job_id: str | None = None
    results: list[GateResult] = Field(default_factory=list[GateResult], max_length=7)

    @model_validator(mode="after")
    def _one_result_per_gate(self) -> Self:
        gates = [result.gate for result in self.results]
        if len(gates) != len(set(gates)):
            raise ValueError("a gate report has at most one result per gate")
        return self

    def result(self, gate: GateName) -> GateResult | None:
        return next((result for result in self.results if result.gate is gate), None)

    @property
    def complete(self) -> bool:
        return {result.gate for result in self.results} == set(GATE_ORDER)

    @property
    def auto_publish_ready(self) -> bool:
        """All seven gates passed on their own (overrides never count for auto-publish)."""
        return self.complete and all(result.passed for result in self.results)

    @property
    def manual_publish_ready(self) -> bool:
        """All seven gates passed or were overridden where overriding is allowed."""
        return self.complete and all(result.cleared_for_manual_publish for result in self.results)

    def blocking(self) -> list[GateName]:
        """Gates that stop a manual publish."""
        missing = [gate for gate in GATE_ORDER if self.result(gate) is None]
        failing = [r.gate for r in self.results if not r.cleared_for_manual_publish]
        return [gate for gate in GATE_ORDER if gate in missing or gate in failing]


# --------------------------------------------------------------------------------------------
# Article parts
# --------------------------------------------------------------------------------------------


class OwnerSignalRef(DomainModel):
    """Owner material a version draws on (gate 6 needs at least one)."""

    kind: Literal["story", "pov", "interview"]
    id: DocId
    quote: str | None = Field(default=None, max_length=1000)
    """For ``interview``: the verbatim (anonymized) answer text quoted in the article."""


class Clearance(DomainModel):
    """The owner's per-version attestation, required for case studies and anonymized stories."""

    required: bool = False
    content_sha256: Sha256Hex | None = None
    attested_at: Timestamp | None = None
    statement: str | None = Field(default=None, max_length=500)

    def valid_for(self, content_sha256: str) -> bool:
        return not self.required or (
            self.attested_at is not None and self.content_sha256 == content_sha256
        )


class Correction(DomainModel):
    """A dated post-publish correction note (published-blogs ``corrections[]``)."""

    date: LocalDate
    """UTC calendar date of the correction."""
    note: str = Field(min_length=1, max_length=1000)
    created_at: Timestamp


class PublishedAsset(DomainModel):
    """published-blogs ``assets[]``: a file the portfolio copies from Blob ``published``."""

    path: str = Field(max_length=300, pattern=r"^/blog-media/")
    blob: str = Field(max_length=300)
    sha256: Sha256Hex


class PublishState(DomainModel):
    """Publication bookkeeping (owner: publish). The slug is immutable after first publish."""

    slug: Slug | None = None
    live: bool = False
    published_at: Timestamp | None = None
    last_published_at: Timestamp | None = None
    last_published_version: int | None = Field(default=None, ge=1)
    last_published_sha256: Sha256Hex | None = None
    url: HttpsUrl | None = None
    verified_at: Timestamp | None = None
    publish_at: Timestamp | None = None
    """When an approved article goes out (default: Monday 09:00 local)."""
    previous_slugs: list[Slug] = Field(default_factory=list[str], max_length=20)
    """Old slugs that now hold redirect stubs."""
    auto: bool = False
    """Approved on the auto-publish path (pipeline finalize): the veto notice and the publish
    check select on it. The approve and publish-now routes set it to ``False``."""
    last_operation: Literal["publish", "unpublish"] | None = None
    """What the publish job did last; a retry from ``publish_failed`` repeats it."""
    job_id: str | None = None
    portfolio_run_id: str | None = Field(default=None, max_length=40)
    assets: list[PublishedAsset] = Field(default_factory=list[PublishedAsset], max_length=200)
    last_error: str | None = Field(default=None, max_length=64)


type IllustrationRole = Literal["hero", "inline"]
"""What an illustration was planned as: the landscape hero, or an image inside the body."""

MAX_ILLUSTRATIONS: Final = 12
"""Candidates kept on an article (the oldest unused ones are dropped first)."""
MAX_ILLUSTRATION_ALT: Final = 300
"""Longest alt text (the published ``heroImage.alt`` takes 300 characters)."""
ILLUSTRATION_ID_PATTERN: Final = r"^ill-[0-9a-f]{16}$"


class Illustration(DomainModel):
    """An AI illustration candidate (``gpt-image-2``) that passed its vision check.

    Added by the illustrations job (``illustrations.generate``) without touching the body or
    the hero: a candidate is not content. The owner edits ``alt_text``, discards it, makes it
    the hero (a ``hero_image`` media the publish job uses for ``heroImage``) or inserts it into
    the body as a normal Markdown image (versioned, so the approval covers it). ``media`` is the
    re-encoded WebP (pixels only, metadata stripped) in Blob ``media`` under its content hash.
    ``served_by`` lists the plan, generation and vision check calls; ``cost_usd`` is what
    generating and checking this image cost (discarded attempts included).
    """

    id: str = Field(pattern=ILLUSTRATION_ID_PATTERN)
    role: IllustrationRole
    media: MediaRef
    alt_text: str = Field(min_length=1, max_length=MAX_ILLUSTRATION_ALT)
    prompt_summary: str = Field(min_length=1, max_length=300)
    """What the image was asked to show (the planned concept), never the full prompt."""
    section: str | None = Field(default=None, max_length=200)
    """For an inline image: the heading the plan placed it after (a suggestion)."""
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=8)
    cost_usd: float = Field(default=0.0, ge=0)
    job_id: DocId
    created_at: Timestamp

    @staticmethod
    def id_for(sha256: str) -> str:
        """``ill-<first 16 hex of the image's SHA-256>``: the same image is one candidate."""
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return f"ill-{sha256[:16]}"


class VetoNotice(DomainModel):
    """The veto notice for an auto-published article (owner: planner).

    Auto-publish proceeds only with a delivery receipt (push 201); otherwise the article is
    held and the owner is alerted out of band.
    """

    sent_at: Timestamp | None = None
    delivered: bool | None = None
    receipt_at: Timestamp | None = None
    vetoed_at: Timestamp | None = None
    alerted_at: Timestamp | None = None


class CalibrationFlags(DomainModel):
    """How this article counted toward auto-publish calibration (owner: pipeline)."""

    gated_sha256: Sha256Hex | None = None
    gated_version: int | None = Field(default=None, ge=1)
    """The version the pipeline gated (a pipeline write, so an ``ArticleVersion`` exists; it is
    pinned against pruning until the review is classified)."""
    approved_sha256: Sha256Hex | None = None
    substantive_edit: bool | None = None
    gates_passed: bool | None = None
    review_only: bool = False
    outcome: (
        Literal["clean", "edited", "gates_failed", "review_only", "correction", "vetoed"] | None
    ) = None
    counted_at: Timestamp | None = None


# --------------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------------


def _check_markdown_size(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_ARTICLE_MARKDOWN_BYTES:
        raise ValueError(f"markdown is larger than {MAX_ARTICLE_MARKDOWN_BYTES} bytes")
    return value


class Article(Document):
    """A flagship article from plan to publication."""

    CONTAINER: ClassVar[str] = "articles"
    KIND: ClassVar[str] = "article"
    ID_PREFIX: ClassVar[str] = "art"
    OWNER: ClassVar[str] = "articles"

    status: ArticleStatus = ArticleStatus.PLANNED
    status_changed_at: Timestamp
    purpose: Literal["flagship", "eval"] = "flagship"
    """``eval`` articles belong to the eval set (analytics); lists hide them by default."""
    updated_by: Literal["owner", "pipeline", "system"] = "owner"
    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    markdown: str = ""
    version: int = Field(default=0, ge=0)
    """Incremented on every saved change of ``meta`` or ``markdown`` (versions keep copies)."""
    content_sha256: Sha256Hex | None = None
    word_count: int = Field(default=0, ge=0)
    reading_time_minutes: int = Field(default=0, ge=0)

    idea_id: DocId | None = None
    week_plan_id: DocId | None = None
    research_run_id: DocId | None = None
    brief_id: DocId | None = None
    interview_id: DocId | None = None
    owner_signals: list[OwnerSignalRef] = Field(default_factory=list[OwnerSignalRef], max_length=50)
    pipeline_job_id: DocId | None = None
    pipeline_runs: int = Field(default=0, ge=0)

    gates: GateReport | None = None
    media: list[MediaRef] = Field(default_factory=list[MediaRef], max_length=50)
    """Draft assets for review (diagram renders, OG and hero previews; owner: pipeline). The
    publish job renders the published assets itself."""
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=50)
    review_only: bool = False
    """Drafted by a fallback model or with a refusal in the chain: never auto-published."""
    review_only_reasons: list[str] = Field(default_factory=list[str], max_length=20)
    embedding: list[float] | None = Field(default=None, max_length=MAX_EMBEDDING_DIMENSIONS)
    embedding_sha256: Sha256Hex | None = None
    calibration: CalibrationFlags | None = None
    clearance: Clearance = Field(default_factory=Clearance)
    corrections: list[Correction] = Field(default_factory=list[Correction], max_length=100)
    publish: PublishState = Field(default_factory=PublishState)
    veto: VetoNotice | None = None
    last_error: str | None = Field(default=None, max_length=64)
    """Code of the failure that put the article in ``needs_attention`` or ``publish_failed``."""
    illustrations: list[Illustration] = Field(
        default_factory=list[Illustration], max_length=MAX_ILLUSTRATIONS
    )
    """AI illustration candidates (owner: the illustrations job, through the articles API).
    Not content: the body and the hero change only through the owner's actions."""
    illustration_job_id: DocId | None = None
    """The last ``illustrations.generate`` run started for this article."""

    @field_validator("markdown")
    @classmethod
    def _markdown_size(cls, value: str) -> str:
        return _check_markdown_size(value)

    def illustration(self, illustration_id: str) -> Illustration | None:
        """The candidate with that id, or ``None``."""
        return next((item for item in self.illustrations if item.id == illustration_id), None)

    def hero_media(self) -> MediaRef | None:
        """The ``hero_image`` media the publish job and the preview use, if any."""
        return next((item for item in self.media if item.role == "hero_image"), None)

    def references_media(self, name: str) -> bool:
        """Whether the body shows ``/api/media/<name>`` or the hero is that blob."""
        hero = self.hero_media()
        if hero is not None and hero.container == "media" and hero.name == name:
            return True
        return f"{MediaRef.URL_PREFIX}{name}" in self.markdown

    def compute_sha256(self) -> str:
        """:func:`~blogwriter.domain.hashing.article_sha256` of the current meta and body."""
        return article_sha256(self.meta.model_dump(mode="json", by_alias=True), self.markdown)

    @property
    def writer(self) -> Writer:
        """Who may write the Markdown and metadata now (``ARTICLE_WRITER``)."""
        return ARTICLE_WRITER[self.status]

    def pinned_versions(self) -> frozenset[int]:
        """Versions that version pruning must keep: the pipeline's gated version (calibration),
        the version of the current gate report and the last published version (restore and
        corrections)."""
        pinned = {
            self.calibration.gated_version if self.calibration is not None else None,
            self.gates.version if self.gates is not None else None,
            self.publish.last_published_version,
        }
        return frozenset(version for version in pinned if version is not None)

    def owns_slug(self, slug: str) -> bool:
        """Whether ``published-blogs/<slug>`` (if it exists) is this article's own document:
        its current slug or one of its redirect stubs. Any other existing document with that id
        is someone else's (another article, a migrated v1 post): ``slug_taken``."""
        return slug == self.publish.slug or slug in self.publish.previous_slugs

    @staticmethod
    def id_for_eval(topic_id: str, eval_job_id: str) -> str:
        """The eval article of one topic in one eval run (``analytics.eval``), retry-safe."""
        return derived_id("art", "eval", topic_id, eval_job_id)


def check_article_transition(article: Article, target: ArticleStatus) -> ArticleStatus:
    """``ARTICLE_MACHINE`` plus the article rules every package must apply on a status change.

    Raises :class:`~blogwriter.domain.states.TransitionError` for a missing edge and
    :class:`~blogwriter.domain.states.TransitionRefused` for ``live_article`` (archiving a live
    article: its published document would stay up; unpublish first) and ``eval_article`` (an
    eval article is never approved or published).
    """
    ARTICLE_MACHINE.transition(article.status, target)
    if target is ArticleStatus.ARCHIVED and article.publish.live:
        raise TransitionRefused("live_article", "This article is live. Unpublish it first.")
    if article.purpose == "eval" and target in (ArticleStatus.APPROVED, ArticleStatus.PUBLISHING):
        raise TransitionRefused("eval_article", "Eval articles are never published.")
    return target


AUTOSAVE_SNAPSHOT_EVERY: Final = timedelta(minutes=10)
"""Owner autosaves store a new ``ArticleVersion`` at most this often (coalescing)."""


def autosave_snapshot_due(
    last_source: str | None, last_created_at: str | None, *, now: datetime
) -> bool:
    """Whether an owner autosave also stores an ``ArticleVersion``.

    Always when there is no version yet or the latest one is not an autosave (a pipeline,
    restore, publish or correction copy must never be overwritten by typing), otherwise only
    once the latest autosave copy is :data:`AUTOSAVE_SNAPSHOT_EVERY` old. Pipeline writes,
    status transitions, restores and publishes always store a version.
    """
    if last_source != "autosave" or last_created_at is None:
        return True
    return now - parse_iso(last_created_at) >= AUTOSAVE_SNAPSHOT_EVERY


def versions_to_prune(
    versions: Iterable[int], *, keep: int, pinned: Collection[int] = ()
) -> list[int]:
    """Stored version numbers to delete: all but the newest ``keep`` and the ``pinned`` ones
    (:meth:`Article.pinned_versions`), oldest first."""
    if keep < 1:
        raise ValueError("keep at least one version")
    newest_first = sorted(set(versions), reverse=True)
    return sorted(v for v in newest_first[keep:] if v not in pinned)


PUBLISHED_MARKER_NAME: Final = "bw-updated-at"
"""``<meta name="bw-updated-at" content="<published-blogs updatedAt>">``: the portfolio renders
it on every article page (portfolio PR B), so the publish job can tell the new version is live
(a republish or correction keeps answering 200 with the old page until the build deploys)."""
_META_TAG: Final = re.compile(r"<meta\b([^>]*)>", re.IGNORECASE)
_ATTRIBUTE: Final = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def has_published_marker(html: str | bytes, updated_at: str) -> bool:
    """Whether a fetched article page carries the version marker for ``updated_at``."""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    for tag in _META_TAG.finditer(text):
        attributes = {
            name.lower(): double if double else single
            for name, double, single in _ATTRIBUTE.findall(tag.group(1))
        }
        if attributes.get("name") == PUBLISHED_MARKER_NAME:
            return attributes.get("content") == updated_at
    return False


class ArticleVersion(Document):
    """A saved copy of one article version (partition ``/articleId``; keep the last N)."""

    CONTAINER: ClassVar[str] = "article-versions"
    KIND: ClassVar[str] = "article_version"
    PARTITION_FIELD: ClassVar[str] = "article_id"
    OWNER: ClassVar[str] = "articles"

    article_id: DocId
    version: int = Field(ge=1)
    meta: ArticleMeta
    markdown: str
    content_sha256: Sha256Hex
    source: Literal["autosave", "pipeline", "restore", "publish", "correction"]
    author: Literal["owner", "pipeline", "system"]
    status_at_save: ArticleStatus
    note: str | None = Field(default=None, max_length=300)

    @field_validator("markdown")
    @classmethod
    def _markdown_size(cls, value: str) -> str:
        return _check_markdown_size(value)

    @staticmethod
    def id_for(article_id: str, version: int) -> str:
        """``<articleId>-v00042``: deterministic, so a retried save cannot duplicate a version."""
        if version < 1:
            raise ValueError("versions start at 1")
        return f"{article_id}-v{version:05d}"
