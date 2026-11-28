"""Backup routes for Settings > System: status and dumps, run now, download, restore drill.

- ``GET /api/backups``: :class:`BackupsView` (status, dumps of the last 35 days, drill due);
- ``POST /api/backups/run``: 202 ``JobAccepted`` (``backups.dump`` for today, origin owner);
- ``GET /api/backups/{date}/{container}``: the gzip JSON Lines dump as an attachment;
- ``POST /api/backups/restore-drill``: ``Versioned[BackupStatus]`` with
  ``lastRestoreDrillAt = now``.

Every route needs storage except the drill attestation (503 ``storage_unavailable``).
"""

from __future__ import annotations

import re
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Final

from fastapi import APIRouter, Response

from blogwriter.backups.dump import (
    DUMP_CONTENT_TYPE,
    dump_blob_name,
    restore_drill_due,
    valid_dump_date,
)
from blogwriter.backups.jobs import BackupDumpInput, backup_run_job_id
from blogwriter.core.errors import ProblemError
from blogwriter.domain.api import ApiModel, JobAccepted, Versioned
from blogwriter.domain.backups import BACKUP_CONTAINERS, BackupStatus
from blogwriter.domain.base import to_iso
from blogwriter.domain.deps import (
    BlobsDep,
    ClockDep,
    JobsDep,
    StoreDep,
    problems,
    versioned_singleton,
)
from blogwriter.domain.docstore import read_singleton, update_singleton
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import BACKUPS_DUMP
from blogwriter.domain.ports import BlobInfo, BlobNotFound

__all__ = [
    "DUMP_LIST_DAYS",
    "BackupDumpFileView",
    "BackupDumpView",
    "BackupsView",
    "download_dump",
    "get_backups",
    "get_router",
    "recent_dumps",
    "record_restore_drill",
    "run_backup",
]

DUMP_LIST_DAYS: Final = 35
"""Dumps older than this are deleted by the Blob lifecycle rule and not listed."""
_DUMP_FILE_RE: Final = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})/(?:(?P<container>[a-z0-9-]+)\.jsonl\.gz|manifest\.json)$"
)


class BackupDumpFileView(ApiModel):
    container: str
    blob: str
    size_bytes: int
    sha256: str


class BackupDumpView(ApiModel):
    date: str
    complete: bool
    """The day's ``manifest.json`` exists (the dump finished)."""
    files: list[BackupDumpFileView]


class BackupsView(ApiModel):
    status: Versioned[BackupStatus]
    dumps: list[BackupDumpView]
    restore_drill_due: bool


@contextmanager
def _storage_problems() -> Generator[None]:
    """A Blob failure as 503 ``storage_unavailable`` (no SDK text reaches the client)."""
    try:
        yield
    except (RuntimeError, OSError):
        raise ProblemError(
            503,
            code="storage_unavailable",
            detail="Storage is not reachable right now.",
            headers={"Retry-After": "5"},
        ) from None


def recent_dumps(
    infos: Sequence[BlobInfo], *, now: datetime, days: int = DUMP_LIST_DAYS
) -> list[BackupDumpView]:
    """Group dump blobs by date (newest first), within the last ``days`` days of ``now``."""
    cutoff = (now.astimezone(UTC).date() - timedelta(days=days)).isoformat()
    files: dict[str, list[BackupDumpFileView]] = {}
    complete: set[str] = set()
    for info in infos:
        match = _DUMP_FILE_RE.fullmatch(info.name)
        if match is None or not valid_dump_date(match["date"]) or match["date"] < cutoff:
            continue
        date = match["date"]
        files.setdefault(date, [])
        container = match["container"]
        if container is None:
            complete.add(date)
        else:
            files[date].append(
                BackupDumpFileView(
                    container=container,
                    blob=info.name,
                    size_bytes=info.size_bytes,
                    sha256=info.sha256,
                )
            )
    return [
        BackupDumpView(
            date=date,
            complete=date in complete,
            files=sorted(files[date], key=lambda file: file.container),
        )
        for date in sorted(files, reverse=True)
    ]


async def get_backups(store: StoreDep, blobs: BlobsDep, clock: ClockDep) -> BackupsView:
    """The backup status, the dumps of the last 35 days and whether a restore drill is due."""
    now = clock.now()
    with problems():
        status, etag = await read_singleton(store, BackupStatus, now=to_iso(now))
    with _storage_problems():
        infos = await blobs.list_blobs("backups")
    return BackupsView(
        status=versioned_singleton(status, etag),
        dumps=recent_dumps(infos, now=now),
        restore_drill_due=restore_drill_due(status, now=now),
    )


async def run_backup(
    jobs: JobsDep, clock: ClockDep, blobs: BlobsDep, response: Response
) -> JobAccepted:
    """Start a backup of today (UTC) now; a second click while it runs returns the same job."""
    del blobs  # required: without storage the job could only fail
    date = clock.now().astimezone(UTC).date().isoformat()
    payload = BackupDumpInput(origin="owner", date=date).to_json()
    with problems():
        try:
            job = await enqueue_once(
                jobs, BACKUPS_DUMP, payload, job_id=backup_run_job_id(date), on_conflict="rerun"
            )
        except ValueError:
            raise ProblemError(
                503,
                code="jobs_unavailable",
                detail="Backups cannot run right now.",
                headers={"Retry-After": "5"},
            ) from None
    response.status_code = 202
    return JobAccepted(job_id=job.id, status=job.status)


async def download_dump(date: str, container: str, blobs: BlobsDep) -> Response:
    """One day's dump of one container (gzip JSON Lines) as a download."""
    if not valid_dump_date(date) or container not in BACKUP_CONTAINERS:
        raise ProblemError(404, code="not_found")
    with _storage_problems():
        try:
            data = await blobs.get("backups", dump_blob_name(date, container))
        except BlobNotFound:
            raise ProblemError(404, code="not_found") from None
    return Response(
        content=data,
        media_type=DUMP_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="backup-{date}-{container}.jsonl.gz"',
            "Cache-Control": "no-store",
        },
    )


async def record_restore_drill(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[BackupStatus]:
    """The owner attests that the monthly restore drill (``RESTORE.md``) was done now."""
    now = to_iso(clock.now())

    def drilled(status: BackupStatus) -> BackupStatus:
        return status.model_copy(update={"last_restore_drill_at": now, "updated_at": now})

    with problems():
        status, etag = await update_singleton(store, BackupStatus, drilled, now=now)
    return versioned_singleton(status, etag, response)


def get_router() -> APIRouter:
    """The four backup routes."""
    router = APIRouter(tags=["backups"])
    router.add_api_route(
        "/api/backups",
        get_backups,
        methods=["GET"],
        response_model=BackupsView,
        summary="Backup status and recent dumps",
    )
    router.add_api_route(
        "/api/backups/run",
        run_backup,
        methods=["POST"],
        status_code=202,
        response_model=JobAccepted,
        summary="Back up now",
    )
    router.add_api_route(
        "/api/backups/restore-drill",
        record_restore_drill,
        methods=["POST"],
        response_model=Versioned[BackupStatus],
        summary="Record a restore drill",
    )
    router.add_api_route(
        "/api/backups/{date}/{container}",
        download_dump,
        methods=["GET"],
        response_class=Response,
        summary="Download one dump file",
    )
    return router
