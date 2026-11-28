"""The articles routes through the real app (auth, CSRF, problems): lists, create, autosave with
If-Match plus ``baseVersion``, owner transitions, versions, restore, export and clearance."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

import httpx
import pytest

from blogwriter.articles import (
    OWNER_TRANSITIONS,
    ArticleLinks,
    compose_document,
    get_router,
    set_links,
)
from blogwriter.articles.versions import read_version
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import Store
from blogwriter.domain.articles import (
    MAX_ARTICLE_MARKDOWN_BYTES,
    Article,
    ArticleMeta,
    CalibrationFlags,
    GateReport,
    PublishState,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.docstore import create_doc, read_doc, save_singleton, update_doc
from blogwriter.domain.jobtypes import publish_job_id
from blogwriter.domain.settings import EmployerPolicy
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ARTICLE_MACHINE, ArticleStatus, IdeaStatus
from blogwriter.domain.states import OWNER_EDGES as DOMAIN_OWNER_EDGES
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    make_app,
    make_services,
    sample_document,
)

READ: Final = principal_headers(OWNER_OID)
WRITE: Final = {**READ, **write_headers(make_settings())}
S = ArticleStatus
FULL_META: Final[dict[str, Any]] = {
    "title": "Least privilege for agents",
    "slug": "least-privilege-agents",
    "excerpt": "Scope every tool call, not every agent.",
    "pillar": "agents",
    "format": "deep-explainer",
    "tags": ["identity", "mcp"],
}


def write(etag: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag}


async def seed(store: Store, status: ArticleStatus = S.PLANNED, **fields: object) -> Article:
    article = sample_document(Article, status=status, **fields)
    await create_doc(store, article)
    return article


async def etag_of(store: Store, article_id: str) -> str:
    return (await read_doc(store, Article, article_id)).etag


async def put(
    client: httpx.AsyncClient,
    article_id: str,
    etag: str,
    *,
    markdown: str,
    base_version: int,
    meta: dict[str, Any] | None = None,
) -> httpx.Response:
    body = {"meta": meta or {}, "markdown": markdown, "baseVersion": base_version}
    return await client.put(f"/api/articles/{article_id}", json=body, headers=write(etag))


# --------------------------------------------------------------------------------------------
# Lists, create, read
# --------------------------------------------------------------------------------------------


async def test_create_then_read_the_article(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/articles", json={"meta": {"title": "Hi"}}, headers=WRITE)
    assert created.status_code == 201
    body = created.json()
    assert body["etag"] == created.headers["etag"]
    item = body["item"]
    assert item["status"] == "planned"
    assert item["meta"]["title"] == "Hi"
    assert item["version"] == 0
    fetched = await client.get(f"/api/articles/{item['id']}", headers=READ)
    assert fetched.status_code == 200
    assert fetched.json()["etag"] == body["etag"]
    assert fetched.json()["item"]["statusChangedAt"] == item["statusChangedAt"]
    missing = await client.get("/api/articles/art-missing", headers=READ)
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


async def test_create_refuses_documents_and_unknown_fields(client: httpx.AsyncClient) -> None:
    for body in (
        {"meta": {"title": "x", "evil": True}},
        {"status": "published"},
        {"meta": {"tags": ["a", "a"]}},
        {"meta": {"series": {"id": "s", "title": "S", "part": 3, "total": 2}}},
        {"ideaId": "bad#id"},
    ):
        response = await client.post("/api/articles", json=body, headers=WRITE)
        assert response.status_code == 422, body
        assert response.json()["code"] == "validation_failed"
    no_csrf = await client.post("/api/articles", json={}, headers=READ)
    assert no_csrf.status_code == 403


async def test_creating_from_an_idea_uses_it(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    idea = sample_document(Idea, status=IdeaStatus.ACCEPTED, pillar="agents")
    await create_doc(store, idea)
    services.clock.advance(timedelta(minutes=5))
    created = await client.post("/api/articles", json={"ideaId": idea.id}, headers=WRITE)
    assert created.status_code == 201
    article_id = created.json()["item"]["id"]
    assert created.json()["item"]["ideaId"] == idea.id
    used = (await read_doc(store, Idea, idea.id)).doc
    assert used.status is IdeaStatus.USED
    assert used.article_id == article_id
    assert used.status_changed_at == to_iso(services.clock.now())
    again = await client.post("/api/articles", json={"ideaId": idea.id}, headers=WRITE)
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    missing = await client.post("/api/articles", json={"ideaId": "idea-gone"}, headers=WRITE)
    assert missing.status_code == 404


async def test_lists_are_summaries_newest_first_without_eval_articles(
    client: httpx.AsyncClient, store: Store
) -> None:
    first = await seed(
        store,
        S.IN_REVIEW,
        meta=ArticleMeta(title="One", pillar="agents"),
        markdown="Secret body.",
        updated_at="2026-11-26T12:00:00.000001Z",
    )
    second = await seed(
        store,
        S.PLANNED,
        meta=ArticleMeta(title="Two", pillar="identity"),
        updated_at="2026-11-26T12:00:00.000002Z",
    )
    await seed(store, S.IN_REVIEW, purpose="eval", updated_at="2026-11-26T12:00:00.000003Z")
    response = await client.get("/api/articles", headers=READ)
    assert response.status_code == 200
    page = response.json()
    assert [entry["item"]["id"] for entry in page["items"]] == [second.id, first.id]
    assert page["nextCursor"] is None
    summary = page["items"][1]["item"]
    assert "markdown" not in summary
    assert "embedding" not in summary
    assert summary["meta"] == {"title": "One", "slug": None, "pillar": "agents", "format": None}
    assert summary["gates"] is None
    assert summary["publish"] == {"live": False, "publishAt": None, "url": None}
    assert page["items"][1]["etag"] == await etag_of(store, first.id)
    assert "Secret body" not in response.text

    by_pillar = await client.get("/api/articles?pillar=identity", headers=READ)
    assert [e["item"]["id"] for e in by_pillar.json()["items"]] == [second.id]
    by_status = await client.get("/api/articles?status=in_review&status=archived", headers=READ)
    assert [e["item"]["id"] for e in by_status.json()["items"]] == [first.id]
    evals = await client.get("/api/articles?purpose=eval", headers=READ)
    assert [e["item"]["purpose"] for e in evals.json()["items"]] == ["eval"]
    everything = await client.get("/api/articles?purpose=all", headers=READ)
    assert len(everything.json()["items"]) == 3


async def test_list_pages_with_a_cursor(client: httpx.AsyncClient, store: Store) -> None:
    ids = [(await seed(store, updated_at=f"2026-11-26T12:00:00.00000{n}Z")).id for n in range(1, 6)]
    seen: list[str] = []
    url = "/api/articles?limit=2"
    while True:
        page = (await client.get(url, headers=READ)).json()
        seen.extend(entry["item"]["id"] for entry in page["items"])
        if page["nextCursor"] is None:
            break
        url = f"/api/articles?limit=2&cursor={page['nextCursor']}"
    assert seen == list(reversed(ids))
    bad = await client.get("/api/articles?cursor=not-a-cursor!", headers=READ)
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_cursor"


async def test_summaries_report_stale_and_ready_gates(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = sample_document(Article, status=S.IN_REVIEW, markdown="Body.")
    report = GateReport(
        content_sha256=article.compute_sha256(), version=1, ran_at=SAMPLE_NOW, results=[]
    )
    await create_doc(store, article.model_copy(update={"gates": report}))
    item = (await client.get("/api/articles", headers=READ)).json()["items"][0]["item"]
    assert item["gates"]["stale"] is False
    assert item["gates"]["autoPublishReady"] is False  # an incomplete report is never ready
    assert len(item["gates"]["blocking"]) == 7
    await update_doc(store, Article, article.id, lambda a: a.model_copy(update={"markdown": "x"}))
    item = (await client.get("/api/articles", headers=READ)).json()["items"][0]["item"]
    assert item["gates"]["stale"] is True


# --------------------------------------------------------------------------------------------
# Autosave
# --------------------------------------------------------------------------------------------


async def test_autosave_answers_the_normalized_article_and_findings(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(store)
    response = await put(
        client,
        article.id,
        await etag_of(store, article.id),
        markdown="* one\n* two\n\nA claim.[^gone]\n",
        base_version=0,
        meta={"title": "Lists"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["etag"] == response.headers["etag"]
    assert body["item"]["markdown"].startswith("- one\n- two\n")
    assert body["item"]["version"] == 1
    assert [finding["code"] for finding in body["findings"]] == ["citation-undefined"]
    assert body["findings"][0]["severity"] == "block"


async def test_autosave_needs_if_match(client: httpx.AsyncClient, store: Store) -> None:
    article = await seed(store)
    response = await client.put(
        f"/api/articles/{article.id}",
        json={"meta": {}, "markdown": "x", "baseVersion": 0},
        headers=WRITE,
    )
    assert response.status_code == 428
    assert response.json()["code"] == "if_match_required"


async def test_another_tabs_save_is_a_412_with_current(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(store)
    loaded = await etag_of(store, article.id)
    first = await put(client, article.id, loaded, markdown="Tab A.", base_version=0)
    assert first.status_code == 200
    second = await put(client, article.id, loaded, markdown="Tab B.", base_version=0)
    assert second.status_code == 412
    problem = second.json()
    assert problem["code"] == "etag_mismatch"
    assert problem["current"] == {
        "etag": first.json()["etag"],
        "updatedAt": first.json()["item"]["updatedAt"],
        "updatedBy": "owner",
        "version": 1,
        "status": "planned",
    }
    assert "Tab A" not in second.text


async def test_gate_reports_and_links_between_load_and_autosave_are_merged(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    article = await seed(store, S.IN_REVIEW, version=4, markdown="Loaded.\n")
    loaded = await etag_of(store, article.id)
    report = GateReport(content_sha256=sha256_hex("x"), version=4, ran_at=SAMPLE_NOW)
    await update_doc(store, Article, article.id, lambda a: a.model_copy(update={"gates": report}))
    await set_links(
        store, article.id, ArticleLinks(interview_id="intv-1"), now=services.clock.now()
    )
    response = await put(client, article.id, loaded, markdown="Edited.", base_version=4)
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["version"] == 5
    assert item["markdown"] == "Edited.\n"
    assert item["interviewId"] == "intv-1"
    assert item["gates"]["version"] == 4


async def test_autosave_is_read_only_in_pipeline_states(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(store, S.DRAFTING)
    response = await put(
        client, article.id, await etag_of(store, article.id), markdown="x", base_version=0
    )
    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "read_only_state"
    assert (problem["status"], problem["state"], problem["writer"]) == (409, "drafting", "pipeline")


async def test_markdown_over_512_kib_is_413(client: httpx.AsyncClient, store: Store) -> None:
    article = await seed(store)
    etag = await etag_of(store, article.id)
    exact = await put(
        client, article.id, etag, markdown="a" * MAX_ARTICLE_MARKDOWN_BYTES, base_version=0
    )
    assert exact.status_code == 200
    too_large = await put(
        client,
        article.id,
        exact.json()["etag"],
        markdown="a" * (513 * 1024),
        base_version=1,
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "markdown_too_large"


async def test_text_the_store_cannot_hold_is_422(client: httpx.AsyncClient, store: Store) -> None:
    article = await seed(store)
    content = '{"meta": {}, "markdown": "bad \\ud800 text", "baseVersion": 0}'
    response = await client.put(
        f"/api/articles/{article.id}",
        content=content,
        headers={
            **write(await etag_of(store, article.id)),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------------------------
# Owner transitions
# --------------------------------------------------------------------------------------------

OWNER_EDGES: Final = [
    (source, target) for source, targets in OWNER_TRANSITIONS.items() for target in sorted(targets)
]
OTHER_EDGES: Final = [
    (source, target)
    for source in ArticleStatus
    for target in sorted(ARTICLE_MACHINE.allowed(source))
    if target not in OWNER_TRANSITIONS.get(source, frozenset())
]


def test_owner_edges_are_edges_of_the_machine() -> None:
    assert all(ARTICLE_MACHINE.can(source, target) for source, target in OWNER_EDGES)
    assert len(OWNER_EDGES) == 10
    # Every edge this route makes is one the owner takes (GET /api/meta/machines says so).
    owner = DOMAIN_OWNER_EDGES[ARTICLE_MACHINE.name]
    assert all(target.value in owner[source.value] for source, target in OWNER_EDGES)


@pytest.mark.parametrize(("source", "target"), OWNER_EDGES)
async def test_the_owner_makes_the_owner_edges(
    client: httpx.AsyncClient, store: Store, source: ArticleStatus, target: ArticleStatus
) -> None:
    article = await seed(store, source)
    response = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": target.value},
        headers=write(await etag_of(store, article.id)),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["item"]["status"] == target.value
    assert response.json()["item"]["updatedBy"] == "owner"


@pytest.mark.parametrize(("source", "target"), OTHER_EDGES)
async def test_every_other_edge_is_not_allowed_here(
    client: httpx.AsyncClient, store: Store, source: ArticleStatus, target: ArticleStatus
) -> None:
    article = await seed(store, source)
    etag = await etag_of(store, article.id)
    response = await client.post(
        f"/api/articles/{article.id}/transition", json={"to": target.value}, headers=write(etag)
    )
    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "transition_not_allowed_here"
    assert (problem["from"], problem["to"]) == (source.value, target.value)
    assert await etag_of(store, article.id) == etag


async def test_non_edges_live_articles_and_stale_screens(
    client: httpx.AsyncClient, store: Store
) -> None:
    planned = await seed(store)
    invalid = await client.post(
        f"/api/articles/{planned.id}/transition",
        json={"to": "published"},
        headers=write(await etag_of(store, planned.id)),
    )
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "invalid_transition"
    assert "archived" in invalid.json()["allowed"]
    live = await seed(store, S.IN_REVIEW, publish=PublishState(live=True, slug="live-post"))
    refused = await client.post(
        f"/api/articles/{live.id}/transition",
        json={"to": "archived"},
        headers=write(await etag_of(store, live.id)),
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "live_article"
    stale = await client.post(
        f"/api/articles/{planned.id}/transition", json={"to": "archived"}, headers=write('"old"')
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["status"] == "planned"
    unknown = await client.post(
        f"/api/articles/{planned.id}/transition", json={"to": "nowhere"}, headers=write('"x"')
    )
    assert unknown.status_code == 422


async def test_pulling_back_cancels_the_scheduled_publish(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    article = await seed(store, S.APPROVED, version=3)
    job_id = publish_job_id(article.id, 3)
    later = services.clock.now() + timedelta(days=2)
    await services.jobs.enqueue(
        "publish.article", {"articleId": article.id}, job_id=job_id, run_after=later
    )
    response = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "in_review", "reason": "One more pass."},
        headers=write(await etag_of(store, article.id)),
    )
    assert response.status_code == 200
    assert (await services.jobs.get(job_id)).status == "cancelled"
    other = await seed(store, S.APPROVED, publish=PublishState(job_id="publish-custom"))
    pulled = await client.post(
        f"/api/articles/{other.id}/transition",
        json={"to": "in_review"},
        headers=write(await etag_of(store, other.id)),
    )
    assert pulled.status_code == 200  # no job to cancel is fine


async def test_a_failed_publish_goes_back_to_review(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    """publish_failed -> in_review: the owner gives up on the run and edits again. A retry of
    the failed run that is still queued is cancelled; the article's error is cleared."""
    retry = "publish-retry-1"
    article = await seed(
        store,
        S.PUBLISH_FAILED,
        publish=PublishState(job_id=retry, last_operation="publish", last_error="build_failed"),
        last_error="build_failed",
    )
    await services.jobs.enqueue("publish.article", {"articleId": article.id}, job_id=retry)
    response = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "in_review", "reason": "Fix the slug first."},
        headers=write(await etag_of(store, article.id)),
    )
    assert response.status_code == 200, response.json()
    item = response.json()["item"]
    assert (item["status"], item["updatedBy"], item["lastError"]) == ("in_review", "owner", None)
    assert (await services.jobs.get(retry)).status == "cancelled"
    stored = (await read_doc(store, Article, article.id)).doc
    assert stored.status is S.IN_REVIEW


async def test_a_failed_unpublish_of_a_live_page_goes_back_to_review_and_stays_live(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(
        store,
        S.PUBLISH_FAILED,
        publish=PublishState(live=True, slug="live-post", last_operation="unpublish"),
    )
    response = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "in_review"},
        headers=write(await etag_of(store, article.id)),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["item"]["status"] == "in_review"
    assert response.json()["item"]["publish"]["live"] is True  # like a correction: still up
    archived = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "archived"},
        headers=write(await etag_of(store, article.id)),
    )
    assert archived.status_code == 409
    assert archived.json()["code"] == "live_article"


# --------------------------------------------------------------------------------------------
# Versions and restore
# --------------------------------------------------------------------------------------------


async def test_versions_list_marks_pinned_and_pages(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    article = await seed(store)
    etag, version = await etag_of(store, article.id), 0
    for n in range(4):
        response = await put(client, article.id, etag, markdown=f"Text {n}.", base_version=version)
        etag, version = response.json()["etag"], response.json()["item"]["version"]
        services.clock.advance(timedelta(minutes=10))  # so every save stores a version
    await update_doc(
        store,
        Article,
        article.id,
        lambda a: a.model_copy(update={"calibration": CalibrationFlags(gated_version=1)}),
    )
    first = await client.get(f"/api/articles/{article.id}/versions?limit=3", headers=READ)
    assert first.status_code == 200
    page = first.json()
    assert [entry["item"]["version"] for entry in page["items"]] == [4, 3, 2]
    assert page["items"][0]["item"]["source"] == "autosave"
    assert "markdown" not in page["items"][0]["item"]
    rest = await client.get(
        f"/api/articles/{article.id}/versions?limit=3&cursor={page['nextCursor']}", headers=READ
    )
    assert [entry["item"]["version"] for entry in rest.json()["items"]] == [1]
    assert rest.json()["items"][0]["item"]["pinned"] is True
    assert rest.json()["nextCursor"] is None
    single = await client.get(f"/api/articles/{article.id}/versions/2", headers=READ)
    assert single.status_code == 200
    assert single.json()["item"]["markdown"] == "Text 1.\n"
    for missing in ("9", "0", "-1"):
        response = await client.get(f"/api/articles/{article.id}/versions/{missing}", headers=READ)
        assert response.status_code == 404
    bad = await client.get(f"/api/articles/{article.id}/versions?cursor=eA", headers=READ)
    assert bad.status_code == 422


async def test_restore_through_the_route(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    article = await seed(store)
    etag = await etag_of(store, article.id)
    one = await put(client, article.id, etag, markdown="One.", base_version=0)
    services.clock.advance(timedelta(minutes=11))
    two = await put(client, article.id, one.json()["etag"], markdown="Two.", base_version=1)
    restored = await client.post(
        f"/api/articles/{article.id}/versions/1/restore", headers=write(two.json()["etag"])
    )
    assert restored.status_code == 200
    item = restored.json()["item"]
    assert (item["version"], item["markdown"]) == (3, "One.\n")
    version = (await read_version(store, article.id, 3)).doc
    assert version.source == "restore"
    stale = await client.post(
        f"/api/articles/{article.id}/versions/2/restore", headers=write(two.json()["etag"])
    )
    assert stale.status_code == 412
    drafting = await seed(store, S.DRAFTING)
    refused = await client.post(
        f"/api/articles/{drafting.id}/versions/1/restore",
        headers=write(await etag_of(store, drafting.id)),
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "read_only_state"


# --------------------------------------------------------------------------------------------
# Export and clearance
# --------------------------------------------------------------------------------------------


async def test_export_is_the_composed_document(client: httpx.AsyncClient, store: Store) -> None:
    article = await seed(
        store, S.IN_REVIEW, meta=ArticleMeta.model_validate(FULL_META), markdown="Body text.\n"
    )
    response = await client.get(f"/api/articles/{article.id}/export.md", headers=READ)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="least-privilege-agents.md"'
    )
    assert response.text == compose_document(article.meta, article.markdown)
    assert response.text.startswith('---\ntitle: "Least privilege for agents"\n')


def test_the_openapi_document_types_the_export_as_markdown_text() -> None:
    """The web client reads it as text (``parseAs: 'text'``), typed ``string``."""
    app = make_app(make_services(), [get_router()], settings=make_settings())
    responses = app.openapi()["paths"]["/api/articles/{article_id}/export.md"]["get"]["responses"]
    assert responses["200"]["content"] == {"text/markdown": {"schema": {"type": "string"}}}


async def test_export_needs_complete_and_valid_metadata(
    client: httpx.AsyncClient, store: Store
) -> None:
    incomplete = await seed(store, meta=ArticleMeta(title="Only a title"))
    response = await client.get(f"/api/articles/{incomplete.id}/export.md", headers=READ)
    assert response.status_code == 409
    assert response.json()["code"] == "meta_incomplete"
    assert response.json()["missing"] == ["slug", "excerpt", "pillar", "format"]
    broken = await seed(store, meta=ArticleMeta.model_validate({**FULL_META, "title": "A <b>"}))
    invalid = await client.get(f"/api/articles/{broken.id}/export.md", headers=READ)
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "meta_invalid"
    assert invalid.json()["fields"] == ["title"]
    assert "<b>" not in invalid.text


async def test_clearance_attests_with_the_policy_statement_by_default(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(store, S.IN_REVIEW, markdown="Case.\n")
    default = await client.post(
        f"/api/articles/{article.id}/clearance", headers=write(await etag_of(store, article.id))
    )
    assert default.status_code == 200
    clearance = default.json()["item"]["clearance"]
    assert clearance["statement"] == EmployerPolicy.default(SAMPLE_NOW).clearance_statement
    assert clearance["contentSha256"] == article.compute_sha256()
    policy = EmployerPolicy.default(SAMPLE_NOW).model_copy(
        update={"clearance_statement": "I cleared this piece with my manager."}
    )
    await save_singleton(store, policy, if_match=None)
    saved = await client.post(
        f"/api/articles/{article.id}/clearance", headers=write(default.json()["etag"])
    )
    assert saved.json()["item"]["clearance"]["statement"] == "I cleared this piece with my manager."
    custom = await client.post(
        f"/api/articles/{article.id}/clearance",
        json={"statement": "Checked twice."},
        headers=write(saved.json()["etag"]),
    )
    assert custom.json()["item"]["clearance"]["statement"] == "Checked twice."
    approved = await seed(store, S.APPROVED)
    refused = await client.post(
        f"/api/articles/{approved.id}/clearance", headers=write(await etag_of(store, approved.id))
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "read_only_state"
    missing = await client.post(f"/api/articles/{approved.id}/clearance", headers=WRITE)
    assert missing.status_code == 428


async def test_unknown_articles_are_404_everywhere(client: httpx.AsyncClient) -> None:
    for method, path in (
        ("GET", "/api/articles/art-none"),
        ("GET", "/api/articles/art-none/versions"),
        ("GET", "/api/articles/art-none/preview"),
        ("GET", "/api/articles/art-none/export.md"),
        ("GET", "/api/articles/art%23none"),
    ):
        response = await client.request(method, path, headers=READ)
        assert response.status_code == 404, path
    for path, body in (
        ("/api/articles/art-none/transition", {"to": "archived"}),
        ("/api/articles/art-none/versions/1/restore", None),
        ("/api/articles/art-none/clearance", None),
    ):
        response = await client.post(path, json=body, headers=write('"x"'))
        assert response.status_code == 404, path
    put_missing = await client.put(
        "/api/articles/art-none",
        json={"meta": {}, "markdown": "x", "baseVersion": 0},
        headers=write('"x"'),
    )
    assert put_missing.status_code == 404
