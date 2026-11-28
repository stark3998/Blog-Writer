"""The articles Python API: create, owner autosave (merge and conflicts), pipeline writes, links,
transitions, restore, clearance and ideas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from blogwriter.articles import (
    ArticleLinks,
    MarkdownTooLarge,
    StaleArticle,
    attest_clearance,
    create_article,
    owner_save,
    pipeline_write,
    restore_version,
    set_links,
    transition,
)
from blogwriter.articles.service import check_idea_usable, use_idea
from blogwriter.articles.versions import latest_version, read_version
from blogwriter.db import Conflict, NotFound, Store
from blogwriter.domain.articles import (
    MAX_ARTICLE_MARKDOWN_BYTES,
    Article,
    ArticleMeta,
    ArticleVersion,
    GateReport,
    MediaRef,
    OwnerSignalRef,
    PublishState,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.compose import word_stats
from blogwriter.domain.docstore import Stored, create_doc, delete_doc, read_doc, update_doc
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import (
    ARTICLE_WRITER,
    ArticleStatus,
    IdeaStatus,
    TransitionError,
    TransitionRefused,
    WriterConflict,
)
from blogwriter.domain.stories import StoryEntry
from blogwriter.domain.testing import sample_document

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
S = ArticleStatus


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def make_article(
    store: Store, status: ArticleStatus = S.PLANNED, **fields: object
) -> Stored[Article]:
    return await create_doc(store, sample_document(Article, status=status, **fields))


def served(step: str = "draft") -> ServedByRecord:
    return ServedByRecord(
        step=step,
        at=to_iso(T0),
        task="draft",
        model="claude-opus-5-5",
        deployment="claude-opus-5-5",
        provider="anthropic",
    )


def gates_write(report_version: int) -> GateReport:
    return GateReport(content_sha256=sha256_hex("gated"), version=report_version, ran_at=to_iso(T0))


# --------------------------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------------------------


async def test_create_article_starts_planned_and_empty(store: Store) -> None:
    created = await create_article(store, now=T0, week_plan_id="plan-2026-11-30")
    article = created.doc
    assert article.id.startswith("art-")
    assert article.status is S.PLANNED
    assert article.status_changed_at == to_iso(T0)
    assert (article.version, article.markdown, article.word_count) == (0, "", 0)
    assert article.content_sha256 == article.compute_sha256()
    assert article.updated_by == "owner"
    assert article.purpose == "flagship"
    assert article.week_plan_id == "plan-2026-11-30"
    assert not article.clearance.required
    assert (await read_doc(store, Article, article.id)).etag == created.etag


async def test_case_studies_need_clearance_and_eval_articles_have_fixed_ids(store: Store) -> None:
    study = await create_article(store, now=T0, meta=ArticleMeta(format="case-study"))
    assert study.doc.clearance.required
    eval_id = Article.id_for_eval("evt-1", "job-1")
    evaluation = await create_article(store, now=T0, purpose="eval", article_id=eval_id)
    assert evaluation.doc.id == eval_id
    assert evaluation.doc.updated_by == "system"
    with pytest.raises(Conflict):
        await create_article(store, now=T0, purpose="eval", article_id=eval_id)


# --------------------------------------------------------------------------------------------
# Owner autosave
# --------------------------------------------------------------------------------------------


async def test_owner_save_normalizes_counts_and_snapshots(store: Store) -> None:
    created = await create_article(store, now=T0)
    meta = ArticleMeta(title="Agent identity", pillar="agents")
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=meta,
        markdown="Setext heading\n--------------\n\n* one\n* two\n",
        now=at(1),
    )
    article = saved.stored.doc
    assert article.markdown == "## Setext heading\n\n- one\n- two\n"
    assert saved.findings == []
    assert article.version == 1
    assert article.meta == meta
    assert article.content_sha256 == article.compute_sha256()
    assert (article.word_count, article.reading_time_minutes) == word_stats(article.markdown)
    assert article.updated_by == "owner"
    assert article.updated_at == to_iso(at(1))
    snapshot = (await read_version(store, article.id, 1)).doc
    assert (snapshot.source, snapshot.author, snapshot.status_at_save) == (
        "autosave",
        "owner",
        S.PLANNED,
    )
    assert snapshot.markdown == article.markdown
    assert snapshot.content_sha256 == article.content_sha256
    assert snapshot.id == ArticleVersion.id_for(article.id, 1)


async def test_unchanged_content_writes_nothing(store: Store) -> None:
    created = await create_article(store, now=T0)
    first = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(),
        markdown="Body.\n",
        now=at(1),
    )
    again = await owner_save(
        store,
        created.doc.id,
        if_match=first.stored.etag,
        base_version=1,
        meta=ArticleMeta(),
        markdown="Body.",
        now=at(2),
    )
    assert again.stored.etag == first.stored.etag
    assert again.stored.doc.version == 1


async def test_findings_come_from_the_normalizer_and_the_metadata(store: Store) -> None:
    created = await create_article(store, now=T0)
    body = "Intro.\n\nA claim.[^nope]\n"
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(title="Tags <b> in a title"),
        markdown=body,
        now=at(1),
    )
    codes = [finding.code for finding in saved.findings]
    assert codes == ["frontmatter-invalid", "citation-undefined"]
    meta_finding, citation = saved.findings
    assert meta_finding.severity == "block"
    assert meta_finding.message.startswith("title: ")
    assert "<b>" not in meta_finding.message
    assert citation.start is not None
    assert citation.end is not None
    assert body[citation.start : citation.end] == "A claim.[^nope]"
    # Saved anyway: autosave never loses typing.
    assert saved.stored.doc.version == 1
    assert saved.stored.doc.meta.title == "Tags <b> in a title"


async def test_a_background_write_between_load_and_autosave_is_merged(store: Store) -> None:
    created = await create_article(store, now=T0)
    loaded = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(),
        markdown="First.\n",
        now=at(1),
    )
    report = gates_write(1)
    await update_doc(
        store,
        Article,
        created.doc.id,
        lambda a: a.model_copy(update={"gates": report, "embedding_sha256": sha256_hex("e")}),
    )
    await set_links(store, created.doc.id, ArticleLinks(brief_id="brief-run-1"), now=at(2))
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=loaded.stored.etag,
        base_version=1,
        meta=ArticleMeta(),
        markdown="Second.\n",
        now=at(3),
    )
    article = saved.stored.doc
    assert article.version == 2
    assert article.markdown == "Second.\n"
    assert article.gates == report
    assert article.brief_id == "brief-run-1"
    assert article.embedding_sha256 == sha256_hex("e")


async def test_another_tabs_save_is_a_conflict_with_the_current_document(store: Store) -> None:
    created = await create_article(store, now=T0)
    await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(),
        markdown="Tab A.\n",
        now=at(1),
    )
    with pytest.raises(StaleArticle) as caught:
        await owner_save(
            store,
            created.doc.id,
            if_match=created.etag,
            base_version=0,
            meta=ArticleMeta(),
            markdown="Tab B.\n",
            now=at(2),
        )
    current = caught.value.current
    assert current.doc.version == 1
    assert current.doc.markdown == "Tab A.\n"
    assert current.doc.updated_by == "owner"
    assert (await read_doc(store, Article, created.doc.id)).doc.markdown == "Tab A.\n"


@pytest.mark.parametrize("status", [s for s in ArticleStatus if ARTICLE_WRITER[s] != "owner"])
async def test_owner_save_is_refused_where_the_owner_does_not_write(
    store: Store, status: ArticleStatus
) -> None:
    stored = await make_article(store, status)
    with pytest.raises(WriterConflict) as caught:
        await owner_save(
            store,
            stored.doc.id,
            if_match=stored.etag,
            base_version=0,
            meta=ArticleMeta(),
            markdown="Typing.\n",
            now=at(1),
        )
    assert caught.value.status is status
    assert caught.value.writer == ARTICLE_WRITER[status]
    assert (await read_doc(store, Article, stored.doc.id)).etag == stored.etag


async def test_bodies_over_512_kib_are_refused_before_any_io(store: Store) -> None:
    created = await create_article(store, now=T0)
    with pytest.raises(MarkdownTooLarge) as caught:
        await owner_save(
            store,
            created.doc.id,
            if_match=created.etag,
            base_version=0,
            meta=ArticleMeta(),
            markdown="é" * (MAX_ARTICLE_MARKDOWN_BYTES // 2 + 1),
            now=at(1),
        )
    assert caught.value.size == MAX_ARTICLE_MARKDOWN_BYTES + 2
    assert (await read_doc(store, Article, created.doc.id)).etag == created.etag


# --------------------------------------------------------------------------------------------
# Pipeline writes
# --------------------------------------------------------------------------------------------


async def test_pipeline_writes_content_records_and_versions(store: Store) -> None:
    stored = await make_article(store, S.DRAFTING)
    media = [
        MediaRef(
            role="diagram_svg",
            name="articles/x/d.svg",
            sha256=sha256_hex("d"),
            content_type="image/svg+xml",
            size_bytes=10,
        )
    ]
    written = await pipeline_write(
        store,
        stored.doc.id,
        now=at(1),
        markdown="A draft about agent identity.\n",
        meta=ArticleMeta(title="Agent identity"),
        served_by=[served()],
        review_only_reasons=["fallback_model"],
        media=media,
        note="Draft written.",
    )
    article = written.doc
    assert article.version == 1
    assert article.updated_by == "pipeline"
    assert article.content_sha256 == article.compute_sha256()
    assert article.word_count == 5
    assert [record.step for record in article.served_by] == ["draft"]
    assert article.review_only
    assert article.review_only_reasons == ["fallback_model"]
    assert article.media == media
    version = (await read_version(store, article.id, 1)).doc
    assert (version.source, version.author, version.note) == (
        "pipeline",
        "pipeline",
        "Draft written.",
    )
    again = await pipeline_write(
        store,
        article.id,
        now=at(2),
        served_by=[served("normalize")],
        review_only_reasons=["refusal", "fallback_model"],
    )
    assert again.doc.version == 1
    assert again.doc.review_only_reasons == ["fallback_model", "refusal"]
    assert [record.step for record in again.doc.served_by] == ["draft", "normalize"]


async def test_pipeline_writes_keep_the_newest_served_by_records(store: Store) -> None:
    stored = await make_article(store, S.DRAFTING)
    for index in range(6):
        await pipeline_write(
            store,
            stored.doc.id,
            now=at(index),
            served_by=[served(f"s{index}-{n}") for n in range(10)],
        )
    article = (await read_doc(store, Article, stored.doc.id)).doc
    assert len(article.served_by) == 50
    assert article.served_by[-1].step == "s5-9"
    assert article.served_by[0].step == "s1-0"


@pytest.mark.parametrize(
    ("status", "writer"),
    [
        (S.IN_REVIEW, "owner"),
        (S.PUBLISHING, "publish"),
        (S.UNPUBLISHING, "publish"),
        (S.APPROVED, "none"),
    ],
)
async def test_the_pipeline_cannot_write_outside_pipeline_states(
    store: Store, status: ArticleStatus, writer: str
) -> None:
    stored = await make_article(store, status)
    with pytest.raises(WriterConflict) as caught:
        await pipeline_write(store, stored.doc.id, now=at(1), markdown="Overwrite.\n")
    assert caught.value.writer == writer
    assert (await read_doc(store, Article, stored.doc.id)).etag == stored.etag


async def test_a_resumed_pipeline_step_stores_the_version_it_lost(store: Store) -> None:
    stored = await make_article(store, S.DRAFTING)
    first = await pipeline_write(store, stored.doc.id, now=at(1), markdown="Draft.\n")
    # A crash right after the article write lost the version document.
    await delete_doc(
        store, ArticleVersion, ArticleVersion.id_for(stored.doc.id, 1), partition_key=stored.doc.id
    )
    again = await pipeline_write(store, stored.doc.id, now=at(2), markdown="Draft.\n")
    assert again.etag == first.etag  # same content: nothing written
    assert (await read_version(store, stored.doc.id, 1)).doc.markdown == "Draft.\n"


# --------------------------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------------------------


async def test_links_write_only_the_fields_set(store: Store) -> None:
    stored = await make_article(store, S.RESEARCHING, interview_id="intv-1", pipeline_runs=1)
    linked = await set_links(
        store,
        stored.doc.id,
        ArticleLinks(brief_id="brief-1", research_run_id="run-1", interview_id=None),
        now=at(1),
    )
    article = linked.doc
    assert (article.brief_id, article.research_run_id, article.interview_id) == (
        "brief-1",
        "run-1",
        None,
    )
    assert article.pipeline_runs == 1
    assert article.version == stored.doc.version
    assert article.updated_at == to_iso(at(1))
    same = await set_links(store, stored.doc.id, ArticleLinks(brief_id="brief-1"), now=at(2))
    assert same.etag == linked.etag  # nothing changed, nothing written
    runs = await set_links(store, stored.doc.id, ArticleLinks(pipeline_runs=2), now=at(3))
    assert runs.doc.pipeline_runs == 2


async def test_owner_signals_decide_whether_clearance_is_required(store: Store) -> None:
    anonymized = sample_document(StoryEntry, confidentiality="anonymized")
    public = sample_document(StoryEntry, confidentiality="public")
    await create_doc(store, anonymized)
    await create_doc(store, public)
    stored = await make_article(store, S.DRAFTING)

    async def required(*refs: OwnerSignalRef) -> bool:
        links = ArticleLinks(owner_signals=list(refs))
        return (await set_links(store, stored.doc.id, links, now=at(1))).doc.clearance.required

    assert not await required(OwnerSignalRef(kind="story", id=public.id))
    assert await required(OwnerSignalRef(kind="story", id=anonymized.id))
    assert not await required(OwnerSignalRef(kind="pov", id="pov-1"))
    # A story that cannot be read counts as anonymized (fail closed).
    assert await required(OwnerSignalRef(kind="story", id="story-gone"))


async def test_archived_articles_take_no_links(store: Store) -> None:
    stored = await make_article(store, S.ARCHIVED)
    with pytest.raises(WriterConflict):
        await set_links(store, stored.doc.id, ArticleLinks(brief_id="brief-1"), now=at(1))


# --------------------------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------------------------


async def test_transitions_follow_the_machine_and_the_article_rules(store: Store) -> None:
    planned = await make_article(store)
    with pytest.raises(TransitionError):
        await transition(store, planned.doc.id, S.PUBLISHED, actor="system", now=at(1))
    live = await make_article(store, S.IN_REVIEW, publish=PublishState(live=True, slug="x"))
    with pytest.raises(TransitionRefused) as refused:
        await transition(store, live.doc.id, S.ARCHIVED, actor="owner", now=at(1))
    assert refused.value.code == "live_article"
    evaluation = await make_article(store, S.IN_REVIEW, purpose="eval")
    with pytest.raises(TransitionRefused) as never:
        await transition(store, evaluation.doc.id, S.APPROVED, actor="owner", now=at(1))
    assert never.value.code == "eval_article"


async def test_a_transition_stamps_the_status_and_sets_the_error_code(store: Store) -> None:
    stored = await make_article(store, S.RESEARCHING, last_error="old_failure")
    moved = await transition(
        store,
        stored.doc.id,
        S.NEEDS_ATTENTION,
        actor="pipeline",
        now=at(5),
        reason="research_failed",
    )
    article = moved.doc
    assert article.status is S.NEEDS_ATTENTION
    assert article.status_changed_at == to_iso(at(5))
    assert article.updated_by == "pipeline"
    assert article.last_error == "research_failed"
    back = await transition(store, article.id, S.IN_REVIEW, actor="owner", now=at(6))
    assert back.doc.last_error is None
    noted = await transition(
        store, article.id, S.ARCHIVED, actor="owner", now=at(7), reason="Not worth finishing."
    )
    assert noted.doc.last_error is None


async def test_if_match_makes_a_transition_refuse_a_stale_screen(store: Store) -> None:
    stored = await make_article(store, S.IN_REVIEW)
    await set_links(store, stored.doc.id, ArticleLinks(brief_id="brief-1"), now=at(1))
    with pytest.raises(StaleArticle) as caught:
        await transition(
            store, stored.doc.id, S.ARCHIVED, actor="owner", now=at(2), if_match=stored.etag
        )
    assert caught.value.current.doc.brief_id == "brief-1"
    current = caught.value.current
    moved = await transition(
        store, stored.doc.id, S.ARCHIVED, actor="owner", now=at(2), if_match=current.etag
    )
    assert moved.doc.status is S.ARCHIVED


async def test_update_changes_fields_in_the_same_write(store: Store) -> None:
    stored = await make_article(store, S.APPROVED, version=3)

    def publishing(article: Article) -> Article:
        publish = article.publish.model_copy(update={"last_operation": "publish", "job_id": "j"})
        return article.model_copy(update={"publish": publish})

    moved = await transition(
        store, stored.doc.id, S.PUBLISHING, actor="system", now=at(1), update=publishing
    )
    assert moved.doc.publish.last_operation == "publish"
    assert moved.doc.version == 3

    def sneaky(article: Article) -> Article:
        return article.model_copy(update={"status": S.PUBLISHED})

    with pytest.raises(ValueError, match="must not change the status"):
        await transition(
            store, stored.doc.id, S.PUBLISH_FAILED, actor="system", now=at(2), update=sneaky
        )


async def test_only_the_states_writer_may_change_content_in_a_transition(store: Store) -> None:
    review = await make_article(store, S.IN_REVIEW, meta=ArticleMeta(slug="old-slug"))

    def rename(article: Article) -> Article:
        return article.model_copy(update={"meta": article.meta.model_copy(update={"slug": "new"})})

    approved = await transition(
        store, review.doc.id, S.APPROVED, actor="owner", now=at(1), update=rename
    )
    assert approved.doc.meta.slug == "new"
    assert approved.doc.version == review.doc.version + 1
    assert approved.doc.content_sha256 == approved.doc.compute_sha256()
    version = (await read_version(store, review.doc.id, approved.doc.version)).doc
    assert (version.source, version.author, version.status_at_save) == (
        "autosave",
        "owner",
        S.APPROVED,
    )

    def rename_again(article: Article) -> Article:
        meta = article.meta.model_copy(update={"slug": "newer"})
        return article.model_copy(update={"meta": meta})

    with pytest.raises(WriterConflict):
        await transition(
            store, review.doc.id, S.PUBLISHING, actor="system", now=at(2), update=rename_again
        )
    assert (await read_doc(store, Article, review.doc.id)).doc.status is S.APPROVED


async def test_a_transition_stores_coalesced_autosave_content(store: Store) -> None:
    created = await create_article(store, now=T0)
    first = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(),
        markdown="One.\n",
        now=at(1),
    )
    second = await owner_save(
        store,
        created.doc.id,
        if_match=first.stored.etag,
        base_version=1,
        meta=ArticleMeta(),
        markdown="Two.\n",
        now=at(2),
    )
    latest = await latest_version(store, created.doc.id)
    assert latest is not None
    assert latest.doc.version == 1  # the second save was coalesced
    await transition(
        store,
        created.doc.id,
        S.DRAFTING,
        actor="owner",
        now=at(3),
        if_match=second.stored.etag,
        reason="Revise from here.",
    )
    stored_version = (await read_version(store, created.doc.id, 2)).doc
    assert stored_version.markdown == "Two.\n"
    assert (stored_version.source, stored_version.author, stored_version.status_at_save) == (
        "autosave",
        "owner",
        S.PLANNED,
    )
    assert stored_version.note == "Revise from here."


# --------------------------------------------------------------------------------------------
# Restore and clearance
# --------------------------------------------------------------------------------------------


async def test_restore_makes_an_old_version_current_as_a_new_version(store: Store) -> None:
    stored = await make_article(store, S.DRAFTING)
    await pipeline_write(store, stored.doc.id, now=at(1), markdown="Old *text*.\n")
    await pipeline_write(store, stored.doc.id, now=at(2), markdown="New text.\n")
    review = await transition(store, stored.doc.id, S.IN_REVIEW, actor="pipeline", now=at(3))
    restored = await restore_version(store, stored.doc.id, 1, if_match=review.etag, now=at(4))
    article = restored.doc
    assert article.version == 3
    assert article.markdown == "Old *text*.\n"
    assert article.updated_by == "owner"
    version = (await read_version(store, article.id, 3)).doc
    assert (version.source, version.author, version.note) == (
        "restore",
        "owner",
        "Restored from version 1.",
    )
    same = await restore_version(store, article.id, 3, if_match=restored.etag, now=at(5))
    assert same.etag == restored.etag
    with pytest.raises(StaleArticle):
        await restore_version(store, article.id, 2, if_match=review.etag, now=at(6))
    with pytest.raises(NotFound):
        await restore_version(store, article.id, 9, if_match=restored.etag, now=at(6))
    with pytest.raises(NotFound):
        await restore_version(store, article.id, 0, if_match=restored.etag, now=at(6))


async def test_restore_is_for_owner_states_only(store: Store) -> None:
    stored = await make_article(store, S.DRAFTING)
    written = await pipeline_write(store, stored.doc.id, now=at(1), markdown="Text.\n")
    with pytest.raises(WriterConflict):
        await restore_version(store, stored.doc.id, 1, if_match=written.etag, now=at(2))


async def test_clearance_attests_one_exact_content(store: Store) -> None:
    created = await create_article(store, now=T0, meta=ArticleMeta(format="case-study"))
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=ArticleMeta(format="case-study"),
        markdown="Anonymized case study.\n",
        now=at(1),
    )
    attested = await attest_clearance(
        store, created.doc.id, if_match=saved.stored.etag, statement="Cleared.", now=at(2)
    )
    clearance = attested.doc.clearance
    assert clearance.required
    assert clearance.statement == "Cleared."
    assert clearance.attested_at == to_iso(at(2))
    assert clearance.valid_for(attested.doc.compute_sha256())
    assert attested.doc.version == saved.stored.doc.version
    edited = await owner_save(
        store,
        created.doc.id,
        if_match=attested.etag,
        base_version=attested.doc.version,
        meta=ArticleMeta(format="case-study"),
        markdown="Edited after the attestation.\n",
        now=at(3),
    )
    assert not edited.stored.doc.clearance.valid_for(edited.stored.doc.compute_sha256())
    with pytest.raises(StaleArticle):
        await attest_clearance(
            store, created.doc.id, if_match=attested.etag, statement="Cleared.", now=at(4)
        )
    approved = await make_article(store, S.APPROVED)
    with pytest.raises(WriterConflict):
        await attest_clearance(
            store, approved.doc.id, if_match=approved.etag, statement="Cleared.", now=at(4)
        )


# --------------------------------------------------------------------------------------------
# Ideas
# --------------------------------------------------------------------------------------------


async def test_an_idea_becomes_used_once(store: Store) -> None:
    idea = sample_document(Idea, status=IdeaStatus.ACCEPTED)
    await create_doc(store, idea)
    await check_idea_usable(store, idea.id)
    used = await use_idea(store, idea.id, article_id="art-1", now=at(1))
    assert used.doc.status is IdeaStatus.USED
    assert used.doc.article_id == "art-1"
    assert used.doc.status_changed_at == to_iso(at(1))
    again = await use_idea(store, idea.id, article_id="art-1", now=at(2))
    assert again.etag == used.etag
    with pytest.raises(TransitionError):
        await check_idea_usable(store, idea.id)
    with pytest.raises(NotFound):
        await check_idea_usable(store, "idea-missing")
