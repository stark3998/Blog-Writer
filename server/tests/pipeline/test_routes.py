"""The pipeline routes: start (and every refusal), cancel, owner revisions (with a run of
``pipeline.revise``) and the selection proposal."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.articles import Article, ArticleMeta, CalibrationFlags
from blogwriter.domain.docstore import create_doc, read_doc, update_doc
from blogwriter.domain.jobtypes import (
    PIPELINE_ARTICLE,
    PIPELINE_REVISE,
    PipelineInput,
    ReviseInput,
    pipeline_job_id,
)
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, ScriptedReply
from blogwriter.jobs import InputRequest, Job
from blogwriter.pipeline.testing import (
    BLOCKED,
    CITATION_ID,
    draft_body,
    new_article,
    pipeline_registry,
    running,
    sample_brief,
    script_gates,
    seed_world,
    wait_job,
)
from blogwriter.pipeline.text import bind_citations, clean_draft

from .conftest import WRITE

S = ArticleStatus
BODY = bind_citations(clean_draft(draft_body()), sample_brief().citations).text


async def etag(services: FakeServices, article_id: str) -> str:
    return (await read_doc(services.store, Article, article_id)).etag


def write(etag_value: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag_value}


async def start(
    client: httpx.AsyncClient,
    services: FakeServices,
    article: Article,
    start_from: str = "research",
) -> httpx.Response:
    return await client.post(
        f"/api/articles/{article.id}/pipeline",
        json={"startFrom": start_from},
        headers=write(await etag(services, article.id)),
    )


# --------------------------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------------------------


async def test_start_moves_the_article_and_enqueues_the_owner_run(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(
        services, status=S.IN_REVIEW, brief_id=brief.id, markdown=BODY, pipeline_runs=2
    )
    response = await start(client, services, article)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    assert body["jobId"] == pipeline_job_id(article.id, 3)
    item = body["article"]["item"]
    assert item["status"] == "researching"
    assert item["pipelineJobId"] == body["jobId"]
    assert item["pipelineRuns"] == 3
    assert item["briefId"] is None  # start from research: a fresh brief
    assert response.headers["etag"] == body["article"]["etag"]
    job = await services.jobs.get(body["jobId"])
    payload = PipelineInput.from_json(job.input)
    assert job.type == PIPELINE_ARTICLE
    assert (payload.origin, payload.start_from, payload.interview) == ("owner", "research", "ask")


async def test_start_from_draft_clears_the_old_review_only_flags(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(
        services,
        status=S.NEEDS_ATTENTION,
        brief_id=brief.id,
        markdown=BODY,
        review_only=True,
        review_only_reasons=["fallback_model"],
    )
    response = await start(client, services, article, "draft")
    assert response.status_code == 202, response.text
    item = response.json()["article"]["item"]
    assert item["status"] == "drafting"
    assert item["briefId"] == brief.id
    assert item["reviewOnly"] is False
    assert item["reviewOnlyReasons"] == []


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("no_guide", 409, "voice_guide_missing"),
        ("no_ai_use", 409, "ai_use_not_confirmed"),
        ("no_blocklist", 409, "blocklist_not_configured"),
        ("no_brief", 409, "brief_missing"),
        ("no_pillar", 409, "meta_incomplete"),
        ("approved", 409, "invalid_transition"),
        ("busy", 409, "pipeline_active"),
        ("stale", 412, "etag_mismatch"),
        ("no_ai", 503, "ai_unavailable"),
    ],
)
async def test_start_refusals(
    client: httpx.AsyncClient, services: FakeServices, setup: str, status: int, code: str
) -> None:
    await seed_world(
        services,
        guide=setup != "no_guide",
        ai_use=setup != "no_ai_use",
        blocklist=setup != "no_blocklist",
    )
    status_of = S.PLANNED
    meta: ArticleMeta | None = None
    job_link: str | None = None
    if setup == "no_pillar":
        meta = ArticleMeta(title="Agent tokens", format="deep-explainer")
    if setup == "approved":
        status_of = S.APPROVED
    if setup == "busy":
        await services.jobs.enqueue(PIPELINE_ARTICLE, {"articleId": "x"}, job_id="pipeline-busy-1")
        raw = await services.store.read("jobs", "pipeline-busy-1")
        job = Job.from_body(raw.body)
        job.status = "waiting_input"
        job.awaiting = InputRequest(
            step="interview", key="interview", prompt="Answer.", requested_at=job.created_at
        )
        await services.store.replace("jobs", job.to_body(), if_match=raw.etag)
        job_link = "pipeline-busy-1"
        status_of = S.NEEDS_ATTENTION
    if setup == "no_ai":
        services.ai = None
    article = await new_article(services, status=status_of, meta=meta, pipeline_job_id=job_link)
    start_from = "draft" if setup == "no_brief" else "research"
    if setup == "stale":
        response = await client.post(
            f"/api/articles/{article.id}/pipeline",
            json={"startFrom": start_from},
            headers=write('"stale"'),
        )
        assert response.json()["current"]["etag"] == await etag(services, article.id)
    else:
        response = await start(client, services, article, start_from)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    if code == "meta_incomplete":
        assert response.json()["missing"] == ["pillar"]
    after = (await read_doc(services.store, Article, article.id)).doc
    assert after.status is article.status
    assert [job.type for job in services.jobs.enqueued if job.id != "pipeline-busy-1"] == []


async def test_a_planned_start_is_replaced_by_the_owners_start(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await seed_world(services)
    article = await new_article(services)
    planned = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, {"articleId": article.id}, job_id=planned)
    await update_doc(
        services.store,
        Article,
        article.id,
        lambda doc: doc.model_copy(update={"pipeline_job_id": planned, "pipeline_runs": 1}),
    )
    response = await start(client, services, article)
    assert response.status_code == 202, response.text
    assert response.json()["jobId"] == pipeline_job_id(article.id, 2)
    assert (await services.jobs.get(planned)).status == "cancelled"


async def test_missing_if_match_is_428(client: httpx.AsyncClient, services: FakeServices) -> None:
    await seed_world(services)
    article = await new_article(services)
    response = await client.post(
        f"/api/articles/{article.id}/pipeline", json={"startFrom": "research"}, headers=WRITE
    )
    assert response.status_code == 428


# --------------------------------------------------------------------------------------------
# Cancel
# --------------------------------------------------------------------------------------------


async def test_cancel_accepts_a_finished_job_and_needs_attention(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await seed_world(services)
    job = await services.jobs.enqueue(PIPELINE_ARTICLE, {"articleId": "x"}, job_id="pipeline-x-1")
    await services.jobs.cancel(job.id)  # already finished: fine
    article = await new_article(services, status=S.DRAFTING, pipeline_job_id=job.id)
    response = await client.post(
        f"/api/articles/{article.id}/pipeline/cancel",
        headers=write(await etag(services, article.id)),
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "needs_attention"
    assert item["lastError"] == "cancelled"

    in_review = await new_article(services, status=S.IN_REVIEW)
    refused = await client.post(
        f"/api/articles/{in_review.id}/pipeline/cancel",
        headers=write(await etag(services, in_review.id)),
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "invalid_transition"


async def test_cancel_a_planned_start(client: httpx.AsyncClient, services: FakeServices) -> None:
    await seed_world(services)
    article = await new_article(services)
    planned = pipeline_job_id(article.id, 1)
    await services.jobs.enqueue(PIPELINE_ARTICLE, {"articleId": article.id}, job_id=planned)
    await update_doc(
        services.store,
        Article,
        article.id,
        lambda doc: doc.model_copy(update={"pipeline_job_id": planned, "pipeline_runs": 1}),
    )
    response = await client.post(
        f"/api/articles/{article.id}/pipeline/cancel",
        headers=write(await etag(services, article.id)),
    )
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "planned"
    assert response.json()["item"]["pipelineJobId"] is None
    assert (await services.jobs.get(planned)).status == "cancelled"


# --------------------------------------------------------------------------------------------
# Revise
# --------------------------------------------------------------------------------------------


async def test_a_blocklisted_revise_instruction_never_reaches_a_model(
    client: httpx.AsyncClient, services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    article = await new_article(services, status=S.IN_REVIEW, markdown=BODY)
    response = await client.post(
        f"/api/articles/{article.id}/revise",
        json={"instruction": f"Mention that {BLOCKED} was the client."},
        headers=write(await etag(services, article.id)),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "confidential_text"
    assert response.json()["findings"][0]["field"] == "instruction"
    assert BLOCKED not in response.text
    assert ai.calls == []
    assert services.jobs.enqueued == []
    assert (await read_doc(services.store, Article, article.id)).doc.status is S.IN_REVIEW


async def test_an_owner_revision_runs_and_returns_to_review(
    client: httpx.AsyncClient, services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    brief = sample_brief()
    await create_doc(services.store, brief)
    article = await new_article(services, status=S.IN_REVIEW, brief_id=brief.id, markdown=BODY)
    gated = CalibrationFlags(gated_sha256=article.compute_sha256(), gated_version=article.version)
    await update_doc(
        services.store,
        Article,
        article.id,
        lambda doc: doc.model_copy(update={"calibration": gated}),
    )
    revised = BODY.replace("Twelve agents ran", "Twelve internal agents ran")
    ai.add(Task.REVISE, ScriptedReply(text=revised))
    script_gates(ai)
    response = await client.post(
        f"/api/articles/{article.id}/revise",
        json={"instruction": "Say the agents were internal ones."},
        headers=write(await etag(services, article.id)),
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    assert response.json()["article"]["item"]["status"] == "drafting"
    job = await services.jobs.get(job_id)
    assert job.type == PIPELINE_REVISE
    assert ReviseInput.from_json(job.input).origin == "owner"
    async with running(services, pipeline_registry()):
        done = await wait_job(services, job_id, {"succeeded", "failed"})
    assert done.status == "succeeded", done.error
    final = (await read_doc(services.store, Article, article.id)).doc
    assert final.status is S.IN_REVIEW
    assert "Twelve internal agents ran" in final.markdown
    assert f"[^{CITATION_ID}]:" in final.markdown  # the brief's footnote survives
    assert final.calibration is not None
    assert final.calibration.gated_version == article.version  # the owner intervened
    request = ai.calls_for(Task.REVISE)[0].request
    assert request.sensitivity is Sensitivity.ANONYMIZED
    assert "<author_instruction>\nSay the agents were internal ones.\n</author_instruction>" in (
        request.messages[0].text
    )
    assert [p.title for p, _ in services.notifier.sent] == ["Your revised article is ready"]


# --------------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------------


async def test_selection_proposal_saves_nothing(
    client: httpx.AsyncClient, services: FakeServices, ai: ScriptedModelRunner
) -> None:
    await seed_world(services)
    article = await new_article(services, status=S.IN_REVIEW, markdown=BODY)
    before = await etag(services, article.id)
    ai.add(
        Task.REVISE, ScriptedReply(text='"Issue a token per tool call; let it die with the call."')
    )
    response = await client.post(
        f"/api/articles/{article.id}/selection",
        json={
            "text": "Issue a token per tool call and let it expire with the call.",
            "instruction": "Tighter.",
        },
        headers=WRITE,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "proposal": "Issue a token per tool call; let it die with the call.",
        "reason": None,
        "reviewOnly": False,
    }
    assert await etag(services, article.id) == before
    call = ai.calls_for(Task.REVISE)[0]
    assert call.origin == "owner"
    assert call.request.sensitivity is Sensitivity.ANONYMIZED

    ai.add(Task.REVISE, ScriptedReply(refused=True))
    refused = await client.post(
        f"/api/articles/{article.id}/selection",
        json={"text": "Some passage.", "instruction": "Rewrite."},
        headers=WRITE,
    )
    assert refused.json() == {"proposal": None, "reason": "refused", "reviewOnly": True}


@pytest.mark.parametrize(
    ("setup", "body", "status", "code"),
    [
        (
            "plain",
            {"text": f"We shipped it at {BLOCKED}.", "instruction": "Shorter."},
            422,
            "confidential_text",
        ),
        (
            "plain",
            {"text": "A passage.", "instruction": f"Name {BLOCKED}."},
            422,
            "confidential_text",
        ),
        (
            "no_blocklist",
            {"text": "A passage.", "instruction": "Shorter."},
            409,
            "blocklist_not_configured",
        ),
        (
            "no_ai_use",
            {"text": "A passage.", "instruction": "Shorter."},
            409,
            "ai_use_not_confirmed",
        ),
        ("no_ai", {"text": "A passage.", "instruction": "Shorter."}, 503, "ai_unavailable"),
    ],
)
async def test_selection_refusals_make_no_model_call(
    client: httpx.AsyncClient,
    services: FakeServices,
    ai: ScriptedModelRunner,
    setup: str,
    body: dict[str, str],
    status: int,
    code: str,
) -> None:
    await seed_world(services, blocklist=setup != "no_blocklist", ai_use=setup != "no_ai_use")
    if setup == "no_ai":
        services.ai = None
    article = await new_article(services, status=S.IN_REVIEW, markdown=BODY)
    response = await client.post(f"/api/articles/{article.id}/selection", json=body, headers=WRITE)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert BLOCKED not in response.text
    assert ai.calls == []
