"""Schedule entries of the engagement package: the 6-hourly purge of other members' text."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import ENGAGEMENT_PURGE
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.scheduler import every

__all__ = [
    "PURGE_INTERVAL_SECONDS",
    "PURGE_MAX_LATENESS",
    "PURGE_SCHEDULE_ID",
    "schedule_entries",
]

PURGE_SCHEDULE_ID: Final = "sched-engagement-purge"
PURGE_INTERVAL_SECONDS: Final = 6 * 3600
PURGE_MAX_LATENESS: Final = timedelta(hours=6)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-engagement-purge``: ``engagement.purge`` every 6 hours (``every`` takes seconds),
    6 h maximum lateness. Pure and deterministic; the job generates no content."""
    return [
        ScheduleEntry(
            id=PURGE_SCHEDULE_ID,
            name="Delete other members' text older than 48 hours",
            job_type=ENGAGEMENT_PURGE,
            rule=every(PURGE_INTERVAL_SECONDS),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=PURGE_MAX_LATENESS,
        )
    ]
