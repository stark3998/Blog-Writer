"""Article and version lists read projections (contract I-10): rows mirror the documents, the
summaries built from rows equal the ones built from whole documents, and the routes never ask
the store for Markdown, embeddings or served-by records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, override

from pydantic import BaseModel

from blogwriter.articles import get_router, owner_save, pipeline_write, transition
from blogwriter.articles.models import ArticleRow, ArticleSummary, VersionRow, VersionSummary
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.db import Doc, Filter, MemoryStore, OrderBy, Store
from blogwriter.domain.api import Versioned
from blogwriter.domain.articles import (
    GATE_ORDER,
    Article,
    ArticleMeta,
    ArticleVersion,
    GateReport,
    GateResult,
    GateVerdict,
    PublishState,
)
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, update_doc
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import SAMPLE_NOW, make_app, make_services, sample_document
from blogwriter.jobs.testing import DelegatingStore

T: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
S = ArticleStatus
READ: Final = principal_headers(OWNER_OID)
LARGE: Final = frozenset({"markdown", "embedding", "servedBy", "media", "ownerSignals"})


class ProjectionLog(DelegatingStore):
    """A MemoryStore that records every query's projection and every point read."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.finds: list[tuple[str, tuple[str, ...] | None]] = []
        self.reads: list[str] = []

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        self.reads.append(f"{container}/{id}")
        return await super().read(container, id, partition_key=partition_key)

    @override
    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> list[Doc]:
        self.finds.append((container, None if fields is None else tuple(fields)))
        return await super().find(
            container,
            filters,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=fields,
        )


def assert_mirrors(row: type[BaseModel], document: type[BaseModel]) -> None:
    """Every row field is the document's field: same name, alias, type, rules and default."""
    for name, field in row.model_fields.items():
        source = document.model_fields[name]
        assert field.alias == source.alias, name
        assert field.annotation == source.annotation, name
        assert field.metadata == source.metadata, name
        assert field.is_required() == source.is_required(), name
        if not field.is_required():
            assert field.get_default(call_default_factory=True) == source.get_default(
                call_default_factory=True
            ), name


def passing_report(article: Article) -> GateReport:
    results = [
        GateResult(gate=gate, verdict=GateVerdict.PASS, ran_at=SAMPLE_NOW) for gate in GATE_ORDER
    ]
    return GateReport(
        content_sha256=article.compute_sha256(),
        version=max(article.version, 1),
        ran_at=SAMPLE_NOW,
        results=results,
    )


async def drafted(store: Store, markdown: str, **fields: Any) -> Article:
    """An article written the way the pipeline writes it (content hash and version stored)."""
    created = await create_doc(store, sample_document(Article, status=S.DRAFTING, **fields))
    written = await pipeline_write(
        store,
        created.doc.id,
        now=T,
        markdown=markdown,
        meta=ArticleMeta(title="Least privilege", pillar="agents"),
        served_by=[
            ServedByRecord(
                step="draft",
                task="draft",
                model="m",
                deployment="d",
                provider="openai",
                at=SAMPLE_NOW,
            )
        ],
    )
    return written.doc


async def gated(store: Store, article: Article) -> Article:
    stored = await update_doc(
        store, Article, article.id, lambda a: a.model_copy(update={"gates": passing_report(a)})
    )
    moved = await transition(store, article.id, S.IN_REVIEW, actor="pipeline", now=T)
    assert moved.doc.gates == stored.doc.gates
    return moved.doc


async def summaries_from_whole_documents(store: Store) -> dict[str, tuple[ArticleSummary, str]]:
    return {
        stored.doc.id: (ArticleSummary.of(stored.doc), stored.etag)
        for stored in await find_docs(store, Article)
    }


# --------------------------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------------------------


def test_rows_mirror_their_documents() -> None:
    assert_mirrors(ArticleRow, Article)
    assert_mirrors(VersionRow, ArticleVersion)
    assert not LARGE & {field.alias for field in ArticleRow.model_fields.values()}
    assert {"markdown", "meta"}.isdisjoint(
        field.alias for field in VersionRow.model_fields.values()
    )


async def test_row_summaries_equal_whole_document_summaries() -> None:
    store = MemoryStore()
    fresh = await gated(store, await drafted(store, "Fresh body.\n" * 50))
    stale = await gated(store, await drafted(store, "Old body.\n"))
    current = await read_doc(store, Article, stale.id)
    await owner_save(
        store,
        stale.id,
        if_match=current.etag,
        base_version=current.doc.version,
        meta=current.doc.meta,
        markdown="New body after the gates ran.\n",
        now=T + timedelta(minutes=1),
    )
    plain = await drafted(store, "No report yet.\n")
    live = sample_document(
        Article,
        status=S.PUBLISHED,
        markdown="Live body.\n",
        publish=PublishState(live=True, url="https://example.com/blog/live", slug="live"),
        embedding=[0.25] * 8,
    )
    live = live.model_copy(update={"content_sha256": live.compute_sha256()})
    live = live.model_copy(update={"gates": passing_report(live)})
    await create_doc(store, live)

    expected = await summaries_from_whole_documents(store)
    rows = await find_docs(store, Article, fields=ArticleRow)
    assert {row.doc.id for row in rows} == {fresh.id, stale.id, plain.id, live.id}
    for row in rows:
        assert row.doc.gates_judgeable
        assert (ArticleSummary.of(row.doc), row.etag) == expected[row.doc.id]
    by_id = {row.doc.id: ArticleSummary.of(row.doc) for row in rows}
    fresh_gates, stale_gates = by_id[fresh.id].gates, by_id[stale.id].gates
    assert fresh_gates is not None
    assert stale_gates is not None
    assert (fresh_gates.stale, fresh_gates.auto_publish_ready) == (False, True)
    assert (stale_gates.stale, stale_gates.auto_publish_ready) == (True, False)
    assert by_id[plain.id].gates is None
    assert by_id[live.id].publish.live


async def test_version_rows_summarize_like_whole_versions() -> None:
    store = MemoryStore()
    article = await drafted(store, "One.\n")
    for index in range(2, 4):
        await pipeline_write(store, article.id, now=T, markdown=f"Draft {index}.\n", note="Again")
    wholes = await find_docs(store, ArticleVersion, partition_key=article.id)
    rows = await find_docs(store, ArticleVersion, partition_key=article.id, fields=VersionRow)
    assert len(rows) == len(wholes) == 3
    expected = {s.doc.version: (VersionSummary.of(s.doc, pinned=False), s.etag) for s in wholes}
    for row in rows:
        assert (VersionSummary.of(row.doc, pinned=False), row.etag) == expected[row.doc.version]


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------


async def test_the_lists_never_ask_for_large_fields() -> None:
    log = ProjectionLog()
    services = make_services(store=log)
    article = await gated(log, await drafted(log, "A long body.\n" * 400))
    expected = await summaries_from_whole_documents(log)
    log.finds.clear()
    log.reads.clear()
    app = make_app(services, [get_router()], settings=make_settings())
    async with asgi_client(app) as client:
        listed = await client.get("/api/articles?purpose=all", headers=READ)
        versions = await client.get(f"/api/articles/{article.id}/versions", headers=READ)
    assert listed.status_code == 200, listed.text
    summary, etag = expected[article.id]
    assert listed.json()["items"] == [
        Versioned[ArticleSummary](item=summary, etag=etag).model_dump(mode="json", by_alias=True)
    ]
    assert "A long body" not in listed.text
    assert versions.status_code == 200, versions.text
    assert [item["item"]["version"] for item in versions.json()["items"]] == [1]
    assert log.finds == [
        ("articles", ("kind", *(f.alias for f in ArticleRow.model_fields.values()))),
        ("article-versions", ("kind", *(f.alias for f in VersionRow.model_fields.values()))),
    ]
    for _, fields in log.finds:
        assert fields is not None
        assert not LARGE & set(fields)
    assert log.reads == [f"articles/{article.id}"]  # the versions route reads its article once


async def test_a_row_without_a_content_hash_is_read_whole_once() -> None:
    """An article written outside the articles API has no stored hash: its report is judged
    on the whole article (one point read), so the summary is the same as before."""
    log = ProjectionLog()
    services = make_services(store=log)
    article = sample_document(Article, status=S.IN_REVIEW, markdown="Body.")
    await create_doc(log, article.model_copy(update={"gates": passing_report(article)}))
    hashed = await gated(log, await drafted(log, "Hashed.\n"))
    log.reads.clear()
    app = make_app(services, [get_router()], settings=make_settings())
    async with asgi_client(app) as client:
        items = (await client.get("/api/articles", headers=READ)).json()["items"]
    by_id = {item["item"]["id"]: item["item"] for item in items}
    assert by_id[article.id]["gates"]["stale"] is False
    assert by_id[article.id]["gates"]["autoPublishReady"] is True
    assert by_id[hashed.id]["gates"]["stale"] is False
    assert log.reads == [f"articles/{article.id}"]


async def test_a_row_that_cannot_be_judged_counts_its_report_stale() -> None:
    store = MemoryStore()
    services = make_services(store=store)
    article = sample_document(Article, status=S.IN_REVIEW, markdown="Body.")
    body = article.model_copy(update={"gates": passing_report(article)}).to_document()
    await store.create("articles", {**body, "markdown": 5})  # the whole article cannot be read
    app = make_app(services, [get_router()], settings=make_settings())
    async with asgi_client(app) as client:
        (item,) = (await client.get("/api/articles", headers=READ)).json()["items"]
    assert item["item"]["id"] == article.id
    assert item["item"]["gates"]["stale"] is True  # fail closed: it cannot tell
    assert item["item"]["gates"]["autoPublishReady"] is False
    assert item["item"]["gates"]["manualPublishReady"] is False
