"""The brand headshot: decoded once, re-encoded as a small, metadata-free PNG ``data:`` URI.

The supplied image (PNG, JPEG or WebP, at most 5 MB and 25 MP) is upright-rotated from its EXIF
orientation, centre-cropped to a square (slightly above centre, where faces are), resized to
256 px and saved as PNG with no ancillary chunks (no EXIF, ICC, text or timestamps), so nothing
from the original file reaches a PDF or PNG. The output is deterministic for a given input.

Decoding is CPU-bound: :func:`headshot_data_uri` must be called through ``asyncio.to_thread``.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError

from blogwriter.render.brand import MAX_HEADSHOT_BYTES, sniff_image_type
from blogwriter.render.errors import HeadshotError

__all__ = ["HEADSHOT_PX", "MAX_HEADSHOT_PIXELS", "headshot_data_uri", "prepare_headshot"]

HEADSHOT_PX: Final = 256
MAX_HEADSHOT_PIXELS: Final = 25_000_000
_FORMATS: Final = frozenset({"PNG", "JPEG", "WEBP"})


def prepare_headshot(data: bytes) -> bytes:
    """The re-encoded 256x256 PNG. Raises :class:`HeadshotError`."""
    if len(data) > MAX_HEADSHOT_BYTES or sniff_image_type(data) is None:
        raise HeadshotError("headshot must be a PNG, JPEG or WebP image of at most 5 MB")
    try:
        with Image.open(BytesIO(data), formats=sorted(_FORMATS)) as im:
            width, height = im.size
            if width < 1 or height < 1 or width * height > MAX_HEADSHOT_PIXELS:
                raise HeadshotError("headshot dimensions are out of range (at most 25 MP)")
            im.load()
            upright = ImageOps.exif_transpose(im)
            mode = "RGBA" if "A" in upright.getbands() or "transparency" in upright.info else "RGB"
            square = ImageOps.fit(
                upright.convert(mode),
                (HEADSHOT_PX, HEADSHOT_PX),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.4),
            )
    except HeadshotError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise HeadshotError("headshot could not be decoded") from exc
    clean = Image.new(square.mode, square.size)
    clean.paste(square)
    out = BytesIO()
    clean.save(out, format="PNG", optimize=False)
    return out.getvalue()


@lru_cache(maxsize=8)
def headshot_data_uri(data: bytes) -> str:
    """``data:image/png;base64,...`` for :func:`prepare_headshot` (cached per image)."""
    return "data:image/png;base64," + base64.b64encode(prepare_headshot(data)).decode("ascii")
