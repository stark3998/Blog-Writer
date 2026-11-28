"""Schedule entries of the signals package (seeded by the integrator, I-4).

``sched-signals-crawl``: ``signals.crawl`` every ``cadence.crawl_interval_hours`` (6 h by
default), maximum lateness 6 h. ``sched-signals-scout``: ``signals.scout`` weekly at
``cadence.scout`` (Sunday 15:00 by default), maximum lateness 12 h. Both job types are in
``domain.jobtypes.SCHEDULED_JOB_TYPES`` and neither generates content.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import SIGNALS_CRAWL, SIGNALS_SCOUT
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.scheduler import every, weekly

__all__ = [
    "CRAWL_MAX_LATENESS",
    "CRAWL_SCHEDULE_ID",
    "SCOUT_MAX_LATENESS",
    "SCOUT_SCHEDULE_ID",
    "schedule_entries",
]

CRAWL_SCHEDULE_ID: Final = "sched-signals-crawl"
SCOUT_SCHEDULE_ID: Final = "sched-signals-scout"
CRAWL_MAX_LATENESS: Final = timedelta(hours=6)
SCOUT_MAX_LATENESS: Final = timedelta(hours=12)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """The signals schedule for ``cadence``: pure, deterministic, no I/O."""
    return [
        ScheduleEntry(
            id=CRAWL_SCHEDULE_ID,
            name="Crawl feeds and triage new items",
            job_type=SIGNALS_CRAWL,
            rule=every(cadence.crawl_interval_hours * 3600),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=CRAWL_MAX_LATENESS,
        ),
        ScheduleEntry(
            id=SCOUT_SCHEDULE_ID,
            name="Weekly scouting per pillar",
            job_type=SIGNALS_SCOUT,
            rule=weekly([cadence.scout.day], cadence.scout.time),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=SCOUT_MAX_LATENESS,
        ),
    ]
