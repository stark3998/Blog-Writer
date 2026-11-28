"""Backups (work order W-A10): daily gzip JSONL dumps to Blob, a manifest, status for System and
owner-attested restore drills. The v1 migration CLI lives in :mod:`.migrate_v1` (import it
explicitly; it is not loaded with the app).

- :func:`dump_containers`, :func:`restore_drill_due` (:mod:`.dump`);
- :func:`register_jobs` (``backups.dump``: ``dump`` then ``manifest``) (:mod:`.jobs`);
- :func:`schedule_entries` (``sched-backups-dump``, daily at ``cadence.backup_time``);
- :func:`get_router` (``/api/backups``...).

Settings: :class:`BackupsSettings` (``BW_BACKUPS_PAGE_SIZE``).
"""

from __future__ import annotations

from blogwriter.backups.dump import (
    NEVER_DUMPED,
    dump_blob_name,
    dump_containers,
    manifest_blob_name,
    restore_drill_due,
)
from blogwriter.backups.jobs import (
    BackupDumpInput,
    DumpProgress,
    backup_run_job_id,
    register_jobs,
)
from blogwriter.backups.routes import BackupsView, get_router
from blogwriter.backups.schedule import BACKUP_SCHEDULE_ID, schedule_entries
from blogwriter.backups.settings import BackupsSettings, backups_settings

__all__ = [
    "BACKUP_SCHEDULE_ID",
    "NEVER_DUMPED",
    "BackupDumpInput",
    "BackupsSettings",
    "BackupsView",
    "DumpProgress",
    "backup_run_job_id",
    "backups_settings",
    "dump_blob_name",
    "dump_containers",
    "get_router",
    "manifest_blob_name",
    "register_jobs",
    "restore_drill_due",
    "schedule_entries",
]
