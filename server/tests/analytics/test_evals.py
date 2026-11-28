"""The eval set: topics, the run checks, ``analytics.eval`` on a real runner (stand-ins for the
research and pipeline children, the real gate 1 as the judge), ``pipeline_unavailable`` and the
judge calibration."""

from __future__ import annotations

import contextlib
from collections.abc import Collection
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.analytics import evals
from blogwriter.analytics.evals import (
    MAX_ACTIVE_TOPICS,
    error_code_of,
    judge_stats,
    judge_verdict,
    with_judge,
)
from blogwriter.analytics.testing import TEST_NOW, analytics_registry, run_job, seed_policy
from blogwriter.articles import pipeline_write, transition
from blogwriter.db import Conflict, Filter
from blogwriter.domain.analytics import EvalResult, EvalTopic
from blogwriter.domain.articles import (
    Article,
    ArticleStatus,
    GateName,
    GateReport,
    GateResult,
    GateVerdict,
)
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, read_singleton, upsert_doc
from blogwriter.domain.jobflow import finish_child
from blogwriter.domain.jobtypes import (
    ANALYTICS_EVAL,
    PIPELINE_ARTICLE,
    RESEARCH_RUN,
    ChildResult,
    PipelineInput,
    ResearchRunInput,
)
from blogwriter.domain.ports import Services, current_services
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.settings import AutonomySettings, auto_publish_decision
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    StoreJobQueue,
    sample_document,
)
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates.rubric import RubricScores
from blogwriter.jobs import JobFailed, Step, StepContext

OWNER: dict[str, JsonValue] = {"origin": "owner"}
WINDOW = timedelta(milliseconds=200)
INTERVAL = timedelta(milliseconds=10)
BODY = (
    "We moved 2,000 apps off the old identity provider in 14 months.\n\n"
    "## What the first 1,400 taught us\n\n"
    "Every app with a hard-coded redirect needed its own cut-over window.\n"
)


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


def scores(overall: int, low: int = 8) -> RubricScores:
    return RubricScores(
        clarity=low,
        structure=low,
        accuracy=low,
        depth=low,
        first_hand_specificity=low,
        voice=low,
        originality=low,
        actionability=low,
        overall=overall,
    )


async def add_guide(services: FakeServices) -> None:
    guide = sample_document(StyleGuide, status="active", approved_at=SAMPLE_NOW)
    await create_doc(services.store, guide)


async def add_topic(
    services: FakeServices, topic: str, *, pillar: str = "agents", **fields: Any
) -> EvalTopic:
    doc = sample_document(EvalTopic, pillar=pillar, topic=topic, **fields)
    return (await create_doc(services.store, doc)).doc


async def add_brief(services: FakeServices, topic: EvalTopic) -> str:
    brief = sample_document(Brief, topic=topic.topic, pillar=topic.pillar)
    await create_doc(services.store, brief)
    return brief.id


# --------------------------------------------------------------------------------------------
# Stand-ins for the research and pipeline children (they end like the real ones)
# --------------------------------------------------------------------------------------------


class Children:
    def __init__(self) -> None:
        self.research: list[ResearchRunInput] = []
        self.pipelines: list[PipelineInput] = []

    async def research_step(self, ctx: StepContext) -> JsonValue:
        services = current_services()
        payload = ResearchRunInput.from_json(ctx.input)
        self.research.append(payload)
        run = (await read_doc(services.store, ResearchRun, payload.run_id)).doc
        if "unreachable" in run.topic:
            failure = ChildResult(ok=False, error_code="research_failed")
            await finish_child(
                ctx, services.jobs, payload.parent, failure, window=WINDOW, interval=INTERVAL
            )
            raise JobFailed("research_failed")
        brief = sample_document(
            Brief, id=Brief.id_for(run.id), run_id=run.id, topic=run.topic, pillar=run.pillar
        )
        with contextlib.suppress(Conflict):
            await create_doc(services.store, brief)
        result = ChildResult(ok=True, ref=brief.id)
        await finish_child(
            ctx, services.jobs, payload.parent, result, window=WINDOW, interval=INTERVAL
        )
        return result.to_json()

    async def pipeline_step(self, ctx: StepContext) -> JsonValue:
        services = current_services()
        payload = PipelineInput.from_json(ctx.input)
        self.pipelines.append(payload)
        now = services.clock.now()
        await pipeline_write(services.store, payload.article_id, now=now, markdown=BODY)
        await transition(
            services.store, payload.article_id, ArticleStatus.IN_REVIEW, actor="pipeline", now=now
        )
        result = ChildResult(ok=True, ref=payload.article_id)
        await finish_child(
            ctx, services.jobs, payload.parent, result, window=WINDOW, interval=INTERVAL
        )
        return result.to_json()

    def registry_extra(self) -> dict[str, list[Step]]:
        return {
            RESEARCH_RUN: [Step("work", self.research_step)],
            PIPELINE_ARTICLE: [Step("work", self.pipeline_step)],
        }


# --------------------------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------------------------


async def test_an_eval_run_uses_the_real_pipeline_and_gate_one(services: FakeServices) -> None:
    await seed_policy(services)
    await add_guide(services)
    first = await add_topic(
        services,
        "MCP tool scopes per call",
        format="deep-explainer",
        created_at="2026-11-20T12:00:00.000000Z",
    )
    second = await add_topic(
        services,
        "Okta to Entra cut-over order",
        pillar="identity",
        created_at="2026-11-21T12:00:00.000000Z",
    )
    third = await add_topic(
        services,
        "An unreachable topic",
        pillar="governance",
        created_at="2026-11-22T12:00:00.000000Z",
    )
    await add_topic(services, "Inactive topic", active=False)
    runner(services).add(Task.CRITIQUE, scores(9), scores(6, low=5))
    children = Children()
    registry = analytics_registry(children.registry_extra())
    job = await run_job(
        services, ANALYTICS_EVAL, OWNER, job_id="analytics-eval-20261126", registry=registry
    )
    assert job.status == "succeeded", job.error
    run_id = job.id
    # Research: one child per topic without a brief, owner origin, keyed by topic position
    # (the children run in parallel, so they are compared in key order).
    research = sorted(children.research, key=lambda item: item.parent.key if item.parent else "")
    assert [(item.origin, item.parent.key if item.parent else None) for item in research] == [
        ("owner", "brief-0"),
        ("owner", "brief-1"),
        ("owner", "brief-2"),
    ]
    assert research[0].run_id == ResearchRun.id_for_eval(first.id, run_id)
    stored_first = (await read_doc(services.store, EvalTopic, first.id)).doc
    assert stored_first.brief_id == Brief.id_for(ResearchRun.id_for_eval(first.id, run_id))
    # Drafting: the real pipeline job type, from the draft step, no interview, as a child.
    pipelines = sorted(children.pipelines, key=lambda item: item.parent.key if item.parent else "")
    assert [
        (item.start_from, item.interview, item.origin, item.parent.key if item.parent else None)
        for item in pipelines
    ] == [("draft", "skip", "owner", "eval-0"), ("draft", "skip", "owner", "eval-1")]
    article_id = Article.id_for_eval(first.id, run_id)
    assert pipelines[0].article_id == article_id
    article = (await read_doc(services.store, Article, article_id)).doc
    assert article.purpose == "eval"
    assert article.brief_id == stored_first.brief_id
    assert (article.meta.pillar, article.meta.format) == ("agents", "deep-explainer")
    # Judging: gate 1 alone (two CRITIQUE calls, nothing else).
    assert [call.task for call in runner(services).calls] == [Task.CRITIQUE, Task.CRITIQUE]
    assert {call.origin for call in runner(services).calls} == {"owner"}
    results = {
        stored.doc.topic_id: stored.doc
        for stored in await find_docs(services.store, EvalResult, [Filter("runId", "==", run_id)])
    }
    assert set(results) == {first.id, second.id, third.id}
    passed = results[first.id]
    assert passed.id == EvalResult.id_for(first.id, run_id)
    assert (passed.judge_passed, passed.judge_score, passed.article_id) == (True, 9.0, article_id)
    assert passed.served_by
    assert passed.served_by[0].task == "critique"
    assert (results[second.id].judge_passed, results[second.id].judge_score) == (False, 6.0)
    failed = results[third.id]
    assert (failed.judge_passed, failed.article_id) == (None, None)
    assert error_code_of(failed) == "research_failed"
    assert job.result == {
        "runId": run_id,
        "results": 3,
        "judged": 2,
        "passed": 1,
        "pushed": True,
    }
    stored_job = (await services.store.read("jobs", run_id)).body
    assert "MCP tool scopes" not in str(stored_job)  # the job holds ids and codes only
    [(payload, urgency)] = services.notifier.sent
    assert (payload.title, urgency) == ("Eval drafts ready to label", "low")
    assert payload.url == f"/settings/system?evalRun={run_id}"


async def test_without_a_pipeline_the_run_fails_instead_of_waiting(
    services: FakeServices,
) -> None:
    await seed_policy(services)
    topic = await add_topic(services, "MCP tool scopes per call")
    brief_id = await add_brief(services, topic)
    await upsert_doc(services.store, topic.model_copy(update={"brief_id": brief_id}))
    services.jobs = StoreJobQueue(
        services.store, clock=services.clock, registered={ANALYTICS_EVAL, RESEARCH_RUN}
    )
    job = await run_job(services, ANALYTICS_EVAL, OWNER, job_id="analytics-eval-nopipe")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "pipeline_unavailable"
    article = (await read_doc(services.store, Article, Article.id_for_eval(topic.id, job.id))).doc
    assert (article.status, article.last_error) == (
        ArticleStatus.NEEDS_ATTENTION,
        "pipeline_unavailable",
    )
    assert article.brief_id == brief_id


async def test_the_judge_is_run_gates_with_the_rubric_only(
    services: FakeServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_policy(services)
    topic = await add_topic(services, "MCP tool scopes per call")
    brief_id = await add_brief(services, topic)
    await upsert_doc(services.store, topic.model_copy(update={"brief_id": brief_id}))
    asked: list[tuple[str, frozenset[GateName], Origin]] = []

    async def fake_run_gates(
        article: Article,
        *,
        services: Services,
        origin: Origin,
        job_id: str | None = None,
        gates: Collection[GateName] = (),
    ) -> GateReport:
        asked.append((article.id, frozenset(gates), origin))
        rubric = GateResult(
            gate=GateName.RUBRIC, verdict=GateVerdict.WARN, score=8.5, ran_at=SAMPLE_NOW
        )
        return GateReport(
            content_sha256=article.compute_sha256(), version=1, ran_at=SAMPLE_NOW, results=[rubric]
        )

    monkeypatch.setattr(evals, "run_gates", fake_run_gates)
    children = Children()
    job = await run_job(
        services,
        ANALYTICS_EVAL,
        OWNER,
        job_id="analytics-eval-judge",
        registry=analytics_registry(children.registry_extra()),
    )
    assert job.status == "succeeded", job.error
    assert children.research == []  # the topic's brief is reused
    article_id = Article.id_for_eval(topic.id, job.id)
    assert asked == [(article_id, frozenset({GateName.RUBRIC}), "owner")]
    result = (await read_doc(services.store, EvalResult, EvalResult.id_for(topic.id, job.id))).doc
    assert (result.judge_passed, result.judge_score) == (True, 8.5)


def test_judge_verdicts() -> None:
    def result(verdict: GateVerdict, score: float | None = 7.0) -> GateResult:
        return GateResult(gate=GateName.RUBRIC, verdict=verdict, score=score, ran_at=SAMPLE_NOW)

    assert judge_verdict(result(GateVerdict.PASS))[:3] == (True, 7.0, None)
    assert judge_verdict(result(GateVerdict.FAIL))[:3] == (False, 7.0, None)
    assert judge_verdict(result(GateVerdict.SKIPPED, None))[:3] == (None, None, "judge_skipped")
    assert judge_verdict(result(GateVerdict.ERROR))[:3] == (None, 7.0, "judge_error")
    assert judge_verdict(None)[:3] == (None, None, "judge_missing")


# --------------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------------


def eval_result(index: int, passed: bool | None, label: str | None, **fields: Any) -> EvalResult:
    values: dict[str, Any] = {
        "id": EvalResult.id_for(f"evt-{index}", "run-1"),
        "run_id": "run-1",
        "article_id": f"art-{index}",
        "judge_passed": passed,
        "owner_label": label,
        "created_at": f"2026-11-2{index}T12:00:00.000000Z",
    }
    return sample_document(EvalResult, **{**values, **fields})


def test_judge_calibration_math() -> None:
    results = [
        eval_result(1, True, "as_is"),
        eval_result(2, True, "needs_edits"),
        eval_result(3, True, None),  # not labelled yet: counts against the rate
        eval_result(4, False, "as_is"),  # the judge failed it: not part of the precision
        eval_result(5, None, None),  # the judge did not run: ignored
    ]
    stats = judge_stats(results)
    assert (stats.judge_passed, stats.labelled, stats.as_is) == (3, 2, 1)
    assert stats.as_is_rate == round(1 / 3, 4)
    assert stats.checked_at == "2026-11-24T12:00:00.000000Z"
    autonomy = AutonomySettings.default(SAMPLE_NOW)
    updated = with_judge(autonomy, stats, now="2026-11-26T17:00:00.000000Z")
    assert updated is not None
    assert updated.judge.as_is_rate == round(1 / 3, 4)
    assert (updated.judge.sample_size, updated.judge.threshold) == (3, 0.9)
    assert with_judge(updated, stats, now="2026-11-27T00:00:00.000000Z") is None
    assert judge_stats([eval_result(1, False, "as_is")]).as_is_rate is None


def test_nine_of_ten_meets_the_ninety_percent_bar() -> None:
    results = [eval_result(index, True, "as_is") for index in range(9)]
    results.append(eval_result(9, True, "reject"))
    stats = judge_stats(results)
    assert stats.as_is_rate == 0.9
    autonomy = with_judge(AutonomySettings.default(SAMPLE_NOW), stats, now=SAMPLE_NOW)
    assert autonomy is not None
    decision = auto_publish_decision(autonomy, now=TEST_NOW)
    assert "judge_below_threshold" not in decision.reasons
    assert "judge_uncalibrated" not in decision.reasons
    eight = judge_stats([*results[:8], eval_result(8, True, None), results[9]])
    assert eight.as_is_rate == 0.8


async def test_labelling_recomputes_the_judge(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    labelled = await create_doc(services.store, eval_result(1, True, None))
    await create_doc(services.store, eval_result(2, True, "as_is"))
    no_draft = await create_doc(services.store, eval_result(3, None, None, article_id=None))
    url = f"/api/evals/results/{labelled.doc.id}/label"
    response = await client.post(
        url, json={"label": "as_is"}, headers={**write_headers, "If-Match": labelled.etag}
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert (item["ownerLabel"], item["labelledAt"]) == ("as_is", "2026-11-26T17:00:00.000000Z")
    autonomy, _ = await read_singleton(services.store, AutonomySettings, now=SAMPLE_NOW)
    assert (autonomy.judge.as_is_rate, autonomy.judge.sample_size) == (1.0, 2)
    assert autonomy.judge.checked_at == "2026-11-22T12:00:00.000000Z"
    stale = await client.post(
        url, json={"label": "reject"}, headers={**write_headers, "If-Match": labelled.etag}
    )
    assert stale.status_code == 412
    not_ready = await client.post(
        f"/api/evals/results/{no_draft.doc.id}/label",
        json={"label": "as_is"},
        headers={**write_headers, "If-Match": no_draft.etag},
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["code"] == "eval_not_ready"
    bad = await client.post(
        url, json={"label": "great"}, headers={**write_headers, "If-Match": labelled.etag}
    )
    assert bad.status_code == 422


# --------------------------------------------------------------------------------------------
# Topic and run routes
# --------------------------------------------------------------------------------------------


async def test_topic_routes(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services, terms=["Northwind"])
    body = {"pillar": "agents", "format": "deep-explainer", "topic": "  MCP   tool scopes  "}
    created = await client.post("/api/evals/topics", json=body, headers=write_headers)
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    assert (item["topic"], item["active"], item["briefId"]) == ("MCP tool scopes", True, None)
    for change, code in (
        ({"pillar": "marketing"}, "unknown_pillar"),
        ({"format": "listicle"}, "unknown_format"),
        ({"topic": "Northwind identity cut-over"}, "confidential_text"),
    ):
        refused = await client.post(
            "/api/evals/topics", json={**body, **change}, headers=write_headers
        )
        assert refused.status_code == 422, change
        assert refused.json()["code"] == code
    topic_id = item["id"]
    brief = await create_doc(
        services.store, sample_document(Brief, topic="MCP tool scopes", pillar="agents")
    )
    stored = await read_doc(services.store, EvalTopic, topic_id)
    with_brief = await upsert_doc(
        services.store, stored.doc.model_copy(update={"brief_id": brief.doc.id})
    )
    url = f"/api/evals/topics/{topic_id}"
    kept = await client.put(
        url,
        json={**body, "format": "news-analysis"},
        headers={**write_headers, "If-Match": with_brief.etag},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["item"]["briefId"] == brief.doc.id  # the format does not change research
    refreshed = await client.put(
        url,
        json={**body, "refreshBrief": True},
        headers={**write_headers, "If-Match": kept.json()["etag"]},
    )
    assert refreshed.json()["item"]["briefId"] is None
    listed = await client.get("/api/evals/topics", headers=read_headers)
    assert [entry["item"]["id"] for entry in listed.json()["items"]] == [topic_id]
    deleted = await client.delete(
        url, headers={**write_headers, "If-Match": refreshed.json()["etag"]}
    )
    assert deleted.status_code == 204
    assert (await client.get("/api/evals/topics", headers=read_headers)).json()["items"] == []
    await seed_policy(services, blocklist_configured=False)
    unset = await client.post("/api/evals/topics", json=body, headers=write_headers)
    assert unset.status_code == 409
    assert unset.json()["code"] == "blocklist_not_configured"


async def test_at_most_twenty_active_topics(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    for index in range(MAX_ACTIVE_TOPICS):
        await add_topic(services, f"Topic number {index}")
    body = {"pillar": "agents", "format": "framework", "topic": "One too many"}
    full = await client.post("/api/evals/topics", json=body, headers=write_headers)
    assert full.status_code == 409
    assert full.json()["code"] == "too_many_topics"
    inactive = await client.post(
        "/api/evals/topics", json={**body, "active": False}, headers=write_headers
    )
    assert inactive.status_code == 201


async def test_run_route_checks_and_starts_one_run(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    async def code() -> str:
        response = await client.post("/api/evals/runs", headers=write_headers)
        assert response.status_code == 409, response.text
        return str(response.json()["code"])

    assert await code() == "no_eval_topics"
    await add_topic(services, "MCP tool scopes per call")
    assert await code() == "voice_guide_missing"
    await add_guide(services)
    assert await code() == "blocklist_not_configured"
    await seed_policy(services, ai_use_confirmed=False)
    assert await code() == "ai_use_not_confirmed"
    await seed_policy(services)
    started = await client.post("/api/evals/runs", headers=write_headers)
    assert started.status_code == 202
    assert started.json() == {"jobId": "analytics-eval-20261126", "status": "queued"}
    again = await client.post("/api/evals/runs", headers=write_headers)
    assert again.json()["jobId"] == "analytics-eval-20261126"
    job = await services.jobs.get("analytics-eval-20261126")
    assert (job.type, job.input) == (ANALYTICS_EVAL, {"origin": "owner"})


async def test_results_list_by_run(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    await create_doc(services.store, eval_result(1, True, None))
    other = sample_document(
        EvalResult,
        id=EvalResult.id_for("evt-x", "run-2"),
        run_id="run-2",
        created_at="2026-11-25T12:00:00.000000Z",
    )
    await create_doc(services.store, other)
    response = await client.get(
        "/api/evals/results", params={"runId": "run-1"}, headers=read_headers
    )
    assert [entry["item"]["runId"] for entry in response.json()["items"]] == ["run-1"]
    everything = await client.get("/api/evals/results", headers=read_headers)
    assert [entry["item"]["runId"] for entry in everything.json()["items"]] == ["run-2", "run-1"]
