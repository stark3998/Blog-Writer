"""``POST /api/media`` (owner uploads) and ``GET /api/media/{name}`` (same-origin media).

Uploads are raw bodies (no multipart) with ``Content-Type: image/png|image/jpeg|image/webp``,
read with a running byte cap, re-encoded by :func:`~blogwriter.storage.media.ingest_image` and
stored under ``uploads/<YYYY-MM>/<sha256>.<ext>`` in the ``media`` container.

Media are served only from the four media areas, with immutable private caching, a sandboxing
CSP and ``nosniff``; any other name (``..`` segments included) is a 404.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from blogwriter.core.errors import ProblemError
from blogwriter.domain.common import MediaRef
from blogwriter.domain.deps import BlobsDep, ClockDep
from blogwriter.domain.ports import BlobNotFound
from blogwriter.storage.blobs import StorageError
from blogwriter.storage.media import (
    MEDIA_EXTENSIONS,
    UPLOAD_CONTENT_TYPES,
    MediaRejected,
    RejectionCode,
    ingest_image,
    store_media,
)
from blogwriter.storage.settings import StorageSettings, storage_settings

__all__ = [
    "MEDIA_RESPONSES",
    "MEDIA_RESPONSE_HEADERS",
    "UPLOAD_REQUEST_BODY",
    "LimitsDep",
    "UploadRole",
    "get_media",
    "get_router",
    "is_media_name",
    "upload_limits",
    "upload_media",
]

type UploadRole = Literal["upload", "headshot", "name_mark", "image"]

MEDIA_RESPONSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
}
_MEDIA_NAME_RE: Final = re.compile(r"^(?:uploads|posts|articles|brand)/[a-z0-9][a-z0-9/._-]*$")
_MAX_MEDIA_NAME: Final = 300
_FILENAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,119}$"
_REJECTION_STATUS: Final[dict[RejectionCode, int]] = {
    "too_large": 413,
    "unsupported_media_type": 415,
    "invalid_image": 422,
}


def _binary(content_types: Iterable[str]) -> dict[str, dict[str, object]]:
    """OpenAPI 3.1 media type objects for raw bytes (no schema: any octets)."""
    return {content_type: {} for content_type in sorted(content_types)}


UPLOAD_REQUEST_BODY: Final[dict[str, object]] = {
    "required": True,
    "description": (
        "The image itself as the raw body (no multipart), with its own Content-Type. At most "
        "BW_STORAGE_MAX_UPLOAD_BYTES (413 too_large); it is re-encoded, so no metadata survives."
    ),
    "content": _binary(UPLOAD_CONTENT_TYPES),
}
"""``POST /api/media``'s request body in the OpenAPI document (the route reads the bytes)."""

MEDIA_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": (
            "The stored bytes, sandboxed (CSP sandbox, nosniff) with a private immutable cache. "
            "Any other stored type is served as an application/octet-stream attachment."
        ),
        "content": _binary([*MEDIA_EXTENSIONS, "application/octet-stream"]),
    }
}
"""``GET /api/media/{name}``'s answers in the OpenAPI document."""

MEDIA_NAME_DESCRIPTION: Final = (
    "The media name with its folders, slashes kept (uploads/2026-11/<sha256>.png, "
    "posts/<postId>/<sha256>.pdf, articles/..., brand/...): GET /api/media/uploads/2026-11/x.png. "
    "Each segment is URL-encoded on its own; encoded slashes (%2F) resolve too."
)


def is_media_name(name: str) -> bool:
    """Whether ``name`` may be served: one of the media areas, no empty/``.``/``..`` segment."""
    if len(name) > _MAX_MEDIA_NAME or not _MEDIA_NAME_RE.fullmatch(name):
        return False
    return all(segment not in ("", ".", "..") for segment in name.split("/"))


def _not_found() -> ProblemError:
    return ProblemError(404, code="not_found")


def _too_large(limit: int) -> ProblemError:
    return ProblemError(413, code="too_large", detail=f"Uploads are limited to {limit} bytes.")


@contextmanager
def _storage_problems() -> Generator[None]:
    """Blob failures as 503 ``storage_unavailable`` (never with SDK text)."""
    try:
        yield
    except StorageError:
        raise ProblemError(
            503,
            code="storage_unavailable",
            detail="Storage is not reachable right now.",
            headers={"Retry-After": "5"},
        ) from None


def _declared_type(request: Request) -> str:
    raw = request.headers.get("content-type", "")
    return raw.split(";", 1)[0].strip().lower()


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it passes ``limit`` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise _too_large(limit)
    return bytes(received)


def upload_limits() -> StorageSettings:
    """FastAPI dependency: the upload limits (``BW_STORAGE_*``; tests override it)."""
    return storage_settings()


LimitsDep = Annotated[StorageSettings, Depends(upload_limits)]


async def upload_media(
    request: Request,
    blobs: BlobsDep,
    clock: ClockDep,
    limits: LimitsDep,
    role: UploadRole = "upload",
    filename: Annotated[str | None, Query(pattern=_FILENAME_PATTERN)] = None,
) -> MediaRef:
    """Upload an image (raw body). The bytes are re-encoded; metadata never survives."""
    declared = _declared_type(request)
    if declared not in UPLOAD_CONTENT_TYPES:
        raise ProblemError(
            415,
            code="unsupported_media_type",
            detail="Send Content-Type image/png, image/jpeg or image/webp.",
        )
    body = await _read_capped(request, limits.max_upload_bytes)
    if not body:
        raise ProblemError(422, code="invalid_image", detail="The upload is empty.")
    try:
        image = await ingest_image(
            body, max_pixels=limits.max_image_pixels, max_bytes=limits.max_upload_bytes
        )
    except MediaRejected as exc:
        raise ProblemError(_REJECTION_STATUS[exc.code], code=exc.code, detail=exc.detail) from None
    if image.content_type != declared:
        raise ProblemError(
            415,
            code="unsupported_media_type",
            detail="The file does not match its Content-Type.",
        )
    with _storage_problems():
        return await store_media(
            blobs,
            image.data,
            role=role,
            area="uploads",
            owner_id=None,
            content_type=image.content_type,
            now=clock.now(),
            filename=filename,
            width=image.width,
            height=image.height,
        )


async def get_media(
    name: Annotated[str, Path(description=MEDIA_NAME_DESCRIPTION)], blobs: BlobsDep
) -> Response:
    """Serve one media blob (same origin, private immutable cache, sandboxed)."""
    if not is_media_name(name):
        raise _not_found()
    with _storage_problems():
        info = await blobs.head("media", name)
        if info is None:
            raise _not_found()
        try:
            data = await blobs.get("media", name)
        except BlobNotFound:
            raise _not_found() from None
    headers = dict(MEDIA_RESPONSE_HEADERS)
    media_type = info.content_type
    if media_type not in MEDIA_EXTENSIONS:
        media_type = "application/octet-stream"
        headers["Content-Disposition"] = "attachment"
    return Response(content=data, media_type=media_type, headers=headers)


def get_router() -> APIRouter:
    """``POST /api/media`` and ``GET /api/media/{name}`` (a path parameter: names have
    folders)."""
    router = APIRouter(tags=["media"])
    router.add_api_route(
        "/api/media",
        upload_media,
        methods=["POST"],
        status_code=201,
        response_model=MediaRef,
        summary="Upload an image",
        openapi_extra={"requestBody": UPLOAD_REQUEST_BODY},
    )
    router.add_api_route(
        "/api/media/{name:path}",
        get_media,
        methods=["GET"],
        response_class=Response,
        responses=MEDIA_RESPONSES,
        summary="Read a media file",
    )
    return router
