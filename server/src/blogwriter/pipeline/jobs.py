"""Job registration and schedule entries of the pipeline.

- ``pipeline.article`` (``PipelineInput``, id ``pipeline_job_id(articleId, n)``): research,
  interview_questions, interview, outline, draft, normalize, critique_revise, gates, assets,
  finalize (:mod:`blogwriter.pipeline.steps`).
- ``pipeline.revise`` (``ReviseInput``, random id): revise, normalize, gates, finalize.
- ``pipeline.interview-expire`` (``InterviewInput``, id ``interview_expire_job_id(interviewId)``,
  ``run_after = dueAt``): expire.

No schedule: content work starts from owner actions only (the pipeline route, plan approval,
an eval run). Every pipeline job type generates content (``domain.jobtypes.generates_content``)
and none is in ``SCHEDULED_JOB_TYPES``.
"""

from __future__ import annotations

from pydantic import JsonValue, ValidationError

from blogwriter.domain.jobflow import step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    PIPELINE_ARTICLE,
    PIPELINE_INTERVIEW_EXPIRE,
    PIPELINE_REVISE,
    InterviewInput,
    entity_changed,
)
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext
from blogwriter.pipeline.interviews import expire_interview
from blogwriter.pipeline.steps import article_steps, revise_steps

__all__ = ["expire_step", "register_jobs", "schedule_entries"]


async def expire_step(ctx: StepContext) -> JsonValue:
    """At the interview's ``dueAt``: a pending interview expires and the waiting pipeline goes
    on without answers. Runs whenever it is claimed: lateness is what it handles."""
    try:
        payload = InterviewInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None
    services = step_services()
    interview = await expire_interview(services, payload.interview_id, now=services.clock.now())
    if interview is None:
        return {"interviewId": payload.interview_id, "skipped": "missing"}
    await ctx.emit(ENTITY_CHANGED, entity_changed("interview", interview.id))
    return {"interviewId": interview.id, "status": interview.status.value}


def register_jobs(registry: JobRegistry) -> None:
    """Register ``pipeline.article``, ``pipeline.revise`` and ``pipeline.interview-expire``."""
    registry.register(PIPELINE_ARTICLE, [Step(name, run) for name, run in article_steps()])
    registry.register(PIPELINE_REVISE, [Step(name, run) for name, run in revise_steps()])
    registry.register(PIPELINE_INTERVIEW_EXPIRE, [Step("expire", expire_step)])


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """No schedule items: pipelines start from owner actions only."""
    return []
