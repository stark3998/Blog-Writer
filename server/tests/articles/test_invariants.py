"""Invariants of the articles package: reads never write, article text never reaches the logs,
every route is owner-only, the who-writes table holds for every writer, derived fields always
match the content, and a maximal article still saves."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
import pytest
from pydantic import ValidationError

from blogwriter.articles import (
    ArticleLinks,
    ArticlesSettings,
    StaleArticle,
    attest_clearance,
    create_article,
    get_router,
    owner_save,
    pipeline_write,
    restore_version,
    set_links,
    transition,
)
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import Store
from blogwriter.db.base import MAX_DOCUMENT_BYTES, prepare_write
from blogwriter.domain.articles import (
    GATE_ORDER,
    MAX_ARTICLE_MARKDOWN_BYTES,
    MAX_EMBEDDING_DIMENSIONS,
    Article,
    ArticleMeta,
    GateReport,
    GateResult,
    GateVerdict,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import MAX_STORED_FINDINGS, Finding
from blogwriter.domain.compose import word_stats
from blogwriter.domain.docstore import create_doc, read_doc, update_doc
from blogwriter.domain.states import ARTICLE_WRITER, ArticleStatus, WriterConflict
from blogwriter.domain.testing import FakeServices, make_app, make_services, sample_document

type StateReader = Callable[[], Awaitable[dict[str, dict[str, str]]]]

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
READ: Final = principal_headers(OWNER_OID)
WRITE: Final = {**READ, **write_headers(make_settings())}
MARKER: Final = "zq-private-marker-7731"
S = ArticleStatus


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def drafted_article(store: Store) -> Article:
    """An article in review with three stored versions."""
    article = sample_document(Article, status=S.DRAFTING)
    await create_doc(store, article)
    for index in range(1, 4):
        await pipeline_write(store, article.id, now=at(index), markdown=f"Draft {index}.\n")
    await transition(store, article.id, S.IN_REVIEW, actor="pipeline", now=at(4))
    return (await read_doc(store, Article, article.id)).doc


async def test_reads_never_change_state(
    client: httpx.AsyncClient, store: Store, store_state: StateReader
) -> None:
    article = await drafted_article(store)
    before = await store_state()
    for path in (
        "/api/articles",
        "/api/articles?purpose=all&status=in_review",
        f"/api/articles/{article.id}",
        f"/api/articles/{article.id}/versions",
        f"/api/articles/{article.id}/versions/2",
        f"/api/articles/{article.id}/preview",
        f"/api/articles/{article.id}/preview?version=1",
        f"/api/articles/{article.id}/export.md",
    ):
        response = await client.get(path, headers=READ)
        assert response.status_code in (200, 409), path
    assert await store_state() == before


async def test_article_text_never_reaches_the_logs(
    client: httpx.AsyncClient,
    store: Store,
    services: FakeServices,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    created = await client.post(
        "/api/articles", json={"meta": {"title": f"Title {MARKER}"}}, headers=WRITE
    )
    article_id = created.json()["item"]["id"]
    saved = await client.put(
        f"/api/articles/{article_id}",
        json={"meta": {"title": MARKER}, "markdown": f"Body {MARKER}.[^x]", "baseVersion": 0},
        headers={**WRITE, "If-Match": created.json()["etag"]},
    )
    assert saved.status_code == 200
    stale = await client.put(
        f"/api/articles/{article_id}",
        json={"meta": {}, "markdown": f"Other {MARKER}", "baseVersion": 0},
        headers={**WRITE, "If-Match": created.json()["etag"]},
    )
    assert stale.status_code == 412
    await client.get(f"/api/articles/{article_id}/preview", headers=READ)
    await client.get(f"/api/articles/{article_id}/export.md", headers=READ)
    moved = await client.post(
        f"/api/articles/{article_id}/transition",
        json={"to": "archived", "reason": f"Because {MARKER}"},
        headers={**WRITE, "If-Match": saved.json()["etag"]},
    )
    assert moved.status_code == 200
    drafting = sample_document(Article, status=S.DRAFTING)
    await create_doc(store, drafting)
    await pipeline_write(store, drafting.id, now=services.clock.now(), markdown=MARKER, note=MARKER)
    events = {record.getMessage() for record in caplog.records}
    assert {"article_created", "article_transition", "article_pipeline_write"} <= events
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        for value in record.__dict__.values():
            assert MARKER not in repr(value), record.getMessage()


ROUTES: Final = (
    ("GET", "/api/articles"),
    ("POST", "/api/articles"),
    ("GET", "/api/articles/art-1"),
    ("PUT", "/api/articles/art-1"),
    ("POST", "/api/articles/art-1/transition"),
    ("GET", "/api/articles/art-1/versions"),
    ("GET", "/api/articles/art-1/versions/1"),
    ("POST", "/api/articles/art-1/versions/1/restore"),
    ("GET", "/api/articles/art-1/preview"),
    ("GET", "/api/articles/art-1/export.md"),
    ("POST", "/api/articles/art-1/clearance"),
)


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_is_owner_only(client: httpx.AsyncClient, method: str, path: str) -> None:
    anonymous = await client.request(method, path)
    assert anonymous.status_code == 401
    stranger = await client.request(method, path, headers=principal_headers("someone-else"))
    assert stranger.status_code in (401, 403)
    if method != "GET":
        no_csrf = await client.request(method, path, headers=READ, json={})
        assert no_csrf.status_code == 403
        assert no_csrf.json()["code"] == "csrf_failed"


@pytest.mark.parametrize("status", list(ArticleStatus))
async def test_who_writes_holds_for_every_writer(store: Store, status: ArticleStatus) -> None:
    article = sample_document(Article, status=status, version=1, markdown="Text.\n")
    stored = await create_doc(store, article)
    writer = ARTICLE_WRITER[status]

    async def owner() -> object:
        return await owner_save(
            store,
            article.id,
            if_match=stored.etag,
            base_version=1,
            meta=ArticleMeta(),
            markdown="Owner edit.\n",
            now=at(1),
        )

    async def pipeline() -> object:
        return await pipeline_write(store, article.id, now=at(1), markdown="Pipeline edit.\n")

    for actor, attempt in (("owner", owner), ("pipeline", pipeline)):
        if writer == actor:
            continue
        with pytest.raises(WriterConflict):
            await attempt()
    for owner_action in (
        lambda: attest_clearance(store, article.id, if_match=stored.etag, statement="Ok.", now=T0),
        lambda: restore_version(store, article.id, 1, if_match=stored.etag, now=T0),
    ):
        if writer != "owner":
            with pytest.raises(WriterConflict):
                await owner_action()
    assert (await read_doc(store, Article, article.id)).etag == stored.etag
    if status is not S.ARCHIVED:
        linked = await set_links(store, article.id, ArticleLinks(brief_id="brief-1"), now=at(2))
        assert linked.doc.version == 1
    else:
        with pytest.raises(WriterConflict):
            await set_links(store, article.id, ArticleLinks(brief_id="brief-1"), now=at(2))


async def test_derived_fields_always_match_the_content(store: Store) -> None:
    created = await create_article(store, now=T0, meta=ArticleMeta(title="T"))
    docs = [created.doc]
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(title="T", format="case-study"),
        markdown="Some *words* here.\n\n```python\nignored = 1\n```\n",
        now=at(1),
    )
    docs.append(saved.stored.doc)
    drafting = await transition(store, created.doc.id, S.DRAFTING, actor="owner", now=at(2))
    docs.append(drafting.doc)
    written = await pipeline_write(
        store, created.doc.id, now=at(3), markdown="Pipeline words.[^a]\n\n[^a]: Source.\n"
    )
    docs.append(written.doc)
    review = await transition(store, created.doc.id, S.IN_REVIEW, actor="pipeline", now=at(4))
    docs.append(review.doc)
    restored = await restore_version(store, created.doc.id, 1, if_match=review.etag, now=at(5))
    docs.append(restored.doc)
    for doc in docs:
        assert doc.content_sha256 == doc.compute_sha256()
        assert (doc.word_count, doc.reading_time_minutes) == word_stats(doc.markdown)
    assert written.doc.word_count == 2  # footnotes do not count
    assert restored.doc.clearance.required  # the restored meta is a case study again


async def test_an_article_at_its_maximum_still_saves(store: Store) -> None:
    findings = [
        Finding(code="x", severity="block", message="m" * 500, ref="r" * 200, start=1, end=2)
        for _ in range(MAX_STORED_FINDINGS)
    ]
    report = GateReport(
        content_sha256=sha256_hex("c"),
        version=1,
        ran_at=to_iso(T0),
        results=[
            GateResult(gate=gate, verdict=GateVerdict.FAIL, findings=findings, ran_at=to_iso(T0))
            for gate in GATE_ORDER
        ],
    )
    article = sample_document(
        Article,
        status=S.IN_REVIEW,
        gates=report,
        embedding=[0.123456789012345678] * MAX_EMBEDDING_DIMENSIONS,
    )
    stored = await create_doc(store, article)
    body = "a" * MAX_ARTICLE_MARKDOWN_BYTES
    saved = await owner_save(
        store,
        article.id,
        if_match=stored.etag,
        base_version=0,
        meta=ArticleMeta(title="t" * 150),
        markdown=body,
        now=at(1),
    )
    assert saved.stored.doc.markdown == body + "\n" or saved.stored.doc.markdown == body
    size = len(prepare_write("articles", saved.stored.doc.to_document()).text.encode("utf-8"))
    assert size < MAX_DOCUMENT_BYTES


async def test_a_stale_owner_transition_writes_nothing(store: Store) -> None:
    created = await create_article(store, now=T0)
    await update_doc(
        store, Article, created.doc.id, lambda a: a.model_copy(update={"pipeline_runs": 1})
    )
    with pytest.raises(StaleArticle):
        await transition(
            store, created.doc.id, S.ARCHIVED, actor="owner", now=at(1), if_match=created.etag
        )
    assert (await read_doc(store, Article, created.doc.id)).doc.status is S.PLANNED


def test_settings_defaults_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ArticlesSettings().keep_versions == 50
    monkeypatch.setenv("BW_ARTICLES_KEEP_VERSIONS", "7")
    assert ArticlesSettings().keep_versions == 7
    monkeypatch.setenv("BW_ARTICLES_KEEP_VERSIONS", "0")
    with pytest.raises(ValidationError):
        ArticlesSettings()


def test_the_openapi_document_builds_with_the_articles_routes() -> None:
    app = make_app(make_services(), [get_router()], settings=make_settings())
    paths = app.openapi()["paths"]
    for path in (
        "/api/articles",
        "/api/articles/{article_id}",
        "/api/articles/{article_id}/transition",
        "/api/articles/{article_id}/versions",
        "/api/articles/{article_id}/versions/{version}",
        "/api/articles/{article_id}/versions/{version}/restore",
        "/api/articles/{article_id}/preview",
        "/api/articles/{article_id}/export.md",
        "/api/articles/{article_id}/clearance",
    ):
        assert path in paths, path
    save = paths["/api/articles/{article_id}"]["put"]
    assert "ArticleSaveIn" in str(save["requestBody"])
