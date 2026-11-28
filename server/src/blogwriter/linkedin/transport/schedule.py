"""The transport's schedule item (contract section 8): daily token introspection at 08:30."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import LINKEDIN_INTROSPECT
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import daily

__all__ = [
    "INTROSPECT_MAX_LATENESS",
    "INTROSPECT_SCHEDULE_ID",
    "INTROSPECT_TIME",
    "schedule_entries",
]

INTROSPECT_SCHEDULE_ID: Final = "sched-linkedin-introspect"
INTROSPECT_TIME: Final = "08:30"
INTROSPECT_MAX_LATENESS: Final = timedelta(hours=24)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-linkedin-introspect``: ``daily("08:30")`` in the cadence time zone (pure)."""
    return [
        ScheduleEntry(
            id=INTROSPECT_SCHEDULE_ID,
            name="Daily LinkedIn token check and alerts",
            job_type=LINKEDIN_INTROSPECT,
            rule=daily(INTROSPECT_TIME),
            timezone=cadence.timezone,
            job_input=None,
            max_lateness=INTROSPECT_MAX_LATENESS,
        )
    ]
