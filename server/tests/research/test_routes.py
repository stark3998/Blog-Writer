"""The research routes through the real app (auth, CSRF, problems)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.common import Citation, Quote
from blogwriter.domain.docstore import create_doc, read_doc, update_doc
from blogwriter.domain.jobtypes import ChildResult, ParentRef, research_job_id
from blogwriter.domain.research import Brief, BriefClaim, BriefFinding, BriefStat, ResearchRun
from blogwriter.domain.states import ResearchStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, make_app
from blogwriter.jobs import Job
from blogwriter.research import get_router
from blogwriter.research.run import start_run
from blogwriter.research.testing import (
    BLOCKED_TERM,
    OWNER_URL,
    new_run,
    page_fetcher,
    research_services,
    save_blocklist,
)

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}


@pytest.fixture
def services() -> FakeServices:
    return research_services(fetcher=page_fetcher())


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(make_app(services, [get_router()], settings=SETTINGS)) as http:
        yield http


def body(response: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()
    return data


# ---------------------------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------------------------


async def test_creating_a_run_enqueues_the_owner_job(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    response = await client.post(
        "/api/research/runs",
        json={
            "topic": "Agent identities in Microsoft Entra",
            "pillar": "agents",
            "audience": "identity engineers",
            "recencyDays": 180,
            "articleId": "art-0000000000000000000000000001",
            "ownerUrls": [OWNER_URL],
        },
        headers=WRITE,
    )
    assert response.status_code == 202, response.text
    data = body(response)
    run = data["run"]["item"]
    assert run["status"] == "queued"
    assert run["recencyDays"] == 180
    assert run["ownerUrls"] == [OWNER_URL]
    assert data["jobId"] == research_job_id(run["id"]) == run["jobId"]
    assert data["run"]["etag"]
    job = await services.jobs.get(data["jobId"])
    assert job.type == "research.run"
    assert job.input == {"origin": "owner", "runId": run["id"], "parent": None}


async def test_a_blocklisted_topic_is_a_422_without_the_term(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    response = await client.post(
        "/api/research/runs",
        json={"topic": f"How {BLOCKED_TERM} moved to Entra", "pillar": "identity"},
        headers=WRITE,
    )
    assert response.status_code == 422
    problem = body(response)
    assert problem["code"] == "confidential_text"
    assert [finding["field"] for finding in problem["findings"]] == ["topic"]
    assert problem["findings"][0]["ref"] == "t-1"
    assert BLOCKED_TERM not in response.text
    assert services.jobs.enqueued == []


async def test_an_unsaved_blocklist_is_a_409(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/research/runs", json={"topic": "Agent identities", "pillar": "agents"}, headers=WRITE
    )
    assert response.status_code == 409
    assert body(response)["code"] == "blocklist_not_configured"


@pytest.mark.parametrize(
    "payload",
    [
        {"topic": "ab", "pillar": "agents"},
        {"topic": "Agent identities", "pillar": "Not A Slug"},
        {"topic": "Agent identities", "pillar": "agents", "ownerUrls": ["http://example.org/a"]},
        {"topic": "Agent identities", "pillar": "agents", "ownerUrls": [OWNER_URL] * 11},
        {"topic": "Agent identities", "pillar": "agents", "recencyDays": 0},
        {"topic": "Agent identities", "pillar": "agents", "status": "succeeded"},
    ],
)
async def test_run_input_is_validated(
    client: httpx.AsyncClient, services: FakeServices, payload: dict[str, object]
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    response = await client.post("/api/research/runs", json=payload, headers=WRITE)
    assert response.status_code == 422


async def test_writes_need_the_owner_cookie(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/research/runs", json={"topic": "Agent identities", "pillar": "agents"}, headers=READ
    )
    assert response.status_code == 403


async def test_runs_list_filter_and_page(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    article = "art-0000000000000000000000000001"
    ids: list[str] = []
    for index in range(3):
        services.clock.advance(1)
        stored = await new_run(services, topic=f"Topic number {index}")
        if index < 2:

            def link(run: ResearchRun) -> ResearchRun:
                return run.model_copy(update={"article_id": article, "updated_at": run.created_at})

            await update_doc(services.store, ResearchRun, stored.doc.id, link)
        ids.append(stored.doc.id)

    first = await client.get(f"/api/research/runs?articleId={article}&limit=1", headers=READ)
    assert first.status_code == 200
    page = body(first)
    assert [item["item"]["id"] for item in page["items"]] == [ids[1]]
    assert page["items"][0]["etag"]
    second = await client.get(
        f"/api/research/runs?articleId={article}&limit=1&cursor={page['nextCursor']}", headers=READ
    )
    assert [item["item"]["id"] for item in body(second)["items"]] == [ids[0]]
    assert body(second)["nextCursor"] is None
    everything = body(await client.get("/api/research/runs?status=queued", headers=READ))
    assert [item["item"]["id"] for item in everything["items"]] == list(reversed(ids))
    none = body(await client.get("/api/research/runs?status=succeeded", headers=READ))
    assert none["items"] == []
    bad = await client.get("/api/research/runs?cursor=%%%", headers=READ)
    assert bad.status_code == 422
    assert body(bad)["code"] == "invalid_cursor"


async def test_get_one_run(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    stored = await new_run(services)
    response = await client.get(f"/api/research/runs/{stored.doc.id}", headers=READ)
    assert response.status_code == 200
    assert response.headers["etag"] == stored.etag == body(response)["etag"]
    missing = await client.get("/api/research/runs/run-missing", headers=READ)
    assert missing.status_code == 404


# ---------------------------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------------------------


async def test_cancel_needs_a_fresh_etag(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    stored = await new_run(services)
    url = f"/api/research/runs/{stored.doc.id}/cancel"
    assert (await client.post(url, headers=WRITE)).status_code == 428
    stale = await client.post(url, headers={**WRITE, "If-Match": '"stale"'})
    assert stale.status_code == 412
    assert body(stale)["current"]["etag"] == stored.etag
    assert body(stale)["current"]["status"] == "queued"


async def test_cancel_cancels_the_job_and_the_run(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    stored = await new_run(services)
    job = await start_run(services, stored.doc, origin="owner")
    url = f"/api/research/runs/{stored.doc.id}/cancel"

    response = await client.post(url, headers={**WRITE, "If-Match": stored.etag})

    assert response.status_code == 200, response.text
    item = body(response)["item"]
    assert item["status"] == "cancelled"
    assert item["errorCode"] == "cancelled"
    assert (await services.jobs.get(job.id)).status == "cancelled"
    again = await client.post(url, headers={**WRITE, "If-Match": body(response)["etag"]})
    assert again.status_code == 409
    assert body(again)["code"] == "invalid_transition"


async def test_cancel_works_when_the_job_already_ended(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    stored = await new_run(services)

    def running(run: ResearchRun) -> ResearchRun:
        return run.model_copy(update={"status": ResearchStatus.RUNNING})

    current = await update_doc(services.store, ResearchRun, stored.doc.id, running)
    response = await client.post(
        f"/api/research/runs/{stored.doc.id}/cancel", headers={**WRITE, "If-Match": current.etag}
    )
    assert response.status_code == 200
    assert body(response)["item"]["status"] == "cancelled"


async def test_cancel_tells_a_waiting_parent(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    stamp = SAMPLE_NOW
    parent = Job.model_validate(
        {
            "id": "pipeline-art-1-1",
            "type": "pipeline.article",
            "status": "waiting_input",
            "runAfter": stamp,
            "createdAt": stamp,
            "updatedAt": stamp,
            "awaiting": {
                "step": "research",
                "key": "research",
                "prompt": "Research is running.",
                "requestedAt": stamp,
            },
            "steps": [{"name": "research", "status": "waiting_input"}],
        }
    )
    await services.store.create("jobs", parent.to_body())
    stored = await new_run(services)
    await start_run(
        services, stored.doc, origin="owner", parent=ParentRef(job_id=parent.id, key="research")
    )

    response = await client.post(
        f"/api/research/runs/{stored.doc.id}/cancel", headers={**WRITE, "If-Match": stored.etag}
    )

    assert response.status_code == 200
    resumed = await services.jobs.get(parent.id)
    assert resumed.status == "queued"
    research = resumed.step("research")
    assert research is not None
    assert ChildResult.from_json(research.inputs["research"]) == ChildResult(
        ok=False, error_code="cancelled"
    )


# ---------------------------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------------------------

NIST = "src-nist-gov-aaaaaa"
OWASP = "src-owasp-org-bbbbbb"


async def a_brief(services: FakeServices) -> Brief:
    brief = Brief(
        id=Brief.id_for("run-0000000000000000000000000001"),
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        run_id="run-0000000000000000000000000001",
        topic="Agent identities",
        pillar="agents",
        executive_summary="What changed and why it matters.",
        findings=[
            BriefFinding(id="f-1", text="Finding one.", citation_ids=[NIST, OWASP]),
            BriefFinding(id="f-2", text="Finding two.", citation_ids=[OWASP]),
        ],
        stats=[
            BriefStat(
                id="st-1",
                value="62%",
                label="Teams sharing one principal",
                key=True,
                citation_ids=[NIST, OWASP],
            ),
        ],
        claims=[
            BriefClaim(id="cl-1", text="Claim one.", citation_id=NIST, quote_ids=["q-1"]),
            BriefClaim(id="cl-2", text="Claim two.", citation_id=OWASP),
        ],
        quotes=[Quote(id="q-1", citation_id=NIST, text="shall offer a phishing-resistant option")],
        citations=[
            Citation(id=NIST, url="https://nist.gov/a", title="NIST", domain="nist.gov"),
            Citation(id=OWASP, url="https://owasp.org/b", title="OWASP", domain="owasp.org"),
        ],
        hook_seeds=["A seed."],
    )
    await create_doc(services.store, brief)
    return brief


async def test_get_a_brief(client: httpx.AsyncClient, services: FakeServices) -> None:
    brief = await a_brief(services)
    response = await client.get(f"/api/research/briefs/{brief.id}", headers=READ)
    assert response.status_code == 200
    assert body(response)["item"]["executiveSummary"] == "What changed and why it matters."
    assert (await client.get("/api/research/briefs/brief-none", headers=READ)).status_code == 404


async def test_the_owner_edits_text_and_removes_items(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    brief = await a_brief(services)
    etag = (await read_doc(services.store, Brief, brief.id)).etag
    services.clock.advance(60)
    response = await client.put(
        f"/api/research/briefs/{brief.id}",
        json={
            "executiveSummary": "My own summary.",
            "findings": [{"id": "f-2", "text": "Finding two, sharper."}],
            "hookSeeds": [],
            "angles": ["My angle."],
            "removeCitationIds": [NIST],
            "removeClaimIds": ["cl-9"],
        },
        headers={**WRITE, "If-Match": etag},
    )
    assert response.status_code == 200, response.text
    item = body(response)["item"]
    assert item["editedByOwner"] is True
    assert item["executiveSummary"] == "My own summary."
    assert item["findings"] == [
        {"id": "f-2", "text": "Finding two, sharper.", "citationIds": [OWASP]}
    ]
    assert [claim["id"] for claim in item["claims"]] == ["cl-2"]
    assert item["quotes"] == []
    assert [citation["id"] for citation in item["citations"]] == [OWASP]
    assert item["stats"][0]["citationIds"] == [OWASP]
    assert item["stats"][0]["key"] is False
    assert item["hookSeeds"] == []
    assert item["angles"] == ["My angle."]
    assert item["updatedAt"] == "2026-11-26T12:01:00.000000Z"


async def test_brief_edits_are_checked(client: httpx.AsyncClient, services: FakeServices) -> None:
    await save_blocklist(services.store, BLOCKED_TERM)
    brief = await a_brief(services)
    url = f"/api/research/briefs/{brief.id}"
    etag = (await read_doc(services.store, Brief, brief.id)).etag
    assert (await client.put(url, json={}, headers=WRITE)).status_code == 428
    stale = await client.put(url, json={}, headers={**WRITE, "If-Match": '"old"'})
    assert stale.status_code == 412
    added = await client.put(
        url, json={"findings": [{"id": "f-9", "text": "New."}]}, headers={**WRITE, "If-Match": etag}
    )
    assert added.status_code == 422
    assert body(added)["code"] == "unknown_finding"
    secret = await client.put(
        url,
        json={"angles": [f"What {BLOCKED_TERM} taught me"]},
        headers={**WRITE, "If-Match": etag},
    )
    assert secret.status_code == 422
    assert body(secret)["code"] == "confidential_text"
    assert body(secret)["findings"][0]["field"] == "angles[0]"
    assert BLOCKED_TERM not in secret.text
    sneaky = await client.put(
        url,
        json={"quotes": [{"id": "q-2", "citationId": NIST, "text": "added"}]},
        headers={**WRITE, "If-Match": etag},
    )
    assert sneaky.status_code == 422  # the owner never adds quotes or citations
    unchanged = (await read_doc(services.store, Brief, brief.id)).doc
    assert unchanged == brief
