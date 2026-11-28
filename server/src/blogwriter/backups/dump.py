"""Daily dumps of the durable containers to Blob ``backups``: gzip JSONL, one file per container.

Format of ``<YYYY-MM-DD>/<container>.jsonl.gz`` (what ``RESTORE.md`` reads back):

- one JSON object per line: the stored document body exactly as the store returns it (no
  etags or Cosmos system properties), keys sorted, ASCII-only escapes, ``\\n`` line ends;
- lines sorted by ``id`` (ids are unique per container, ``Signal`` ids included);
- gzip with ``mtime=0`` and no file name, so the same documents give the same bytes;
- ``engagement`` holds only ``engagement_record`` and ``watch_entry`` documents
  (:data:`~blogwriter.domain.backups.BACKUP_KINDS`): other members' pasted text and
  reminders are never backed up. ``jobs`` and ``ephemeral`` are never dumped.

Containers are read in keyset pages (``Filter("id", ">", last)`` + ``OrderBy("id")``), so a page
boundary never skips or repeats a document. JSON encoding and compression run in
``asyncio.to_thread``; only the compressed bytes stay in memory.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date as calendar_date
from datetime import datetime, timedelta
from typing import Any, Final

from blogwriter.db import CONTAINERS, Filter, OrderBy, Store
from blogwriter.domain.backups import BACKUP_CONTAINERS, BACKUP_KINDS, BackupFile, BackupStatus
from blogwriter.domain.base import parse_iso
from blogwriter.domain.ports import BlobStore

__all__ = [
    "DUMP_CONTENT_TYPE",
    "NEVER_DUMPED",
    "RESTORE_DRILL_INTERVAL",
    "dump_blob_name",
    "dump_containers",
    "encode_lines",
    "manifest_blob_name",
    "restore_drill_due",
    "valid_dump_date",
]

DUMP_CONTENT_TYPE: Final = "application/gzip"
NEVER_DUMPED: Final = frozenset({"jobs", "ephemeral"})
"""Transient work and short-lived secrets (OAuth states): never in a backup."""
RESTORE_DRILL_INTERVAL: Final = timedelta(days=31)
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_dump_date(value: str) -> bool:
    """Whether ``value`` is a real calendar date written ``YYYY-MM-DD``."""
    if not _DATE_RE.fullmatch(value):
        return False
    try:
        calendar_date.fromisoformat(value)
    except ValueError:
        return False
    return True


def dump_blob_name(date: str, container: str) -> str:
    """``<date>/<container>.jsonl.gz`` in the ``backups`` container."""
    return f"{date}/{container}.jsonl.gz"


def manifest_blob_name(date: str) -> str:
    """``<date>/manifest.json`` in the ``backups`` container."""
    return f"{date}/manifest.json"


def encode_lines(bodies: Sequence[Mapping[str, Any]]) -> bytes:
    """JSON Lines: keys sorted, ASCII escapes, one ``\\n``-terminated object per body."""
    return b"".join(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        + b"\n"
        for body in bodies
    )


class _GzipJsonl:
    """A gzip stream of JSON Lines in memory (deterministic: ``mtime=0``, no file name)."""

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._gzip = gzip.GzipFile(filename="", mode="wb", fileobj=self._buffer, mtime=0)

    def write(self, bodies: Sequence[Mapping[str, Any]]) -> None:
        self._gzip.write(encode_lines(bodies))

    def finish(self) -> bytes:
        self._gzip.close()
        return self._buffer.getvalue()


def _check_containers(containers: Sequence[str]) -> None:
    for container in containers:
        if container in NEVER_DUMPED:
            raise ValueError(f"{container} is never backed up")
        if container not in CONTAINERS:
            raise ValueError(f"unknown container {container!r}")


async def _dump_one(
    store: Store, blobs: BlobStore, container: str, *, date: str, page_size: int
) -> BackupFile:
    kinds = BACKUP_KINDS.get(container)
    stream = _GzipJsonl()
    last: str | None = None
    documents = 0
    while True:
        filters: list[Filter] = []
        if kinds is not None:
            filters.append(Filter("kind", "in", sorted(kinds)))
        if last is not None:
            filters.append(Filter("id", ">", last))
        page = await store.find(container, filters, order_by=OrderBy("id"), limit=page_size)
        if not page:
            break
        await asyncio.to_thread(stream.write, [doc.body for doc in page])
        documents += len(page)
        last = page[-1].id
        if len(page) < page_size:
            break
    data = await asyncio.to_thread(stream.finish)
    name = dump_blob_name(date, container)
    await blobs.put("backups", name, data, content_type=DUMP_CONTENT_TYPE, overwrite=True)
    return BackupFile(
        container=container,
        blob=name,
        documents=documents,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


async def dump_containers(
    store: Store,
    blobs: BlobStore,
    *,
    date: str,
    now: datetime,
    containers: Sequence[str] = BACKUP_CONTAINERS,
    page_size: int = 200,
    done: Collection[str] = (),
) -> list[BackupFile]:
    """Dump each container (except those in ``done``) to ``<date>/<container>.jsonl.gz``.

    Blobs are overwritten, so a re-run the same day replaces that day's files. Returns one
    :class:`~blogwriter.domain.backups.BackupFile` per dumped container, in order; its
    ``sha256`` is the hash of the gzip bytes as stored. ``now`` must be timezone-aware (the
    caller's clock; the files themselves carry no time).
    """
    if not valid_dump_date(date):
        raise ValueError("date must be YYYY-MM-DD")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    _check_containers(containers)
    files: list[BackupFile] = []
    for container in containers:
        if container in done:
            continue
        files.append(await _dump_one(store, blobs, container, date=date, page_size=page_size))
    return files


def restore_drill_due(status: BackupStatus, *, now: datetime) -> bool:
    """Whether the owner should run the restore drill: never done, or more than 31 days ago."""
    if status.last_restore_drill_at is None:
        return True
    return now - parse_iso(status.last_restore_drill_at) > RESTORE_DRILL_INTERVAL
