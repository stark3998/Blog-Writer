"""Backup bookkeeping (owner: backups): the daily JSONL dumps to Blob ``backups``.

Each daily dump writes ``<YYYY-MM-DD>/<container>.jsonl.gz`` and ``<YYYY-MM-DD>/manifest.json``
(:class:`BackupManifest`). :class:`BackupStatus` (in ``settings``) keeps the last outcome for
the System page and the restore-drill reminder.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar, Final

from pydantic import Field

from blogwriter.domain.base import (
    Code,
    DomainModel,
    LocalDate,
    Sha256Hex,
    SingletonDocument,
    Timestamp,
)

__all__ = [
    "BACKUP_CONTAINERS",
    "BACKUP_KINDS",
    "BackupFile",
    "BackupManifest",
    "BackupStatus",
]

BACKUP_CONTAINERS: Final = (
    "settings",
    "stories",
    "ideas",
    "research",
    "articles",
    "article-versions",
    "posts",
    "sources",
    "signals",
    "usage",
    "engagement",
    "published-blogs",
    "published-blogs-staging",
)
"""The containers with durable data. Not dumped: ``jobs`` (transient work) and ``ephemeral``.
``engagement`` is dumped for its durable kinds only (:data:`BACKUP_KINDS`)."""

BACKUP_KINDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {"engagement": frozenset({"engagement_record", "watch_entry"})}
)
"""Containers dumped only for these kinds (``Filter("kind", "in", ...)``): other members'
48-hour text and reminders are never backed up."""


class BackupFile(DomainModel):
    container: str = Field(min_length=1, max_length=64)
    blob: str = Field(min_length=1, max_length=300)
    documents: int = Field(ge=0)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex


class BackupManifest(DomainModel):
    date: LocalDate
    started_at: Timestamp
    finished_at: Timestamp
    files: list[BackupFile] = Field(default_factory=list[BackupFile], max_length=50)
    app_sha: str | None = Field(default=None, max_length=64)


class BackupStatus(SingletonDocument):
    KIND: ClassVar[str] = "backup_status"
    SINGLETON_ID: ClassVar[str] = "backup-status"
    OWNER: ClassVar[str] = "backups"

    last_success: BackupManifest | None = None
    last_attempt_at: Timestamp | None = None
    last_error: Code | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    last_restore_drill_at: Timestamp | None = None
    """Set by the owner after the monthly restore drill (``RESTORE.md``)."""
