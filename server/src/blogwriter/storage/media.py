"""Media ingest and naming: owner uploads re-encoded, every blob content-addressed.

- :func:`ingest_image` accepts PNG, JPEG and WebP only (sniffed from the bytes, never from a
  file name), refuses SVG, HTML, polyglots (markup inside the file, bytes after the image's end)
  and images above the pixel cap, applies the EXIF orientation, keeps the first frame, converts
  an embedded colour profile to sRGB and re-encodes the pixels into a fresh image. Nothing of
  the original file survives but its pixels: EXIF (GPS included), XMP, ICC profiles, text
  chunks and comments are dropped. Pillow runs in ``asyncio.to_thread``.
- :func:`media_name` builds content-addressed names: ``uploads/<YYYY-MM>/<sha256>.<ext>``,
  ``posts/<postId>/<sha256>.<ext>``, ``articles/<articleId>/<sha256>.<ext>``,
  ``brand/<sha256>.<ext>``. The same bytes always get the same name in an area, so a reference
  can never silently point at changed bytes.
- :func:`store_media` writes bytes to the ``media`` container under that name and returns the
  :class:`~blogwriter.domain.common.MediaRef`. ``BlobExists`` means the same bytes are already
  stored, which is fine.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, get_args

from PIL import Image, ImageCms, ImageOps

from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MediaRef, MediaRole
from blogwriter.domain.ports import BlobExists, BlobStore

__all__ = [
    "MEDIA_AREAS",
    "MEDIA_EXTENSIONS",
    "UPLOAD_CONTENT_TYPES",
    "IngestedImage",
    "MediaArea",
    "MediaRejected",
    "MediaRole",
    "RejectionCode",
    "ingest_image",
    "media_name",
    "sniff_image_type",
    "store_media",
    "to_srgb",
]

type MediaArea = Literal["uploads", "posts", "articles", "brand"]
MEDIA_AREAS: Final[tuple[MediaArea, ...]] = ("uploads", "posts", "articles", "brand")

type RejectionCode = Literal["too_large", "unsupported_media_type", "invalid_image"]

UPLOAD_CONTENT_TYPES: Final[dict[str, str]] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
"""Upload content types (``POST /api/media``) and their file extensions."""
MEDIA_EXTENSIONS: Final[dict[str, str]] = {
    **UPLOAD_CONTENT_TYPES,
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
}
"""Every content type a :class:`~blogwriter.domain.common.MediaRef` may have, by extension."""

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_OWNER_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_MARKUP_RE: Final = re.compile(
    rb"<(?:script|html|svg|iframe|object|embed|body|!doctype|\?xml)[\s>/]|javascript:",
    re.IGNORECASE,
)
"""Markup a browser could sniff or run. Long enough that compressed pixel data practically never
matches by chance (about 1 in 10,000 for a 10 MB file)."""
_PIL_FORMATS: Final[dict[str, str]] = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}
_JPEG_QUALITY: Final = 90
_WEBP_QUALITY: Final = 90


class MediaRejected(ValueError):
    """An upload was refused. ``code``: ``too_large`` (413), ``unsupported_media_type`` (415) or
    ``invalid_image`` (422). ``detail`` is short owner-facing text without file content."""

    def __init__(self, code: RejectionCode, detail: str) -> None:
        super().__init__(code)
        self.code: RejectionCode = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class IngestedImage:
    """A re-encoded image: only its pixels survive, as PNG, JPEG or WebP."""

    data: bytes = field(repr=False)
    content_type: str
    width: int
    height: int
    sha256: str


def media_name(
    area: MediaArea, owner_id: str | None, sha256: str, ext: str, *, now: datetime
) -> str:
    """The content-addressed blob name of media in ``area``.

    ``uploads/<YYYY-MM>/<sha256>.<ext>`` (UTC month of ``now``), ``posts/<postId>/...`` and
    ``articles/<articleId>/...`` (``owner_id`` required), ``brand/<sha256>.<ext>``. Raises
    ``ValueError`` for a malformed hash, extension or owner id.
    """
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if ext not in MEDIA_EXTENSIONS.values():
        raise ValueError(f"unsupported media extension {ext!r}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    file = f"{sha256}.{ext}"
    match area:
        case "uploads":
            return f"uploads/{now.astimezone(UTC):%Y-%m}/{file}"
        case "brand":
            return f"brand/{file}"
        case "posts" | "articles":
            if owner_id is None or not _OWNER_ID_RE.fullmatch(owner_id):
                raise ValueError(f"{area} media need the id of the {area[:-1]} they belong to")
            return f"{area}/{owner_id}/{file}"


def sniff_image_type(data: bytes) -> str | None:
    """``image/png``, ``image/jpeg`` or ``image/webp`` from the leading bytes, else ``None``."""
    if data.startswith(_PNG_SIGNATURE):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _png_end(data: bytes) -> int | None:
    """Offset just past the IEND chunk, or ``None`` when the chunk list is truncated."""
    position = len(_PNG_SIGNATURE)
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4], "big")
        chunk_type = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data):
            return None
        if chunk_type == b"IEND":
            return end
        position = end
    return None


def _check_polyglot(data: bytes, content_type: str) -> None:
    if _MARKUP_RE.search(data):
        raise MediaRejected("unsupported_media_type", "The file contains markup.")
    if content_type == "image/png":
        end = _png_end(data)
        if end is not None and end != len(data):
            raise MediaRejected("unsupported_media_type", "The file has data after the image.")
    elif content_type == "image/webp":
        declared = int.from_bytes(data[4:8], "little") + 8
        if declared != len(data):
            raise MediaRejected("unsupported_media_type", "The file has data after the image.")


def to_srgb(image: Image.Image) -> Image.Image:
    """Convert pixels in an embedded colour profile to sRGB; on any problem keep them as is.

    RGB and RGBA only (other modes, and images without a profile, come back unchanged). Sync and
    CPU-bound: call it from a thread. Used by :func:`ingest_image` and by the AI illustrations'
    re-encoding, so a profile is applied to the pixels before every metadata chunk is dropped."""
    icc: object = image.info.get("icc_profile")
    if not isinstance(icc, bytes) or not icc or image.mode not in ("RGB", "RGBA"):
        return image
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        srgb = ImageCms.createProfile("sRGB")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        target = ImageCms.ImageCmsProfile(srgb)  # pyright: ignore[reportUnknownArgumentType]
        converted = ImageCms.profileToProfile(image, source, target, outputMode=image.mode)
    except (ImageCms.PyCMSError, OSError, ValueError, TypeError):
        return image
    return converted if converted is not None else image


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info


def _ingest_sync(data: bytes, max_pixels: int, max_bytes: int) -> IngestedImage:
    if len(data) > max_bytes:
        raise MediaRejected("too_large", f"Uploads are limited to {max_bytes} bytes.")
    content_type = sniff_image_type(data)
    if content_type is None:
        raise MediaRejected("unsupported_media_type", "Upload a PNG, JPEG or WebP image.")
    _check_polyglot(data, content_type)
    too_many = MediaRejected("too_large", f"Images are limited to {max_pixels} pixels.")
    try:
        with Image.open(io.BytesIO(data), formats=[_PIL_FORMATS[content_type]]) as image:
            if image.width * image.height > max_pixels:
                raise too_many
            image.seek(0)
            image.load()
            oriented = ImageOps.exif_transpose(image)
            mode = "RGBA" if content_type != "image/jpeg" and _has_alpha(oriented) else "RGB"
            pixels = to_srgb(oriented.convert(mode) if oriented.mode != mode else oriented)
            if pixels.mode != mode:
                pixels = pixels.convert(mode)
            # A fresh image from raw pixels: no metadata of the upload can travel along.
            clean = Image.frombytes(mode, pixels.size, pixels.tobytes())
    except MediaRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise too_many from None
    except Exception:
        raise MediaRejected("invalid_image", "The image could not be read.") from None
    out = io.BytesIO()
    match content_type:
        case "image/png":
            clean.save(out, format="PNG", optimize=False, compress_level=6)
        case "image/jpeg":
            clean.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=False)
        case _:
            clean.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
    encoded = out.getvalue()
    if len(encoded) > max_bytes:
        raise MediaRejected("too_large", f"Uploads are limited to {max_bytes} bytes.")
    return IngestedImage(
        data=encoded,
        content_type=content_type,
        width=clean.width,
        height=clean.height,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


async def ingest_image(data: bytes, *, max_pixels: int, max_bytes: int) -> IngestedImage:
    """Validate and re-encode an uploaded image (PNG, JPEG or WebP; first frame only).

    Raises :class:`MediaRejected`: ``too_large`` (bytes or pixels over the caps),
    ``unsupported_media_type`` (anything but PNG/JPEG/WebP: SVG, HTML, GIF; markup inside the
    file; data after the image) or ``invalid_image`` (cannot be decoded).
    """
    if max_pixels < 1 or max_bytes < 1:
        raise ValueError("max_pixels and max_bytes must be positive")
    return await asyncio.to_thread(_ingest_sync, bytes(data), max_pixels, max_bytes)


async def store_media(
    blobs: BlobStore,
    data: bytes,
    *,
    role: MediaRole,
    area: MediaArea,
    owner_id: str | None,
    content_type: str,
    now: datetime,
    alt_text: str | None = None,
    filename: str | None = None,
    width: int | None = None,
    height: int | None = None,
    page_count: int | None = None,
) -> MediaRef:
    """Write ``data`` to the ``media`` container under its content-addressed name.

    An existing blob with that name holds the same bytes (``BlobExists`` is fine); a stored hash
    that disagrees means the name was written by something else and raises ``ValueError``.
    """
    if role not in get_args(MediaRole.__value__):
        raise ValueError(f"unknown media role {role!r}")
    ext = MEDIA_EXTENSIONS.get(content_type)
    if ext is None:
        raise ValueError(f"unsupported media content type {content_type!r}")
    payload = bytes(data)
    if not payload:
        raise ValueError("media must not be empty")
    digest = hashlib.sha256(payload).hexdigest()
    name = media_name(area, owner_id, digest, ext, now=now)
    try:
        await blobs.put("media", name, payload, content_type=content_type, overwrite=False)
    except BlobExists:
        existing = await blobs.head("media", name)
        if existing is not None and existing.sha256 and existing.sha256 != digest:
            raise ValueError("a different blob already has this content-addressed name") from None
    return MediaRef.model_validate(
        {
            "role": role,
            "container": "media",
            "name": name,
            "sha256": digest,
            "content_type": content_type,
            "size_bytes": len(payload),
            "width": width,
            "height": height,
            "page_count": page_count,
            "alt_text": alt_text,
            "filename": filename,
            "uploaded_at": to_iso(now),
        }
    )
