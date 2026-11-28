"""Response envelopes, pagination cursors and fixed response headers shared by every router.

- A single resource is returned as ``Versioned[T]``: ``{"item": {...}, "etag": "\\"...\\""}``,
  and the same etag in the ``ETag`` response header. ``etag`` is ``null`` for a singleton
  settings document that was never saved (the owner sees the defaults).
- A list is ``Page[T]``: ``{"items": [Versioned[T], ...], "nextCursor": "..." | null}``. List
  items are summaries (no Markdown, payloads, embeddings or other large fields).
- An accepted background job is ``JobAccepted`` with HTTP 202; the web follows it on the
  ``/api/events`` stream and ``GET /api/jobs/{id}``.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PREVIEW_CSP",
    "PREVIEW_HEADERS",
    "ApiModel",
    "InvalidCursor",
    "JobAccepted",
    "Page",
    "Versioned",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
]

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 100
MAX_CURSOR_CHARS: Final = 512


class ApiModel(BaseModel):
    """Base of request and response models: camelCase on the wire, unknown input refused."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class Versioned[T: BaseModel](ApiModel):
    item: T
    etag: str | None


class Page[T: BaseModel](ApiModel):
    items: list[Versioned[T]]
    next_cursor: str | None = None


class JobAccepted(ApiModel):
    job_id: str
    status: str = Field(default="queued")


def clamp_limit(limit: int | None) -> int:
    """A page size between 1 and :data:`MAX_PAGE_SIZE` (default :data:`DEFAULT_PAGE_SIZE`)."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


class InvalidCursor(ValueError):
    """A malformed ``?cursor=`` (422 ``invalid_cursor`` through ``domain.deps.problems``)."""

    def __init__(self) -> None:
        super().__init__("invalid cursor")


def encode_cursor(value: str) -> str:
    """An opaque cursor for the last sort key of a page (base64url, no padding)."""
    return base64.urlsafe_b64encode(value.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> str:
    """The sort key in a cursor; :class:`InvalidCursor` (a 422 for the route) when malformed."""
    if not cursor or len(cursor) > MAX_CURSOR_CHARS:
        raise InvalidCursor()
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        value = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        text = value.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        raise InvalidCursor() from None
    if not text or encode_cursor(text) != cursor:
        raise InvalidCursor()
    return text


PREVIEW_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'none'; sandbox"
)
"""CSP of server-rendered previews (portfolio page, carousel HTML) loaded in a sandboxed
iframe (no ``allow-scripts``, no ``allow-same-origin``). No scripts at all."""

PREVIEW_HEADERS: Final[Mapping[str, str]] = {
    "Content-Security-Policy": PREVIEW_CSP,
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-store",
}
"""Set these on preview responses; core's defaults would forbid framing (``DENY``)."""
