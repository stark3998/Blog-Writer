"""The ``backups.dump`` job: dump every durable container, then write the manifest and status.

Steps:

``dump``
    One container at a time (:func:`~blogwriter.backups.dump.dump_containers`), checkpointing
    the finished files after each, so a resumed job continues where it stopped. The date is
    the input's ``date``, else the UTC date of the scheduled slot, else today (UTC); it is fixed
    in the first checkpoint.
``manifest``
    ``<date>/manifest.json`` (:class:`~blogwriter.domain.backups.BackupManifest`), then
    :class:`~blogwriter.domain.backups.BackupStatus` through ``update_singleton``
    (``lastSuccess``, ``consecutiveFailures = 0``) and an ``entity.changed`` event.

A failure (no storage: ``JobFailed("storage_unavailable")``; any other error on the job's last
attempt) records ``lastError`` and ``consecutiveFailures + 1`` on the status and raises the
out-of-band alert ``emit_alert("job_failed", job_id)``. Earlier attempts just let the runner
retry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Final

from pydantic import Field, JsonValue, ValidationError

from blogwriter.backups.dump import dump_containers, manifest_blob_name
from blogwriter.backups.settings import backups_settings
from blogwriter.core.logging import register_log_fields
from blogwriter.core.settings import CoreSettings
from blogwriter.domain.backups import BACKUP_CONTAINERS, BackupFile, BackupManifest, BackupStatus
from blogwriter.domain.base import LocalDate, Timestamp, parse_iso, to_iso
from blogwriter.domain.docstore import update_singleton
from blogwriter.domain.jobflow import JobDeferred, require_service, step_services
from blogwriter.domain.jobtypes import (
    BACKUPS_DUMP,
    ENTITY_CHANGED,
    JobInput,
    JsonPayload,
    entity_changed,
)
from blogwriter.domain.ports import Services
from blogwriter.jobs import ClaimLost, JobFailed, JobRegistry, Step, StepContext
from blogwriter.notify import emit_alert

__all__ = [
    "BackupDumpInput",
    "DumpProgress",
    "backup_run_job_id",
    "dump_step",
    "manifest_step",
    "register_jobs",
]

logger: Final = logging.getLogger(__name__)
register_log_fields("job_id", "documents", "files")

MANIFEST_CONTENT_TYPE: Final = "application/json"


class BackupDumpInput(JobInput):
    """``{date?}``: the day to write (``YYYY-MM-DD``); default: the scheduled slot's UTC date."""

    date: LocalDate | None = None


class DumpProgress(JsonPayload):
    """The ``dump`` step's checkpoint and output: the date and the files written so far."""

    date: LocalDate
    started_at: Timestamp
    files: list[BackupFile] = Field(default_factory=list[BackupFile], max_length=50)

    def done(self) -> set[str]:
        return {file.container for file in self.files}


def backup_run_job_id(date: str) -> str:
    """The job id of an owner-started backup for ``date`` (re-runs get ``-r<n>``)."""
    return f"backups-run-{date}"


def _input(value: JsonValue) -> BackupDumpInput:
    if value is None:
        return BackupDumpInput()
    try:
        return BackupDumpInput.from_json(value)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input", "The backup job input is not valid.") from None


def _job_date(ctx: StepContext, requested: str | None, now: datetime) -> str:
    if requested is not None:
        return requested
    if ctx.scheduled_for is not None:
        return parse_iso(ctx.scheduled_for).date().isoformat()
    return now.astimezone(UTC).date().isoformat()


def _app_sha() -> str | None:
    try:
        sha = CoreSettings().build_sha
    except (ValueError, ValidationError):
        return None
    return sha[:64] if sha else None


async def _final_attempt(services: Services, ctx: StepContext) -> bool:
    try:
        job = await services.jobs.get(ctx.job_id)
    except Exception:
        return True  # cannot tell: report it rather than stay silent
    return ctx.attempt >= job.max_attempts


async def _note_failure(services: Services, ctx: StepContext, code: str) -> None:
    """Alert out of band, then record the failure on the status (best effort)."""
    emit_alert("job_failed", ctx.job_id)
    now = to_iso(services.clock.now())

    def failed(status: BackupStatus) -> BackupStatus:
        return status.model_copy(
            update={
                "last_attempt_at": now,
                "last_error": code,
                "consecutive_failures": status.consecutive_failures + 1,
                "updated_at": now,
            }
        )

    try:
        await update_singleton(services.store, BackupStatus, failed, now=now)
    except Exception:
        logger.warning("backup_status_not_updated", extra={"job_id": ctx.job_id})


@asynccontextmanager
async def _failures_recorded(services: Services, ctx: StepContext) -> AsyncGenerator[None]:
    try:
        yield
    except (ClaimLost, JobDeferred):
        raise
    except JobFailed as exc:
        await _note_failure(services, ctx, exc.code)
        raise
    except Exception:
        if await _final_attempt(services, ctx):
            await _note_failure(services, ctx, "backup_failed")
        raise


async def dump_step(ctx: StepContext) -> JsonValue:
    """Dump the durable containers, one checkpoint per container."""
    services = step_services()
    async with _failures_recorded(services, ctx):
        blobs = require_service(services.blobs, "storage")
        params = _input(ctx.input)
        now = services.clock.now()
        progress: DumpProgress | None = None
        if ctx.output is not None:
            try:
                progress = DumpProgress.from_json(ctx.output)
            except (ValueError, ValidationError):
                progress = None
        if progress is None:
            progress = DumpProgress(date=_job_date(ctx, params.date, now), started_at=to_iso(now))
        page_size = backups_settings().page_size
        for container in BACKUP_CONTAINERS:
            if container in progress.done():
                continue
            files = await dump_containers(
                services.store,
                blobs,
                date=progress.date,
                now=services.clock.now(),
                containers=[container],
                page_size=page_size,
            )
            progress = progress.model_copy(update={"files": [*progress.files, *files]})
            await ctx.checkpoint(progress.to_json())
            await ctx.emit(
                "progress",
                {"done": len(progress.files), "total": len(BACKUP_CONTAINERS), "label": container},
            )
        documents = sum(file.documents for file in progress.files)
        logger.info(
            "backup_dumped",
            extra={"job_id": ctx.job_id, "files": len(progress.files), "documents": documents},
        )
        return progress.to_json()


async def manifest_step(ctx: StepContext) -> JsonValue:
    """Write ``<date>/manifest.json`` and record the success on the backup status."""
    services = step_services()
    async with _failures_recorded(services, ctx):
        blobs = require_service(services.blobs, "storage")
        try:
            progress = DumpProgress.from_json(ctx.outputs.get("dump"))
        except (ValueError, ValidationError):
            raise JobFailed("dump_missing", "The dump step left no result.") from None
        now = services.clock.now()
        manifest = BackupManifest(
            date=progress.date,
            started_at=progress.started_at,
            finished_at=to_iso(now),
            files=progress.files,
            app_sha=_app_sha(),
        )
        body = json.dumps(
            manifest.model_dump(mode="json", by_alias=True), sort_keys=True, indent=2
        ).encode("ascii")
        await blobs.put(
            "backups",
            manifest_blob_name(progress.date),
            body,
            content_type=MANIFEST_CONTENT_TYPE,
            overwrite=True,
        )
        stamp = to_iso(now)

        def succeeded(status: BackupStatus) -> BackupStatus:
            return status.model_copy(
                update={
                    "last_success": manifest,
                    "last_attempt_at": stamp,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "updated_at": stamp,
                }
            )

        await update_singleton(services.store, BackupStatus, succeeded, now=stamp)
        await ctx.emit(ENTITY_CHANGED, entity_changed("backup_status", BackupStatus.SINGLETON_ID))
        return {
            "date": progress.date,
            "files": len(progress.files),
            "documents": sum(file.documents for file in progress.files),
        }


def register_jobs(registry: JobRegistry) -> None:
    """Register ``backups.dump`` (steps ``dump`` then ``manifest``)."""
    registry.register(BACKUPS_DUMP, [Step("dump", dump_step), Step("manifest", manifest_step)])
