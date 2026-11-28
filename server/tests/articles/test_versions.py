"""Versions: deterministic ids, autosave coalescing, pruning that keeps N plus the pinned
versions (gated, gate report, last published)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from blogwriter.articles import create_article, owner_save, pipeline_write, transition
from blogwriter.articles.versions import (
    ensure_version,
    list_versions,
    new_version,
    prune_versions,
    store_version,
)
from blogwriter.db import Filter, Store
from blogwriter.domain.articles import (
    AUTOSAVE_SNAPSHOT_EVERY,
    Article,
    ArticleMeta,
    ArticleVersion,
    CalibrationFlags,
    GateReport,
    PublishState,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, update_doc
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import sample_document

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
S = ArticleStatus


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


async def stored_versions(store: Store, article_id: str) -> list[ArticleVersion]:
    found = await find_docs(
        store, ArticleVersion, [Filter("articleId", "==", article_id)], partition_key=article_id
    )
    return sorted((item.doc for item in found), key=lambda version: version.version)


async def test_version_ids_are_deterministic(store: Store) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    for index in range(3):
        await pipeline_write(store, article.doc.id, now=at(index), markdown=f"Draft {index}.\n")
    versions = await stored_versions(store, article.doc.id)
    assert [version.id for version in versions] == [
        f"{article.doc.id}-v00001",
        f"{article.doc.id}-v00002",
        f"{article.doc.id}-v00003",
    ]
    assert [version.id for version in versions] == [
        ArticleVersion.id_for(article.doc.id, n) for n in (1, 2, 3)
    ]
    current = (await read_doc(store, Article, article.doc.id)).doc
    copy = new_version(
        current, source="pipeline", author="pipeline", status_at_save=S.DRAFTING, now=T0
    )
    assert copy.id == versions[-1].id
    assert not await store_version(store, copy)  # a retried store is a Conflict, not a duplicate


async def test_pruning_keeps_the_newest_n_and_the_pinned_versions(
    store: Store, keep_three: int
) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    for index in range(1, 4):
        await pipeline_write(store, article.doc.id, now=at(index), markdown=f"Draft {index}.\n")
    report = GateReport(content_sha256=sha256_hex("v3"), version=3, ran_at=to_iso(T0))
    await update_doc(
        store,
        Article,
        article.doc.id,
        lambda a: a.model_copy(
            update={
                "calibration": CalibrationFlags(gated_version=2),
                "gates": report,
                "publish": PublishState(last_published_version=1),
            }
        ),
    )
    for index in range(4, 10):
        await pipeline_write(store, article.doc.id, now=at(index), markdown=f"Draft {index}.\n")
    kept = [version.version for version in await stored_versions(store, article.doc.id)]
    assert kept == [1, 2, 3, 7, 8, 9]
    assert keep_three == 3


async def test_pruning_deletes_unpinned_versions_oldest_first(
    store: Store, keep_three: int
) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    for index in range(1, 6):
        await pipeline_write(store, article.doc.id, now=at(index), markdown=f"Draft {index}.\n")
    assert [v.version for v in await stored_versions(store, article.doc.id)] == [3, 4, 5]
    assert await prune_versions(store, article.doc.id, keep=1) == [3, 4]
    assert await prune_versions(store, "art-gone", keep=1) == []


async def test_an_hour_of_typing_stores_at_most_six_autosave_versions(store: Store) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    drafted = await pipeline_write(store, article.doc.id, now=T0, markdown="Draft.\n")
    review = await transition(store, article.doc.id, S.IN_REVIEW, actor="pipeline", now=T0)
    assert drafted.doc.version == 1
    etag, version = review.etag, review.doc.version
    for second in range(0, 3600, 15):
        saved = await owner_save(
            store,
            article.doc.id,
            if_match=etag,
            base_version=version,
            meta=ArticleMeta(),
            markdown=f"Draft, edited at second {second}.\n",
            now=at(second),
        )
        etag, version = saved.stored.etag, saved.stored.doc.version
    autosaves = [v for v in await stored_versions(store, article.doc.id) if v.source == "autosave"]
    assert len(autosaves) == 6
    assert [v.created_at for v in autosaves] == [
        to_iso(at(minutes * 60)) for minutes in (0, 10, 20, 30, 40, 50)
    ]
    assert version == 241
    later = await owner_save(
        store,
        article.doc.id,
        if_match=etag,
        base_version=version,
        meta=ArticleMeta(),
        markdown="The hour is over.\n",
        now=at(3600),
    )
    seventh = await stored_versions(store, article.doc.id)
    assert seventh[-1].version == later.stored.doc.version
    assert sum(v.source == "autosave" for v in seventh) == 7


async def test_typing_right_after_a_pipeline_copy_stores_a_new_autosave(store: Store) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    await pipeline_write(store, article.doc.id, now=T0, markdown="Draft.\n")
    review = await transition(store, article.doc.id, S.IN_REVIEW, actor="pipeline", now=T0)
    saved = await owner_save(
        store,
        article.doc.id,
        if_match=review.etag,
        base_version=1,
        meta=ArticleMeta(),
        markdown="Edited a second later.\n",
        now=at(1),
    )
    sources = [v.source for v in await stored_versions(store, article.doc.id)]
    assert sources == ["pipeline", "autosave"]
    again = await owner_save(
        store,
        article.doc.id,
        if_match=saved.stored.etag,
        base_version=2,
        meta=ArticleMeta(),
        markdown="Edited before ten minutes passed.\n",
        now=at(1) + AUTOSAVE_SNAPSHOT_EVERY - timedelta(seconds=1),
    )
    assert again.stored.doc.version == 3
    assert [v.version for v in await stored_versions(store, article.doc.id)] == [1, 2]


async def test_ensure_version_stores_only_missing_content(store: Store) -> None:
    created = await create_article(store, now=T0)
    assert not await ensure_version(
        store,
        created.doc,
        source="autosave",
        author="owner",
        status_at_save=S.PLANNED,
        now=T0,
        keep=50,
    )
    article = sample_document(Article, version=4, markdown="Text.\n")
    await create_doc(store, article)
    assert await ensure_version(
        store,
        article,
        source="publish",
        author="system",
        status_at_save=S.APPROVED,
        now=T0,
        keep=50,
    )
    assert not await ensure_version(
        store,
        article,
        source="publish",
        author="system",
        status_at_save=S.APPROVED,
        now=T0,
        keep=50,
    )


async def test_version_pages_run_newest_first(store: Store) -> None:
    article = await create_doc(store, sample_document(Article, status=S.DRAFTING))
    for index in range(1, 6):
        await pipeline_write(store, article.doc.id, now=at(index), markdown=f"Draft {index}.\n")
    first = await list_versions(store, article.doc.id, limit=2)
    assert [item.doc.version for item in first] == [5, 4]
    rest = await list_versions(store, article.doc.id, limit=10, before=4)
    assert [item.doc.version for item in rest] == [3, 2, 1]
