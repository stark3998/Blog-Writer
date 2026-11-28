"""Test helpers for the analytics package (no network, no real time). Never used in production.

- :func:`seed_policy` saves the blocklist and the employer policy.
- :func:`posted_post` / :func:`snapshot` build live posts with metrics.
- :func:`usage_document` is one ``usage`` ledger document (the ai package's shape).
- :func:`run_job` runs jobs on a real ``JobRunner`` with the services installed, the analytics
  job types plus any stand-ins (``extra``) for other packages' child jobs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from pydantic import JsonValue

from blogwriter.ai.ledger import CallOutcome, UsageRecord
from blogwriter.ai.types import Task, Usage
from blogwriter.analytics.jobs import register_jobs
from blogwriter.db import NotFound
from blogwriter.domain.docstore import create_doc, upsert_doc
from blogwriter.domain.ports import services_installed
from blogwriter.domain.posts import MetricsSnapshot, Post
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.jobs import Job, JobRegistry, JobRunner, Step
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import eventually, fast_settings

__all__ = [
    "TEST_NOW",
    "StepError",
    "analytics_registry",
    "posted_post",
    "run_job",
    "run_until_settled",
    "seed_policy",
    "snapshot",
    "usage_document",
]

_SNAPSHOT_ID: Final = "form-d7"
TEST_NOW: Final = datetime(2026, 11, 26, 17, 0, tzinfo=UTC)
"""The tests' fixed time: Thursday 2026-11-26, 12:00 in New York."""


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


def snapshot(
    *,
    taken_at: str,
    impressions: int | None,
    offset: str = "d7",
    snapshot_id: str = _SNAPSHOT_ID,
    **metrics: int | None,
) -> MetricsSnapshot:
    """A form snapshot with the given numbers."""
    return MetricsSnapshot.model_validate(
        {
            "id": snapshot_id,
            "takenAt": taken_at,
            "offset": offset,
            "impressions": impressions,
            **metrics,
        }
    )


async def posted_post(
    services: FakeServices,
    *,
    posted_at: str,
    snapshots: Sequence[MetricsSnapshot] = (),
    status: str = "posted",
    **fields: Any,
) -> Post:
    """Store a live post (``status=posted`` by default) with ``snapshots``."""
    post = sample_document(
        Post,
        status=status,
        posted_at=posted_at,
        snapshots=list(snapshots),
        **fields,
    )
    return (await create_doc(services.store, post)).doc


def usage_document(
    *,
    at: datetime,
    cost: str,
    task: Task = Task.SYNTHESIS,
    model: str = "gpt-6.2-sol",
    outcome: CallOutcome = "completed",
    record_id: str | None = None,
) -> dict[str, Any]:
    """One ``usage`` document as the ai ledger writes it (``UsageRecord.to_document``)."""
    record = UsageRecord(
        task=task,
        model=model,
        deployment=model,
        provider="anthropic" if model.startswith("claude") else "openai",
        usage=Usage(input=100, output=50),
        cost_usd=Decimal(cost),
        at=at.astimezone(UTC),
        origin="background",
        outcome=outcome,
        **({"record_id": record_id} if record_id is not None else {}),
    )
    return dict(record.to_document())


def analytics_registry(extra: Mapping[str, Sequence[Step]] | None = None) -> JobRegistry:
    """The analytics job types plus stand-ins for other packages' jobs."""
    registry = JobRegistry()
    register_jobs(registry)
    for job_type, steps in (extra or {}).items():
        registry.register(job_type, steps)
    return registry


async def run_until_settled(
    services: FakeServices,
    job_id: str,
    *,
    registry: JobRegistry,
    within: float = 10.0,
) -> Job:
    """Run the registered job types on a real ``JobRunner`` until ``job_id`` ends.

    Raises :class:`StepError` when a step raised an unexpected exception instead of waiting for
    the runner's retry.
    """
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)

    async def settled() -> bool:
        try:
            job = await services.jobs.get(job_id)
        except NotFound:
            return False
        return job.is_terminal or (job.error is not None and job.error.code == "step_error")

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
    job_input: JsonValue = None,
    *,
    job_id: str,
    registry: JobRegistry | None = None,
    scheduled_for: datetime | None = None,
    within: float = 10.0,
) -> Job:
    """Enqueue ``job_type`` (as a schedule run with ``scheduled_for``) and run it to the end."""
    chosen = registry if registry is not None else analytics_registry()
    definition = chosen.get(job_type)
    await enqueue(
        services.store,
        job_type,
        job_input,
        job_id=job_id,
        steps=definition.step_names if definition is not None else (),
        schedule_id="sched-analytics-suggest" if scheduled_for is not None else None,
        scheduled_for=scheduled_for,
        clock=services.clock,
    )
    return await run_until_settled(services, job_id, registry=chosen, within=within)
