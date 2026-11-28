"""Test helpers for the planner (no network, no real time). Never used in production.

- :func:`seed_policy` saves the blocklist and the employer policy PLAN needs.
- :func:`story`, :func:`pov`, :func:`idea`, :func:`post`, :func:`article` build documents.
- :func:`run_job` runs a planner job to the end on a real ``JobRunner`` with the services
  installed (steps call ``current_services()``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from blogwriter.db import NotFound
from blogwriter.domain.articles import Article, ArticleMeta, PublishState
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, upsert_doc
from blogwriter.domain.planner import FlagshipPlan, PlannedSlot, WeekPlan
from blogwriter.domain.ports import services_installed
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import (
    ArticleStatus,
    IdeaStatus,
    PlanStatus,
    PostStatus,
    ReviewStatus,
)
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.domain.weeks import plan_id
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.planner.jobs import register_jobs
from blogwriter.planner.plan import SlotRefs, with_refs

__all__ = [
    "NEW_YORK",
    "T0",
    "StepError",
    "article",
    "enqueue_job",
    "idea",
    "planned_slot",
    "planner_registry",
    "post",
    "pov",
    "run_job",
    "run_until_settled",
    "save_plan",
    "seed_policy",
    "story",
]

NEW_YORK: Final = "America/New_York"
T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
"""Thursday 2026-11-26 07:00 in New York (the sample documents' time)."""


class StepError(AssertionError):
    """A job step raised an unexpected exception (the runner would retry it)."""


async def seed_policy(
    services: FakeServices,
    *,
    ai_use_confirmed: bool = True,
    blocklist_configured: bool = True,
    terms: Sequence[str] = (),
) -> None:
    """Save the blocklist (``terms`` as whole-word client names) and the employer policy."""
    blocklist = Blocklist(
        id=Blocklist.SINGLETON_ID,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        configured=blocklist_configured,
        terms=[
            BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
            for index, term in enumerate(terms)
        ],
    )
    policy = EmployerPolicy(
        id=EmployerPolicy.SINGLETON_ID,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        ai_use_confirmed=ai_use_confirmed,
        ai_use_confirmed_at=SAMPLE_NOW if ai_use_confirmed else None,
        configured=True,
    )
    await upsert_doc(services.store, blocklist)
    await upsert_doc(services.store, policy)


async def story(
    services: FakeServices, title: str, pillars: Sequence[str], **fields: object
) -> StoryEntry:
    doc = sample_document(
        StoryEntry,
        title=title,
        pillars=list(pillars),
        status=ReviewStatus.APPROVED,
        classifier={"passed": True, "checkedAt": SAMPLE_NOW},
        **fields,
    )
    return (await create_doc(services.store, doc)).doc


async def pov(services: FakeServices, statement: str, pillar: str, **fields: object) -> PovEntry:
    doc = sample_document(
        PovEntry,
        statement=statement,
        pillar=pillar,
        status=ReviewStatus.APPROVED,
        classifier={"passed": True, "checkedAt": SAMPLE_NOW},
        **fields,
    )
    return (await create_doc(services.store, doc)).doc


async def idea(services: FakeServices, title: str, **fields: object) -> Idea:
    values: dict[str, object] = {"title": title, "origin": "owner", "status": IdeaStatus.ACCEPTED}
    values.update(fields)
    return (await create_doc(services.store, sample_document(Idea, **values))).doc


async def post(
    services: FakeServices,
    *,
    status: PostStatus = PostStatus.APPROVED,
    slot: Slot | None = None,
    lane: str = "native",
    pillar: str = "agents",
    **fields: object,
) -> Post:
    doc = sample_document(
        Post,
        status=status,
        slot=slot,
        lane=lane,
        pillar=pillar,
        format="text",
        **fields,
    )
    return (await create_doc(services.store, doc)).doc


async def article(
    services: FakeServices,
    *,
    status: ArticleStatus = ArticleStatus.APPROVED,
    title: str = "Agent identity in MCP servers",
    pillar: str = "agents",
    publish_at: str | None = None,
    auto: bool = False,
    **fields: object,
) -> Article:
    meta = ArticleMeta(title=title, pillar=pillar, format="deep-explainer")
    publish = PublishState(publish_at=publish_at, auto=auto)
    doc = sample_document(Article, status=status, meta=meta, publish=publish, version=1, **fields)
    return (await create_doc(services.store, doc)).doc


def planner_registry() -> JobRegistry:
    registry = JobRegistry()
    register_jobs(registry)
    return registry


async def enqueue_job(
    services: FakeServices,
    job_type: str,
    job_input: JsonValue = None,
    *,
    job_id: str,
    scheduled_for: datetime | None = None,
    schedule_id: str | None = None,
) -> Job:
    """Enqueue a planner job with its steps (``scheduled_for`` makes it a schedule run)."""
    definition = planner_registry().get(job_type)
    return await enqueue(
        services.store,
        job_type,
        job_input,
        job_id=job_id,
        steps=definition.step_names if definition is not None else (),
        schedule_id=schedule_id if scheduled_for is not None else None,
        scheduled_for=scheduled_for,
        clock=services.clock,
    )


async def run_until_settled(services: FakeServices, job_id: str, *, within: float = 10.0) -> Job:
    """Run the planner job types on a real ``JobRunner`` until ``job_id`` ends.

    Raises :class:`StepError` when a step raised an unexpected exception (the runner would
    have retried it later) instead of waiting for the retry.
    """
    runner = JobRunner(
        services.store, planner_registry(), settings=fast_settings(), clock=services.clock
    )

    async def settled() -> bool:
        try:
            job = await services.jobs.get(job_id)
        except NotFound:
            return False
        return job.is_terminal or job.error is not None

    with services_installed(services):
        await runner.start()
        try:
            await eventually(settled, within=within)
        finally:
            await runner.stop()
    job = await services.jobs.get(job_id)
    if not job.is_terminal and job.error is not None:
        raise StepError(f"step {job.error.step} raised {job.error.error_type}")
    return job


async def run_job(
    services: FakeServices,
    job_type: str,
    job_input: Mapping[str, JsonValue] | None = None,
    *,
    job_id: str,
    scheduled_for: datetime | None = None,
    schedule_id: str | None = None,
    within: float = 10.0,
) -> Job:
    """:func:`enqueue_job` then :func:`run_until_settled`."""
    payload: JsonValue = dict(job_input) if job_input is not None else {}
    await enqueue_job(
        services,
        job_type,
        payload,
        job_id=job_id,
        scheduled_for=scheduled_for,
        schedule_id=schedule_id or "sched-planner-test",
    )
    return await run_until_settled(services, job_id, within=within)


def planned_slot(
    slot_id: str,
    local_date: str,
    local_time: str = "10:00",
    *,
    lane: str | None = "native",
    pillar: str | None = "identity",
    format: str | None = "text",
    refs: SlotRefs | None = None,
    post_id: str | None = None,
    zone: str = NEW_YORK,
) -> PlannedSlot:
    """A planned slot at a local date and time (``utc`` computed)."""
    slot = PlannedSlot.model_validate(
        {
            "id": slot_id,
            "slot": Slot.at(local_date, local_time, zone),
            "lane": lane,
            "pillar": pillar,
            "format": format,
            "title": f"Working title {slot_id}",
            "postId": post_id,
        }
    )
    return with_refs(slot, refs) if refs is not None else slot


async def save_plan(
    services: FakeServices,
    week_start: str,
    slots: Sequence[PlannedSlot] = (),
    *,
    status: PlanStatus = PlanStatus.PROPOSED,
    mode: str = "normal",
    flagship: FlagshipPlan | None = None,
    job_id: str | None = None,
) -> Stored[WeekPlan]:
    """Store a week plan (``plan-<weekStart>``)."""
    plan = sample_document(
        WeekPlan,
        id=plan_id(week_start),
        week_start=week_start,
        slots=list(slots),
        status=status,
        mode=mode,
        flagship=flagship,
        job_id=job_id,
    )
    return await create_doc(services.store, plan)


def iso(moment: datetime) -> str:
    return to_iso(moment)
