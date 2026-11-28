"""v1 images (base64 in Cosmos) and the generated Open Graph / hero cards.

- :func:`decode_v1_image`: an ``images`` document into verified raster bytes (PNG, JPEG, WebP
  or GIF, decoded by Pillow; SVG and anything else refused: published media are served from the
  portfolio's origin). The bytes are kept exactly as v1 stored them.
- :func:`cover_png`: a 1200x630 PNG cut from an image (the OG image of a post with a v1 hero).
- :func:`title_card_png`: a 1200x630 typographic PNG card (title, brand colours, byline) for
  posts without one. Deterministic for a given font and Pillow build.

All functions are synchronous and CPU-bound: call them from a worker thread.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Final

from PIL import Image, ImageDraw, ImageFont, ImageOps

__all__ = [
    "OG_HEIGHT",
    "OG_WIDTH",
    "CardStyle",
    "V1Image",
    "cover_png",
    "decode_v1_image",
    "png_of",
    "title_card_png",
]

OG_WIDTH: Final = 1200
OG_HEIGHT: Final = 630
MAX_V1_IMAGE_PIXELS: Final = 50_000_000
_FORMATS: Final[Mapping[str, tuple[str, str]]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
    "GIF": ("image/gif", "gif"),
}
_MARGIN: Final = 80


@dataclass(frozen=True, slots=True)
class V1Image:
    """A verified v1 image: the stored bytes and what Pillow read from them."""

    data: bytes = field(repr=False)
    content_type: str
    ext: str
    width: int
    height: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CardStyle:
    """Colours (``#RRGGBB``) and byline of generated cards (from the v2 brand kit)."""

    background: str = "#0E2A24"
    text: str = "#F3F5F2"
    accent: str = "#F2A93B"
    muted: str = "#A8C1B8"
    byline: str | None = None


def decode_v1_image(item: Mapping[str, Any]) -> V1Image | None:
    """The image of a v1 ``images`` document, or ``None`` when it is missing, not base64, not
    a PNG/JPEG/WebP/GIF Pillow can decode, or over 50 megapixels."""
    encoded: object = item.get("imageBase64")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        with Image.open(io.BytesIO(data), formats=list(_FORMATS)) as image:
            image_format = image.format
            width, height = image.size
            if image_format not in _FORMATS or width * height > MAX_V1_IMAGE_PIXELS:
                return None
            image.load()
    except Exception:
        return None
    content_type, ext = _FORMATS[image_format]
    return V1Image(
        data=data,
        content_type=content_type,
        ext=ext,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False, compress_level=6)
    return out.getvalue()


def png_of(image: V1Image) -> bytes:
    """The first frame as a PNG without metadata (GIFs for the ``media`` container)."""
    with Image.open(io.BytesIO(image.data)) as source:
        source.seek(0)
        frame = source.convert("RGBA")
    return _png(Image.frombytes("RGBA", frame.size, frame.tobytes()))


def cover_png(image: V1Image, width: int = OG_WIDTH, height: int = OG_HEIGHT) -> bytes:
    """A centred ``width`` x ``height`` crop of ``image`` as an RGB PNG."""
    with Image.open(io.BytesIO(image.data)) as source:
        source.seek(0)
        frame = source.convert("RGB")
    fitted = ImageOps.fit(frame, (width, height), method=Image.Resampling.LANCZOS)
    return _png(Image.frombytes("RGB", fitted.size, fitted.tobytes()))


@functools.cache
def _font_bytes(name: str) -> bytes | None:
    try:
        return resources.files("blogwriter.render").joinpath(f"fonts/{name}").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


def _font(size: int, *, display: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    data = _font_bytes("InterDisplay-Bold.woff2" if display else "Inter-Regular.woff2")
    if data is not None:
        try:
            return ImageFont.truetype(io.BytesIO(data), size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def title_card_png(title: str, style: CardStyle) -> bytes:
    """A 1200x630 card: an accent rule, the title (wrapped, at most four lines) and a byline."""
    card = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), style.background)
    draw = ImageDraw.Draw(card)
    draw.rectangle((_MARGIN, 96, _MARGIN + 96, 104), fill=style.accent)
    text_width = OG_WIDTH - 2 * _MARGIN
    size = 68
    font = _font(size, display=True)
    lines = _wrap(draw, title, font, text_width)
    while len(lines) > 4 and size > 40:
        size -= 6
        font = _font(size, display=True)
        lines = _wrap(draw, title, font, text_width)
    if len(lines) > 4:
        lines = [*lines[:3], lines[3] + " ..."]
    y = 150
    for line in lines:
        draw.text((_MARGIN, y), line, font=font, fill=style.text)
        y += int(size * 1.2)
    if style.byline:
        draw.text(
            (_MARGIN, OG_HEIGHT - 110),
            style.byline,
            font=_font(30, display=False),
            fill=style.muted,
        )
    return _png(card)
