"""Job registration and schedule entries of the research package.

``research.run`` (input ``domain.jobtypes.ResearchRunInput``, id ``research_job_id(runId)``) runs
the steps in :mod:`blogwriter.research.run`. Research has no schedule: runs start from the owner
(the route), the pipeline or an eval, always as a chained or owner action.
"""

from __future__ import annotations

from blogwriter.domain.jobtypes import RESEARCH_RUN
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import JobRegistry, Step
from blogwriter.research.run import (
    audit_step,
    brief_step,
    extract_step,
    plan_step,
    search_step,
    verify_step,
)

__all__ = ["register_jobs", "schedule_entries"]


def register_jobs(registry: JobRegistry) -> None:
    """Register ``research.run`` (plan, search, extract, verify, brief, audit)."""
    registry.register(
        RESEARCH_RUN,
        [
            Step("plan", plan_step),
            Step("search", search_step),
            Step("extract", extract_step),
            Step("verify", verify_step),
            Step("brief", brief_step),
            Step("audit", audit_step),
        ],
    )


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """No schedule items: research never starts on its own."""
    return []
