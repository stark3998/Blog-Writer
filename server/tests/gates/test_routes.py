"""The gates routes through the real app (auth, CSRF, problems)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as csrf_headers
from blogwriter.domain.articles import Article, GateName
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.jobflow import rerun_job_id
from blogwriter.domain.jobtypes import GATES_RUN, GATES_STANDARDS_REFRESH, gates_job_id
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import make_app, make_services
from blogwriter.gates import SlopDetectors, get_router, run_gates_detailed, save_report
from blogwriter.gates.testing import (
    BLOCKED_TERM,
    SAMPLE_BODY,
    SAMPLE_META,
    GateWorld,
    gate_world,
    script_passing_ai,
)

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **csrf_headers(SETTINGS)}


@pytest.fixture
async def world() -> GateWorld:
    return await gate_world(services=make_services(embedder=None))


@pytest.fixture
async def client(world: GateWorld) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(world.services, routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        yield http


async def gated(world: GateWorld) -> str:
    """Run the gates and save the report; return the article's etag."""
    assert world.services.ai is not None
    script_passing_ai(world.services.ai, story_id=world.story.id)
    outcome = await run_gates_detailed(world.article, services=world.services, origin="owner")
    stored = await save_report(
        world.services.store, world.article.id, outcome.report, now=world.services.clock.now()
    )
    assert stored is not None
    return stored.etag


async def test_run_enqueues_once_and_reruns_finished_jobs(
    client: httpx.AsyncClient, world: GateWorld
) -> None:
    url = f"/api/articles/{world.article.id}/gates/run"
    first = await client.post(url, headers=WRITE)
    assert first.status_code == 202, first.text
    job_id = gates_job_id(world.article.id, world.article.compute_sha256())
    assert first.json() == {"jobId": job_id, "status": "queued"}
    again = await client.post(url, headers=WRITE)
    assert again.json()["jobId"] == job_id  # still active: the same run
    await world.services.jobs.cancel(job_id)
    rerun = await client.post(url, headers=WRITE)
    assert rerun.json()["jobId"] == rerun_job_id(job_id, 1)
    job = await world.services.jobs.get(job_id)
    assert job.type == GATES_RUN
    assert job.input == {
        "origin": "owner",
        "articleId": world.article.id,
        "contentSha256": world.article.compute_sha256(),
        "parent": None,
    }


async def test_run_is_refused_in_pipeline_states_and_for_missing_articles(
    client: httpx.AsyncClient, world: GateWorld
) -> None:
    current = await read_doc(world.services.store, Article, world.article.id)
    drafting = current.doc.model_copy(update={"status": ArticleStatus.DRAFTING})
    await world.services.store.replace("articles", drafting.to_document(), if_match=current.etag)
    refused = await client.post(f"/api/articles/{world.article.id}/gates/run", headers=WRITE)
    assert refused.status_code == 409
    assert refused.json()["code"] == "read_only_state"
    assert (refused.json()["state"], refused.json()["writer"]) == ("drafting", "pipeline")
    missing = await client.post("/api/articles/art-missing/gates/run", headers=WRITE)
    assert missing.status_code == 404
    no_csrf = await client.post(f"/api/articles/{world.article.id}/gates/run", headers=READ)
    assert no_csrf.status_code == 403


async def test_the_view_without_and_with_a_report(
    client: httpx.AsyncClient, world: GateWorld
) -> None:
    url = f"/api/articles/{world.article.id}/gates"
    empty = await client.get(url, headers=READ)
    assert empty.status_code == 200
    assert empty.json() == {
        "report": None,
        "stale": False,
        "autoPublishReady": False,
        "manualPublishReady": False,
        "blocking": [gate.value for gate in GateName],
    }
    etag = await gated(world)
    view = await client.get(url, headers=READ)
    assert view.headers["ETag"] == etag
    body = view.json()
    assert body["stale"] is False
    assert body["autoPublishReady"] is False  # no embedder: originality fails closed
    assert body["blocking"] == ["originality"]
    assert len(body["report"]["results"]) == 7


async def test_override_an_overridable_gate(client: httpx.AsyncClient, world: GateWorld) -> None:
    etag = await gated(world)
    url = f"/api/articles/{world.article.id}/gates/originality/override"
    missing = await client.post(url, json={"reason": "Checked by hand."}, headers=WRITE)
    assert missing.status_code == 428
    stale = await client.post(
        url, json={"reason": "Checked by hand."}, headers={**WRITE, "If-Match": '"stale"'}
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == etag
    short = await client.post(url, json={"reason": "  x "}, headers={**WRITE, "If-Match": etag})
    assert short.status_code == 422
    done = await client.post(
        url, json={"reason": "Novelty checked by hand."}, headers={**WRITE, "If-Match": etag}
    )
    assert done.status_code == 200, done.text
    item = done.json()["item"]
    result = next(r for r in item["gates"]["results"] if r["gate"] == "originality")
    assert result["override"]["reason"] == "Novelty checked by hand."
    assert item["version"] == world.article.version
    assert done.headers["ETag"] == done.json()["etag"]
    view = (await client.get(f"/api/articles/{world.article.id}/gates", headers=READ)).json()
    assert view["manualPublishReady"] is True
    assert view["autoPublishReady"] is False


async def test_overrides_that_are_not_allowed(client: httpx.AsyncClient, world: GateWorld) -> None:
    etag = await gated(world)
    base = f"/api/articles/{world.article.id}/gates"
    passed = await client.post(
        f"{base}/rubric/override", json={"reason": "No need."}, headers={**WRITE, "If-Match": etag}
    )
    assert (passed.status_code, passed.json()["code"]) == (409, "not_overridable")
    unknown = await client.post(
        f"{base}/vibes/override", json={"reason": "Nope."}, headers={**WRITE, "If-Match": etag}
    )
    assert unknown.status_code == 404
    current = await read_doc(world.services.store, Article, world.article.id)
    edited = current.doc.model_copy(update={"markdown": SAMPLE_BODY + "\nEdited.\n"})
    stored = await world.services.store.replace(
        "articles", edited.to_document(), if_match=current.etag
    )
    stale = await client.post(
        f"{base}/originality/override",
        json={"reason": "Checked."},
        headers={**WRITE, "If-Match": stored.etag},
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "report_stale")


async def test_a_hard_failure_cannot_be_overridden(world: GateWorld) -> None:
    body = SAMPLE_BODY.replace("Last March", f"Last March, at {BLOCKED_TERM},")
    current = await read_doc(world.services.store, Article, world.article.id)
    changed = current.doc.model_copy(update={"markdown": body})
    changed = changed.model_copy(update={"content_sha256": changed.compute_sha256()})
    await world.services.store.replace("articles", changed.to_document(), if_match=current.etag)
    outcome = await run_gates_detailed(changed, services=world.services, origin="owner")
    stored = await save_report(
        world.services.store, world.article.id, outcome.report, now=world.services.clock.now()
    )
    assert stored is not None
    app = make_app(world.services, routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        refused = await http.post(
            f"/api/articles/{world.article.id}/gates/confidentiality/override",
            json={"reason": "It is fine."},
            headers={**WRITE, "If-Match": stored.etag},
        )
    assert (refused.status_code, refused.json()["code"]) == (409, "not_overridable")


async def test_live_lint_needs_the_slop_port() -> None:
    app = make_app(make_services(slop=None), routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        unavailable = await http.post("/api/gates/lint", json={"markdown": "Text."}, headers=WRITE)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "slop_unavailable"


async def test_live_lint() -> None:
    world = await gate_world(services=make_services(slop=SlopDetectors()))
    app = make_app(world.services, routers=[get_router()], settings=SETTINGS)
    markdown = (
        f"Security isn't a tool, it's a mindset. We worked for {BLOCKED_TERM} on LLM11.\n\n"
        "See [this](https://random.example.net/x?utm_source=feed).\n"
    )
    async with asgi_client(app) as http:
        response = await http.post(
            "/api/gates/lint",
            json={
                "meta": SAMPLE_META.model_dump(mode="json"),
                "markdown": markdown,
                "briefId": world.brief.id,
            },
            headers=WRITE,
        )
    assert response.status_code == 200, response.text
    codes = {finding["code"] for finding in response.json()["findings"]}
    assert {
        "isnt-x-its-y",
        "blocklisted-term",
        "standard-id-unknown",
        "outbound-domain",
        "tracking-parameter",
    } <= codes
    assert BLOCKED_TERM not in response.text


async def test_lint_limits(client: httpx.AsyncClient) -> None:
    too_large = "a" * (512 * 1024 + 1)
    response = await client.post("/api/gates/lint", json={"markdown": too_large}, headers=WRITE)
    assert (response.status_code, response.json()["code"]) == (413, "markdown_too_large")
    missing = await client.post(
        "/api/gates/lint", json={"markdown": "Text.", "briefId": "brief-missing"}, headers=WRITE
    )
    assert missing.status_code == 404
    unknown_field = await client.post(
        "/api/gates/lint", json={"markdown": "Text.", "other": 1}, headers=WRITE
    )
    assert unknown_field.status_code == 422


async def test_standards_catalog_and_refresh(client: httpx.AsyncClient, world: GateWorld) -> None:
    catalog = await client.get("/api/gates/standards", headers=READ)
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["etag"] is None
    assert "LLM01" in body["item"]["families"]["owasp_llm"]
    assert body["item"]["refreshedAt"] is None
    refresh = await client.post("/api/gates/standards/refresh", headers=WRITE)
    assert refresh.status_code == 202
    job = await world.services.jobs.get(refresh.json()["jobId"])
    assert job.type == GATES_STANDARDS_REFRESH
    assert job.input == {"origin": "owner"}
