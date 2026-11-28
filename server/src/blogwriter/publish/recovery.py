"""Exits for an article whose publish job ended without moving it (contract 1.7: "Stuck states
have exits"; a failed or quarantined job never strands its document).

The publish job moves its article out of ``publishing``/``unpublishing`` itself, on success and
on every failure it sees (``jobs.guarded``). Some endings run none of that code: a System cancel
of a running job (its step is cancelled), a quarantine (the job crashed the process on three
claims, or kept losing its lease) and a crash between a route's article write and its enqueue.
The article then names a job that is missing or no longer active, and no owner action applies
to ``publishing``/``unpublishing``.

:func:`release_stranded` moves such an article to ``publish_failed`` (an existing edge) with
``lastError`` (``job_ended``, ``cancelled``, ``quarantined``), so publish-now or unpublish
retries it. It never touches an article whose named job is still active (a live run owns it).
Callers: publish-now and unpublish (first, quietly: the owner is acting), the System cancel of a
publish job (:func:`after_cancel`, which waits briefly for the runner to stop the step) and the
quarantine hook (:func:`after_quarantine`), both with the ``publish_failed`` alert and a push.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from pydantic import JsonValue, ValidationError

from blogwriter.articles import transition
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import NotFound, StoreError
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    Stored,
    read_doc,
    read_singleton,
)
from blogwriter.domain.jobtypes import PUBLISH_ARTICLE, PUBLISH_UNPUBLISH, ArticleJobInput
from blogwriter.domain.ports import JobQueue, Services
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.states import ArticleStatus, TransitionError, TransitionRefused
from blogwriter.notify import emit_alert
from blogwriter.publish.screen import cleared_title
from blogwriter.publish.state import push_notice, studio_path

__all__ = [
    "CANCEL_WAIT_SECONDS",
    "PUBLISH_JOB_TYPES",
    "STRANDABLE",
    "after_cancel",
    "after_quarantine",
    "named_job_ended",
    "publish_in_progress",
    "release_stranded",
]

S = ArticleStatus
STRANDABLE: Final = frozenset({S.PUBLISHING, S.UNPUBLISHING})
PUBLISH_JOB_TYPES: Final = frozenset({PUBLISH_ARTICLE, PUBLISH_UNPUBLISH})
CANCEL_WAIT_SECONDS: Final = 5.0
"""How long the System cancel waits for the runner to stop a running publish step."""
_CANCEL_POLL_SECONDS: Final = 0.05

_logger: Final = logging.getLogger("blogwriter.publish.recovery")
register_log_fields("article_id", "job_id", "publish_code")


class _Moved(Exception):
    """The article no longer names the job that ended (raised inside the CAS mutation)."""


def publish_in_progress() -> ProblemError:
    return ProblemError(
        409,
        code="publish_in_progress",
        detail="A publish run is still working on this article. Wait for it or cancel it first.",
    )


async def named_job_ended(jobs: JobQueue, article: Article) -> bool:
    """Whether the job the article names is missing or finished (``None`` names nothing)."""
    job_id = article.publish.job_id
    if job_id is None:
        return True
    try:
        job = await jobs.get(job_id)
    except NotFound:
        return True
    return not job.is_active


async def _notify(services: Services, article: Article, reason: str) -> None:
    emit_alert("publish_failed", article.id)
    try:
        blocklist, _ = await read_singleton(
            services.store, Blocklist, now=to_iso(services.clock.now())
        )
        title = cleared_title(article.meta.title, blocklist)
    except (StoreError, InvalidStoredDocument):
        title = None
    unpublish = article.publish.last_operation == "unpublish"
    prefix = "Unpublish failed: " if unpublish else "Publish failed: "
    await push_notice(
        services.notifier,
        prefix,
        title,
        url=studio_path(article.id),
        tag=f"publish:{article.id}",
        article_id=article.id,
    )
    _logger.warning("publish_released", extra={"article_id": article.id, "publish_code": reason})


async def release_stranded(
    services: Services,
    article_id: str,
    *,
    reason: str,
    if_match: str | None = None,
    job_id: str | None = None,
    notify: bool = False,
) -> Stored[Article] | None:
    """``publishing``/``unpublishing`` -> ``publish_failed`` when the named job ended (module
    docs). ``job_id``: only when the article still names that job. ``if_match``: the owner's
    etag (a stale one raises ``StaleArticle``). Returns the moved article, or ``None`` when it is
    not stranded (another state, another job, or the job is still active)."""
    store = services.store
    article = (await read_doc(store, Article, article_id)).doc
    if article.status not in STRANDABLE:
        return None
    named = article.publish.job_id
    if job_id is not None and named != job_id:
        return None
    if not await named_job_ended(services.jobs, article):
        return None

    def update(doc: Article) -> Article:
        if doc.publish.job_id != named:
            raise _Moved()
        publish = doc.publish.model_copy(update={"last_error": reason})
        return doc.model_copy(update={"publish": publish})

    try:
        moved = await transition(
            store,
            article.id,
            S.PUBLISH_FAILED,
            actor="system",
            now=services.clock.now(),
            if_match=if_match,
            reason=reason,
            update=update,
        )
    except (_Moved, TransitionError, TransitionRefused):
        return None
    if notify:
        await _notify(services, moved.doc, reason)
    else:
        _logger.info("publish_released", extra={"article_id": article.id, "publish_code": reason})
    return moved


def _article_of(job_input: JsonValue) -> str | None:
    try:
        return ArticleJobInput.from_json(job_input).article_id
    except (ValueError, ValidationError):
        return None


async def after_quarantine(services: Services, job_id: str) -> Stored[Article] | None:
    """The quarantine hook for a publish job: release the article it stranded."""
    try:
        job = await services.jobs.get(job_id)
    except NotFound:
        return None
    if job.type not in PUBLISH_JOB_TYPES:
        return None
    article_id = _article_of(job.input)
    if article_id is None:
        return None
    try:
        return await release_stranded(
            services, article_id, reason="quarantined", job_id=job_id, notify=True
        )
    except NotFound:
        return None


async def after_cancel(
    services: Services, job_id: str, *, wait_seconds: float = CANCEL_WAIT_SECONDS
) -> Stored[Article] | None:
    """After a System cancel of a publish job: wait (bounded) until the runner has stopped it,
    then release the article it stranded. A run still going after ``wait_seconds`` is left to
    the owner's exits (publish-now, unpublish)."""
    try:
        job = await services.jobs.get(job_id)
    except NotFound:
        return None
    if job.type not in PUBLISH_JOB_TYPES:
        return None
    article_id = _article_of(job.input)
    if article_id is None:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while job.is_active and loop.time() < deadline:
        await asyncio.sleep(_CANCEL_POLL_SECONDS)
        job = await services.jobs.get(job_id)
    if job.is_active:
        return None
    try:
        return await release_stranded(
            services, article_id, reason="cancelled", job_id=job_id, notify=True
        )
    except NotFound:
        return None
