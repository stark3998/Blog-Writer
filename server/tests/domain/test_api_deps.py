"""Response envelopes, cursors, FastAPI dependencies and the problem mapping, through the app."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from blogwriter.core.errors import ProblemError, problem_response
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import Conflict, NotFound, PreconditionFailed, StoreUnavailable
from blogwriter.db.base import InvalidRequest
from blogwriter.domain.api import (
    MAX_PAGE_SIZE,
    PREVIEW_HEADERS,
    InvalidCursor,
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.articles import Article
from blogwriter.domain.common import Finding
from blogwriter.domain.compose import MetaIncomplete
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
)
from blogwriter.domain.deps import (
    AIDep,
    BlobsDep,
    ClassifierDep,
    EmbedderDep,
    IfMatch,
    JobsDep,
    RenderDep,
    SingletonPrecondition,
    StoreDep,
    VaultDep,
    etag_mismatch,
    problem_from,
    problems,
    versioned,
    versioned_singleton,
)
from blogwriter.domain.docstore import (
    InvalidDocumentWrite,
    Stored,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    save_singleton,
)
from blogwriter.domain.ports import BlobStoreUnavailable, ServiceUnavailable
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ArticleStatus,
    TransitionError,
    TransitionRefused,
    WriterConflict,
)
from blogwriter.domain.testing import SAMPLE_NOW, make_app, make_services, sample_document

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}


class TitleInput(BaseModel):
    title: str


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/test")

    @router.get("/articles/{article_id}")
    async def get_article(
        article_id: str, store: StoreDep, response: Response
    ) -> Versioned[Article]:
        with problems():
            stored = await read_doc(store, Article, article_id)
        return versioned(stored, response)

    @router.put("/articles/{article_id}")
    async def put_article(
        article_id: str, body: TitleInput, store: StoreDep, if_match: IfMatch, response: Response
    ) -> Versioned[Article]:
        with problems():
            current = await read_doc(store, Article, article_id)
            if current.etag != if_match:
                raise etag_mismatch(current)
            changed = current.doc.model_copy(update={"markdown": body.title})
            stored = await replace_doc(store, changed, if_match=if_match)
        return versioned(stored, response)

    @router.post("/articles/{article_id}/publish")
    async def publish(article_id: str, store: StoreDep) -> Versioned[Article]:
        with problems():
            current = await read_doc(store, Article, article_id)
            ARTICLE_MACHINE.transition(current.doc.status, ArticleStatus.PUBLISHED)
        return versioned(current)  # pragma: no cover - the transition is refused

    @router.put("/cadence")
    async def put_cadence(
        store: StoreDep, precondition: SingletonPrecondition, response: Response
    ) -> Versioned[CadenceSettings]:
        with problems():
            current, _etag = await read_singleton(store, CadenceSettings, now=SAMPLE_NOW)
            stored = await save_singleton(store, current, if_match=precondition)
        return versioned(stored, response)

    @router.get("/needs")
    async def needs(
        ai: AIDep,
        blobs: BlobsDep,
        embedder: EmbedderDep,
        render: RenderDep,
        vault: VaultDep,
        jobs: JobsDep,
    ) -> dict[str, bool]:
        services: tuple[object, ...] = (ai, blobs, embedder, render, vault, jobs)
        return {"ok": len(services) == 6}

    @router.get("/page")
    async def page(
        store: StoreDep,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Page[Article]:
        with problems():
            if cursor is not None:
                decode_cursor(cursor)
            found = await find_docs(store, Article, limit=clamp_limit(limit))
        items = [Versioned[Article](item=s.doc, etag=s.etag) for s in found]
        return Page[Article](items=items, next_cursor=encode_cursor(SAMPLE_NOW))

    @router.get("/classifier")
    async def classifier(classifier: ClassifierDep) -> dict[str, bool]:
        return {"ok": callable(classifier.classify)}

    @router.get("/cadence")
    async def get_cadence(store: StoreDep, response: Response) -> Versioned[CadenceSettings]:
        with problems():
            doc, etag = await read_singleton(store, CadenceSettings, now=SAMPLE_NOW)
        return versioned_singleton(doc, etag, response)

    return router


async def test_versioned_envelope_and_etag_header() -> None:
    services = make_services()
    article = sample_document(Article, markdown="Body")
    created = await create_doc(services.store, article)
    app = make_app(services, [build_router()], settings=SETTINGS)
    async with asgi_client(app) as client:
        response = await client.get(f"/api/test/articles/{article.id}", headers=READ)
    assert response.status_code == 200
    assert response.headers["etag"] == created.etag
    body = response.json()
    assert body["etag"] == created.etag
    assert body["item"]["id"] == article.id
    assert body["item"]["markdown"] == "Body"
    assert body["item"]["statusChangedAt"] == SAMPLE_NOW


async def test_if_match_flow_and_conflicts() -> None:
    services = make_services()
    article = sample_document(Article)
    created = await create_doc(services.store, article)
    app = make_app(services, [build_router()], settings=SETTINGS)
    url = f"/api/test/articles/{article.id}"
    async with asgi_client(app) as client:
        missing = await client.put(url, json={"title": "x"}, headers=WRITE)
        ok = await client.put(url, json={"title": "x"}, headers={**WRITE, "If-Match": created.etag})
        stale = await client.put(
            url, json={"title": "y"}, headers={**WRITE, "If-Match": created.etag}
        )
        unknown = await client.get("/api/test/articles/art-missing", headers=READ)
        refused = await client.post(f"{url}/publish", headers=WRITE)
        no_csrf = await client.put(url, json={"title": "z"}, headers={**READ, "If-Match": "x"})
    assert missing.status_code == 428
    assert missing.json()["code"] == "if_match_required"
    assert ok.status_code == 200
    assert ok.json()["item"]["markdown"] == "x"
    assert stale.status_code == 412
    problem = stale.json()
    assert problem["code"] == "etag_mismatch"
    assert problem["current"]["etag"] == ok.json()["etag"]
    assert problem["current"]["status"] == "planned"
    assert problem["current"]["updatedBy"] == "owner"
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "not_found"
    assert refused.status_code == 409
    assert refused.json()["code"] == "invalid_transition"
    assert refused.json()["allowed"] == [
        "archived",
        "drafting",
        "in_review",
        "interviewing",
        "researching",
    ]
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_failed"


async def test_singleton_preconditions() -> None:
    services = make_services()
    app = make_app(services, [build_router()], settings=SETTINGS)
    async with asgi_client(app) as client:
        none = await client.put("/api/test/cadence", headers=WRITE)
        created = await client.put("/api/test/cadence", headers={**WRITE, "If-None-Match": "*"})
        again = await client.put("/api/test/cadence", headers={**WRITE, "If-None-Match": "*"})
        replaced = await client.put(
            "/api/test/cadence", headers={**WRITE, "If-Match": created.json()["etag"]}
        )
    assert none.status_code == 428
    assert created.status_code == 200
    assert created.json()["item"]["id"] == "cadence"
    assert again.status_code == 412
    assert replaced.status_code == 200


async def test_unavailable_services_answer_503() -> None:
    full = make_app(make_services(), [build_router()], settings=SETTINGS)
    no_ai = make_app(make_services(ai=None), [build_router()], settings=SETTINGS)
    no_blobs = make_app(make_services(blobs=None), [build_router()], settings=SETTINGS)
    async with asgi_client(full) as client:
        assert (await client.get("/api/test/needs", headers=READ)).json() == {"ok": True}
    async with asgi_client(no_ai) as client:
        response = await client.get("/api/test/needs", headers=READ)
        assert response.status_code == 503
        assert response.json()["code"] == "ai_unavailable"
        assert response.headers["retry-after"] == "5"
    async with asgi_client(no_blobs) as client:
        response = await client.get("/api/test/needs", headers=READ)
        assert response.json()["code"] == "storage_unavailable"


async def test_no_services_wired_is_503_and_auth_still_comes_first() -> None:
    app = make_app(make_services(), [build_router()], settings=SETTINGS)
    del app.state.services
    async with asgi_client(app) as client:
        unauthenticated = await client.get("/api/test/articles/x")
        response = await client.get("/api/test/articles/x", headers=READ)
    assert unauthenticated.status_code == 401
    assert response.status_code == 503
    assert response.json()["code"] == "services_unavailable"


async def test_pages_serialize_camel_case() -> None:
    services = make_services()
    await create_doc(services.store, sample_document(Article))
    app = make_app(services, [build_router()], settings=SETTINGS)
    async with asgi_client(app) as client:
        body = (await client.get("/api/test/page?limit=500", headers=READ)).json()
    assert len(body["items"]) == 1
    assert decode_cursor(body["nextCursor"]) == SAMPLE_NOW
    assert set(body["items"][0]) == {"item", "etag"}


def test_cursor_and_limits() -> None:
    assert decode_cursor(encode_cursor("2026-11-26T12:00:00.000000Z|art-1")) == (
        "2026-11-26T12:00:00.000000Z|art-1"
    )
    for bad in ("", "%%%", "x" * 600, "_w"):
        with pytest.raises(ValueError, match="cursor"):
            decode_cursor(bad)
    assert clamp_limit(None) == 50
    assert clamp_limit(0) == 1
    assert clamp_limit(10_000) == MAX_PAGE_SIZE


def test_job_accepted_and_preview_headers() -> None:
    assert JobAccepted(job_id="job-1").model_dump() == {"jobId": "job-1", "status": "queued"}
    csp = PREVIEW_HEADERS["Content-Security-Policy"]
    assert "script-src" not in csp
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert PREVIEW_HEADERS["X-Frame-Options"] == "SAMEORIGIN"


def test_problem_mapping() -> None:
    cases: list[tuple[Exception, int, str]] = [
        (NotFound("x"), 404, "not_found"),
        (InvalidRequest("id contains a forbidden character"), 404, "not_found"),
        (Conflict("x"), 409, "conflict"),
        (StoreUnavailable("x"), 503, "store_unavailable"),
        (ServiceUnavailable("render"), 503, "render_unavailable"),
        (BlobStoreUnavailable("throttled"), 503, "storage_unavailable"),
        (WriterConflict(ArticleStatus.DRAFTING, "owner"), 409, "read_only_state"),
        (WriterConflict(ArticleStatus.PUBLISHING, "pipeline"), 409, "read_only_state"),
        (InvalidCursor(), 422, "invalid_cursor"),
        (BlocklistNotConfigured(), 409, "blocklist_not_configured"),
        (AiUseNotConfirmed(), 409, "ai_use_not_confirmed"),
        (TransitionRefused("live_article", "Unpublish it first."), 409, "live_article"),
        (
            TransitionError("article", "planned", "published", ["drafting"]),
            409,
            "invalid_transition",
        ),
        (MetaIncomplete(["slug"]), 409, "meta_incomplete"),
        (ConfidentialText({"topic": []}), 422, "confidential_text"),
        (PreconditionFailed("x"), 412, "etag_mismatch"),
        (InvalidDocumentWrite("articles/art-1: refusing"), 500, "invalid_document"),
        (RuntimeError("boom"), 500, "internal_error"),
    ]
    for exc, status, code in cases:
        problem = problem_from(exc)
        assert (problem.status, problem.code) == (status, code)
        # Every mapped problem renders: no extension may reuse a standard member ("status").
        response = problem_response(
            problem.status,
            code=problem.code,
            detail=problem.detail,
            headers=problem.headers,
            extensions=problem.extensions,
        )
        assert response.status_code == status
    read_only = problem_from(WriterConflict(ArticleStatus.DRAFTING, "pipeline"))
    assert read_only.extensions == {"state": "drafting", "writer": "pipeline"}
    passthrough = ProblemError(418, code="teapot")
    assert problem_from(passthrough) is passthrough
    with pytest.raises(ProblemError) as caught, problems():
        raise NotFound("x")
    assert caught.value.status == 404
    with pytest.raises(RuntimeError), problems():
        raise RuntimeError("not mapped")


def test_versioned_without_a_response() -> None:
    article = sample_document(Article)
    envelope = versioned(Stored(article, '"e1"'))
    assert envelope.model_dump()["item"]["statusChangedAt"] == SAMPLE_NOW
    assert envelope.etag == '"e1"'


def test_confidential_text_problems_carry_term_ids_never_terms() -> None:
    finding = Finding(
        code="blocklisted-term",
        severity="block",
        message="Matches a blocklisted client name.",
        start=4,
        end=11,
        ref="t-1",
    )
    problem = problem_from(ConfidentialText({"instruction": [finding], "topic": []}))
    assert (problem.status, problem.code) == (422, "confidential_text")
    body = problem.extensions or {}
    assert body["findings"] == [
        {
            "field": "instruction",
            "code": "blocklisted-term",
            "severity": "block",
            "message": "Matches a blocklisted client name.",
            "start": 4,
            "end": 11,
            "ref": "t-1",
            "overridable": False,
        }
    ]
    incomplete = problem_from(MetaIncomplete(["slug", "excerpt"]))
    assert (incomplete.status, incomplete.code) == (409, "meta_incomplete")
    assert (incomplete.extensions or {})["missing"] == ["slug", "excerpt"]


async def test_malformed_cursors_answer_422() -> None:
    app = make_app(make_services(), [build_router()], settings=SETTINGS)
    async with asgi_client(app) as client:
        bad = await client.get("/api/test/page?cursor=%25%25%25", headers=READ)
        good = await client.get(f"/api/test/page?cursor={encode_cursor('x')}", headers=READ)
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_cursor"
    assert good.status_code == 200


async def test_classifier_dependency() -> None:
    with_classifier = make_app(make_services(), [build_router()], settings=SETTINGS)
    without = make_app(make_services(classifier=None), [build_router()], settings=SETTINGS)
    async with asgi_client(with_classifier) as client:
        assert (await client.get("/api/test/classifier", headers=READ)).json() == {"ok": True}
    async with asgi_client(without) as client:
        response = await client.get("/api/test/classifier", headers=READ)
    assert response.status_code == 503
    assert response.json()["code"] == "classifier_unavailable"


async def test_never_saved_singletons_have_a_null_etag() -> None:
    services = make_services()
    app = make_app(services, [build_router()], settings=SETTINGS)
    async with asgi_client(app) as client:
        default = await client.get("/api/test/cadence", headers=READ)
        await client.put("/api/test/cadence", headers={**WRITE, "If-None-Match": "*"})
        saved = await client.get("/api/test/cadence", headers=READ)
    assert default.json()["etag"] is None
    assert "etag" not in default.headers
    assert saved.json()["etag"] == saved.headers["etag"]
