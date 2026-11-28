"""Typed store access over MemoryStore: kind isolation, CAS, singletons, bad documents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, override

import pytest
from pydantic import BaseModel, Field

from blogwriter.db import Conflict, Doc, Filter, MemoryStore, NotFound, OrderBy, PreconditionFailed
from blogwriter.domain.articles import Article, ArticleVersion
from blogwriter.domain.base import DocId, DomainModel
from blogwriter.domain.docstore import (
    InvalidDocumentWrite,
    InvalidStoredDocument,
    Projected,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    save_singleton,
    update_doc,
    update_singleton,
    upsert_doc,
)
from blogwriter.domain.planner import WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import AutonomySettings, CadenceSettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ARTICLE_MACHINE, ArticleStatus, TransitionError
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.jobs.testing import DelegatingStore


async def test_create_read_replace_delete() -> None:
    store = MemoryStore()
    article = sample_document(Article)
    created = await create_doc(store, article)
    with pytest.raises(Conflict):
        await create_doc(store, article)
    changed = created.doc.model_copy(update={"markdown": "New body", "version": 1})
    replaced = await replace_doc(store, changed, if_match=created.etag)
    assert replaced.doc.markdown == "New body"
    with pytest.raises(PreconditionFailed):
        await replace_doc(store, changed, if_match=created.etag)
    await delete_doc(store, Article, article.id, if_match=replaced.etag)
    with pytest.raises(NotFound):
        await read_doc(store, Article, article.id)


async def test_kinds_sharing_a_container_stay_apart() -> None:
    store = MemoryStore()
    idea = sample_document(Idea)
    plan = sample_document(WeekPlan)
    await create_doc(store, idea)
    await create_doc(store, plan)
    assert [s.doc.id for s in await find_docs(store, Idea)] == [idea.id]
    assert [s.doc.id for s in await find_docs(store, WeekPlan)] == [plan.id]
    with pytest.raises(NotFound):
        await read_doc(store, Idea, plan.id)


async def test_find_with_filters_order_and_limit() -> None:
    store = MemoryStore()
    for index in range(5):
        await create_doc(
            store,
            sample_document(
                Article,
                id=f"art-{index}",
                updated_at=f"2026-11-2{index}T12:00:00.000000Z",
                status=ArticleStatus.IN_REVIEW if index % 2 else ArticleStatus.PLANNED,
            ),
        )
    found = await find_docs(
        store,
        Article,
        [Filter("status", "==", "in_review")],
        order_by=OrderBy("updatedAt", descending=True),
        limit=1,
    )
    assert [s.doc.id for s in found] == ["art-3"]


async def test_partitioned_containers_need_the_partition_key() -> None:
    store = MemoryStore()
    version = sample_document(ArticleVersion)
    await create_doc(store, version)
    read = await read_doc(store, ArticleVersion, version.id, partition_key=version.article_id)
    assert read.doc == version
    in_partition = await find_docs(store, ArticleVersion, partition_key=version.article_id)
    assert [s.doc.id for s in in_partition] == [version.id]


async def test_bad_documents_are_skipped_in_lists_and_refused_on_read() -> None:
    store = MemoryStore()
    good = sample_document(Idea)
    await create_doc(store, good)
    await store.create("ideas", {"id": "idea-bad", "kind": "idea", "title": 5})
    assert [s.doc.id for s in await find_docs(store, Idea)] == [good.id]
    with pytest.raises(InvalidStoredDocument):
        await read_doc(store, Idea, "idea-bad")


async def test_update_retries_lost_cas_rounds() -> None:
    class Interfering(DelegatingStore):
        """Writes behind the caller's back once, right after its first read."""

        def __init__(self, inner: MemoryStore) -> None:
            super().__init__(inner)
            self.interfered = False

        @override
        async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
            doc = await super().read(container, id, partition_key=partition_key)
            if not self.interfered:
                self.interfered = True
                body: Mapping[str, Any] = {**doc.body, "version": 7}
                await super().replace(container, body, if_match=doc.etag)
            return doc

    inner = MemoryStore()
    article = sample_document(Article)
    await create_doc(inner, article)
    store = Interfering(inner)
    calls: list[int] = []

    def bump(doc: Article) -> Article:
        calls.append(doc.version)
        return doc.model_copy(update={"version": doc.version + 1})

    updated = await update_doc(store, Article, article.id, bump)
    assert calls == [0, 7]
    assert updated.doc.version == 8


async def test_update_can_leave_the_document_unchanged_or_refuse() -> None:
    store = MemoryStore()
    article = sample_document(Article)
    created = await create_doc(store, article)
    same = await update_doc(store, Article, article.id, lambda _doc: None)
    assert same.etag == created.etag

    def bad(doc: Article) -> Article:
        target = ARTICLE_MACHINE.transition(doc.status, ArticleStatus.PUBLISHED)
        return doc.model_copy(update={"status": target})

    with pytest.raises(TransitionError):
        await update_doc(store, Article, article.id, bad)
    assert (await read_doc(store, Article, article.id)).etag == created.etag
    with pytest.raises(ValueError, match="attempts"):
        await update_doc(store, Article, article.id, bad, attempts=0)


async def test_update_gives_up_after_too_many_lost_rounds() -> None:
    class AlwaysLosing(DelegatingStore):
        @override
        async def replace(
            self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
        ) -> Doc:
            raise PreconditionFailed("lost")

    inner = MemoryStore()
    article = sample_document(Article)
    await create_doc(inner, article)
    with pytest.raises(PreconditionFailed, match="3 CAS rounds"):
        await update_doc(
            AlwaysLosing(inner),
            Article,
            article.id,
            lambda doc: doc.model_copy(update={"version": 1}),
            attempts=3,
        )


async def test_singletons_default_create_and_replace() -> None:
    store = MemoryStore()
    cadence, etag = await read_singleton(store, CadenceSettings, now=SAMPLE_NOW)
    assert etag is None
    assert cadence.timezone == "America/New_York"
    created = await save_singleton(
        store, cadence.model_copy(update={"timezone": "UTC"}), if_match=None
    )
    with pytest.raises(PreconditionFailed):
        await save_singleton(store, cadence, if_match=None)
    replaced = await save_singleton(
        store, created.doc.model_copy(update={"timezone": "Europe/London"}), if_match=created.etag
    )
    stored, stored_etag = await read_singleton(store, CadenceSettings, now=SAMPLE_NOW)
    assert stored.timezone == "Europe/London"
    assert stored_etag == replaced.etag


async def test_upsert() -> None:
    store = MemoryStore()
    idea = sample_document(Idea)
    first = await upsert_doc(store, idea)
    second = await upsert_doc(store, idea.model_copy(update={"title": "Changed"}))
    assert first.etag != second.etag
    assert (await read_doc(store, Idea, idea.id)).doc.title == "Changed"


async def test_writes_validate_before_any_io() -> None:
    """Probe from the review: a model_copy with a bad status was committed, then vanished."""
    store = MemoryStore()
    article = sample_document(Article)
    created = await create_doc(store, article)
    bogus = created.doc.model_copy(update={"status": "bogus"})
    with pytest.raises(InvalidDocumentWrite):
        await replace_doc(store, bogus, if_match=created.etag)
    with pytest.raises(InvalidDocumentWrite):
        await upsert_doc(store, bogus)
    with pytest.raises(InvalidDocumentWrite):
        await create_doc(store, bogus.model_copy(update={"id": "art-other"}))
    with pytest.raises(InvalidDocumentWrite):
        await update_doc(store, Article, article.id, lambda doc: bogus)
    unchanged = await read_doc(store, Article, article.id)
    assert unchanged.etag == created.etag
    assert [s.doc.id for s in await find_docs(store, Article)] == [article.id]


async def test_documents_too_large_for_cosmos_are_refused_before_io() -> None:
    store = MemoryStore()
    huge = sample_document(Post, carousel_spec={"slides": ["x" * 2_100_000]})
    with pytest.raises(InvalidDocumentWrite, match="cannot be stored"):
        await create_doc(store, huge)
    assert await find_docs(store, Post) == []


async def test_strict_finds_fail_closed_on_bad_documents() -> None:
    store = MemoryStore()
    await create_doc(store, sample_document(Idea))
    await store.create("ideas", {"id": "idea-bad", "kind": "idea", "title": 5})
    assert len(await find_docs(store, Idea)) == 1
    with pytest.raises(InvalidStoredDocument):
        await find_docs(store, Idea, strict=True)


async def test_ids_that_cannot_exist_read_as_not_found() -> None:
    store = MemoryStore()
    for bad in ("art#1", "a/b", ""):
        with pytest.raises(NotFound):
            await read_doc(store, Article, bad)
        with pytest.raises(NotFound):
            await delete_doc(store, Article, bad)


async def test_update_singleton_creates_replaces_and_leaves_alone() -> None:
    store = MemoryStore()

    def pause(doc: AutonomySettings) -> AutonomySettings:
        return doc.model_copy(update={"paused": True, "paused_at": SAMPLE_NOW})

    untouched, etag = await update_singleton(
        store, AutonomySettings, lambda _d: None, now=SAMPLE_NOW
    )
    assert etag is None
    assert not untouched.paused
    with pytest.raises(NotFound):
        await read_doc(store, AutonomySettings, AutonomySettings.SINGLETON_ID)
    paused, first = await update_singleton(store, AutonomySettings, pause, now=SAMPLE_NOW)
    assert paused.paused
    assert first is not None
    resumed, second = await update_singleton(
        store,
        AutonomySettings,
        lambda doc: doc.model_copy(update={"paused": False}),
        now=SAMPLE_NOW,
    )
    assert not resumed.paused
    assert second not in (None, first)
    same, third = await update_singleton(store, AutonomySettings, lambda _d: None, now=SAMPLE_NOW)
    assert (same.paused, third) == (False, second)


async def test_update_singleton_retries_a_lost_create() -> None:
    class CreatesFirst(DelegatingStore):
        """Another writer creates the singleton right after our first read."""

        def __init__(self, inner: MemoryStore) -> None:
            super().__init__(inner)
            self.raced = False

        @override
        async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
            try:
                return await super().read(container, id, partition_key=partition_key)
            except NotFound:
                if not self.raced:
                    self.raced = True
                    other = AutonomySettings.default(SAMPLE_NOW).model_copy(
                        update={"auto_publish_enabled": True}
                    )
                    await super().create(container, other.to_document())
                raise

    store = CreatesFirst(MemoryStore())
    doc, etag = await update_singleton(
        store,
        AutonomySettings,
        lambda d: d.model_copy(update={"paused": True}),
        now=SAMPLE_NOW,
    )
    assert etag is not None
    assert doc.paused
    assert doc.auto_publish_enabled  # built on the other writer's document, not over it


# --------------------------------------------------------------------------------------------
# Projections (contract I-10)
# --------------------------------------------------------------------------------------------


class ArticleHead(DomainModel):
    """A test view: three fields of an article."""

    id: DocId
    status: ArticleStatus = ArticleStatus.PLANNED
    version: int = Field(default=0, ge=0)


class IdeaTitle(DomainModel):
    id: DocId
    title: str = Field(min_length=1, max_length=300)


class RecordingStore(DelegatingStore):
    """Records the projection of every query."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.projections: list[list[str] | None] = []

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
        self.projections.append(None if fields is None else list(fields))
        return await super().find(
            container,
            filters,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=fields,
        )


async def test_projected_finds_read_only_the_view_fields() -> None:
    store = RecordingStore()
    first = sample_document(
        Article, status=ArticleStatus.IN_REVIEW, version=2, markdown="Secret body."
    )
    second = sample_document(Article, version=1, updated_at="2026-11-26T12:00:01.000000Z")
    stored = [await create_doc(store, first), await create_doc(store, second)]
    rows = await find_docs(
        store,
        Article,
        [Filter("version", ">=", 1)],
        order_by=OrderBy("version", descending=True),
        limit=5,
        fields=ArticleHead,
    )
    assert store.projections == [["kind", "id", "status", "version"]]
    assert rows == [
        Projected(
            ArticleHead(id=first.id, status=ArticleStatus.IN_REVIEW, version=2), stored[0].etag
        ),
        Projected(ArticleHead(id=second.id, version=1), stored[1].etag),
    ]
    assert rows[0].doc.model_extra == {}  # nothing beyond the view's own fields
    (whole,) = await find_docs(store, Article, [Filter("id", "==", first.id)])
    assert store.projections[-1] is None  # whole documents unless asked
    assert whole.doc.markdown == "Secret body."


async def test_projected_finds_keep_kinds_apart() -> None:
    store = MemoryStore()
    idea = sample_document(Idea, title="Least privilege")
    await create_doc(store, idea)
    await create_doc(store, sample_document(WeekPlan))
    rows = await find_docs(store, Idea, fields=IdeaTitle)
    assert [(row.doc.id, row.doc.title) for row in rows] == [(idea.id, "Least privilege")]


async def test_projected_finds_judge_only_the_projected_fields() -> None:
    store = MemoryStore()
    good = sample_document(Article, version=1)
    await create_doc(store, good)
    body = sample_document(Article).to_document()
    await store.create("articles", {**body, "id": "art-bad-version", "version": -1})
    await store.create("articles", {**body, "id": "art-bad-markdown", "markdown": 5})
    rows = await find_docs(store, Article, fields=ArticleHead)
    assert sorted(row.doc.id for row in rows) == sorted([good.id, "art-bad-markdown"])
    with pytest.raises(InvalidStoredDocument, match="art-bad-version"):
        await find_docs(store, Article, fields=ArticleHead, strict=True)


async def test_views_are_plain_models_naming_fields_of_the_document() -> None:
    store = MemoryStore()

    class Misspelt(DomainModel):
        id: DocId
        markdown_length: int = 0

    class Unaliased(BaseModel):
        status_changed_at: str

    with pytest.raises(TypeError, match="is a document"):
        await find_docs(store, Article, fields=Article)
    with pytest.raises(TypeError, match=r"Misspelt\.markdown_length is not a field of Article"):
        await find_docs(store, Article, fields=Misspelt)
    with pytest.raises(TypeError, match="not a field of Article"):
        await find_docs(store, Article, fields=Unaliased)
