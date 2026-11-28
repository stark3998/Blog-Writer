"""``POST /api/render/mermaid``: the Draft tab's live diagram preview (thin over
``services.render``).

::

    POST /api/render/mermaid   {source}  ->  200 {svg, width, height}

- **Owner-only, CSRF**: core's middleware, like every route.
- **Capped**: the JSON body is read with a running cap of :data:`MAX_BODY_BYTES` (413
  ``too_large``) before it is parsed, and ``source`` holds at most the renderer's own limit
  (:data:`MAX_SOURCE_CHARS`, 422 ``diagram_too_long``), so nothing large reaches Chromium.
- **Rendered by** the ``RenderService`` adapter (``render_mermaid_svg``: strict Mermaid, the
  output sanitised by the render package, no brand: the portfolio preview's look). The answer
  is that SVG as text, for the web to show only as an image (``data:`` URI), never inline.
- **Off**: 503 ``render_unavailable`` without a renderer (and when Chromium is missing). A
  source Mermaid cannot draw is 422 ``mermaid_error``; a render over its time is 504
  ``render_timeout``.
- **Cached**: renders are deterministic and slow, so an in-process LRU keeps recent SVGs by the
  SHA-256 of their source (:class:`DiagramCache`). Nothing is stored and no GET changes state.

The source is never logged (nor its hash): failures log their code only (the adapter's
``render_adapter_failed``).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import OrderedDict
from typing import Final

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import Field, ValidationError

from blogwriter.core.errors import ProblemError
from blogwriter.domain.api import ApiModel
from blogwriter.domain.deps import RenderDep
from blogwriter.domain.ports import RenderFailed
from blogwriter.render.spec import MAX_MERMAID_SOURCE

__all__ = [
    "MAX_BODY_BYTES",
    "MAX_SOURCE_CHARS",
    "RENDER_TIMEOUT_SECONDS",
    "DiagramCache",
    "MermaidIn",
    "MermaidOut",
    "clear_cache",
    "get_router",
]

MAX_SOURCE_CHARS: Final = MAX_MERMAID_SOURCE
"""The renderer's limit for one diagram's source (characters)."""
MAX_BODY_BYTES: Final = 20 * 1024
"""The request body cap: room for a source at :data:`MAX_SOURCE_CHARS` with JSON escapes."""
RENDER_TIMEOUT_SECONDS: Final = 30.0
_SVG_SIZE: Final = re.compile(
    r'<svg\b[^>]*?\swidth="(?P<w>\d{1,6})(?:\.\d+)?(?:px)?"[^>]*?\sheight="(?P<h>\d{1,6})'
)
_SVG_SIZE_REVERSED: Final = re.compile(
    r'<svg\b[^>]*?\sheight="(?P<h>\d{1,6})(?:\.\d+)?(?:px)?"[^>]*?\swidth="(?P<w>\d{1,6})'
)
_STATUS: Final[dict[str, tuple[int, str]]] = {
    "unavailable": (503, "render_unavailable"),
    "timeout": (504, "render_timeout"),
    "asset_integrity": (500, "asset_integrity"),
}


class MermaidIn(ApiModel):
    source: str = Field(min_length=1, max_length=MAX_BODY_BYTES)
    """The diagram's Mermaid source (the fence's body, without the fences)."""


class MermaidOut(ApiModel):
    svg: str
    """The sanitised SVG (text embedded, explicit size). Show it only as an image."""
    width: int | None = None
    height: int | None = None


# --------------------------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------------------------


class DiagramCache:
    """Rendered SVGs by the SHA-256 of their source, least recently used out, bounded by count
    and by bytes (an optimisation only: a miss renders again)."""

    def __init__(self, *, max_entries: int = 128, max_bytes: int = 8 * 1024 * 1024) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("the cache needs room for one entry")
        self._items: OrderedDict[str, str] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def key(source: str) -> str:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        svg = self._items.get(key)
        if svg is not None:
            self._items.move_to_end(key)
        return svg

    def put(self, key: str, svg: str) -> None:
        size = len(svg)
        if key in self._items or size > self._max_bytes:
            return
        self._items[key] = svg
        self._bytes += size
        while len(self._items) > self._max_entries or self._bytes > self._max_bytes:
            _, dropped = self._items.popitem(last=False)
            self._bytes -= len(dropped)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


_CACHE: Final = DiagramCache()


def clear_cache() -> None:
    """Empty the process's diagram cache (tests)."""
    _CACHE.clear()


# --------------------------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------------------------


async def _read_capped(request: Request) -> bytes:
    too_large = ProblemError(
        413, code="too_large", detail=f"Diagram requests are limited to {MAX_BODY_BYTES} bytes."
    )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > MAX_BODY_BYTES:
            raise too_large
    return bytes(received)


async def _body(request: Request) -> MermaidIn:
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise ProblemError(415, code="unsupported_media_type", detail="Send JSON: {source}.")
    raw = await _read_capped(request)
    try:
        body = MermaidIn.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in errors]
        ) from None
    if len(body.source) > MAX_SOURCE_CHARS:
        raise ProblemError(
            422,
            code="diagram_too_long",
            detail=f"Diagrams are limited to {MAX_SOURCE_CHARS:,} characters. Shorten this one.",
        )
    if not body.source.strip() or "\x00" in body.source:
        raise ProblemError(422, code="mermaid_error", detail="Write the diagram's source first.")
    return body


def _size(svg: str) -> tuple[int | None, int | None]:
    head = svg[:2048]
    found = _SVG_SIZE.search(head) or _SVG_SIZE_REVERSED.search(head)
    if found is None:
        return None, None
    return int(found.group("w")), int(found.group("h"))


def _problem(exc: RenderFailed) -> ProblemError:
    status, code = _STATUS.get(exc.code, (422, exc.code))
    headers = {"Retry-After": "5"} if status == 503 else None
    detail = exc.detail or "The diagram could not be drawn."
    return ProblemError(status, code=code, detail=detail, headers=headers)


async def render_mermaid(request: Request, render: RenderDep) -> MermaidOut:
    """Render one diagram's Mermaid source as a sanitised SVG (cached by its hash)."""
    body = await _body(request)
    key = DiagramCache.key(body.source)
    svg = _CACHE.get(key)
    if svg is None:
        try:
            async with asyncio.timeout(RENDER_TIMEOUT_SECONDS):
                data = await render.render_mermaid_svg(body.source)
        except TimeoutError:
            raise _problem(RenderFailed("timeout", "The diagram took too long.")) from None
        except RenderFailed as exc:
            raise _problem(exc) from None
        svg = data.decode("utf-8")
        _CACHE.put(key, svg)
    width, height = _size(svg)
    return MermaidOut(svg=svg, width=width, height=height)


def get_router() -> APIRouter:
    """``POST /api/render/mermaid``."""
    router = APIRouter(tags=["render"])
    router.add_api_route(
        "/api/render/mermaid",
        render_mermaid,
        methods=["POST"],
        response_model=MermaidOut,
        summary="Render a Mermaid diagram as a sanitised SVG (live preview)",
        openapi_extra={
            "requestBody": {
                "required": True,
                "description": f"At most {MAX_BODY_BYTES} bytes (413 too_large).",
                "content": {
                    "application/json": {"schema": MermaidIn.model_json_schema(by_alias=True)}
                },
            }
        },
    )
    return router
