"""Article versions: snapshots (``article_version``, partition ``/articleId``) and pruning.

Rules (contract W-A7, ``domain.articles``):

- A version document holds the meta and body of one article ``version``; its id is
  ``ArticleVersion.id_for(article_id, version)``, so a retried store is a ``Conflict``, never a
  duplicate. Every content write increments ``Article.version``, so a version number names
  exactly one content.
- The article is written first and its version document second. A crash in between loses at
  most that snapshot; the next pipeline write or status transition stores it
  (:func:`ensure_version`), so the pipeline's gated version always exists.
- Owner autosaves are coalesced (:func:`snapshot_if_due`): at most one ``autosave`` copy per
  ``AUTOSAVE_SNAPSHOT_EVERY`` of typing, and never on top of a pipeline, restore, publish or
  correction copy without storing a new one.
- After a new version is stored, all but the newest ``keep`` versions and the pinned ones
  (``Article.pinned_versions()``) are deleted.

Lists (:func:`list_versions`, :func:`latest_version`, the version numbers for pruning) read
projections (:class:`~blogwriter.articles.models.VersionRow`: no meta, no Markdown), never the
version bodies.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Final, Literal

from blogwriter.articles.models import VersionRow
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.articles import (
    Article,
    ArticleVersion,
    autosave_snapshot_due,
    versions_to_prune,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import (
    Projected,
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
)
from blogwriter.domain.states import ArticleStatus

__all__ = [
    "VersionAuthor",
    "VersionSource",
    "ensure_version",
    "latest_version",
    "list_versions",
    "new_version",
    "prune_versions",
    "read_version",
    "snapshot_if_due",
    "store_version",
]

type VersionSource = Literal["autosave", "pipeline", "restore", "publish", "correction"]
type VersionAuthor = Literal["owner", "pipeline", "system"]

_logger: Final = logging.getLogger("blogwriter.articles.versions")
register_log_fields("article_id", "version", "pruned")

_MAX_NOTE: Final = 300


async def read_version(store: Store, article_id: str, version: int) -> Stored[ArticleVersion]:
    """One stored version; :class:`~blogwriter.db.NotFound` when missing or impossible."""
    if version < 1:
        raise NotFound("read article-versions: versions start at 1")
    return await read_doc(
        store, ArticleVersion, ArticleVersion.id_for(article_id, version), partition_key=article_id
    )


async def latest_version(store: Store, article_id: str) -> Projected[VersionRow] | None:
    """The stored version with the highest number, if any (its row: no meta or Markdown)."""
    found = await find_docs(
        store,
        ArticleVersion,
        [Filter("articleId", "==", article_id)],
        order_by=OrderBy("version", descending=True),
        limit=1,
        partition_key=article_id,
        fields=VersionRow,
    )
    return found[0] if found else None


async def list_versions(
    store: Store, article_id: str, *, limit: int, before: int | None = None
) -> list[Projected[VersionRow]]:
    """Stored versions, newest first (rows: no meta or Markdown); ``before`` pages below a
    version number."""
    filters = [Filter("articleId", "==", article_id)]
    if before is not None:
        filters.append(Filter("version", "<", before))
    return await find_docs(
        store,
        ArticleVersion,
        filters,
        order_by=OrderBy("version", descending=True),
        limit=limit,
        partition_key=article_id,
        fields=VersionRow,
    )


def new_version(
    article: Article,
    *,
    source: VersionSource,
    author: VersionAuthor,
    status_at_save: ArticleStatus,
    now: datetime,
    note: str | None = None,
) -> ArticleVersion:
    """The version document of ``article``'s current content (``article.version`` >= 1)."""
    stamp = to_iso(now)
    return ArticleVersion(
        id=ArticleVersion.id_for(article.id, article.version),
        article_id=article.id,
        version=article.version,
        meta=article.meta.model_copy(deep=True),
        markdown=article.markdown,
        content_sha256=article.compute_sha256(),
        source=source,
        author=author,
        status_at_save=status_at_save,
        note=note[:_MAX_NOTE] if note else None,
        created_at=stamp,
        updated_at=stamp,
    )


async def store_version(store: Store, version: ArticleVersion) -> bool:
    """Create the version document; ``False`` when it already exists (a retried store)."""
    try:
        await create_doc(store, version)
    except Conflict:
        return False
    _logger.debug(
        "article_version_stored",
        extra={"article_id": version.article_id, "version": version.version},
    )
    return True


async def prune_versions(store: Store, article_id: str, *, keep: int) -> list[int]:
    """Delete all but the newest ``keep`` stored versions and the pinned ones.

    The article is read again first, so pins set by another writer a moment ago (a gate report,
    a publish) count. Returns the deleted version numbers, oldest first.
    """
    try:
        article = await read_doc(store, Article, article_id)
    except NotFound:
        return []
    stored = await find_docs(
        store,
        ArticleVersion,
        [Filter("articleId", "==", article_id)],
        partition_key=article_id,
        fields=VersionRow,
    )
    doomed = versions_to_prune(
        [item.doc.version for item in stored], keep=keep, pinned=article.doc.pinned_versions()
    )
    for number in doomed:
        try:
            await delete_doc(
                store,
                ArticleVersion,
                ArticleVersion.id_for(article_id, number),
                partition_key=article_id,
            )
        except NotFound:
            continue
    if doomed:
        _logger.info(
            "article_versions_pruned", extra={"article_id": article_id, "pruned": len(doomed)}
        )
    return doomed


async def _store_and_prune(store: Store, version: ArticleVersion, *, keep: int) -> bool:
    created = await store_version(store, version)
    if created:
        await prune_versions(store, version.article_id, keep=keep)
    return created


async def ensure_version(
    store: Store,
    article: Article,
    *,
    source: VersionSource,
    author: VersionAuthor,
    status_at_save: ArticleStatus,
    now: datetime,
    keep: int,
    note: str | None = None,
) -> bool:
    """Store the version of ``article``'s current content unless it is stored already (pipeline
    writes, transitions, restores). Returns whether a version document was created."""
    if article.version < 1:
        return False
    try:
        await read_version(store, article.id, article.version)
    except NotFound:
        version = new_version(
            article,
            source=source,
            author=author,
            status_at_save=status_at_save,
            now=now,
            note=note,
        )
        return await _store_and_prune(store, version, keep=keep)
    return False


async def snapshot_if_due(store: Store, article: Article, *, now: datetime, keep: int) -> bool:
    """After an owner autosave: store an ``autosave`` version when one is due
    (``domain.articles.autosave_snapshot_due``). Returns whether one was created."""
    if article.version < 1:
        return False
    latest = await latest_version(store, article.id)
    if latest is not None and latest.doc.version >= article.version:
        return False
    due = autosave_snapshot_due(
        latest.doc.source if latest is not None else None,
        latest.doc.created_at if latest is not None else None,
        now=now,
    )
    if not due:
        return False
    version = new_version(
        article, source="autosave", author="owner", status_at_save=article.status, now=now
    )
    return await _store_and_prune(store, version, keep=keep)
