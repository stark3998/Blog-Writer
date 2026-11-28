"""HTTP routes of the articles package (contract W-A7; every route is owner-only and every
non-GET passes CSRF, both enforced by core).

| Method | Path (``/api/articles``...) | Notes |
|---|---|---|
| GET | ``?status=&pillar=&purpose=&ideaId=&weekPlanId=&limit=&cursor=`` | summaries, newest first |
| POST | | ``ArticleIn``; 201; the idea becomes ``used`` |
| GET, PUT | ``/{id}`` | PUT is the autosave (If-Match plus ``baseVersion``) |
| POST | ``/{id}/transition`` | owner edges only (:data:`OWNER_TRANSITIONS`) |
| GET | ``/{id}/versions``, ``/{id}/versions/{version}`` | |
| POST | ``/{id}/versions/{version}/restore`` | If-Match |
| GET | ``/{id}/preview?version=``, ``/{id}/export.md`` | read-only |
| POST | ``/{id}/clearance`` | If-Match |
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import HTMLResponse

from blogwriter.articles.content import MarkdownTooLarge
from blogwriter.articles.models import (
    ArticleIn,
    ArticleRow,
    ArticleSaved,
    ArticleSaveIn,
    ArticleSummary,
    ClearanceIn,
    TransitionIn,
    VersionSummary,
)
from blogwriter.articles.preview import build_preview
from blogwriter.articles.service import (
    StaleArticle,
    attest_clearance,
    check_idea_usable,
    create_article,
    owner_save,
    restore_version,
    transition,
    use_idea,
)
from blogwriter.articles.versions import list_versions, read_version
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, OrderBy, Store, StoreError
from blogwriter.domain.api import (
    PREVIEW_HEADERS,
    InvalidCursor,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.articles import Article, ArticleVersion
from blogwriter.domain.base import is_timestamp, to_iso
from blogwriter.domain.compose import compose_document
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    Projected,
    find_docs,
    read_doc,
    read_singleton,
)
from blogwriter.domain.jobtypes import publish_job_id
from blogwriter.domain.ports import Services, ServiceUnavailable
from blogwriter.domain.settings import EmployerPolicy
from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ArticleStatus,
    TransitionError,
    WriterConflict,
)
from blogwriter.publish.frontmatter import FrontmatterError

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "OWNER_TRANSITIONS",
    "article_problems",
    "get_router",
    "read_only_problem",
]

DEFAULT_LIST_LIMIT: Final = 20
"""List pages of large kinds default to 20 items."""

_S = ArticleStatus
OWNER_TRANSITIONS: Final[Mapping[ArticleStatus, frozenset[ArticleStatus]]] = MappingProxyType(
    {
        _S.PLANNED: frozenset({_S.ARCHIVED}),
        _S.IN_REVIEW: frozenset({_S.ARCHIVED}),
        _S.NEEDS_ATTENTION: frozenset({_S.IN_REVIEW, _S.ARCHIVED}),
        _S.ARCHIVED: frozenset({_S.PLANNED}),
        _S.APPROVED: frozenset({_S.IN_REVIEW}),  # pull back (the scheduled publish is cancelled)
        _S.PUBLISHED: frozenset({_S.IN_REVIEW}),  # start a correction; the live page stays up
        # Give up on a failed publish (or unpublish) and edit again; a retry of the failed run
        # still queued is cancelled, and a live page stays up (like a correction).
        _S.PUBLISH_FAILED: frozenset({_S.IN_REVIEW}),
        _S.UNPUBLISHED: frozenset({_S.IN_REVIEW, _S.ARCHIVED}),
    }
)
"""The edges ``POST /api/articles/{id}/transition`` makes. Every other edge belongs to the
pipeline, gates, publish or planner routes and jobs (409 ``transition_not_allowed_here``)."""

_logger: Final = logging.getLogger("blogwriter.articles.routes")
register_log_fields("article_id")


def read_only_problem(exc: WriterConflict) -> ProblemError:
    """409 ``read_only_state`` with the article status as ``state`` and the state's ``writer``.

    ``domain.deps.problem_from`` names the status extension ``status``, which is the problem's
    reserved HTTP status member: rendering it fails with a 500. Until the domain is fixed (see
    the articles report), articles answers with ``state`` instead.
    """
    return ProblemError(
        409,
        code=WriterConflict.code,
        detail=f"Read-only while {exc.status.value}: the {exc.writer} writes now.",
        extensions={"state": exc.status.value, "writer": exc.writer},
    )


@contextmanager
def article_problems() -> Generator[None]:
    """``domain.deps.problems()`` plus the articles errors: :class:`StaleArticle` (412 with
    ``current``), :class:`MarkdownTooLarge` (413), ``WriterConflict`` (409 ``read_only_state``,
    :func:`read_only_problem`) and ``FrontmatterError`` (409 ``meta_invalid``: set metadata
    breaks the frontmatter rules)."""
    with problems():
        try:
            yield
        except StaleArticle as exc:
            raise etag_mismatch(exc.current) from None
        except WriterConflict as exc:
            raise read_only_problem(exc) from None
        except MarkdownTooLarge:
            raise ProblemError(
                413,
                code=MarkdownTooLarge.code,
                detail="The article is larger than 512 KiB. Split it or shorten it.",
            ) from None
        except FrontmatterError as exc:
            fields: list[JsonValue] = []
            fields.extend(sorted({issue.path for issue in exc.issues}))
            raise ProblemError(
                409,
                code="meta_invalid",
                detail="Some article details break the frontmatter rules. Fix them first.",
                extensions={"fields": fields},
            ) from None


def _updated_cursor(cursor: str) -> str:
    value = decode_cursor(cursor)
    if not is_timestamp(value):
        raise InvalidCursor()
    return value


def _version_cursor(cursor: str) -> int:
    value = decode_cursor(cursor)
    if not value.isascii() or not value.isdigit() or len(value) > 9 or int(value) < 1:
        raise InvalidCursor()
    return int(value)


def _not_allowed_here(
    source: ArticleStatus, target: ArticleStatus, allowed: frozenset[ArticleStatus]
) -> ProblemError:
    targets: list[JsonValue] = []
    targets.extend(sorted(status.value for status in allowed))
    return ProblemError(
        409,
        code="transition_not_allowed_here",
        detail="This status change is made by the pipeline, the gates, publishing or the plan.",
        extensions={"from": source.value, "to": target.value, "allowed": targets},
    )


async def _cancel_publish_job(services: Services, article: Article) -> None:
    """After a pull back (approved -> in_review) or giving up on a failed publish
    (publish_failed -> in_review): cancel the publish job the article names, best effort (a
    finished job is left as it is). The publish job re-checks the status anyway; cancelling
    keeps a later re-approval from reusing a stale delayed job (``enqueue_once(...,
    on_conflict="rerun")`` returns an active one) and a queued retry from running."""
    job_id = article.publish.job_id or publish_job_id(article.id, article.version)
    try:
        await services.jobs.cancel(job_id)
    except NotFound:
        return
    except (StoreError, ServiceUnavailable, TimeoutError):
        _logger.warning("article_publish_job_not_cancelled", extra={"article_id": article.id})


async def _summary(store: Store, row: Projected[ArticleRow]) -> Versioned[ArticleSummary]:
    """A list item from its row. A row that cannot judge its gate report (no stored content
    hash: written outside the articles API) is read whole once; if that read fails the row's
    own summary stands, with the report counted as stale (fail closed)."""
    if not row.doc.gates_judgeable:
        try:
            whole = await read_doc(store, Article, row.doc.id)
        except (NotFound, InvalidStoredDocument):
            pass
        else:
            return Versioned[ArticleSummary](item=ArticleSummary.of(whole.doc), etag=whole.etag)
    return Versioned[ArticleSummary](item=ArticleSummary.of(row.doc), etag=row.etag)


async def _use_idea(store: Store, idea_id: str, *, article_id: str, services: Services) -> None:
    try:
        await use_idea(store, idea_id, article_id=article_id, now=services.clock.now())
    except (StoreError, TransitionError):
        # The article exists and is the answer; the idea changed under us or the store
        # hiccupped. Nothing to undo: log and go on (the idea stays as it was).
        _logger.warning("article_idea_not_marked", extra={"article_id": article_id})


def get_router() -> APIRouter:
    """The articles routes (``/api/articles``...)."""
    router = APIRouter(prefix="/api/articles", tags=["articles"])

    @router.get("")
    async def list_articles(
        store: StoreDep,
        status: Annotated[list[ArticleStatus] | None, Query()] = None,
        pillar: Annotated[str | None, Query(max_length=40)] = None,
        purpose: Annotated[Literal["flagship", "eval", "all"], Query()] = "flagship",
        idea_id: Annotated[str | None, Query(alias="ideaId", max_length=255)] = None,
        week_plan_id: Annotated[str | None, Query(alias="weekPlanId", max_length=255)] = None,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Page[ArticleSummary]:
        size = clamp_limit(limit) if limit is not None else DEFAULT_LIST_LIMIT
        filters: list[Filter] = []
        if status:
            filters.append(Filter("status", "in", [item.value for item in status]))
        if pillar:
            filters.append(Filter("meta.pillar", "==", pillar))
        if purpose != "all":
            filters.append(Filter("purpose", "==", purpose))
        if idea_id:
            filters.append(Filter("ideaId", "==", idea_id))
        if week_plan_id:
            filters.append(Filter("weekPlanId", "==", week_plan_id))
        with article_problems():
            if cursor is not None:
                filters.append(Filter("updatedAt", "<", _updated_cursor(cursor)))
            found = await find_docs(
                store,
                Article,
                filters,
                order_by=OrderBy("updatedAt", descending=True),
                limit=size + 1,
                fields=ArticleRow,
            )
            page = found[:size]
            items = [await _summary(store, row) for row in page]
        more = len(found) > size and bool(page)
        next_cursor = encode_cursor(page[-1].doc.updated_at) if more else None
        return Page[ArticleSummary](items=items, next_cursor=next_cursor)

    @router.post("", status_code=201)
    async def create(
        body: ArticleIn, store: StoreDep, services: ServicesDep, response: Response
    ) -> Versioned[Article]:
        with article_problems():
            now = services.clock.now()
            if body.idea_id is not None:
                await check_idea_usable(store, body.idea_id)
            stored = await create_article(
                store,
                now=now,
                meta=body.meta.to_meta() if body.meta is not None else None,
                idea_id=body.idea_id,
                week_plan_id=body.week_plan_id,
            )
            if body.idea_id is not None:
                await _use_idea(store, body.idea_id, article_id=stored.doc.id, services=services)
        return versioned(stored, response)

    @router.get("/{article_id}")
    async def get_article(
        article_id: str, store: StoreDep, response: Response
    ) -> Versioned[Article]:
        with article_problems():
            stored = await read_doc(store, Article, article_id)
        return versioned(stored, response)

    @router.put("/{article_id}")
    async def save_article(
        article_id: str,
        body: ArticleSaveIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> ArticleSaved:
        with article_problems():
            saved = await owner_save(
                store,
                article_id,
                if_match=if_match,
                base_version=body.base_version,
                meta=body.meta.to_meta(),
                markdown=body.markdown,
                now=clock.now(),
            )
        response.headers["ETag"] = saved.stored.etag
        return ArticleSaved(item=saved.stored.doc, etag=saved.stored.etag, findings=saved.findings)

    @router.post("/{article_id}/transition")
    async def post_transition(
        article_id: str,
        body: TransitionIn,
        store: StoreDep,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        with article_problems():
            current = await read_doc(store, Article, article_id)
            if current.etag != if_match:
                raise StaleArticle(current)
            source = current.doc.status
            ARTICLE_MACHINE.transition(source, body.to)
            allowed = OWNER_TRANSITIONS.get(source, frozenset[ArticleStatus]())
            if body.to not in allowed:
                raise _not_allowed_here(source, body.to, allowed)
            stored = await transition(
                store,
                article_id,
                body.to,
                actor="owner",
                now=services.clock.now(),
                if_match=if_match,
                reason=body.reason,
            )
        if source in (ArticleStatus.APPROVED, ArticleStatus.PUBLISH_FAILED):
            await _cancel_publish_job(services, current.doc)
        return versioned(stored, response)

    @router.get("/{article_id}/versions")
    async def get_versions(
        article_id: str,
        store: StoreDep,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Page[VersionSummary]:
        size = clamp_limit(limit) if limit is not None else DEFAULT_LIST_LIMIT
        with article_problems():
            article = await read_doc(store, Article, article_id)
            before = _version_cursor(cursor) if cursor is not None else None
            found = await list_versions(store, article_id, limit=size + 1, before=before)
        pinned = article.doc.pinned_versions()
        page = found[:size]
        items = [
            Versioned[VersionSummary](
                item=VersionSummary.of(stored.doc, pinned=stored.doc.version in pinned),
                etag=stored.etag,
            )
            for stored in page
        ]
        more = len(found) > size and bool(page)
        next_cursor = encode_cursor(str(page[-1].doc.version)) if more else None
        return Page[VersionSummary](items=items, next_cursor=next_cursor)

    @router.get("/{article_id}/versions/{version}")
    async def get_version(
        article_id: str, version: int, store: StoreDep, response: Response
    ) -> Versioned[ArticleVersion]:
        with article_problems():
            stored = await read_version(store, article_id, version)
        return versioned(stored, response)

    @router.post("/{article_id}/versions/{version}/restore")
    async def restore(
        article_id: str,
        version: int,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        with article_problems():
            stored = await restore_version(
                store, article_id, version, if_match=if_match, now=clock.now()
            )
        return versioned(stored, response)

    @router.get("/{article_id}/preview", response_class=HTMLResponse)
    async def preview(
        article_id: str,
        store: StoreDep,
        services: ServicesDep,
        version: Annotated[int | None, Query()] = None,
    ) -> HTMLResponse:
        with article_problems():
            article = (await read_doc(store, Article, article_id)).doc
            meta, body = article.meta, article.markdown
            if version is not None:
                snapshot = (await read_version(store, article_id, version)).doc
                meta, body = snapshot.meta, snapshot.markdown
            policy, _ = await read_singleton(
                store, EmployerPolicy, now=to_iso(services.clock.now())
            )
            page = await build_preview(
                article_id=article.id,
                meta=meta,
                body=body,
                media=article.media,
                blobs=services.blobs,
                render=services.render,
                version=version,
                live=article.publish.live,
                disclaimer=policy.disclaimer_long,
            )
        return HTMLResponse(page, headers=dict(PREVIEW_HEADERS))

    @router.get(
        "/{article_id}/export.md",
        response_class=Response,
        responses={
            200: {
                "content": {"text/markdown": {"schema": {"type": "string"}}},
                "description": "The canonical document: frontmatter and body, as a file",
            }
        },
    )
    async def export_markdown(article_id: str, store: StoreDep) -> Response:
        with article_problems():
            article = (await read_doc(store, Article, article_id)).doc
            document = await asyncio.to_thread(compose_document, article.meta, article.markdown)
        filename = f"{article.meta.slug or article.id}.md"
        return Response(
            content=document.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/{article_id}/clearance")
    async def clearance(
        article_id: str,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
        body: ClearanceIn | None = None,
    ) -> Versioned[Article]:
        now = clock.now()
        with article_problems():
            statement = body.statement if body is not None else None
            if statement is None:
                policy, _ = await read_singleton(store, EmployerPolicy, now=to_iso(now))
                statement = policy.clearance_statement
            stored = await attest_clearance(
                store, article_id, if_match=if_match, statement=statement, now=now
            )
        return versioned(stored, response)

    return router
