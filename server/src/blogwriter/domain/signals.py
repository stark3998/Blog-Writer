"""Sources, signals and the ideas inbox (containers ``sources``, ``signals``, ``ideas``).

Owner: signals. Signals store **hashes only** (dedupe of feed items, 90-day container TTL).
Feed items never generate content without an owner action: triage only files ideas.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from blogwriter.domain.base import (
    Code,
    DocId,
    Document,
    DomainModel,
    HttpsUrl,
    Sha256Hex,
    Slug,
    Timestamp,
    derived_id,
)
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.posts import Lane
from blogwriter.domain.states import IdeaStatus, SourceStatus

__all__ = [
    "FORMAT_ID_PATTERN",
    "Idea",
    "IdeaOrigin",
    "Signal",
    "Source",
    "SourceType",
    "TriageInfo",
]

FORMAT_ID_PATTERN: Final = r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"
"""A format-pack slug or a post format (``image_card``): lowercase words joined by - or _."""

type SourceType = Literal["feed", "scout_query"]
"""``feed``: an RSS/Atom/JSON feed fetched through the egress helper; ``scout_query``: a weekly
per-pillar ``web_search`` (QUICK_SEARCH) with the pillar's allowlist."""


class Source(Document):
    CONTAINER: ClassVar[str] = "sources"
    KIND: ClassVar[str] = "source"
    ID_PREFIX: ClassVar[str] = "feed"
    OWNER: ClassVar[str] = "signals"

    type: SourceType
    title: str = Field(min_length=1, max_length=200)
    url: HttpsUrl | None = None
    """The feed URL (``feed``)."""
    site_url: HttpsUrl | None = None
    query: str | None = Field(default=None, min_length=3, max_length=300)
    """The search query (``scout_query``)."""
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    status: SourceStatus = SourceStatus.ACTIVE
    status_changed_at: Timestamp
    etag: str | None = Field(default=None, max_length=300)
    last_modified: str | None = Field(default=None, max_length=100)
    last_crawled_at: Timestamp | None = None
    last_success_at: Timestamp | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: Code | None = None
    items_seen: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.type == "feed" and self.url is None:
            raise ValueError("a feed source needs a url")
        if self.type == "scout_query" and self.query is None:
            raise ValueError("a scout source needs a query")
        return self


class Signal(Document):
    """A feed item seen once (partition ``/sourceId``). Hashes only: no title, no URL."""

    CONTAINER: ClassVar[str] = "signals"
    KIND: ClassVar[str] = "signal"
    PARTITION_FIELD: ClassVar[str] = "source_id"
    OWNER: ClassVar[str] = "signals"

    source_id: DocId
    """The feed source's id, or ``scout-<pillarId>`` for per-pillar scouting (no source
    document behind it: those ideas have no ``sourceId``)."""
    url_sha256: Sha256Hex
    """:func:`blogwriter.domain.hashing.url_sha256` of the item link."""
    title_sha256: Sha256Hex | None = None
    item_published_at: Timestamp | None = None
    seen_at: Timestamp
    triaged: bool = False
    idea_id: DocId | None = None

    @staticmethod
    def id_for(source_id: str, url_sha256: str) -> str:
        """Deterministic id, so seeing the same item twice in one source is a ``Conflict``.

        The source is part of it: ids stay unique across the container (Cosmos only enforces
        uniqueness per partition), so keyset pages on ``id`` (backups) never skip a document.
        """
        return derived_id("sig", source_id, url_sha256)


type IdeaOrigin = Literal["feed", "scout", "quick_capture", "owner", "repurpose", "plan"]


class TriageInfo(DomainModel):
    ran_at: Timestamp
    reasons: str | None = Field(default=None, max_length=500)
    served_by: ServedByRecord | None = None


class Idea(Document):
    """An item in the ideas inbox: owner-captured, triaged from feeds or found by scouting."""

    CONTAINER: ClassVar[str] = "ideas"
    KIND: ClassVar[str] = "idea"
    ID_PREFIX: ClassVar[str] = "idea"
    OWNER: ClassVar[str] = "signals"

    status: IdeaStatus = IdeaStatus.NEW
    status_changed_at: Timestamp
    origin: IdeaOrigin
    title: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    """Owner-typed note (checked against the blocklist on save)."""
    url: HttpsUrl | None = None
    url_sha256: Sha256Hex | None = None
    publisher: str | None = Field(default=None, max_length=200)
    published_at: Timestamp | None = None
    source_id: DocId | None = None
    signal_id: DocId | None = None
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    relevance: float | None = Field(default=None, ge=0, le=1)
    novelty: float | None = Field(default=None, ge=0, le=1)
    suggested_lane: Lane | Literal["flagship"] | None = None
    suggested_format: (
        Annotated[str, StringConstraints(pattern=FORMAT_ID_PATTERN, max_length=40)] | None
    ) = None
    """An article format-pack id (a slug) or a post format (``image_card`` has an underscore)."""
    angle: str | None = Field(default=None, max_length=500)
    time_sensitive: bool = False
    expires_at: Timestamp | None = None
    """News goes to the <= 72 h quick-take lane; after this the idea expires."""
    snoozed_until: Timestamp | None = None
    triage: TriageInfo | None = None
    article_id: DocId | None = None
    post_id: DocId | None = None
    """Set when the idea is used (status ``used``)."""
    repurpose_of: DocId | None = None
    """For ``repurpose``: the post that beat the owner's median."""
