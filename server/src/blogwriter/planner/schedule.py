"""Schedule entries of the planner (seeded by the integrator, contract I-4 and section 8).

All times are local to ``cadence.timezone`` (``America/New_York`` by default; the scheduler's
DST rule: a time in the spring-forward gap fires at the same offset after it, a repeated
fall-back time fires once at its first occurrence).

``sched-planner-plan-week``
    ``planner.plan-week`` weekly at ``cadence.plan_week`` minus ``plan_lead_minutes`` (Sunday
    18:00 - 60 min = Sunday 17:00), so the plan is ready when the owner sits down; the day
    shifts when the lead crosses midnight. Max lateness 6 h.
``sched-planner-veto-notice``
    ``planner.veto-notice`` weekly at ``cadence.veto_notice`` (Friday 12:00, at least
    ``min_veto_lead_hours`` before Monday's 09:00 publish). Max lateness 4 h.
``sched-planner-scorecard``
    ``planner.scorecard`` every Monday 07:00. Max lateness 24 h.

Every job type here is in ``domain.jobtypes.SCHEDULED_JOB_TYPES`` and none generates content.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from blogwriter.domain.jobtypes import PLANNER_PLAN_WEEK, PLANNER_SCORECARD, PLANNER_VETO_NOTICE
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings, WeeklyTime
from blogwriter.jobs.scheduler import weekly
from blogwriter.planner.slots import shift_weekly

__all__ = [
    "PLAN_WEEK_MAX_LATENESS",
    "PLAN_WEEK_SCHEDULE_ID",
    "SCORECARD_MAX_LATENESS",
    "SCORECARD_SCHEDULE_ID",
    "SCORECARD_TIME",
    "VETO_NOTICE_MAX_LATENESS",
    "VETO_NOTICE_SCHEDULE_ID",
    "plan_week_time",
    "schedule_entries",
]

PLAN_WEEK_SCHEDULE_ID: Final = "sched-planner-plan-week"
VETO_NOTICE_SCHEDULE_ID: Final = "sched-planner-veto-notice"
SCORECARD_SCHEDULE_ID: Final = "sched-planner-scorecard"
PLAN_WEEK_MAX_LATENESS: Final = timedelta(hours=6)
VETO_NOTICE_MAX_LATENESS: Final = timedelta(hours=4)
SCORECARD_MAX_LATENESS: Final = timedelta(hours=24)
SCORECARD_TIME: Final = WeeklyTime(day="mon", time="07:00")


def plan_week_time(cadence: CadenceSettings) -> WeeklyTime:
    """``cadence.plan_week`` minus ``plan_lead_minutes`` (the day shifts across midnight)."""
    return shift_weekly(cadence.plan_week, -cadence.plan_lead_minutes)


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """The planner's schedule for ``cadence``: pure, deterministic, no I/O."""
    plan_at = plan_week_time(cadence)
    return [
        ScheduleEntry(
            id=PLAN_WEEK_SCHEDULE_ID,
            name="Plan next week",
            job_type=PLANNER_PLAN_WEEK,
            rule=weekly([plan_at.day], plan_at.time),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=PLAN_WEEK_MAX_LATENESS,
        ),
        ScheduleEntry(
            id=VETO_NOTICE_SCHEDULE_ID,
            name="Veto notice for auto-published articles",
            job_type=PLANNER_VETO_NOTICE,
            rule=weekly([cadence.veto_notice.day], cadence.veto_notice.time),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=VETO_NOTICE_MAX_LATENESS,
        ),
        ScheduleEntry(
            id=SCORECARD_SCHEDULE_ID,
            name="Weekly scorecard",
            job_type=PLANNER_SCORECARD,
            rule=weekly([SCORECARD_TIME.day], SCORECARD_TIME.time),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=SCORECARD_MAX_LATENESS,
        ),
    ]
