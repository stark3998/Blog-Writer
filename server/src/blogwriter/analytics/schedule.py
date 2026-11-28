"""The analytics schedule entry (contract section 8): weekly insight suggestions.

``sched-analytics-suggest`` runs ``analytics.suggest`` every Monday at 07:30 in the cadence time
zone as background work (paused autonomy skips it), with a maximum lateness of 24 hours. It never
generates content: suggestions are proposals the owner decides on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import ANALYTICS_SUGGEST, JobInput
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.scheduler import weekly

__all__ = ["SUGGEST_AT", "SUGGEST_MAX_LATENESS", "SUGGEST_SCHEDULE_ID", "schedule_entries"]

SUGGEST_SCHEDULE_ID: Final = "sched-analytics-suggest"
SUGGEST_AT: Final = "07:30"
SUGGEST_MAX_LATENESS: Final = timedelta(hours=24)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """``sched-analytics-suggest`` (pure and deterministic; no I/O)."""
    return [
        ScheduleEntry(
            id=SUGGEST_SCHEDULE_ID,
            name="Insight suggestions",
            job_type=ANALYTICS_SUGGEST,
            rule=weekly(["mon"], SUGGEST_AT),
            timezone=cadence.timezone,
            job_input=JobInput(origin="background").to_json(),
            max_lateness=SUGGEST_MAX_LATENESS,
        )
    ]
