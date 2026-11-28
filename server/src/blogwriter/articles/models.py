"""Request and response models of the articles routes (camelCase, unknown input refused).

Requests never carry a whole document: metadata arrives as :class:`ArticleMetaIn` (the same
constraints as ``domain.articles.ArticleMeta``, but ``extra="forbid"``) and is converted with
:meth:`ArticleMetaIn.to_meta`. Lists answer summaries (no Markdown, embeddings or reports),
built from projections (:class:`ArticleRow`, :class:`VersionRow`: ``find_docs(..., fields=)``)
so they never read those fields either.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import (
    Article,
    ArticleMeta,
    ArticleVersion,
    GateName,
    GateReport,
    PublishState,
    SeriesRef,
)
from blogwriter.domain.base import DocId, DomainModel, Sha256Hex, Slug, Timestamp
from blogwriter.domain.common import Finding
from blogwriter.domain.states import ArticleStatus

__all__ = [
    "ArticleIn",
    "ArticleMetaIn",
    "ArticleRow",
    "ArticleSaveIn",
    "ArticleSaved",
    "ArticleSummary",
    "ClearanceIn",
    "GatesSummary",
    "MetaSummary",
    "PublishSummary",
    "SeriesIn",
    "TransitionIn",
    "VersionRow",
    "VersionSummary",
]

type _ShortSlug = Annotated[Slug, Field(max_length=40)]


def _no_lone_surrogates(value: str) -> str:
    """Text that UTF-8 (and so the store) can hold: no unpaired surrogate code points."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text contains an unpaired surrogate code point") from None
    return value


class SeriesIn(ApiModel):
    id: Slug
    title: str = Field(min_length=1, max_length=150)
    part: int = Field(ge=1, le=9999)
    total: int | None = Field(default=None, ge=1, le=9999)

    @model_validator(mode="after")
    def _total_covers_part(self) -> Self:
        if self.total is not None and self.total < self.part:
            raise ValueError("series total must be at least the part number")
        return self


class ArticleMetaIn(ApiModel):
    """The frontmatter fields as the editor sends them (all optional while drafting)."""

    title: str | None = Field(default=None, min_length=1, max_length=150)
    slug: Slug | None = None
    excerpt: str | None = Field(default=None, min_length=1, max_length=400)
    pillar: _ShortSlug | None = None
    format: _ShortSlug | None = None
    tags: list[_ShortSlug] = Field(default_factory=list[str], max_length=10)
    series: SeriesIn | None = None

    @field_validator("title", "excerpt")
    @classmethod
    def _storable(cls, value: str | None) -> str | None:
        return None if value is None else _no_lone_surrogates(value)

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    def to_meta(self) -> ArticleMeta:
        series = self.series
        return ArticleMeta(
            title=self.title,
            slug=self.slug,
            excerpt=self.excerpt,
            pillar=self.pillar,
            format=self.format,
            tags=list(self.tags),
            series=None
            if series is None
            else SeriesRef(id=series.id, title=series.title, part=series.part, total=series.total),
        )


class ArticleIn(ApiModel):
    """``POST /api/articles``."""

    meta: ArticleMetaIn | None = None
    idea_id: DocId | None = None
    week_plan_id: DocId | None = None


class ArticleSaveIn(ApiModel):
    """``PUT /api/articles/{id}`` (autosave). The 512 KiB cap on ``markdown`` is checked by the
    route (413 ``markdown_too_large``), not here (that would be a 422)."""

    meta: ArticleMetaIn
    markdown: str
    base_version: int = Field(ge=0)
    """The article ``version`` the editor loaded (content conflicts, contract section 1.4)."""

    @field_validator("markdown")
    @classmethod
    def _storable(cls, value: str) -> str:
        return _no_lone_surrogates(value)


class ArticleSaved(ApiModel):
    """The autosave answer: the stored article, its etag and the normalizer's findings."""

    item: Article
    etag: str
    findings: list[Finding]


class TransitionIn(ApiModel):
    to: ArticleStatus
    reason: str | None = Field(default=None, min_length=1, max_length=300)
    """A short note, kept on the version the transition stores (if it stores one)."""

    @field_validator("reason")
    @classmethod
    def _storable(cls, value: str | None) -> str | None:
        return None if value is None else _no_lone_surrogates(value)


class ClearanceIn(ApiModel):
    statement: str | None = Field(default=None, min_length=1, max_length=500)
    """Defaults to the employer policy's clearance statement."""

    @field_validator("statement")
    @classmethod
    def _storable(cls, value: str | None) -> str | None:
        return None if value is None else _no_lone_surrogates(value)


class MetaSummary(ApiModel):
    title: str | None
    slug: str | None
    pillar: str | None
    format: str | None


class GatesSummary(ApiModel):
    stale: bool
    """The report is about another content than the current one (re-run the gates)."""
    auto_publish_ready: bool
    manual_publish_ready: bool
    blocking: list[GateName]


class PublishSummary(ApiModel):
    live: bool
    publish_at: str | None
    url: str | None


class ArticleRow(DomainModel):
    """What an article list item reads (a projection of ``Article``: same names, types and
    defaults; never written back). The stored ``contentSha256`` stands in for the Markdown:
    every content write recomputes it, so a report is stale exactly when its hash differs."""

    id: DocId
    status: ArticleStatus = ArticleStatus.PLANNED
    status_changed_at: Timestamp
    purpose: Literal["flagship", "eval"] = "flagship"
    updated_by: Literal["owner", "pipeline", "system"] = "owner"
    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    version: int = Field(default=0, ge=0)
    content_sha256: Sha256Hex | None = None
    word_count: int = Field(default=0, ge=0)
    idea_id: DocId | None = None
    week_plan_id: DocId | None = None
    gates: GateReport | None = None
    review_only: bool = False
    publish: PublishState = Field(default_factory=PublishState)
    updated_at: Timestamp

    @property
    def gates_judgeable(self) -> bool:
        """Whether the row can tell a stale report: no report, or a stored content hash (an
        article written outside the articles API may lack one; read it whole then)."""
        return self.gates is None or self.content_sha256 is not None


class VersionRow(DomainModel):
    """What a version list reads (a projection of ``ArticleVersion``: no meta, no Markdown)."""

    id: DocId
    article_id: DocId
    version: int = Field(ge=1)
    content_sha256: Sha256Hex
    source: Literal["autosave", "pipeline", "restore", "publish", "correction"]
    author: Literal["owner", "pipeline", "system"]
    status_at_save: ArticleStatus
    note: str | None = Field(default=None, max_length=300)
    created_at: Timestamp


def _current_sha256(article: Article | ArticleRow) -> str | None:
    """The hash of the current content: computed from a whole article, stored on a row."""
    if isinstance(article, Article):
        return article.compute_sha256()
    return article.content_sha256


class ArticleSummary(ApiModel):
    """A list item (``GET /api/articles``): no Markdown, report findings or embedding."""

    id: str
    status: ArticleStatus
    status_changed_at: str
    purpose: Literal["flagship", "eval"]
    meta: MetaSummary
    version: int
    word_count: int
    updated_at: str
    updated_by: str
    review_only: bool
    gates: GatesSummary | None
    publish: PublishSummary
    idea_id: str | None
    week_plan_id: str | None

    @classmethod
    def of(cls, article: Article | ArticleRow) -> ArticleSummary:
        """The summary of a whole article or of its list row. A row without a stored content
        hash cannot tell, so its report counts as stale (fail closed); the list reads such an
        article whole instead (:attr:`ArticleRow.gates_judgeable`)."""
        report = article.gates
        gates: GatesSummary | None = None
        if report is not None:
            stale = report.content_sha256 != _current_sha256(article)
            gates = GatesSummary(
                stale=stale,
                auto_publish_ready=report.auto_publish_ready and not stale,
                manual_publish_ready=report.manual_publish_ready and not stale,
                blocking=report.blocking(),
            )
        return cls(
            id=article.id,
            status=article.status,
            status_changed_at=article.status_changed_at,
            purpose=article.purpose,
            meta=MetaSummary(
                title=article.meta.title,
                slug=article.meta.slug,
                pillar=article.meta.pillar,
                format=article.meta.format,
            ),
            version=article.version,
            word_count=article.word_count,
            updated_at=article.updated_at,
            updated_by=article.updated_by,
            review_only=article.review_only,
            gates=gates,
            publish=PublishSummary(
                live=article.publish.live,
                publish_at=article.publish.publish_at,
                url=article.publish.url,
            ),
            idea_id=article.idea_id,
            week_plan_id=article.week_plan_id,
        )


class VersionSummary(ApiModel):
    """A list item of ``GET /api/articles/{id}/versions`` (no meta, no Markdown)."""

    version: int
    source: str
    author: str
    status_at_save: ArticleStatus
    created_at: str
    content_sha256: str
    note: str | None
    pinned: bool
    """Kept regardless of the version limit (gated, gate report or last published version)."""

    @classmethod
    def of(cls, version: ArticleVersion | VersionRow, *, pinned: bool) -> VersionSummary:
        return cls(
            version=version.version,
            source=version.source,
            author=version.author,
            status_at_save=version.status_at_save,
            created_at=version.created_at,
            content_sha256=version.content_sha256,
            note=version.note,
            pinned=pinned,
        )
