"""Engagement helpers (container ``engagement``). Owner: engagement.

Other members' text is untrusted and short-lived: an :class:`EngagementItem` holds it for
48 hours at most (``expires_at`` checked in code; Cosmos TTL only collects garbage). Only the
owner's final text and the post URL are kept, in an :class:`EngagementRecord`. Nothing is
automated on LinkedIn.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from blogwriter.domain.base import (
    DocId,
    Document,
    DomainModel,
    ExpiringDocument,
    HttpsUrl,
    Slug,
    Timestamp,
    to_iso,
)
from blogwriter.domain.common import Finding, ServedByRecord
from blogwriter.domain.states import EngagementStatus

__all__ = [
    "THIRD_PARTY_TTL",
    "Angle",
    "EngagementItem",
    "EngagementRecord",
    "WatchEntry",
    "third_party_expiry",
]

THIRD_PARTY_TTL: Final = timedelta(hours=48)
"""How long pasted third-party text may live (plan: "48 h TTL")."""


def third_party_expiry(now: datetime) -> tuple[str, int]:
    """``(expires_at, ttl_seconds)`` for a new item holding third-party text."""
    return to_iso(now + THIRD_PARTY_TTL), int(THIRD_PARTY_TTL.total_seconds())


class Angle(DomainModel):
    """A reply or comment angle grounded in the owner's material."""

    id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9-]+$")
    text: str = Field(min_length=1, max_length=1000)
    grounded_in: list[DocId] = Field(default_factory=list[str], max_length=5)
    """Story, POV or brief ids the angle draws on (it must add something specific)."""


class EngagementItem(ExpiringDocument):
    """One reply (on the owner's post) or comment (on someone else's post)."""

    CONTAINER: ClassVar[str] = "engagement"
    KIND: ClassVar[str] = "engagement_item"
    ID_PREFIX: ClassVar[str] = "eng"
    OWNER: ClassVar[str] = "engagement"

    item_kind: Literal["reply", "comment"]
    status: EngagementStatus = EngagementStatus.OPEN
    status_changed_at: Timestamp
    post_id: DocId | None = None
    """The owner's post a reply belongs to."""
    target_url: HttpsUrl | None = None
    """The LinkedIn post or comment being answered."""
    pasted_text: str = Field(min_length=1, max_length=5000)
    """Other members' text: untrusted, expires with the item."""
    angles: list[Angle] = Field(default_factory=list[Angle], max_length=5)
    draft_text: str | None = Field(default=None, max_length=1250)
    """The same 1-1250 character budget as a final reply (``EngagementRecord.final_text``)."""
    lint: list[Finding] = Field(default_factory=list[Finding], max_length=50)
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord], max_length=5)


class EngagementRecord(Document):
    """What the owner actually posted (kept; no third-party text)."""

    CONTAINER: ClassVar[str] = "engagement"
    KIND: ClassVar[str] = "engagement_record"
    ID_PREFIX: ClassVar[str] = "rec"
    OWNER: ClassVar[str] = "engagement"

    item_kind: Literal["reply", "comment"]
    final_text: str = Field(min_length=1, max_length=1250)
    url: HttpsUrl | None = None
    post_id: DocId | None = None
    posted_at: Timestamp
    substantive: bool = True
    """Counted on the weekly scorecard as a substantive comment."""
    watch_entry_id: DocId | None = None


class WatchEntry(Document):
    """A niche voice outside the owner's first-degree network (daily 15-minute session)."""

    CONTAINER: ClassVar[str] = "engagement"
    KIND: ClassVar[str] = "watch_entry"
    ID_PREFIX: ClassVar[str] = "watch"
    OWNER: ClassVar[str] = "engagement"

    name: str = Field(min_length=1, max_length=120)
    profile_url: HttpsUrl | None = None
    pillar: Annotated[Slug, Field(max_length=40)] | None = None
    note: str | None = Field(default=None, max_length=500)
    last_engaged_at: Timestamp | None = None
    active: bool = True
