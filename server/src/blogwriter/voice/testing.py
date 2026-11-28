"""Test helpers for the voice package (no network, no real time). Never used in production.

- :func:`seed_policy` saves the blocklist and the employer policy the voice routes and jobs need.
- :class:`MarkerSlopChecker` flags texts that contain a marker, like the structural detectors.
- :data:`LINKEDIN_POSTS` are clean, distinct sample texts (no AI tells).
- :func:`run_job` runs a voice job to the end on a real ``JobRunner`` with the services installed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, override

from pydantic import JsonValue

from blogwriter.db import NotFound
from blogwriter.domain.common import Finding
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.ports import services_installed
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, FakeSlopChecker
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.voice.jobs import register_jobs

__all__ = [
    "LINKEDIN_POSTS",
    "MarkerSlopChecker",
    "StepError",
    "enqueue_job",
    "preset_steps",
    "run_job",
    "run_until_settled",
    "seed_policy",
    "voice_registry",
]

LINKEDIN_POSTS: Final[tuple[str, ...]] = (
    "Last March we moved 2,000 apps off our old identity provider in 14 months.\n\n"
    "The first 1,400 took ten weeks. The last 600 took the rest, because every one of them had "
    "a hard-coded redirect, a service account or a vendor who had left.\n\n"
    "If you plan a migration, count the strange apps first. The easy ones never set the date.",
    "An agent that calls a tool with my token can do everything I can do.\n\n"
    "We scoped tokens per tool call instead: one audience, five minutes, no refresh. The agent "
    "asks again for every call, and the log shows which call asked for what.\n\n"
    "It cost us a week of plumbing. It saved us the incident review I did not want to write.",
    "I stopped writing risk registers for AI pilots.\n\n"
    "Every pilot now gets three lines: what data it touches, who can switch it off, and the date "
    "we look at it again. Two of eleven pilots did not survive that third line.",
    "A client asked why their MFA prompts doubled after the cut-over.\n\n"
    "The answer was a conditional access policy that matched both the old and the new app "
    "registration. We found it in the sign-in logs in twenty minutes, after two days of guessing.",
    "Prompt injection is an access control problem with a new input channel.\n\n"
    "We treat every retrieved document as untrusted input to a privileged process. That means "
    "the model never holds a credential the document's author could not hold.",
    "Our first agent inventory found 37 agents. Nine had an owner on paper.\n\n"
    "We gave every agent a sponsor, an expiry date and a kill switch before we wrote any policy. "
    "The policy was easier to write once the list was real.",
)
"""Clean, distinct LinkedIn-style posts (no AI tells, no blocklisted terms)."""


class StepError(AssertionError):
    """A job step raised an unexpected exception (the runner would retry it)."""


class MarkerSlopChecker(FakeSlopChecker):
    """A ``SlopChecker`` that flags the first occurrence of each marker found in a text."""

    def __init__(self, markers: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self.markers: dict[str, str] = dict(markers or {})

    @override
    def check(self, text: str) -> list[Finding]:
        self.checked.append(text)
        findings: list[Finding] = []
        for marker, code in self.markers.items():
            index = text.find(marker)
            if index >= 0:
                findings.append(
                    Finding(
                        code=code,
                        severity="warn",
                        message="Flagged by the structural detectors.",
                        start=index,
                        end=index + len(marker),
                    )
                )
        return findings


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


def voice_registry() -> JobRegistry:
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
) -> Job:
    """Enqueue a voice job with its steps (``scheduled_for`` makes it look like a schedule run)."""
    definition = voice_registry().get(job_type)
    return await enqueue(
        services.store,
        job_type,
        job_input,
        job_id=job_id,
        steps=definition.step_names if definition is not None else (),
        schedule_id="sched-voice-digest" if scheduled_for is not None else None,
        scheduled_for=scheduled_for,
        clock=services.clock,
    )


async def preset_steps(
    services: FakeServices,
    job_id: str,
    *,
    done: Mapping[str, JsonValue],
    checkpoints: Mapping[str, JsonValue] | None = None,
) -> None:
    """Mark steps of a queued job as already succeeded (with their outputs) and give others a
    checkpoint, as if an earlier attempt had got that far before the process died."""
    raw = await services.store.read("jobs", job_id)
    job = Job.from_body(raw.body)
    for step in job.steps:
        if step.name in done:
            step.status = "succeeded"
            step.output = done[step.name]
        elif checkpoints is not None and step.name in checkpoints:
            step.output = checkpoints[step.name]
    await services.store.replace("jobs", job.to_body(), if_match=raw.etag)


async def run_until_settled(services: FakeServices, job_id: str, *, within: float = 10.0) -> Job:
    """Run the voice job types on a real ``JobRunner`` until ``job_id`` ends.

    Raises :class:`StepError` when a step raised an unexpected exception (the runner would have
    retried it later) instead of waiting for the retry.
    """
    runner = JobRunner(
        services.store, voice_registry(), settings=fast_settings(), clock=services.clock
    )

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
    scheduled_for: datetime | None = None,
    within: float = 10.0,
) -> Job:
    """:func:`enqueue_job` then :func:`run_until_settled`."""
    await enqueue_job(services, job_type, job_input, job_id=job_id, scheduled_for=scheduled_for)
    return await run_until_settled(services, job_id, within=within)
