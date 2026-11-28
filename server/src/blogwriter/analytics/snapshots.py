"""The 30-second snapshot form (+48 h and +7 d): metrics the owner types for a live post.

Analytics writes only ``Post.snapshots`` (contract section 1.3), always with an If-Match CAS. A
form snapshot for ``h48`` or ``d7`` has a fixed id (``form-h48``, ``form-d7``), so sending the
form again corrects the numbers instead of adding a second snapshot; an ``other`` snapshot is
keyed by the time it was taken. Import snapshots (``analytics.imports``) use the import id.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import Timestamp, parse_iso, to_iso
from blogwriter.domain.posts import MetricsSnapshot, Post
from blogwriter.domain.states import PostStatus

__all__ = [
    "FORM_PREFIX",
    "MAX_COUNT",
    "MAX_SNAPSHOTS",
    "MetricsSnapshotIn",
    "SnapshotRefused",
    "add_form_snapshot",
    "form_snapshot_id",
    "offset_for",
    "with_snapshot",
]

FORM_PREFIX: Final = "form-"
MAX_SNAPSHOTS: Final = 20
"""``Post.snapshots`` holds at most this many."""
MAX_COUNT: Final = 1_000_000_000
FUTURE_TOLERANCE: Final = timedelta(minutes=5)

type SnapshotOffset = Literal["h48", "d7", "other"]


class SnapshotRefused(ValueError):
    """The snapshot cannot be stored. ``code``/``status``/``detail`` become the problem."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


class MetricsSnapshotIn(ApiModel):
    """``POST /api/posts/{id}/snapshots``: what the owner read off LinkedIn's post analytics."""

    offset: SnapshotOffset
    impressions: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    reactions: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    comments: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    reposts: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    saves: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    sends: int | None = Field(default=None, ge=0, le=MAX_COUNT)
    taken_at: Timestamp | None = None
    """When the numbers were read (default: now)."""

    @model_validator(mode="after")
    def _some_number(self) -> Self:
        numbers = (
            self.impressions,
            self.reactions,
            self.comments,
            self.reposts,
            self.saves,
            self.sends,
        )
        if all(value is None for value in numbers):
            raise ValueError("enter at least one number")
        return self


def form_snapshot_id(offset: SnapshotOffset, taken_at: str) -> str:
    """``form-h48`` / ``form-d7`` (one each, corrected in place), ``form-<yyyymmddThhmmss>``
    for ``other``."""
    if offset != "other":
        return f"{FORM_PREFIX}{offset}"
    return f"{FORM_PREFIX}{parse_iso(taken_at):%Y%m%dt%H%M%S}"


def offset_for(posted_at: str | None, taken_at: str) -> SnapshotOffset:
    """The offset a snapshot taken at ``taken_at`` stands for: ``h48`` between 36 and 72 hours
    after going live, ``d7`` between 6 and 8 days, ``other`` otherwise."""
    if posted_at is None:
        return "other"
    age = parse_iso(taken_at) - parse_iso(posted_at)
    if timedelta(hours=36) <= age < timedelta(hours=72):
        return "h48"
    if timedelta(days=6) <= age < timedelta(days=8):
        return "d7"
    return "other"


def with_snapshot(
    post: Post, snapshot: MetricsSnapshot, *, now: str, evict_imports: bool = False
) -> Post:
    """A copy of ``post`` with ``snapshot`` added, replacing one with the same id.

    Snapshots stay ordered by ``takenAt``. When all :data:`MAX_SNAPSHOTS` places are taken,
    ``evict_imports`` drops the oldest import snapshot (imports are cumulative, so a newer one
    supersedes it); otherwise :class:`SnapshotRefused` (409 ``too_many_snapshots``).
    """
    kept = [item for item in post.snapshots if item.id != snapshot.id]
    if len(kept) >= MAX_SNAPSHOTS:
        imported = [item for item in kept if item.source == "import"]
        if not evict_imports or not imported:
            raise SnapshotRefused(409, "too_many_snapshots", "This post already has 20 snapshots.")
        oldest = min(imported, key=lambda item: (item.taken_at, item.id))
        kept = [item for item in kept if item.id != oldest.id]
    snapshots = sorted([*kept, snapshot], key=lambda item: (item.taken_at, item.id))
    return post.model_copy(update={"snapshots": snapshots, "updated_at": now})


def add_form_snapshot(post: Post, body: MetricsSnapshotIn, *, now: datetime) -> Post:
    """``post`` with the form snapshot added (raises :class:`SnapshotRefused`)."""
    if post.status is not PostStatus.POSTED:
        raise SnapshotRefused(409, "not_posted", "Add numbers once the post is live on LinkedIn.")
    stamp = to_iso(now)
    taken_at = body.taken_at or stamp
    if parse_iso(taken_at) > now + FUTURE_TOLERANCE:
        raise SnapshotRefused(422, "snapshot_in_future", "The time is in the future.")
    if post.posted_at is not None and taken_at < post.posted_at:
        raise SnapshotRefused(422, "snapshot_before_post", "The time is before the post went live.")
    snapshot = MetricsSnapshot(
        id=form_snapshot_id(body.offset, taken_at),
        taken_at=taken_at,
        offset=body.offset,
        impressions=body.impressions,
        reactions=body.reactions,
        comments=body.comments,
        reposts=body.reposts,
        saves=body.saves,
        sends=body.sends,
        source="form",
    )
    return with_snapshot(post, snapshot, now=stamp)
