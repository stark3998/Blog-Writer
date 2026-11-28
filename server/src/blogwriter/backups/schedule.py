"""The backups schedule item (contract section 8): one daily dump at ``cadence.backup_time``."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import BACKUPS_DUMP
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import daily

__all__ = ["BACKUP_MAX_LATENESS", "BACKUP_SCHEDULE_ID", "schedule_entries"]

BACKUP_SCHEDULE_ID: Final = "sched-backups-dump"
BACKUP_MAX_LATENESS: Final = timedelta(hours=12)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-backups-dump``: ``daily(cadence.backup_time)`` in the cadence time zone (pure)."""
    return [
        ScheduleEntry(
            id=BACKUP_SCHEDULE_ID,
            name="Daily backup of the durable containers",
            job_type=BACKUPS_DUMP,
            rule=daily(cadence.backup_time),
            timezone=cadence.timezone,
            job_input=None,
            max_lateness=BACKUP_MAX_LATENESS,
        )
    ]
