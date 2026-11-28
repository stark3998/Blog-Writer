"""The articles Python API (contract W-A7): create, owner autosave, pipeline writes, links, status
transitions, restore and the clearance attestation. Wave B (pipeline, publish, planner,
analytics) and the owner routes use these; nobody else writes an article's content.

**Who writes** (``domain.states.ARTICLE_WRITER``): the owner writes Markdown and metadata in
planned, in_review and needs_attention; the pipeline in researching, interviewing, drafting and
gating; the publish job only its publish fields in publishing and unpublishing (through
:func:`transition` with ``update``); nobody in approved, published, publish_failed, unpublished
and archived. Links (references, never content) may be set in any state but archived. A refused
write raises :class:`~blogwriter.domain.states.WriterConflict` (409 ``read_only_state``).

**Content conflicts are about content** (contract section 1.4). Background writers (gate
reports, links, embeddings) never change ``version``, ``meta`` or ``markdown``, so an autosave
whose etag is stale while the stored ``version`` still equals its ``base_version`` is applied on
top of the current document. Only a changed ``version`` (another tab, a restore) is a 412:
:class:`StaleArticle` carries the current document for the conflict dialog.

Every content write increments ``version`` and recomputes ``content_sha256``, the word counts and
``clearance.required``. Every write is validated before any I/O (``domain.docstore``);
normalization and word counts run in a worker thread before the compare-and-swap loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from pydantic import Field

from blogwriter.articles.content import (
    CASE_STUDY_FORMAT,
    check_markdown_size,
    clearance_required,
    content_sha256,
    meta_findings,
    prepare_body,
)
from blogwriter.articles.settings import get_settings
from blogwriter.articles.versions import (
    VersionAuthor,
    VersionSource,
    ensure_version,
    read_version,
    snapshot_if_due,
)
from blogwriter.core.logging import register_log_fields
from blogwriter.db import PreconditionFailed, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import (
    Article,
    ArticleMeta,
    Clearance,
    OwnerSignalRef,
    check_article_transition,
)
from blogwriter.domain.base import CODE_PATTERN, DocId, to_iso
from blogwriter.domain.common import MAX_SERVED_BY, Finding, MediaRef, ServedByRecord
from blogwriter.domain.compose import word_stats
from blogwriter.domain.docstore import (
    DEFAULT_CAS_ATTEMPTS,
    Stored,
    create_doc,
    read_doc,
    replace_doc,
    update_doc,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import (
    ARTICLE_WRITER,
    IDEA_MACHINE,
    ArticleStatus,
    IdeaStatus,
    WriterActor,
    WriterConflict,
    check_article_writer,
)

__all__ = [
    "Actor",
    "ArticleLinks",
    "SavedArticle",
    "StaleArticle",
    "attest_clearance",
    "check_idea_usable",
    "create_article",
    "owner_save",
    "pipeline_write",
    "restore_version",
    "set_links",
    "transition",
    "use_idea",
]

type Actor = Literal["owner", "pipeline", "system"]
"""Who changes an article: the owner (routes), the pipeline (its jobs) or the system (publish
job, planner, analytics)."""

_logger: Final = logging.getLogger("blogwriter.articles")
register_log_fields(
    "article_id", "version", "actor", "from_status", "to_status", "merged", "findings"
)

_MAX_REVIEW_ONLY_REASONS: Final = 20
_ERROR_STATES: Final = frozenset({ArticleStatus.NEEDS_ATTENTION, ArticleStatus.PUBLISH_FAILED})
_CODE_RE: Final = re.compile(CODE_PATTERN)
_SOURCE_BY_ACTOR: Final[Mapping[Actor, VersionSource]] = {
    "owner": "autosave",
    "pipeline": "pipeline",
    "system": "publish",
}


class StaleArticle(PreconditionFailed):
    """412: the article changed since the caller loaded it (a content change for autosaves, any
    change for If-Match actions). ``current`` is the stored document and its etag; routes answer
    ``domain.deps.etag_mismatch(current)`` so the conflict dialog can say who changed what."""

    def __init__(self, current: Stored[Article]) -> None:
        super().__init__(f"articles/{current.doc.id}: changed since it was loaded")
        self.current = current


@dataclass(frozen=True, slots=True)
class SavedArticle:
    """An owner save: the stored article and the normalizer's findings (with the metadata's
    frontmatter findings first)."""

    stored: Stored[Article]
    findings: list[Finding]


class ArticleLinks(ApiModel):
    """References an article points at (ids and counts, never content).

    Only the fields set (``model_fields_set``) are written: ``ArticleLinks(brief_id=x)`` leaves
    every other link alone, and an id set to ``None`` clears that link. ``owner_signals`` and
    ``pipeline_runs`` set to ``None`` are ignored.
    """

    brief_id: DocId | None = None
    research_run_id: DocId | None = None
    interview_id: DocId | None = None
    pipeline_job_id: DocId | None = None
    week_plan_id: DocId | None = None
    owner_signals: list[OwnerSignalRef] | None = Field(default=None, max_length=50)
    pipeline_runs: int | None = Field(default=None, ge=0)


def _keep() -> int:
    return get_settings().keep_versions


# --------------------------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------------------------


async def create_article(
    store: Store,
    *,
    now: datetime,
    meta: ArticleMeta | None = None,
    idea_id: str | None = None,
    week_plan_id: str | None = None,
    purpose: Literal["flagship", "eval"] = "flagship",
    article_id: str | None = None,
) -> Stored[Article]:
    """A new ``planned`` article (version 0, empty body).

    ``article_id`` is for retry-safe creators (``Article.id_for_eval`` in ``analytics.eval``): a
    second create raises :class:`~blogwriter.db.Conflict`. Without it a fresh id is minted.
    Ideas are not touched here (the route marks its idea used; planner uses signals).
    """
    stamp = to_iso(now)
    article_meta = meta.model_copy(deep=True) if meta is not None else ArticleMeta()
    article = Article(
        id=article_id if article_id is not None else Article.new_id(now=now),
        created_at=stamp,
        updated_at=stamp,
        status=ArticleStatus.PLANNED,
        status_changed_at=stamp,
        purpose=purpose,
        updated_by="system" if purpose == "eval" else "owner",
        meta=article_meta,
        content_sha256=content_sha256(article_meta, ""),
        idea_id=idea_id,
        week_plan_id=week_plan_id,
        clearance=Clearance(required=article_meta.format == CASE_STUDY_FORMAT),
    )
    stored = await create_doc(store, article)
    _logger.info("article_created", extra={"article_id": stored.doc.id})
    return stored


async def check_idea_usable(store: Store, idea_id: str) -> Stored[Idea]:
    """The idea a new article starts from; ``NotFound``, or ``TransitionError`` (409
    ``invalid_transition``) when it cannot become ``used`` (it already is)."""
    idea = await read_doc(store, Idea, idea_id)
    IDEA_MACHINE.transition(idea.doc.status, IdeaStatus.USED)
    return idea


async def use_idea(store: Store, idea_id: str, *, article_id: str, now: datetime) -> Stored[Idea]:
    """``idea -> used`` with ``articleId`` (``IDEA_MACHINE``); a no-op when this article already
    used it."""
    stamp = to_iso(now)

    def mutate(idea: Idea) -> Idea | None:
        if idea.status is IdeaStatus.USED and idea.article_id == article_id:
            return None
        status = IDEA_MACHINE.transition(idea.status, IdeaStatus.USED)
        return idea.model_copy(
            update={
                "status": status,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "article_id": article_id,
            }
        )

    return await update_doc(store, Idea, idea_id, mutate)


# --------------------------------------------------------------------------------------------
# Owner autosave
# --------------------------------------------------------------------------------------------


def _check_owner_save(current: Stored[Article], *, if_match: str, base_version: int) -> None:
    check_article_writer(current.doc.status, "owner")
    if current.etag != if_match and current.doc.version != base_version:
        raise StaleArticle(current)


async def owner_save(
    store: Store,
    article_id: str,
    *,
    if_match: str,
    base_version: int,
    meta: ArticleMeta,
    markdown: str,
    now: datetime,
    update: Callable[[Article], Article] | None = None,
) -> SavedArticle:
    """The owner's autosave (``PUT /api/articles/{id}``).

    Refused outside owner states (``WriterConflict``) and above 512 KiB (``MarkdownTooLarge``).
    The body is normalized in a thread (``blogwriter.publish.normalize``). A stale ``if_match``
    with an unchanged ``version`` is merged on top of the current document (CAS loop); a changed
    ``version`` raises :class:`StaleArticle`. Unchanged content writes nothing. A new
    ``autosave`` version is stored only when one is due (at most one per 10 minutes of typing),
    then old versions are pruned.

    ``update`` applies other owner fields in the same write (the illustrations' alt text next to
    the body that shows it); it must not change the meta or the body. With an ``update``, an
    unchanged body still writes the update (without a new version).
    """
    check_markdown_size(markdown)
    current = await read_doc(store, Article, article_id)
    _check_owner_save(current, if_match=if_match, base_version=base_version)
    prepared = await asyncio.to_thread(prepare_body, markdown)
    findings = [*meta_findings(meta), *prepared.findings]
    target_sha = content_sha256(meta, prepared.text)
    stamp = to_iso(now)
    for attempt in range(DEFAULT_CAS_ATTEMPTS):
        if attempt:
            current = await read_doc(store, Article, article_id)
            _check_owner_save(current, if_match=if_match, base_version=base_version)
        doc = current.doc
        unchanged = doc.compute_sha256() == target_sha
        if unchanged and update is None:
            return SavedArticle(current, findings)
        if unchanged:
            changed = doc.model_copy(deep=True, update={"updated_at": stamp, "updated_by": "owner"})
        else:
            required = await clearance_required(store, meta, doc.owner_signals)
            changed = doc.model_copy(
                deep=True,
                update={
                    "meta": meta.model_copy(deep=True),
                    "markdown": prepared.text,
                    "version": doc.version + 1,
                    "content_sha256": target_sha,
                    "word_count": prepared.word_count,
                    "reading_time_minutes": prepared.reading_time_minutes,
                    "clearance": doc.clearance.model_copy(update={"required": required}),
                    "updated_at": stamp,
                    "updated_by": "owner",
                },
            )
        if update is not None:
            before = changed.compute_sha256()
            changed = update(changed)
            if changed.compute_sha256() != before:
                raise ValueError("an owner save's update must not change the meta or the body")
        try:
            stored = await replace_doc(store, changed, if_match=current.etag)
        except PreconditionFailed:
            continue
        if unchanged:
            return SavedArticle(stored, findings)
        _logger.debug(
            "article_saved",
            extra={
                "article_id": article_id,
                "version": stored.doc.version,
                "merged": current.etag != if_match,
                "findings": len(findings),
            },
        )
        await snapshot_if_due(store, stored.doc, now=now, keep=_keep())
        return SavedArticle(stored, findings)
    raise StaleArticle(await read_doc(store, Article, article_id))


# --------------------------------------------------------------------------------------------
# Pipeline writes and links
# --------------------------------------------------------------------------------------------


async def pipeline_write(
    store: Store,
    article_id: str,
    *,
    now: datetime,
    markdown: str | None = None,
    meta: ArticleMeta | None = None,
    served_by: Sequence[ServedByRecord] = (),
    review_only_reasons: Sequence[str] = (),
    media: Sequence[MediaRef] | None = None,
    note: str | None = None,
) -> Stored[Article]:
    """A pipeline job's write (``draft``, ``normalize``, ``critique_revise``, ``assets``...).

    Only in pipeline states (``WriterConflict`` otherwise, also in publishing and unpublishing,
    where the publish job writes). The body is stored as given (the pipeline normalizes in its
    own step). ``served_by`` records are appended (the newest 50 kept); review-only reasons
    accumulate and set ``review_only`` (a pipeline that starts over may reset them through
    :func:`transition`'s ``update``); ``media`` replaces the draft assets. A change of meta or
    body increments ``version`` and stores an ``ArticleVersion`` (source ``pipeline``, with
    ``note``). Writing the same content again writes nothing, but stores a version the previous
    attempt lost in a crash (a resumed step).
    """
    stats: tuple[int, int] | None = None
    if markdown is not None:
        check_markdown_size(markdown)
        stats = await asyncio.to_thread(word_stats, markdown)
    stamp = to_iso(now)
    for _ in range(DEFAULT_CAS_ATTEMPTS):
        current = await read_doc(store, Article, article_id)
        doc = current.doc
        check_article_writer(doc.status, "pipeline")
        new_meta = meta if meta is not None else doc.meta
        new_markdown = markdown if markdown is not None else doc.markdown
        new_sha = content_sha256(new_meta, new_markdown)
        content_changed = new_sha != doc.compute_sha256()
        updates: dict[str, object] = {}
        if served_by:
            updates["served_by"] = [
                *doc.served_by,
                *(record.model_copy(deep=True) for record in served_by),
            ][-MAX_SERVED_BY:]
        if review_only_reasons:
            reasons = list(dict.fromkeys([*doc.review_only_reasons, *review_only_reasons]))
            updates["review_only"] = True
            updates["review_only_reasons"] = reasons[:_MAX_REVIEW_ONLY_REASONS]
        if media is not None:
            updates["media"] = [item.model_copy(deep=True) for item in media]
        if content_changed:
            words, minutes = (
                stats if stats is not None else (doc.word_count, doc.reading_time_minutes)
            )
            required = await clearance_required(store, new_meta, doc.owner_signals)
            updates.update(
                {
                    "meta": new_meta.model_copy(deep=True),
                    "markdown": new_markdown,
                    "version": doc.version + 1,
                    "content_sha256": new_sha,
                    "word_count": words,
                    "reading_time_minutes": minutes,
                    "clearance": doc.clearance.model_copy(update={"required": required}),
                }
            )
        if not updates:
            stored = current
            break
        updates.update({"updated_at": stamp, "updated_by": "pipeline"})
        try:
            stored = await replace_doc(
                store, doc.model_copy(deep=True, update=updates), if_match=current.etag
            )
        except PreconditionFailed:
            continue
        break
    else:
        raise PreconditionFailed(f"update articles/{article_id}: lost {DEFAULT_CAS_ATTEMPTS} CAS")
    if content_changed or stored.doc.updated_by == "pipeline":
        await ensure_version(
            store,
            stored.doc,
            source="pipeline",
            author="pipeline",
            status_at_save=stored.doc.status,
            now=now,
            keep=_keep(),
            note=note,
        )
    if content_changed:
        _logger.info(
            "article_pipeline_write",
            extra={"article_id": article_id, "version": stored.doc.version},
        )
    return stored


def _link_updates(doc: Article, links: ArticleLinks) -> dict[str, object]:
    chosen = links.model_fields_set
    ids: tuple[tuple[str, str | None, str | None], ...] = (
        ("brief_id", links.brief_id, doc.brief_id),
        ("research_run_id", links.research_run_id, doc.research_run_id),
        ("interview_id", links.interview_id, doc.interview_id),
        ("pipeline_job_id", links.pipeline_job_id, doc.pipeline_job_id),
        ("week_plan_id", links.week_plan_id, doc.week_plan_id),
    )
    updates: dict[str, object] = {
        name: new for name, new, old in ids if name in chosen and new != old
    }
    signals = links.owner_signals
    if "owner_signals" in chosen and signals is not None:
        new_dump = [ref.model_dump(mode="json") for ref in signals]
        if new_dump != [ref.model_dump(mode="json") for ref in doc.owner_signals]:
            updates["owner_signals"] = [ref.model_copy(deep=True) for ref in signals]
    runs = links.pipeline_runs
    if "pipeline_runs" in chosen and runs is not None and runs != doc.pipeline_runs:
        updates["pipeline_runs"] = runs
    return updates


async def set_links(
    store: Store, article_id: str, links: ArticleLinks, *, now: datetime
) -> Stored[Article]:
    """Write the links that are set on ``links`` (any writer, any state but archived, which is
    ``WriterConflict``). Never changes content or ``version``; unchanged links write nothing.
    New ``owner_signals`` recompute ``clearance.required``."""
    stamp = to_iso(now)
    for _ in range(DEFAULT_CAS_ATTEMPTS):
        current = await read_doc(store, Article, article_id)
        doc = current.doc
        if doc.status is ArticleStatus.ARCHIVED:
            raise WriterConflict(ArticleStatus.ARCHIVED, "pipeline")
        updates = _link_updates(doc, links)
        if not updates:
            return current
        if "owner_signals" in updates:
            required = await clearance_required(store, doc.meta, links.owner_signals or ())
            updates["clearance"] = doc.clearance.model_copy(update={"required": required})
        updates["updated_at"] = stamp
        try:
            return await replace_doc(
                store, doc.model_copy(deep=True, update=updates), if_match=current.etag
            )
        except PreconditionFailed:
            continue
    raise PreconditionFailed(f"update articles/{article_id}: lost {DEFAULT_CAS_ATTEMPTS} CAS")


# --------------------------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------------------------


def _version_origin(
    before: ArticleStatus, target: ArticleStatus, actor: Actor, *, content_changed: bool
) -> tuple[VersionSource, VersionAuthor, ArticleStatus]:
    """Source, author and ``statusAtSave`` of the version a transition stores."""
    if content_changed:
        return _SOURCE_BY_ACTOR[actor], actor, target
    writer = ARTICLE_WRITER[before]
    if writer == "owner":
        return "autosave", "owner", before
    if writer == "pipeline":
        return "pipeline", "pipeline", before
    return _SOURCE_BY_ACTOR[actor], actor, before


async def _settle_content(
    store: Store, before: Article, changed: Article, *, actor: Actor
) -> Article:
    """Derived fields after a transition's ``update``: unchanged content keeps them; changed
    content must come from the writer of the current state and gets a new version."""
    new_sha = changed.compute_sha256()
    if new_sha == before.compute_sha256():
        return changed.model_copy(
            update={
                "version": before.version,
                "content_sha256": new_sha,
                "word_count": before.word_count,
                "reading_time_minutes": before.reading_time_minutes,
            }
        )
    writer: WriterActor = actor if actor != "system" else "publish"
    check_article_writer(before.status, writer)
    check_markdown_size(changed.markdown)
    if changed.markdown != before.markdown:
        words, minutes = await asyncio.to_thread(word_stats, changed.markdown)
    else:
        words, minutes = before.word_count, before.reading_time_minutes
    required = await clearance_required(store, changed.meta, changed.owner_signals)
    return changed.model_copy(
        update={
            "version": before.version + 1,
            "content_sha256": new_sha,
            "word_count": words,
            "reading_time_minutes": minutes,
            "clearance": changed.clearance.model_copy(update={"required": required}),
        }
    )


async def transition(
    store: Store,
    article_id: str,
    to: ArticleStatus,
    *,
    actor: Actor,
    now: datetime,
    if_match: str | None = None,
    reason: str | None = None,
    update: Callable[[Article], Article] | None = None,
) -> Stored[Article]:
    """Move the article to ``to`` (``check_article_transition``: the machine plus the
    ``live_article`` and ``eval_article`` rules).

    With ``if_match`` (owner actions): one replace; a stale etag raises :class:`StaleArticle`.
    Without it (jobs): a CAS loop. Sets ``statusChangedAt`` and ``updatedBy`` and clears
    ``lastError``; ``reason`` is a short code or note: into an error state (needs_attention,
    publish_failed) a code-like reason becomes ``lastError``, and it is the note of the version
    this transition stores. ``update`` applies extra field changes in the same write (publish
    fields, ``lastError``, veto, calibration) and may not change the status; it may change meta or
    Markdown only when ``actor`` writes in the current state (``WriterConflict`` otherwise), and
    then the version is incremented. The transition stores an ``ArticleVersion`` when the
    current content was not stored yet (coalesced autosaves), so the approved, gated and
    published contents always exist as versions.
    """
    target = ArticleStatus(to)
    stamp = to_iso(now)
    last_error = (
        reason
        if reason is not None and target in _ERROR_STATES and _CODE_RE.fullmatch(reason)
        else None
    )
    attempts = 1 if if_match is not None else DEFAULT_CAS_ATTEMPTS
    for _ in range(attempts):
        current = await read_doc(store, Article, article_id)
        if if_match is not None and current.etag != if_match:
            raise StaleArticle(current)
        before = current.doc
        check_article_transition(before, target)
        changed = before.model_copy(
            deep=True,
            update={
                "status": target,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "updated_by": actor,
                "last_error": last_error,
            },
        )
        if update is not None:
            changed = update(changed)
            if changed.status is not target:
                raise ValueError("a transition's update must not change the status")
        changed = await _settle_content(store, before, changed, actor=actor)
        try:
            stored = await replace_doc(store, changed, if_match=current.etag)
        except PreconditionFailed:
            if if_match is not None:
                raise StaleArticle(await read_doc(store, Article, article_id)) from None
            continue
        source, author, status_at_save = _version_origin(
            before.status, target, actor, content_changed=stored.doc.version != before.version
        )
        await ensure_version(
            store,
            stored.doc,
            source=source,
            author=author,
            status_at_save=status_at_save,
            now=now,
            keep=_keep(),
            note=reason,
        )
        _logger.info(
            "article_transition",
            extra={
                "article_id": article_id,
                "from_status": before.status.value,
                "to_status": target.value,
                "actor": actor,
            },
        )
        return stored
    raise PreconditionFailed(f"update articles/{article_id}: lost {DEFAULT_CAS_ATTEMPTS} CAS")


# --------------------------------------------------------------------------------------------
# Restore and clearance (owner actions with If-Match)
# --------------------------------------------------------------------------------------------


async def _read_for_owner_action(store: Store, article_id: str, if_match: str) -> Stored[Article]:
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    check_article_writer(current.doc.status, "owner")
    return current


async def _replace_or_stale(store: Store, changed: Article, if_match: str) -> Stored[Article]:
    try:
        return await replace_doc(store, changed, if_match=if_match)
    except PreconditionFailed:
        raise StaleArticle(await read_doc(store, Article, changed.id)) from None


async def restore_version(
    store: Store, article_id: str, version: int, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Make a stored version the current content again (owner states only), as a new version
    with source ``restore``. The body goes through the normalizer like an owner save. Restoring
    the content the article already has writes nothing."""
    current = await _read_for_owner_action(store, article_id, if_match)
    source = await read_version(store, article_id, version)
    prepared = await asyncio.to_thread(prepare_body, source.doc.markdown)
    meta = source.doc.meta
    target_sha = content_sha256(meta, prepared.text)
    doc = current.doc
    if target_sha == doc.compute_sha256():
        return current
    stamp = to_iso(now)
    required = await clearance_required(store, meta, doc.owner_signals)
    changed = doc.model_copy(
        deep=True,
        update={
            "meta": meta.model_copy(deep=True),
            "markdown": prepared.text,
            "version": doc.version + 1,
            "content_sha256": target_sha,
            "word_count": prepared.word_count,
            "reading_time_minutes": prepared.reading_time_minutes,
            "clearance": doc.clearance.model_copy(update={"required": required}),
            "updated_at": stamp,
            "updated_by": "owner",
        },
    )
    stored = await _replace_or_stale(store, changed, if_match)
    await ensure_version(
        store,
        stored.doc,
        source="restore",
        author="owner",
        status_at_save=stored.doc.status,
        now=now,
        keep=_keep(),
        note=f"Restored from version {version}.",
    )
    _logger.info(
        "article_restored", extra={"article_id": article_id, "version": stored.doc.version}
    )
    return stored


async def attest_clearance(
    store: Store, article_id: str, *, if_match: str, statement: str, now: datetime
) -> Stored[Article]:
    """The owner attests the current content (``clearance.contentSha256``), owner states only.
    Any later content change makes the attestation stale (``Clearance.valid_for``)."""
    current = await _read_for_owner_action(store, article_id, if_match)
    doc = current.doc
    stamp = to_iso(now)
    sha = doc.compute_sha256()
    clearance = doc.clearance.model_copy(
        update={"content_sha256": sha, "attested_at": stamp, "statement": statement}
    )
    changed = doc.model_copy(
        deep=True,
        update={
            "clearance": clearance,
            "content_sha256": sha,
            "updated_at": stamp,
            "updated_by": "owner",
        },
    )
    stored = await _replace_or_stale(store, changed, if_match)
    _logger.info("article_clearance_attested", extra={"article_id": article_id})
    return stored
