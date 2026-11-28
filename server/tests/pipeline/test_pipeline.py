"""``pipeline.article`` on a real job runner: the chain from an idea to review, research
chaining, the interview wait and its expiry, resumes, review-only drafts, the critique loop,
who writes, eval children and the auto-publish decision."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import timedelta

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.ai import FallbackReason
from blogwriter.ai.types import Sensitivity, Task
from blogwriter.db import Conflict, Filter
from blogwriter.domain.articles import (
    GATE_ORDER,
    Article,
    GateReport,
    GateResult,
    GateVerdict,
    PublishState,
)
from blogwriter.domain.docstore import (
    create_doc,
    find_docs,
    read_doc,
    save_singleton,
    update_doc,
)
from blogwriter.domain.jobflow import finish_child, wait_for_child
from blogwriter.domain.jobtypes import (
    PIPELINE_ARTICLE,
    PUBLISH_ARTICLE,
    ChildResult,
    ParentRef,
    PipelineInput,
    PublishInput,
    ResearchRunInput,
    pipeline_job_id,
    publish_job_id,
    research_job_id,
)
from blogwriter.domain.ports import current_services
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.settings import AutonomySettings, Calibration, JudgeCalibration
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ArticleStatus, InterviewStatus
from blogwriter.domain.stories import Interview
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    StoreJobQueue,
    sample_document,
)
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext
from blogwriter.pipeline.testing import (
    ANSWER_TAKE,
    BLOCKED,
    T0,
    World,
    critique,
    draft_body,
    interview_reply,
    new_article,
    outline_reply,
    pipeline_registry,
    rewind,
    running,
    sample_brief,
    script_gates,
    seed_world,
    start_at,
    wait_job,
)
from blogwriter.pipeline.text import bind_citations, clean_draft
from blogwriter.research.claims import citation_id_for
from blogwriter.research.run import create_run, start_run
from blogwriter.research.testing import (
    ALLOW_URL,
    OPEN_URL,
    PASSAGE_TEXT,
    page_fetcher,
    script_happy_path,
    store_dump,
)

from .conftest import WRITE

S = ArticleStatus
MARKER = "PASSAGE-MARKER-7f3a9c"
WINDOW = timedelta(milliseconds=200)
INTERVAL = timedelta(milliseconds=10)
PIPELINE_TASKS = (Task.INTERVIEW, Task.OUTLINE, Task.DRAFT, Task.CRITIQUE, Task.REVISE)


def job_input(article_id: str, **fields: object) -> JsonValue:
    return PipelineInput.model_validate(
        {"article_id": article_id, "origin": "owner", **fields}
    ).to_json()


def plan_output(world: World) -> JsonValue:
    assert world.story is not None
    assert world.pov is not None
    outline = outline_reply(story_ids=[world.story.id], pov_ids=[world.pov.id])
    return {
        "outline": outline.model_dump(mode="json", by_alias=True),
        "servedBy": [],
        "reviewOnly": [],
    }


EARLIER: dict[str, JsonValue] = {
    "research": {"skipped": "brief"},
    "interview_questions": {"skipped": "not_asked"},
    "interview": {"skipped": "not_asked"},
}


def script_after_outline(
    ai: ScriptedModelRunner, *, draft: ScriptedReply | str | None = None
) -> None:
    ai.add(Task.DRAFT, draft if draft is not None else draft_body())
    ai.add(Task.CRITIQUE, critique())
    script_gates(ai)


async def article_doc(services: FakeServices, article_id: str) -> Article:
    return (await read_doc(services.store, Article, article_id)).doc


def event_types(job_events: list[object]) -> list[str]:
    return [getattr(event, "type", "") for event in job_events]


# --------------------------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------------------------


async def test_idea_to_review_through_research_and_a_micro_interview(
    services: FakeServices,
    ai: ScriptedModelRunner,
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    world = await seed_world(services)
    assert world.story is not None
    assert world.pov is not None
    services.fetcher = page_fetcher(
        {ALLOW_URL: f"{PASSAGE_TEXT} {MARKER}", OPEN_URL: f"{PASSAGE_TEXT} {MARKER}"}
    )
    idea = sample_document(Idea, origin="owner", title="Agent identities in Entra", pillar="agents")
    await create_doc(services.store, idea)
    article = await new_article(services, idea_id=idea.id)
    cited = citation_id_for(ALLOW_URL)
    script_happy_path(ai)
    ai.add(Task.INTERVIEW, interview_reply(2))
    ai.add(
        Task.OUTLINE,
        outline_reply(story_ids=[world.story.id], pov_ids=[world.pov.id], citation_id=cited),
    )
    script_after_outline(ai, draft=draft_body(citation_id=cited))

    etag = (await read_doc(services.store, Article, article.id)).etag
    started = await client.post(
        f"/api/articles/{article.id}/pipeline",
        json={"startFrom": "research"},
        headers={**WRITE, "If-Match": etag},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["jobId"]
    assert job_id == pipeline_job_id(article.id, 1)
    assert started.json()["article"]["item"]["status"] == "researching"

    interview_id = Interview.id_for_pipeline(article.id, 1)
    async with running(services, pipeline_registry(research=True)) as runner:
        await wait_job(services, job_id, {"waiting_input"}, key="interview", within=30)
        assert job_id not in runner.active_job_ids  # the wait freed its runner slot
        assert (await article_doc(services, article.id)).status is S.INTERVIEWING
        interview = await read_doc(services.store, Interview, interview_id)
        body = {
            "answers": [
                {"questionId": "q1", "text": ANSWER_TAKE},
                {"questionId": "q2", "text": "List every tool call before you touch scopes."},
            ]
        }
        answered = await client.post(
            f"/api/interviews/{interview_id}/answers",
            json=body,
            headers={**WRITE, "If-Match": interview.etag},
        )
        assert answered.status_code == 200, answered.text
        done = await wait_job(services, job_id, {"succeeded", "failed"}, within=30)
    assert done.status == "succeeded", done.error

    final = await article_doc(services, article.id)
    run_id = ResearchRun.id_for_pipeline(article.id, 1)
    assert final.status is S.IN_REVIEW
    assert final.brief_id == Brief.id_for(run_id)
    assert final.research_run_id == run_id
    assert final.interview_id == interview_id
    assert ANSWER_TAKE in final.markdown  # the owner's confirmed words, quoted
    assert ALLOW_URL in final.markdown  # the canonical footnote of the brief's source
    for junk in ("src-made-up", "example.com/fake", "## References", "```markdown"):
        assert junk not in final.markdown
    assert final.meta.title == "Scope agent tokens per tool call"  # the owner's title is kept
    assert final.meta.slug == "scope-agent-tokens-per-tool-call"
    assert final.meta.tags == ["agents", "entra-id", "least-privilege"]
    assert {(ref.kind, ref.id) for ref in final.owner_signals} == {
        ("story", world.story.id),
        ("pov", world.pov.id),
        ("interview", interview_id),
    }
    assert final.gates is not None
    assert len(final.gates.results) == 7
    assert final.calibration is not None
    assert final.calibration.gated_sha256 == final.compute_sha256()
    assert final.calibration.gated_version == final.version
    assert final.calibration.review_only is False
    assert not final.review_only
    assert sorted(item.role for item in final.media) == [
        "diagram_png",
        "diagram_svg",
        "hero_image",
        "og_image",
    ]
    assert [payload.title for payload, _ in services.notifier.sent] == [
        "2 quick questions",
        "Your article is ready for review",
    ]
    finalize = done.step("finalize")
    assert finalize is not None
    assert isinstance(finalize.output, dict)
    assert finalize.output["decision"] == "review"
    assert "not_enabled" in str(finalize.output["reasons"])

    # One research run, one research job, one interview, asked once.
    runs = await find_docs(services.store, ResearchRun, [Filter("articleId", "==", article.id)])
    assert [stored.doc.id for stored in runs] == [run_id]
    assert services.jobs.types().count("research.run") == 1
    assert len(ai.calls_for(Task.INTERVIEW)) == 1

    # Every pipeline request is anonymized and tool-free; nothing private, nothing fetched.
    assert all(call.request.sensitivity is not Sensitivity.PRIVATE for call in ai.calls)
    assert not ai.calls_for(Task.ANONYMIZE)
    for task in PIPELINE_TASKS:
        for call in ai.calls_for(task):
            assert call.request.sensitivity is Sensitivity.ANONYMIZED
            assert call.request.tools == ()
            assert call.origin == "owner"
            assert MARKER not in call.request.messages[0].text
    brief = (await read_doc(services.store, Brief, Brief.id_for(run_id))).doc
    draft_text = ai.calls_for(Task.DRAFT)[0].request.messages[0].text
    assert brief.executive_summary in draft_text
    assert ANSWER_TAKE in draft_text

    # Nothing fetched or private reaches the store, the job documents or the logs.
    dump = await store_dump(services.store)
    assert MARKER not in dump
    jobs = json.dumps([doc.body for doc in await services.store.find("jobs")])
    assert ANSWER_TAKE not in jobs
    assert MARKER not in jobs
    logged = caplog.text + "".join(str(record.__dict__) for record in caplog.records)
    for secret in (ANSWER_TAKE, MARKER, "Twelve agents ran on one service principal"):
        assert secret not in logged


async def test_expiry_resumes_the_pipeline_without_answers(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(services, brief_id=brief.id)
    ai.add(Task.INTERVIEW, interview_reply(2))
    ai.add(Task.OUTLINE, outline_reply())
    script_after_outline(ai)
    job_id = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, job_input(article.id), job_id=job_id)

    async with running(services, pipeline_registry()):
        await wait_job(services, job_id, {"waiting_input"}, key="interview")
        interview = (
            await read_doc(services.store, Interview, Interview.id_for_pipeline(article.id, 1))
        ).doc
        assert interview.due_at is not None
        due = interview.due_at
        services.clock.set(T0 + timedelta(days=2, hours=10))  # past Wednesday 18:00
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    assert due == "2026-12-02T23:00:00.000000Z"
    expired = (await read_doc(services.store, Interview, interview.id)).doc
    assert expired.status is InterviewStatus.EXPIRED
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW
    assert all(ref.kind != "interview" for ref in final.owner_signals)
    assert "<interview>" not in ai.calls_for(Task.DRAFT)[0].request.messages[0].text
    del world


async def test_research_failure_moves_the_article_to_needs_attention(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    article = await new_article(services)

    async def failing_child(ctx: StepContext) -> JsonValue:
        payload = ResearchRunInput.from_json(ctx.input)
        failure = ChildResult(ok=False, error_code="blocklist_hit")
        await finish_child(
            ctx, current_services().jobs, payload.parent, failure, window=WINDOW, interval=INTERVAL
        )
        raise JobFailed("blocklist_hit")

    registry = pipeline_registry(("research.run", [Step("work", failing_child)]))
    job_id = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, job_input(article.id), job_id=job_id)
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "failed"
    assert done.error is not None
    assert done.error.code == "research_failed"
    final = await article_doc(services, article.id)
    assert final.status is S.NEEDS_ATTENTION
    assert final.last_error == "research_failed"
    assert final.pipeline_job_id == job_id  # the planned start claimed the article
    assert final.brief_id is None
    assert ai.calls == []


async def succeeding_child(ctx: StepContext) -> JsonValue:
    payload = ResearchRunInput.from_json(ctx.input)
    services = current_services()
    brief = sample_brief(id=Brief.id_for(payload.run_id), run_id=payload.run_id)
    with contextlib.suppress(Conflict):
        await create_doc(services.store, brief)
    result = ChildResult(ok=True, ref=brief.id)
    await finish_child(ctx, services.jobs, payload.parent, result, window=WINDOW, interval=INTERVAL)
    return result.to_json()


async def test_a_research_child_that_finished_first_is_read_not_waited_for(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    article = await new_article(services)
    job_id = pipeline_job_id(article.id, 1)
    run_id = ResearchRun.id_for_pipeline(article.id, 1)
    created = await create_run(
        services.store, topic="Agent tokens", pillar="agents", now=T0, run_id=run_id
    )
    child_registry = JobRegistry()
    child_registry.register("research.run", [Step("work", succeeding_child)])
    await start_run(
        services, created.doc, origin="owner", parent=ParentRef(job_id=job_id, key="research")
    )
    async with running(services, child_registry):
        await wait_job(services, research_job_id(run_id), {"succeeded"})

    ai.add(Task.OUTLINE, outline_reply())
    script_after_outline(ai)
    await services.jobs.enqueue(
        PIPELINE_ARTICLE, job_input(article.id, interview="skip"), job_id=job_id
    )
    registry = pipeline_registry(("research.run", [Step("work", succeeding_child)]))
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    research = done.step("research")
    assert research is not None
    assert research.runs == 1  # no wait, no second run
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW
    assert final.brief_id == Brief.id_for(run_id)


# --------------------------------------------------------------------------------------------
# Resumes
# --------------------------------------------------------------------------------------------


async def test_resumed_steps_reuse_the_run_and_the_interview(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    article = await new_article(services)
    job_id = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, job_input(article.id), job_id=job_id)
    # research.run is not registered here: the child stays queued and the pipeline waits.
    async with running(services, pipeline_registry()):
        await wait_job(services, job_id, {"waiting_input"}, key="research")
        await rewind(services, job_id, "research")  # a crash after start_run, before set_links
        await wait_job(services, job_id, {"waiting_input"}, key="research")
    runs = await find_docs(services.store, ResearchRun, [Filter("articleId", "==", article.id)])
    assert len(runs) == 1
    assert services.jobs.types().count("research.run") == 1
    research = (await services.jobs.get(job_id)).step("research")
    assert research is not None
    assert research.runs == 2

    brief = sample_brief()
    await create_doc(services.store, brief)
    other = await new_article(services, brief_id=brief.id)
    other_job = pipeline_job_id(other.id, 1)
    ai.add(Task.INTERVIEW, interview_reply(2))
    await services.jobs.enqueue(PIPELINE_ARTICLE, job_input(other.id), job_id=other_job)
    async with running(services, pipeline_registry()):
        await wait_job(services, other_job, {"waiting_input"}, key="interview")
        await rewind(services, other_job, "interview_questions")
        await wait_job(services, other_job, {"waiting_input"}, key="interview")
    asked = (await services.jobs.get(other_job)).step("interview_questions")
    assert asked is not None
    assert asked.runs == 2
    assert len(ai.calls_for(Task.INTERVIEW)) == 1
    assert [payload.title for payload, _ in services.notifier.sent] == ["2 quick questions"]


# --------------------------------------------------------------------------------------------
# Drafts: review-only, refusals, the critique loop
# --------------------------------------------------------------------------------------------


async def drafting(
    services: FakeServices, *, job_id: str, status: ArticleStatus = S.DRAFTING, markdown: str = ""
) -> Article:
    brief = sample_brief()
    with contextlib.suppress(Conflict):
        await create_doc(services.store, brief)
    return await new_article(
        services,
        status=status,
        brief_id=brief.id,
        pipeline_job_id=job_id,
        pipeline_runs=1,
        markdown=markdown,
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [("unavailable", ["fallback_model"]), ("refusal", ["fallback_model", "refusal"])],
)
async def test_a_fallback_draft_is_review_only_and_resets_the_live_text(
    services: FakeServices, ai: ScriptedModelRunner, reason: FallbackReason, expected: list[str]
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-fallback-1"
    article = await drafting(services, job_id=job_id)
    script_after_outline(ai, draft=ScriptedReply(text=draft_body(), fallback_reason=reason))
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="draft",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world)},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    assert "draft.reset" in event_types(list(done.events))
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW
    assert final.review_only is True
    assert final.review_only_reasons == expected
    assert final.calibration is not None
    assert final.calibration.review_only is True
    finalize = done.step("finalize")
    assert finalize is not None
    assert "review_only" in str(finalize.output)


async def test_a_refused_draft_needs_attention(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-refused-1"
    article = await drafting(services, job_id=job_id)
    ai.add(Task.DRAFT, ScriptedReply(refused=True))
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="draft",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world)},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "failed"
    assert done.error is not None
    assert done.error.code == "refused"
    assert "draft.reset" in event_types(list(done.events))
    final = await article_doc(services, article.id)
    assert final.status is S.NEEDS_ATTENTION
    assert final.last_error == "refused"
    assert final.review_only_reasons == ["refusal"]
    assert final.served_by[-1].refused is True
    assert final.markdown == ""


@pytest.mark.parametrize(
    ("critiques", "revisions", "stopped"),
    [
        ([False, False], 2, "max_loops"),
        ([False, True], 1, "passed"),
        ([True], 0, "passed"),
    ],
)
async def test_the_critique_loop_revises_at_most_twice(
    services: FakeServices,
    ai: ScriptedModelRunner,
    critiques: list[bool],
    revisions: int,
    stopped: str,
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-loop-1"
    body = bind_citations(clean_draft(draft_body()), sample_brief().citations).text
    article = await drafting(services, job_id=job_id, markdown=body)
    for passing in critiques:
        ai.add(Task.CRITIQUE, critique(passing=passing))
    for index in range(revisions):
        ai.add(
            Task.REVISE,
            draft_body().replace("Twelve agents", f"Revision {index + 1}: twelve agents"),
        )
    script_gates(ai)
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="critique_revise",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world), "draft": {}, "normalize": {}},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    loop = done.step("critique_revise")
    assert loop is not None
    assert isinstance(loop.output, dict)
    assert loop.output["loops"] == revisions
    assert loop.output["stopped"] == stopped
    assert len(ai.calls_for(Task.REVISE)) == revisions
    assert len(ai.calls_for(Task.CRITIQUE)) == len(critiques) + 1  # + gate 1
    final = await article_doc(services, article.id)
    assert final.version == article.version + revisions
    if revisions:
        assert f"Revision {revisions}: twelve agents" in final.markdown
        revise_text = ai.calls_for(Task.REVISE)[0].request.messages[0].text
        assert "<critique>" in revise_text
        assert "The section never says what broke in the rollout." in revise_text


async def test_max_revise_loops_zero_only_critiques(
    services: FakeServices, ai: ScriptedModelRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BW_PIPELINE_MAX_REVISE_LOOPS", "0")
    world = await seed_world(services)
    job_id = "pipeline-art-noloop-1"
    body = bind_citations(clean_draft(draft_body()), sample_brief().citations).text
    article = await drafting(services, job_id=job_id, markdown=body)
    ai.add(Task.CRITIQUE, critique(passing=False))
    script_gates(ai)
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="critique_revise",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world), "draft": {}, "normalize": {}},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    loop = done.step("critique_revise")
    assert loop is not None
    assert isinstance(loop.output, dict)
    assert loop.output["stopped"] == "max_loops"
    assert ai.calls_for(Task.REVISE) == []


async def test_a_blocklisted_draft_never_goes_to_the_critic(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-leak-1"
    article = await drafting(services, job_id=job_id, markdown=f"We built this for {BLOCKED}.\n")
    ai.add(Task.CRITIQUE, critique())  # must stay unused
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="critique_revise",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world), "draft": {}, "normalize": {}},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    assert ai.calls == []  # neither the critic nor the gates' models saw it
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW
    assert final.gates is not None
    confidentiality = [r for r in final.gates.results if r.gate.value == "confidentiality"]
    assert confidentiality[0].verdict is GateVerdict.FAIL


# --------------------------------------------------------------------------------------------
# Who writes
# --------------------------------------------------------------------------------------------


async def test_a_job_never_writes_an_article_that_is_not_its_own(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    registry = pipeline_registry()
    moved = await drafting(
        services, job_id="pipeline-art-moved-1", status=S.IN_REVIEW, markdown="Mine.\n"
    )
    taken = await drafting(services, job_id="pipeline-art-newer-2", markdown="Theirs.\n")
    cases = [
        (moved, "pipeline-art-moved-1", "article_moved"),
        (taken, "pipeline-art-old-1", "superseded"),
    ]
    for article, job_id, code in cases:
        before = await read_doc(services.store, Article, article.id)
        await start_at(
            services,
            PIPELINE_ARTICLE,
            job_input(article.id, start_from="draft", interview="skip"),
            job_id=job_id,
            step="draft",
            registry=registry,
            outputs={**EARLIER, "outline": plan_output(world)},
        )
        async with running(services, registry):
            done = await wait_job(services, job_id, {"succeeded", "failed"})
        assert done.error is not None
        assert done.error.code == code
        after = await read_doc(services.store, Article, article.id)
        assert after.etag == before.etag  # untouched: no write, no needs_attention
    assert ai.calls == []


async def test_cancel_while_waiting_stops_the_run(
    services: FakeServices, ai: ScriptedModelRunner, client: httpx.AsyncClient
) -> None:
    await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(services, brief_id=brief.id)
    ai.add(Task.INTERVIEW, interview_reply(2))
    job_id = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, job_input(article.id), job_id=job_id)
    async with running(services, pipeline_registry()):
        await wait_job(services, job_id, {"waiting_input"}, key="interview")
        etag = (await read_doc(services.store, Article, article.id)).etag
        cancelled = await client.post(
            f"/api/articles/{article.id}/pipeline/cancel", headers={**WRITE, "If-Match": etag}
        )
        assert cancelled.status_code == 200, cancelled.text
        interview = await read_doc(
            services.store, Interview, Interview.id_for_pipeline(article.id, 1)
        )
        late = await client.post(
            f"/api/interviews/{interview.doc.id}/answers",
            json={"answers": [{"questionId": "q1", "text": ANSWER_TAKE}]},
            headers={**WRITE, "If-Match": interview.etag},
        )
        assert late.status_code == 200  # accepted: it becomes material and a voice sample
    job = await services.jobs.get(job_id)
    assert job.status == "cancelled"
    final = await article_doc(services, article.id)
    assert final.status is S.NEEDS_ATTENTION
    assert final.last_error == "cancelled"


# --------------------------------------------------------------------------------------------
# Eval children and the auto-publish decision
# --------------------------------------------------------------------------------------------


async def test_an_eval_child_reports_to_its_parent(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(services, brief_id=brief.id, purpose="eval")

    async def parent_step(ctx: StepContext) -> JsonValue:
        jobs = current_services().jobs
        child_id = pipeline_job_id(article.id, 1)
        payload = PipelineInput(
            article_id=article.id,
            start_from="draft",
            interview="skip",
            parent=ParentRef(job_id=ctx.job_id, key="eval-1"),
            origin="owner",
        )
        with contextlib.suppress(Conflict):
            await jobs.enqueue(PIPELINE_ARTICLE, payload.to_json(), job_id=child_id)
        result = await wait_for_child(ctx, jobs, child_id, key="eval-1", prompt="Drafting.")
        return result.to_json()

    ai.add(Task.OUTLINE, outline_reply())
    script_after_outline(ai)
    registry = pipeline_registry(("test.eval", [Step("draft", parent_step)]))
    await services.jobs.enqueue("test.eval", None, job_id="eval-job-1")
    async with running(services, registry):
        parent = await wait_job(services, "eval-job-1", {"succeeded", "failed"}, within=20)
    assert parent.status == "succeeded", parent.error
    assert ChildResult.from_json(parent.result) == ChildResult(ok=True, ref=article.id)
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW  # never the auto path
    assert services.notifier.sent == []  # the eval job notifies once for the whole run
    assert ai.calls_for(Task.INTERVIEW) == []


def all_passed(article: Article) -> GateReport:
    results = [
        GateResult(gate=gate, verdict=GateVerdict.PASS, ran_at=SAMPLE_NOW) for gate in GATE_ORDER
    ]
    return GateReport(
        content_sha256=article.compute_sha256(),
        version=max(1, article.version),
        ran_at=SAMPLE_NOW,
        results=results,
    )


async def gated_article(services: FakeServices, job_id: str, **fields: object) -> Article:
    article = await drafting(services, job_id=job_id, status=S.GATING, markdown="A gated body.\n")

    def with_report(doc: Article) -> Article:
        return doc.model_copy(update={"gates": all_passed(doc), **fields})

    return (await update_doc(services.store, Article, article.id, with_report)).doc


async def calibrated_autonomy(services: FakeServices, **fields: object) -> None:
    autonomy = AutonomySettings.default(SAMPLE_NOW).model_copy(
        update={
            "auto_publish_enabled": True,
            "calibration": Calibration(clean_streak=6),
            "judge": JudgeCalibration(as_is_rate=0.95, sample_size=12, checked_at=SAMPLE_NOW),
            **fields,
        }
    )
    await save_singleton(services.store, autonomy, if_match=None)


async def run_finalize(services: FakeServices, job_id: str, article: Article) -> None:
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="finalize",
        registry=registry,
        outputs={},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error


async def test_the_auto_path_needs_everything_at_once(services: FakeServices) -> None:
    await seed_world(services)
    await calibrated_autonomy(services)
    job_id = "pipeline-art-auto-1"
    article = await gated_article(services, job_id)
    await run_finalize(services, job_id, article)

    final = await article_doc(services, article.id)
    assert final.status is S.APPROVED
    assert final.publish.auto is True
    assert final.publish.publish_at == "2026-12-07T14:00:00.000000Z"  # Monday 09:00, >= 48 h
    assert final.calibration is not None
    assert final.calibration.gates_passed is True
    publish_id = publish_job_id(article.id, article.version)
    assert final.publish.job_id == publish_id
    job = await services.jobs.get(publish_id)
    assert job.type == PUBLISH_ARTICLE
    assert job.run_after == "2026-12-07T14:00:00.000000Z"
    payload = PublishInput.from_json(job.input)
    assert payload.auto is True
    assert payload.version == article.version
    assert services.notifier.sent == []  # the veto notice tells the owner, not the pipeline


@pytest.mark.parametrize(
    "case", ["paused", "review_only", "not_calibrated", "unregistered", "live"]
)
async def test_anything_missing_sends_the_article_to_review(
    services: FakeServices, case: str
) -> None:
    await seed_world(services)
    if case == "paused":
        await calibrated_autonomy(services, paused=True)
    elif case == "not_calibrated":
        await calibrated_autonomy(services, calibration=Calibration(clean_streak=5))
    else:
        await calibrated_autonomy(services)
    if case == "unregistered":
        services.jobs = StoreJobQueue(
            services.store, clock=services.clock, registered={PIPELINE_ARTICLE}
        )
    job_id = f"pipeline-art-{case.replace('_', '-')}-1"
    fields: dict[str, object] = {}
    if case == "review_only":
        fields = {"review_only": True, "review_only_reasons": ["fallback_model"]}
    if case == "live":
        fields = {"publish": PublishState(slug="agent-tokens", live=True)}
    article = await gated_article(services, job_id, **fields)
    await run_finalize(services, job_id, article)
    final = await article_doc(services, article.id)
    assert final.status is S.IN_REVIEW
    assert final.publish.auto is False
    assert [p.title for p, _ in services.notifier.sent] == ["Your article is ready for review"]
    assert all(job.type != PUBLISH_ARTICLE for job in services.jobs.enqueued)


async def test_assets_keep_the_owners_hero_and_survive_render_failures(
    services: FakeServices,
) -> None:
    from blogwriter.domain.common import MediaRef
    from blogwriter.domain.ports import RenderFailed

    await seed_world(services)
    hero = MediaRef(
        role="hero_image",
        name="uploads/2026-11/" + "a" * 64 + ".png",
        sha256="a" * 64,
        content_type="image/png",
        size_bytes=10,
        alt_text="My hero",
    )
    job_id = "pipeline-art-assets-1"
    body = "Intro.\n\n```mermaid\nflowchart LR\n  A --> B\n```\n"
    article = await drafting(services, job_id=job_id, status=S.GATING, markdown=body)
    await update_doc(
        services.store, Article, article.id, lambda doc: doc.model_copy(update={"media": [hero]})
    )
    assert services.render is not None
    services.render.fail_with = RenderFailed("brand_incomplete")
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="assets",
        registry=registry,
        outputs={},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    assets = done.step("assets")
    assert assets is not None
    assert assets.output == {
        "diagrams": 1,
        "og": False,
        "hero": "owner",
        "failures": ["brand_incomplete"],
    }
    final = await article_doc(services, article.id)
    assert [item.name for item in final.media] == [hero.name]


async def test_assets_keep_a_hero_picked_from_the_illustrations(services: FakeServices) -> None:
    """An AI illustration lives under ``articles/<id>/`` like the draft assets, but the owner
    picked it: a pipeline re-run renders a new card and keeps it as the hero."""
    from blogwriter.domain.articles import Illustration
    from blogwriter.domain.common import MediaRef

    await seed_world(services)
    job_id = "pipeline-art-assets-2"
    article = await drafting(services, job_id=job_id, status=S.GATING, markdown="Intro.\n")
    picked = MediaRef(
        role="image",
        name=f"articles/{article.id}/{'b' * 64}.png",
        sha256="b" * 64,
        content_type="image/png",
        size_bytes=10,
        width=1536,
        height=1024,
        alt_text="Glass keys in a row",
    )
    old_card = MediaRef(
        role="og_image",
        name=f"articles/{article.id}/{'c' * 64}.png",
        sha256="c" * 64,
        content_type="image/png",
        size_bytes=10,
        width=1200,
        height=630,
    )
    candidate = Illustration(
        id=Illustration.id_for(picked.sha256),
        role="hero",
        media=picked,
        alt_text="Glass keys in a row",
        prompt_summary="Each tool call gets its own key",
        job_id="illustrations-art-1-abc",
        created_at="2026-11-26T12:00:00.000000Z",
    )
    hero = picked.model_copy(update={"role": "hero_image"})
    await update_doc(
        services.store,
        Article,
        article.id,
        lambda doc: doc.model_copy(
            update={"media": [old_card, hero], "illustrations": [candidate]}
        ),
    )
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="assets",
        registry=registry,
        outputs={},
    )
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    assets = done.step("assets")
    assert assets is not None
    assert assets.output == {"diagrams": 0, "og": True, "hero": "owner", "failures": []}
    final = await article_doc(services, article.id)
    heroes = [item for item in final.media if item.role == "hero_image"]
    assert [item.name for item in heroes] == [picked.name]
    assert old_card.name not in {item.name for item in final.media}  # the card was re-rendered
    assert [item.role for item in final.media].count("og_image") == 1


# --------------------------------------------------------------------------------------------
# Crash-only: a resumed step sees that its write landed and does not call the model again
# --------------------------------------------------------------------------------------------


async def test_a_draft_written_before_a_crash_is_not_drafted_again(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-crash-1"
    body = bind_citations(clean_draft(draft_body()), sample_brief().citations).text
    article = await drafting(services, job_id=job_id, markdown=body)  # version 1: the write
    ai.add(Task.CRITIQUE, critique())
    script_gates(ai)
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="draft",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world)},
    )
    # The draft step had checkpointed the version it started from, then crashed after writing.
    await rewind(services, job_id, "draft", checkpoint={"baseVersion": article.version - 1})
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    draft = done.step("draft")
    assert draft is not None
    assert draft.output == {"version": article.version, "resumed": True}
    assert ai.calls_for(Task.DRAFT) == []
    assert "draft.reset" not in event_types(list(done.events))


async def test_a_revision_written_before_a_crash_counts_as_a_loop(
    services: FakeServices, ai: ScriptedModelRunner
) -> None:
    world = await seed_world(services)
    job_id = "pipeline-art-crash-2"
    body = bind_citations(clean_draft(draft_body()), sample_brief().citations).text
    article = await drafting(services, job_id=job_id, markdown=body)
    script_gates(ai)
    registry = pipeline_registry()
    await start_at(
        services,
        PIPELINE_ARTICLE,
        job_input(article.id, start_from="draft", interview="skip"),
        job_id=job_id,
        step="critique_revise",
        registry=registry,
        outputs={**EARLIER, "outline": plan_output(world), "draft": {}, "normalize": {}},
    )
    crashed: JsonValue = {"loops": 1, "rounds": [], "pending": article.version - 1}
    await rewind(services, job_id, "critique_revise", checkpoint=crashed)
    async with running(services, registry):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    loop = done.step("critique_revise")
    assert loop is not None
    assert isinstance(loop.output, dict)
    assert loop.output["loops"] == 2
    assert loop.output["stopped"] == "max_loops"
    assert ai.calls_for(Task.REVISE) == []
    assert len(ai.calls_for(Task.CRITIQUE)) == 1  # gate 1 only
