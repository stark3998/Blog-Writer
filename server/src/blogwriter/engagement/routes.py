"""HTTP routes of the engagement package (contract W-B5): items, records, watchlist, checklist.

Every route is owner-only and every non-GET passes CSRF (core). Single documents are
``Versioned[...]``, lists ``Page[...]``. Other members' text lives on items for 48 hours: the
list hides expired items (in the query and again in code) and the item routes answer 410
``expired`` (deleting what they find expired). Nothing here posts, comments or reads on
LinkedIn: the owner does that by hand.

Beyond the contract table: ``POST /api/engagement/extract`` reads a screenshot (raw image body,
``Content-Type: image/png|image/jpeg|image/webp``) and returns the text for the owner to check
before creating an item; neither the image nor the text is stored.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from blogwriter.ai import NoRouteError
from blogwriter.ai.providers import ProviderError, UnsupportedRequestError
from blogwriter.ai.schema import StructuredOutputError
from blogwriter.ai.types import MAX_IMAGE_BYTES
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import describe_exception
from blogwriter.db import Filter, OrderBy, Store
from blogwriter.domain.api import Page, Versioned, clamp_limit, decode_cursor, encode_cursor
from blogwriter.domain.base import Document, to_iso
from blogwriter.domain.deps import (
    AIDep,
    ClockDep,
    IfMatch,
    ServicesDep,
    SingletonPrecondition,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
    versioned_singleton,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    save_singleton,
)
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.settings import (
    Blocklist,
    ChecklistItem,
    EmployerPolicy,
    PillarSettings,
    ProfileChecklist,
)
from blogwriter.domain.states import EngagementStatus
from blogwriter.engagement.helpers import (
    SCREENSHOT_CONTENT_TYPES,
    InvalidField,
    ItemExpired,
    ModelTimeout,
    NoOwnerMaterial,
    NothingExtracted,
    create_item,
    dismiss_item,
    extract_screenshot,
    finalize_item,
    generate_angles,
    is_expired,
    mark_engaged,
    owner_post_text,
    read_live_item,
)
from blogwriter.engagement.lint import lint_reply
from blogwriter.engagement.models import (
    ChecklistIn,
    ExtractOut,
    FinalizeIn,
    ItemIn,
    LintIn,
    LintOut,
    WatchIn,
)
from blogwriter.storage import MediaRejected

__all__ = [
    "PREFIX",
    "get_router",
]

PREFIX: Final = "/api/engagement"
_NEWEST_FIRST: Final = OrderBy("createdAt", descending=True)
_OLDEST_FIRST: Final = OrderBy("createdAt")
_REJECTION_STATUS: Final = {"too_large": 413, "unsupported_media_type": 415, "invalid_image": 422}
_logger: Final = logging.getLogger("blogwriter.engagement")

type LimitQuery = Annotated[int | None, Query()]
type CursorQuery = Annotated[str | None, Query()]


# --------------------------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------------------------


def _field_error(field: str, message: str) -> RequestValidationError:
    """A 422 ``validation_failed`` on one body field (core's handler)."""
    return RequestValidationError([{"loc": ("body", field), "msg": message, "type": "value_error"}])


@contextmanager
def _engagement_problems() -> Generator[None]:
    """The package's own errors as problems (inside ``problems()`` for the shared ones)."""
    try:
        yield
    except ItemExpired:
        raise ProblemError(
            410,
            code=ItemExpired.code,
            detail="Other members' text is kept for 48 hours; this item has expired.",
        ) from None
    except NoOwnerMaterial:
        raise ProblemError(
            409,
            code=NoOwnerMaterial.code,
            detail="Angles draw on your own material. Approve a few stories or POVs first.",
        ) from None
    except ModelTimeout:
        raise ProblemError(
            504, code=ModelTimeout.code, detail="The model took too long. Try again."
        ) from None
    except NothingExtracted:
        raise ProblemError(
            422,
            code=NothingExtracted.code,
            detail="No post or comment text could be read from the screenshot. Paste it instead.",
        ) from None
    except InvalidField as exc:
        raise _field_error(exc.field, exc.message) from None
    except MediaRejected as exc:
        raise ProblemError(_REJECTION_STATUS[exc.code], code=exc.code, detail=exc.detail) from None
    except (ProviderError, StructuredOutputError, NoRouteError, UnsupportedRequestError) as exc:
        _logger.warning("engagement_model_failed", extra=describe_exception(exc))
        raise ProblemError(
            503,
            code="ai_unavailable",
            detail="The model did not answer. Try again in a moment.",
            headers={"Retry-After": "5"},
        ) from None


def _cursor_filter(cursor: str | None, *, ascending: bool = False) -> list[Filter]:
    """Keyset paging on ``createdAt`` (descending lists continue below the cursor)."""
    if cursor is None:
        return []
    return [Filter("createdAt", ">" if ascending else "<", decode_cursor(cursor))]


def _next_cursor[D: Document](found: Sequence[Stored[D]], size: int) -> str | None:
    if len(found) <= size:
        return None
    return encode_cursor(found[size - 1].doc.created_at)


async def _check_pillar(store: Store, pillar: str | None, *, now: str) -> None:
    if pillar is None:
        return
    pillars, _ = await read_singleton(store, PillarSettings, now=now)
    if pillars.get(pillar) is None:
        raise _field_error("pillar", "No pillar with this id.")


# --------------------------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------------------------


async def list_items(
    store: StoreDep,
    clock: ClockDep,
    status: Annotated[EngagementStatus, Query()] = EngagementStatus.OPEN,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[EngagementItem]:
    """Items within their 48 hours, newest first (expired ones are filtered out)."""
    size = clamp_limit(limit)
    now = clock.now()
    with problems():
        filters = [
            Filter("status", "==", status.value),
            Filter("expiresAt", ">", to_iso(now)),
            *_cursor_filter(cursor),
        ]
        found = await find_docs(
            store, EngagementItem, filters, order_by=_NEWEST_FIRST, limit=size + 1
        )
    live = [stored for stored in found[:size] if not is_expired(stored.doc, now)]
    return Page[EngagementItem](
        items=[versioned(stored) for stored in live], next_cursor=_next_cursor(found, size)
    )


async def post_item(
    body: ItemIn, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[EngagementItem]:
    """Keep pasted text for 48 hours (``expiresAt`` and ``ttl``)."""
    with problems(), _engagement_problems():
        stored = await create_item(
            store,
            item_kind=body.item_kind,
            pasted_text=body.pasted_text,
            now=clock.now(),
            post_id=body.post_id,
            target_url=body.target_url,
        )
    return versioned(stored, response)


async def post_angles(
    item_id: str, store: StoreDep, clock: ClockDep, ai: AIDep, response: Response
) -> Versioned[EngagementItem]:
    """Up to three angles grounded in the owner's stories, POVs and briefs (ENGAGEMENT)."""
    with problems(), _engagement_problems():
        stored = await generate_angles(store, ai, item_id, now=clock.now())
    return versioned(stored, response)


async def post_lint(
    item_id: str, body: LintIn, services: ServicesDep, store: StoreDep, clock: ClockDep
) -> LintOut:
    """Lint a draft: restating, links, AI tells and slop, over 1,000 characters, blocklist.

    No model call and nothing stored. Without a saved blocklist the findings carry the hard
    ``blocklist-not-configured`` block (fail closed).
    """
    now = clock.now()
    with problems(), _engagement_problems():
        item = (await read_live_item(store, item_id, now=now)).doc
        blocklist, _ = await read_singleton(store, Blocklist, now=to_iso(now))
        policy, _ = await read_singleton(store, EmployerPolicy, now=to_iso(now))
        owner_post = await owner_post_text(store, item)
    sources = [item.pasted_text, *([owner_post] if owner_post else [])]
    findings = await asyncio.to_thread(
        partial(
            lint_reply,
            body.draft_text,
            sources=sources,
            blocklist=blocklist,
            slop=services.slop,
            policy=policy,
        )
    )
    return LintOut(findings=findings)


async def post_finalize(
    item_id: str,
    body: FinalizeIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[EngagementRecord]:
    """Keep what the owner posted (final text, URL) and delete the item at once."""
    with problems(), _engagement_problems():
        current = await read_doc(store, EngagementItem, item_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        stored = await finalize_item(
            store,
            current,
            final_text=body.final_text,
            url=body.url,
            watch_entry_id=body.watch_entry_id,
            now=clock.now(),
        )
    return versioned(stored, response)


async def post_dismiss(
    item_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch
) -> Response:
    """Delete an item without answering it."""
    with problems(), _engagement_problems():
        current = await read_doc(store, EngagementItem, item_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        await dismiss_item(store, current, now=clock.now())
    return Response(status_code=204)


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it passes ``limit`` bytes."""
    too_large = ProblemError(
        413, code="too_large", detail=f"Screenshots are limited to {limit} bytes."
    )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise too_large
    return bytes(received)


EXTRACT_REQUEST_BODY: Final[dict[str, object]] = {
    "required": True,
    "description": (
        "The screenshot itself as the raw body (no multipart), with its own Content-Type, at "
        f"most {MAX_IMAGE_BYTES} bytes (413 too_large). It is read once and never stored."
    ),
    "content": {content_type: {} for content_type in sorted(SCREENSHOT_CONTENT_TYPES)},
}
"""``POST /api/engagement/extract``'s request body in the OpenAPI document (raw image bytes)."""


async def post_extract(request: Request, store: StoreDep, clock: ClockDep, ai: AIDep) -> ExtractOut:
    """Read the post or comment text from the owner's screenshot (nothing is stored)."""
    declared = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared not in SCREENSHOT_CONTENT_TYPES:
        raise ProblemError(
            415,
            code="unsupported_media_type",
            detail="Send a PNG, JPEG or WebP screenshot.",
        )
    data = await _read_capped(request, MAX_IMAGE_BYTES)
    if not data:
        raise ProblemError(422, code="invalid_image", detail="The upload is empty.")
    with problems(), _engagement_problems():
        extracted = await extract_screenshot(
            store, ai, data, content_type=declared, now=clock.now()
        )
    return ExtractOut(text=extracted.text, findings=extracted.findings)


# --------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------


async def list_records(
    store: StoreDep, limit: LimitQuery = None, cursor: CursorQuery = None
) -> Page[EngagementRecord]:
    """What the owner posted, newest first."""
    size = clamp_limit(limit)
    with problems():
        found = await find_docs(
            store,
            EngagementRecord,
            _cursor_filter(cursor),
            order_by=_NEWEST_FIRST,
            limit=size + 1,
        )
    return Page[EngagementRecord](
        items=[versioned(stored) for stored in found[:size]],
        next_cursor=_next_cursor(found, size),
    )


# --------------------------------------------------------------------------------------------
# Watchlist
# --------------------------------------------------------------------------------------------


async def list_watchlist(
    store: StoreDep,
    active: Annotated[bool | None, Query()] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[WatchEntry]:
    """The niche voices, in the order they were added."""
    size = clamp_limit(limit)
    with problems():
        filters = _cursor_filter(cursor, ascending=True)
        if active is not None:
            filters.append(Filter("active", "==", active))
        found = await find_docs(store, WatchEntry, filters, order_by=_OLDEST_FIRST, limit=size + 1)
    return Page[WatchEntry](
        items=[versioned(stored) for stored in found[:size]],
        next_cursor=_next_cursor(found, size),
    )


async def post_watch_entry(
    body: WatchIn, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[WatchEntry]:
    now = clock.now()
    stamp = to_iso(now)
    with problems():
        await _check_pillar(store, body.pillar, now=stamp)
        entry = WatchEntry(
            id=WatchEntry.new_id(now=now),
            created_at=stamp,
            updated_at=stamp,
            name=body.name,
            profile_url=body.profile_url,
            pillar=body.pillar,
            note=body.note,
            active=body.active,
        )
        stored = await create_doc(store, entry)
    return versioned(stored, response)


async def put_watch_entry(
    entry_id: str,
    body: WatchIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[WatchEntry]:
    stamp = to_iso(clock.now())
    with problems():
        current = await read_doc(store, WatchEntry, entry_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        await _check_pillar(store, body.pillar, now=stamp)
        changed = current.doc.model_copy(
            update={
                "name": body.name,
                "profile_url": body.profile_url,
                "pillar": body.pillar,
                "note": body.note,
                "active": body.active,
                "updated_at": stamp,
            }
        )
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def delete_watch_entry(entry_id: str, store: StoreDep, if_match: IfMatch) -> Response:
    with problems():
        current = await read_doc(store, WatchEntry, entry_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        await delete_doc(store, WatchEntry, entry_id, if_match=if_match)
    return Response(status_code=204)


async def post_engaged(
    entry_id: str, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[WatchEntry]:
    """One tap in the daily session: ``lastEngagedAt`` = now (idempotent, no If-Match)."""
    with problems():
        stored = await mark_engaged(store, entry_id, now=to_iso(clock.now()))
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Profile checklist
# --------------------------------------------------------------------------------------------


async def get_checklist(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[ProfileChecklist]:
    """The profile-alignment checklist (``etag: null`` until the first save)."""
    with problems():
        doc, etag = await read_singleton(store, ProfileChecklist, now=to_iso(clock.now()))
    return versioned_singleton(doc, etag, response)


async def put_checklist(
    body: ChecklistIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[ProfileChecklist]:
    """Save the whole checklist; ``doneAt`` is kept while an item stays done."""
    stamp = to_iso(clock.now())
    with problems():
        doc, etag = await read_singleton(store, ProfileChecklist, now=stamp)
        if precondition is None:
            if etag is not None:
                raise etag_mismatch(Stored(doc, etag))
        elif etag != precondition:
            raise etag_mismatch(Stored(doc, etag) if etag is not None else None)
        previous = {item.id: item for item in doc.items}
        items: list[ChecklistItem] = []
        for item in body.items:
            before = previous.get(item.id)
            done_at = None
            if item.done:
                kept = before.done_at if before is not None and before.done else None
                done_at = kept or stamp
            items.append(
                ChecklistItem(
                    id=item.id, label=item.label, done=item.done, done_at=done_at, note=item.note
                )
            )
        changed = doc.model_copy(update={"items": items, "updated_at": stamp})
        stored = await save_singleton(store, changed, if_match=precondition)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


def get_router() -> APIRouter:
    """The engagement routes (contract W-B5), plus ``POST /api/engagement/extract``."""
    router = APIRouter(prefix=PREFIX, tags=["engagement"])
    add = router.add_api_route
    add("/items", list_items, methods=["GET"], summary="Open items (other members' text)")
    add("/items", post_item, methods=["POST"], status_code=201, summary="Paste a post or comment")
    add("/items/{item_id}/angles", post_angles, methods=["POST"], summary="Angles for a reply")
    add("/items/{item_id}/lint", post_lint, methods=["POST"], summary="Lint a reply draft")
    add(
        "/items/{item_id}/finalize",
        post_finalize,
        methods=["POST"],
        status_code=201,
        summary="Keep what was posted",
    )
    add(
        "/items/{item_id}/dismiss",
        post_dismiss,
        methods=["POST"],
        status_code=204,
        response_class=Response,
        summary="Dismiss an item",
    )
    add(
        "/extract",
        post_extract,
        methods=["POST"],
        summary="Read text from a screenshot",
        openapi_extra={"requestBody": EXTRACT_REQUEST_BODY},
    )
    add("/records", list_records, methods=["GET"], summary="Posted replies and comments")
    add("/watchlist", list_watchlist, methods=["GET"], summary="The watchlist")
    add(
        "/watchlist",
        post_watch_entry,
        methods=["POST"],
        status_code=201,
        summary="Add a niche voice",
    )
    add("/watchlist/{entry_id}", put_watch_entry, methods=["PUT"], summary="Edit a watch entry")
    add(
        "/watchlist/{entry_id}",
        delete_watch_entry,
        methods=["DELETE"],
        status_code=204,
        response_class=Response,
        summary="Remove a watch entry",
    )
    add(
        "/watchlist/{entry_id}/engaged",
        post_engaged,
        methods=["POST"],
        summary="Mark a voice as engaged today",
    )
    add("/profile-checklist", get_checklist, methods=["GET"], summary="Profile checklist")
    add("/profile-checklist", put_checklist, methods=["PUT"], summary="Save the checklist")
    return router
