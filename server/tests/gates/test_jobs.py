"""The gates jobs on a real JobRunner: steps, the blocklist stop, stale reports, child results,
passages never stored, and the standards refresh."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import JsonValue

from blogwriter.db import CONTAINERS
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.docstore import read_doc, read_singleton
from blogwriter.domain.jobflow import enqueue_once, wait_for_child
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    GATES_RUN,
    GATES_STANDARDS_REFRESH,
    ChildResult,
    GatesRunInput,
    ParentRef,
    gates_job_id,
)
from blogwriter.domain.ports import services_installed
from blogwriter.domain.settings import StandardsCatalog
from blogwriter.domain.testing import FakeServices
from blogwriter.gates import register_jobs
from blogwriter.gates.jobs import GATES_RUN_STEPS, STALE_EVENT
from blogwriter.gates.settings import gates_settings
from blogwriter.gates.standards import seed_families
from blogwriter.gates.testing import (
    BLOCKED_TERM,
    SAMPLE_PASSAGE,
    GateWorld,
    gate_world,
    script_passing_ai,
)
from blogwriter.jobs import Job, JobRegistry, JobRunner, Step, StepContext
from blogwriter.jobs.testing import eventually, fast_settings


def registry() -> JobRegistry:
    built = JobRegistry()
    register_jobs(built)
    return built


async def run_job(services: FakeServices, job_id: str, *, extra: JobRegistry | None = None) -> Job:
    runner = JobRunner(
        services.store, extra or registry(), settings=fast_settings(), clock=services.clock
    )
    with services_installed(services):
        await runner.start()
        try:

            async def finished() -> bool:
                return (await services.jobs.get(job_id)).is_terminal

            await eventually(finished, within=15)
        finally:
            await runner.stop()
    return await services.jobs.get(job_id)


async def enqueue_run(w: GateWorld, *, parent: ParentRef | None = None) -> Job:
    sha = w.article.compute_sha256()
    payload = GatesRunInput(
        article_id=w.article.id, content_sha256=sha, origin="owner", parent=parent
    ).to_json()
    return await enqueue_once(
        w.services.jobs, GATES_RUN, payload, job_id=gates_job_id(w.article.id, sha)
    )


def event_types(job: Job) -> list[str]:
    return [event.type for event in job.events]


async def test_registration() -> None:
    built = registry()
    assert built.names() == (GATES_RUN, GATES_STANDARDS_REFRESH)
    gates_type = built.get(GATES_RUN)
    assert gates_type is not None
    assert gates_type.step_names == GATES_RUN_STEPS


async def test_a_clean_run_saves_a_complete_report(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = await gate_world()
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    job = await enqueue_run(w)
    done = await run_job(w.services, job.id)
    assert done.status == "succeeded", done.error
    assert [step.status for step in done.steps] == ["succeeded"] * len(GATES_RUN_STEPS)
    assert ENTITY_CHANGED in event_types(done)
    assert isinstance(done.result, dict)
    assert done.result["saved"] is True
    assert done.result["autoPublishReady"] is True
    article = (await read_doc(w.services.store, Article, w.article.id)).doc
    assert article.gates is not None
    assert article.gates.complete
    assert article.gates.auto_publish_ready
    assert article.gates.job_id == job.id
    assert article.embedding is not None
    # Fact-check passages are never stored: not in any document, event or log line.
    fingerprint = "test provisioning on a pilot group"  # in the passage only, not the brief
    assert fingerprint in SAMPLE_PASSAGE
    for container in CONTAINERS:
        for doc in await w.services.store.find(container, []):
            assert fingerprint not in json.dumps(doc.body), container
    assert fingerprint not in caplog.text


async def test_a_blocklisted_article_stops_after_the_deterministic_step() -> None:
    w = await gate_world()
    body = w.article.markdown.replace("Last March", f"Last March, at {BLOCKED_TERM},")
    current = await read_doc(w.services.store, Article, w.article.id)
    changed = current.doc.model_copy(update={"markdown": body})
    changed = changed.model_copy(update={"content_sha256": changed.compute_sha256()})
    await w.services.store.replace("articles", changed.to_document(), if_match=current.etag)
    job = await enqueue_run(GateWorld(w.services, changed, w.brief, w.story))
    done = await run_job(w.services, job.id)
    assert done.status == "succeeded"
    assert w.services.ai is not None
    assert w.services.ai.calls == []
    assert w.services.classifier is not None
    assert w.services.classifier.calls == []
    article = (await read_doc(w.services.store, Article, w.article.id)).doc
    assert article.gates is not None
    by_gate = {result.gate: result.verdict.value for result in article.gates.results}
    assert by_gate[GateName.CONFIDENTIALITY] == "fail"
    assert {by_gate[g] for g in (GateName.RUBRIC, GateName.FACT_CHECK, GateName.LINT)} == {
        "skipped"
    }
    assert BLOCKED_TERM not in json.dumps(done.model_dump(mode="json"))


async def test_an_edit_before_the_run_makes_the_report_stale() -> None:
    w = await gate_world()
    job = await enqueue_run(w)
    current = await read_doc(w.services.store, Article, w.article.id)
    edited = current.doc.model_copy(update={"markdown": current.doc.markdown + "\nMore.\n"})
    await w.services.store.replace("articles", edited.to_document(), if_match=current.etag)
    done = await run_job(w.services, job.id)
    assert done.status == "succeeded"
    assert STALE_EVENT in event_types(done)
    assert ENTITY_CHANGED not in event_types(done)
    assert isinstance(done.result, dict)
    assert done.result["saved"] is False
    assert done.result["errorCode"] == "report_stale"
    assert (await read_doc(w.services.store, Article, w.article.id)).doc.gates is None
    assert w.services.ai is not None
    assert w.services.ai.calls == []


async def test_a_missing_article_fails_the_job() -> None:
    w = await gate_world(store_article=False)
    job = await enqueue_run(w)
    done = await run_job(w.services, job.id)
    assert done.status == "failed"
    assert done.error is not None
    assert done.error.code == "article_not_found"


async def test_a_parent_gets_the_child_result() -> None:
    w = await gate_world()
    assert w.services.ai is not None
    script_passing_ai(w.services.ai, story_id=w.story.id)
    queue = w.services.jobs
    sha = w.article.compute_sha256()
    child_id = gates_job_id(w.article.id, sha)

    async def parent_step(ctx: StepContext) -> JsonValue:
        parent = ParentRef(job_id=ctx.job_id, key="gates")
        payload = GatesRunInput(
            article_id=w.article.id, content_sha256=sha, origin="owner", parent=parent
        ).to_json()
        await enqueue_once(queue, GATES_RUN, payload, job_id=child_id)
        result = await wait_for_child(ctx, queue, child_id, key="gates", prompt="Gates running.")
        return result.to_json()

    combined = registry()
    combined.register("test.parent", [Step("wait", parent_step)])
    await queue.enqueue("test.parent", None, job_id="parent-1")
    parent = await run_job(w.services, "parent-1", extra=combined)
    assert parent.status == "succeeded"
    result = ChildResult.from_json(parent.result)
    assert (result.ok, result.ref) == (True, w.article.id)
    child = await queue.get(child_id)
    assert child.status == "succeeded"


async def test_the_standards_refresh_saves_the_seeds() -> None:
    w = await gate_world()
    job = await w.services.jobs.enqueue(GATES_STANDARDS_REFRESH, {})
    done = await run_job(w.services, job.id)
    assert done.status == "succeeded"
    assert isinstance(done.result, dict)
    assert done.result["atlas"] == "skipped"
    catalog, etag = await read_singleton(
        w.services.store, StandardsCatalog, now="2026-11-26T12:00:00.000000Z"
    )
    assert etag is not None
    assert catalog.refreshed_at is not None
    for family, ids in seed_families().items():
        assert set(ids) <= set(catalog.families[family])
    assert ENTITY_CHANGED in event_types(done)


ATLAS_URL = "https://raw.example.org/atlas/ATLAS.yaml"
ATLAS = b"id: ATLAS\nversion: 5.3.0\nmatrices:\n- techniques:\n  - id: AML.T0120\n"


async def test_the_refresh_merges_the_atlas_data_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_GATES_ATLAS_URL", ATLAS_URL)
    gates_settings.cache_clear()
    w = await gate_world()
    w.services.egress.add(ATLAS_URL, ATLAS, content_type="application/yaml")
    job = await w.services.jobs.enqueue(GATES_STANDARDS_REFRESH, {"origin": "owner"})
    done = await run_job(w.services, job.id)
    assert done.status == "succeeded"
    catalog, _ = await read_singleton(
        w.services.store, StandardsCatalog, now="2026-11-26T12:00:00.000000Z"
    )
    assert "AML.T0120" in catalog.families["mitre_atlas"]
    assert catalog.source_versions["mitre_atlas"] == "5.3.0"
    assert w.services.egress.requested == [ATLAS_URL]


async def test_a_failed_atlas_fetch_keeps_the_old_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_GATES_ATLAS_URL", ATLAS_URL)
    gates_settings.cache_clear()
    w = await gate_world()
    w.services.egress.add(ATLAS_URL, ATLAS, content_type="application/yaml")
    first = await w.services.jobs.enqueue(GATES_STANDARDS_REFRESH, {})
    assert (await run_job(w.services, first.id)).status == "succeeded"
    del w.services.egress.responses[ATLAS_URL]  # now a 404
    second = await w.services.jobs.enqueue(GATES_STANDARDS_REFRESH, {})
    done = await run_job(w.services, second.id)
    assert done.status == "failed"
    assert done.error is not None
    assert done.error.code == "atlas_unavailable"
    catalog, _ = await read_singleton(
        w.services.store, StandardsCatalog, now="2026-11-26T12:00:00.000000Z"
    )
    assert "AML.T0120" in catalog.families["mitre_atlas"]
