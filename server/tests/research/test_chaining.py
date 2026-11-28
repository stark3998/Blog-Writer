"""Research as a child job: a parent (the pipeline's ``research`` step) creates the run with a
derived id, starts it with a ``ParentRef`` and waits; the run reports success and failure, also
when it finishes before the parent suspends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.domain.jobflow import wait_for_child
from blogwriter.domain.jobtypes import ChildResult, ParentRef, ResearchRunInput, research_job_id
from blogwriter.domain.ports import current_services
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner
from blogwriter.jobs import Job, StepContext
from blogwriter.jobs.testing import eventually
from blogwriter.research.run import create_run, start_run
from blogwriter.research.testing import (
    BLOCKED_TERM,
    new_run,
    page_fetcher,
    research_registry,
    research_services,
    run_jobs,
    save_blocklist,
    script_happy_path,
)

ARTICLE = "art-0000000000000000000000000abc"
PARENT = "pipeline-art-0000000000000000000000000abc-1"


def pipeline_step(
    *, wait_first: bool, origin: Origin = "owner"
) -> Callable[[StepContext], Awaitable[JsonValue]]:
    async def step(ctx: StepContext) -> JsonValue:
        services = current_services()
        stored = await create_run(
            services.store,
            topic="Agent identities in Microsoft Entra",
            pillar="agents",
            now=services.clock.now(),
            run_id=ResearchRun.id_for_pipeline(ARTICLE, 1),
        )
        parent = ParentRef(job_id=ctx.job_id, key="research")
        child = await start_run(services, stored.doc, origin=origin, parent=parent)
        if wait_first:

            async def child_done() -> bool:
                return (await services.jobs.get(child.id)).is_terminal

            await eventually(child_done, within=10)
        result = await wait_for_child(
            ctx, services.jobs, child.id, key="research", prompt="Research is running."
        )
        return result.to_json()

    return step


async def run_pipeline(
    services: FakeServices, *, wait_first: bool, origin: Origin = "owner"
) -> Job:
    await services.jobs.enqueue("test.pipeline", None, job_id=PARENT)

    async def settled() -> bool:
        return (await services.jobs.get(PARENT)).is_terminal

    step = pipeline_step(wait_first=wait_first, origin=origin)
    await run_jobs(services, research_registry(("test.pipeline", step)), done=settled)
    return await services.jobs.get(PARENT)


def waited(job: Job) -> bool:
    return any(event.type == "input.requested" for event in job.events)


async def test_the_parent_gets_the_brief_on_success() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(ai)

    parent = await run_pipeline(services, wait_first=False)

    run_id = ResearchRun.id_for_pipeline(ARTICLE, 1)
    assert parent.status == "succeeded", parent.error
    assert ChildResult.from_json(parent.result) == ChildResult(ok=True, ref=Brief.id_for(run_id))
    assert waited(parent)
    child = await services.jobs.get(research_job_id(run_id))
    assert child.status == "succeeded"
    payload = ResearchRunInput.from_json(child.input)
    assert payload.parent == ParentRef(job_id=PARENT, key="research")
    assert payload.origin == "owner"


async def test_the_parent_gets_the_failure_code() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(ai, queries=((f"{BLOCKED_TERM} agent rollout",), ("open",)))

    parent = await run_pipeline(services, wait_first=False)

    assert parent.status == "succeeded", parent.error
    result = ChildResult.from_json(parent.result)
    assert result == ChildResult(ok=False, error_code="blocklist_hit")


async def test_a_child_that_finishes_before_the_parent_suspends_is_not_lost() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(ai)

    parent = await run_pipeline(services, wait_first=True)

    run_id = ResearchRun.id_for_pipeline(ARTICLE, 1)
    assert parent.status == "succeeded", parent.error
    assert ChildResult.from_json(parent.result) == ChildResult(ok=True, ref=Brief.id_for(run_id))
    assert not waited(parent)  # it read the recorded result instead of waiting


async def test_chained_work_inherits_the_parent_origin() -> None:
    ai = ScriptedModelRunner()
    services = research_services(fetcher=page_fetcher(), ai=ai)
    await save_blocklist(services.store, BLOCKED_TERM)
    script_happy_path(ai)

    parent = await run_pipeline(services, wait_first=False, origin="background")

    assert parent.status == "succeeded", parent.error
    assert {call.origin for call in ai.calls} == {"background"}
    assert ai.calls_for(Task.PLAN)


async def test_a_resumed_step_finds_the_run_and_the_job_it_created() -> None:
    services = research_services(fetcher=page_fetcher())
    await save_blocklist(services.store, BLOCKED_TERM)
    run_id = ResearchRun.id_for_pipeline(ARTICLE, 2)
    first = await new_run(services, run_id=run_id)
    again = await new_run(services, topic="A different wording after a retry", run_id=run_id)
    assert again.doc.id == first.doc.id == run_id
    assert again.doc.topic == first.doc.topic
    job = await start_run(services, first.doc, origin="owner")
    same = await start_run(services, again.doc, origin="owner")
    assert job.id == same.id == research_job_id(run_id)
    assert services.jobs.types() == ["research.run"]
    eval_id = ResearchRun.id_for_eval("evt-00000000000000000000000000000001", "job-eval-1")
    assert (await new_run(services, run_id=eval_id)).doc.id == eval_id
