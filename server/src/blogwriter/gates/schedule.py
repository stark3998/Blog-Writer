"""Schedule entries of the gates: the weekly standards catalog refresh."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import GATES_STANDARDS_REFRESH
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.scheduler import weekly

__all__ = ["STANDARDS_SCHEDULE_ID", "schedule_entries"]

STANDARDS_SCHEDULE_ID: Final = "sched-gates-standards"


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-gates-standards``: ``gates.standards-refresh`` on Saturdays at 05:00 local time,
    48 h maximum lateness. Pure and deterministic."""
    return [
        ScheduleEntry(
            id=STANDARDS_SCHEDULE_ID,
            name="Refresh the standards catalog",
            job_type=GATES_STANDARDS_REFRESH,
            rule=weekly(["sat"], "05:00"),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=timedelta(hours=48),
        )
    ]
