"""HTTP routes of the signals package: sources (feeds and scouting queries) and the ideas inbox.

Every route is owner-only and every non-GET passes CSRF (core). Single documents are
``Versioned[T]`` with the ``ETag`` header; lists are ``Page[T]``. Mutations of an existing
document require ``If-Match`` (428 without it, 412 with a stale one, with ``current``).

Blocklist: idea titles, notes, angles and URLs, and scouting queries, are owner-editable text
that later reaches a model or a search, so saving them runs ``check_texts`` (422
``confidential_text``, 409 ``blocklist_not_configured``). Feed discovery fetches only the
owner-typed site, through the egress helper.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from blogwriter.core.egress import EgressError
from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, OrderBy, Store
from blogwriter.domain.api import (
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.base import Document, check_https_url, parse_iso, to_iso
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import (
    ClockDep,
    EgressDep,
    IfMatch,
    JobsDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
)
from blogwriter.domain.hashing import url_sha256
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import SIGNALS_CRAWL, slot_stamp
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.signals import Idea, Source, SourceType
from blogwriter.domain.states import (
    IDEA_MACHINE,
    SOURCE_MACHINE,
    IdeaStatus,
    PostStatus,
    SourceStatus,
)
from blogwriter.signals.feeds import (
    FeedParseError,
    discover_feed_links,
    looks_like_feed,
    parse_feed_document,
)
from blogwriter.signals.ideas import known_formats, known_pillars, quick_take_hours
from blogwriter.signals.jobs import SignalsJobInput
from blogwriter.signals.models import (
    DiscoveredFeedOut,
    DiscoverIn,
    DiscoverOut,
    IdeaEditIn,
    IdeaIn,
    IdeaLane,
    IdeaTransitionIn,
    RepurposeIn,
    SourceIn,
)
from blogwriter.signals.repurpose import propose_repurpose

__all__ = ["DEFAULT_IDEA_STATUSES", "get_router"]

DEFAULT_IDEA_STATUSES: Final = (IdeaStatus.NEW, IdeaStatus.ACCEPTED)
_DISCOVER_ACCEPT: Final = (
    "text/html, application/xhtml+xml, application/rss+xml, application/atom+xml, "
    "application/feed+json;q=0.9, */*;q=0.5"
)
_IDEA_TEXT_FIELDS: Final = ("title", "note", "angle")


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _page[D: Document](docs: list[Stored[D]], size: int, key: Callable[[D], str]) -> Page[D]:
    items = [versioned(stored) for stored in docs[:size]]
    next_cursor = encode_cursor(key(docs[size - 1].doc)) if len(docs) > size else None
    return Page[D](items=items, next_cursor=next_cursor)


async def _blocklist(store: Store, now: datetime) -> Blocklist:
    blocklist, _ = await read_singleton(store, Blocklist, now=to_iso(now))
    return blocklist


async def _check_pillar(store: Store, pillar: str | None, now: datetime) -> None:
    if pillar is None:
        return
    settings = await known_pillars(store, now=now)
    if settings.get(pillar) is None:
        raise ProblemError(422, code="unknown_pillar", detail="Pick one of your pillars.")


async def _refuse_duplicate(store: Store, body: SourceIn, *, exclude: str | None) -> None:
    others = await find_docs(store, Source, [Filter("type", "==", body.type)], strict=True)
    for stored in others:
        other = stored.doc
        if other.id == exclude:
            continue
        if body.type == "feed":
            same = (
                body.url is not None
                and other.url is not None
                and url_sha256(body.url) == url_sha256(other.url)
            )
        else:
            same = (
                body.query is not None
                and other.query is not None
                and body.query.casefold() == other.query.casefold()
            )
        if same:
            raise ProblemError(
                409, code="duplicate_source", detail="You already follow this source."
            )


async def _check_source(
    store: Store, body: SourceIn, now: datetime, *, exclude: str | None
) -> None:
    await _check_pillar(store, body.pillar, now)
    if body.type == "scout_query":
        check_texts({"query": body.query}, await _blocklist(store, now))
    await _refuse_duplicate(store, body, exclude=exclude)


async def _current[D: Document](
    store: Store, cls: type[D], doc_id: str, if_match: str
) -> Stored[D]:
    current = await read_doc(store, cls, doc_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


def _fetch_failed() -> ProblemError:
    return ProblemError(502, code="fetch_failed", detail="The site could not be fetched.")


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


def get_router() -> APIRouter:
    """Routes under ``/api/sources`` and ``/api/ideas``."""
    router = APIRouter(prefix="/api", tags=["signals"])

    # ---- sources ------------------------------------------------------------------------------

    @router.get("/sources")
    async def list_sources(
        store: StoreDep,
        source_type: Annotated[SourceType | None, Query(alias="type")] = None,
        status: SourceStatus | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Source]:
        """Sources in the order they were added."""
        size = clamp_limit(limit)
        with problems():
            filters: list[Filter] = []
            if source_type is not None:
                filters.append(Filter("type", "==", source_type))
            if status is not None:
                filters.append(Filter("status", "==", status.value))
            if cursor is not None:
                filters.append(Filter("id", ">", decode_cursor(cursor)))
            docs = await find_docs(store, Source, filters, order_by=OrderBy("id"), limit=size + 1)
        return _page(docs, size, lambda doc: doc.id)

    @router.post("/sources", status_code=201)
    async def create_source(
        body: SourceIn, store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[Source]:
        now = clock.now()
        stamp = to_iso(now)
        with problems():
            await _check_source(store, body, now, exclude=None)
            source = Source.model_validate(
                {
                    "id": Source.new_id(now=now),
                    "type": body.type,
                    "title": body.title,
                    "url": body.url if body.type == "feed" else None,
                    "site_url": body.site_url,
                    "query": body.query if body.type == "scout_query" else None,
                    "pillar": body.pillar,
                    "status": SourceStatus.ACTIVE,
                    "status_changed_at": stamp,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
            stored = await create_doc(store, source)
        return versioned(stored, response)

    @router.post("/sources/discover")
    async def discover_sources(body: DiscoverIn, egress: EgressDep) -> DiscoverOut:
        """Feeds the owner-typed site links to (or the site itself, when it is a feed)."""
        try:
            fetched = await egress.get(body.site_url, headers={"Accept": _DISCOVER_ACCEPT})
        except EgressError:
            raise _fetch_failed() from None
        if not 200 <= fetched.status < 300:
            raise _fetch_failed()
        if looks_like_feed(fetched.content, fetched.media_type):
            try:
                document = await asyncio.to_thread(
                    parse_feed_document,
                    fetched.content,
                    base_url=fetched.url,
                    max_items=1,
                    content_type=fetched.content_type,
                )
                url = check_https_url(fetched.url)
            except (FeedParseError, ValueError):
                pass
            else:
                return DiscoverOut(feeds=[DiscoveredFeedOut(url=url, title=document.title)])
        try:
            links = await asyncio.to_thread(
                discover_feed_links, fetched.content, base_url=fetched.url
            )
        except ValueError:
            raise _fetch_failed() from None
        return DiscoverOut(
            feeds=[DiscoveredFeedOut(url=link.url, title=link.title) for link in links]
        )

    @router.post("/sources/crawl", status_code=202)
    async def crawl_now(jobs: JobsDep, clock: ClockDep) -> JobAccepted:
        """Crawl every feed now (``signals.crawl``, origin owner). A second tap in the same
        minute returns the same job."""
        now = clock.now()
        job_id = f"signals-crawl-owner-{slot_stamp(to_iso(now))[:13]}"
        with problems():
            try:
                job = await enqueue_once(
                    jobs, SIGNALS_CRAWL, SignalsJobInput(origin="owner").to_json(), job_id=job_id
                )
            except ValueError:
                raise ProblemError(
                    503,
                    code="jobs_unavailable",
                    detail="Crawling is not available right now.",
                    headers={"Retry-After": "5"},
                ) from None
        return JobAccepted(job_id=job.id, status=job.status)

    @router.get("/sources/{source_id}")
    async def get_source(source_id: str, store: StoreDep, response: Response) -> Versioned[Source]:
        with problems():
            stored = await read_doc(store, Source, source_id)
        return versioned(stored, response)

    @router.put("/sources/{source_id}")
    async def put_source(
        source_id: str,
        body: SourceIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Source]:
        now = clock.now()
        stamp = to_iso(now)
        with problems():
            current = await _current(store, Source, source_id, if_match)
            doc = current.doc
            if body.type != doc.type:
                raise ProblemError(
                    422,
                    code="source_type_immutable",
                    detail="A feed stays a feed: add a new source instead.",
                )
            await _check_source(store, body, now, exclude=source_id)
            updates: dict[str, object] = {
                "title": body.title,
                "site_url": body.site_url,
                "pillar": body.pillar,
                "updated_at": stamp,
            }
            changed_target = (body.type == "feed" and body.url != doc.url) or (
                body.type == "scout_query" and body.query != doc.query
            )
            if body.type == "feed":
                updates["url"] = body.url
            else:
                updates["query"] = body.query
            if changed_target:
                # A new address starts with a clean crawl state.
                updates.update(
                    etag=None, last_modified=None, consecutive_failures=0, last_error=None
                )
                if doc.status is SourceStatus.FAILING:
                    updates.update(status=SourceStatus.ACTIVE, status_changed_at=stamp)
            stored = await replace_doc(store, doc.model_copy(update=updates), if_match=if_match)
        return versioned(stored, response)

    @router.delete("/sources/{source_id}", status_code=204)
    async def delete_source(source_id: str, store: StoreDep, if_match: IfMatch) -> Response:
        """Stop following a source. Its ideas stay; its signals age out (90 days)."""
        with problems():
            await _current(store, Source, source_id, if_match)
            await delete_doc(store, Source, source_id, if_match=if_match)
        return Response(status_code=204)

    async def _move_source(
        source_id: str, target: SourceStatus, store: Store, now: datetime, if_match: str
    ) -> Stored[Source]:
        stamp = to_iso(now)
        current = await _current(store, Source, source_id, if_match)
        SOURCE_MACHINE.transition(current.doc.status, target)
        updates: dict[str, object] = {
            "status": target,
            "status_changed_at": stamp,
            "updated_at": stamp,
        }
        if target is SourceStatus.ACTIVE:
            updates.update(consecutive_failures=0, last_error=None)
        return await replace_doc(store, current.doc.model_copy(update=updates), if_match=if_match)

    @router.post("/sources/{source_id}/pause")
    async def pause_source(
        source_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
    ) -> Versioned[Source]:
        with problems():
            stored = await _move_source(
                source_id, SourceStatus.PAUSED, store, clock.now(), if_match
            )
        return versioned(stored, response)

    @router.post("/sources/{source_id}/resume")
    async def resume_source(
        source_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
    ) -> Versioned[Source]:
        """Paused or failing -> active (a failing source gets a fresh error budget)."""
        with problems():
            stored = await _move_source(
                source_id, SourceStatus.ACTIVE, store, clock.now(), if_match
            )
        return versioned(stored, response)

    # ---- ideas --------------------------------------------------------------------------------

    @router.get("/ideas")
    async def list_ideas(
        store: StoreDep,
        status: Annotated[list[IdeaStatus] | None, Query()] = None,
        pillar: str | None = None,
        lane: IdeaLane | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Idea]:
        """The inbox, newest first (default: ``new`` and ``accepted``)."""
        size = clamp_limit(limit)
        statuses = status or list(DEFAULT_IDEA_STATUSES)
        with problems():
            filters = [Filter("status", "in", sorted({item.value for item in statuses}))]
            if pillar is not None:
                filters.append(Filter("pillar", "==", pillar))
            if lane is not None:
                filters.append(Filter("suggestedLane", "==", lane))
            if cursor is not None:
                filters.append(Filter("createdAt", "<", decode_cursor(cursor)))
            docs = await find_docs(
                store,
                Idea,
                filters,
                order_by=OrderBy("createdAt", descending=True),
                limit=size + 1,
            )
        return _page(docs, size, lambda doc: doc.created_at)

    @router.post("/ideas", status_code=201)
    async def create_idea(
        body: IdeaIn, store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[Idea]:
        """Quick capture (origin ``quick_capture``)."""
        now = clock.now()
        stamp = to_iso(now)
        with problems():
            check_texts(
                {"title": body.title, "note": body.note, "url": body.url},
                await _blocklist(store, now),
            )
            await _check_pillar(store, body.pillar, now)
            expires_at = None
            if body.time_sensitive:
                hours = await quick_take_hours(store, now=now)
                expires_at = to_iso(now + timedelta(hours=hours))
            idea = Idea.model_validate(
                {
                    "id": Idea.new_id(now=now),
                    "status": IdeaStatus.NEW,
                    "status_changed_at": stamp,
                    "origin": "quick_capture",
                    "title": body.title,
                    "note": body.note,
                    "url": body.url,
                    "url_sha256": url_sha256(body.url) if body.url is not None else None,
                    "pillar": body.pillar,
                    "suggested_lane": body.suggested_lane,
                    "time_sensitive": body.time_sensitive,
                    "expires_at": expires_at,
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
            stored = await create_doc(store, idea)
        return versioned(stored, response)

    @router.post("/ideas/repurpose", status_code=201)
    async def repurpose_post(
        body: RepurposeIn, store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[Idea]:
        """An ``origin: repurpose`` idea from a post that went out (``repurposeOf``): the
        ``repurpose`` post lane and flagship candidates start from it. Asking again answers the
        same idea (200)."""
        with problems():
            post = (await read_doc(store, Post, body.post_id)).doc
            if post.status is not PostStatus.POSTED:
                raise ProblemError(
                    409,
                    code="not_posted",
                    detail="Only a post that went out can be repurposed.",
                )
            outcome = await propose_repurpose(store, post, kind=body.kind, now=clock.now())
        if not outcome.created:
            response.status_code = 200
        return versioned(outcome.stored, response)

    @router.get("/ideas/{idea_id}")
    async def get_idea(idea_id: str, store: StoreDep, response: Response) -> Versioned[Idea]:
        with problems():
            stored = await read_doc(store, Idea, idea_id)
        return versioned(stored, response)

    @router.put("/ideas/{idea_id}")
    async def edit_idea(
        idea_id: str,
        body: IdeaEditIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Idea]:
        now = clock.now()
        fields = body.model_fields_set
        with problems():
            current = await _current(store, Idea, idea_id, if_match)
            texts: dict[str, str | None] = {
                name: getattr(body, name) for name in _IDEA_TEXT_FIELDS if name in fields
            }
            if any(value is not None for value in texts.values()):
                check_texts(texts, await _blocklist(store, now))
            if "pillar" in fields:
                await _check_pillar(store, body.pillar, now)
            if (
                "suggested_format" in fields
                and body.suggested_format is not None
                and body.suggested_format not in await known_formats(store, now=now)
            ):
                raise ProblemError(422, code="unknown_format", detail="Pick one of your formats.")
            updates: dict[str, object] = {name: getattr(body, name) for name in fields}
            updates["updated_at"] = to_iso(now)
            stored = await replace_doc(
                store, current.doc.model_copy(update=updates), if_match=if_match
            )
        return versioned(stored, response)

    @router.post("/ideas/{idea_id}/transition")
    async def transition_idea(
        idea_id: str,
        body: IdeaTransitionIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Idea]:
        """Accept, snooze (optionally until a time), dismiss, or bring back to ``new``."""
        now = clock.now()
        stamp = to_iso(now)
        with problems():
            current = await _current(store, Idea, idea_id, if_match)
            target = IdeaStatus(body.to)
            IDEA_MACHINE.transition(current.doc.status, target)
            snoozed_until: str | None = None
            if target is IdeaStatus.SNOOZED and body.snoozed_until is not None:
                if parse_iso(body.snoozed_until) <= now:
                    raise ProblemError(
                        422, code="invalid_snooze", detail="Snooze until a time in the future."
                    )
                snoozed_until = body.snoozed_until
            updates: dict[str, object] = {
                "status": target,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "snoozed_until": snoozed_until,
            }
            if current.doc.status is IdeaStatus.EXPIRED:
                updates["expires_at"] = None  # brought back by the owner: no deadline any more
            stored = await replace_doc(
                store, current.doc.model_copy(update=updates), if_match=if_match
            )
        return versioned(stored, response)

    return router
