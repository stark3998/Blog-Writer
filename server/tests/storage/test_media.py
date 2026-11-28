"""Image ingest (re-encoding, metadata stripping, refusals), media names and store_media."""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from datetime import UTC, datetime
from typing import get_args

import pytest
from PIL import Image, ImageCms, PngImagePlugin

from blogwriter.domain.common import MediaRef
from blogwriter.domain.testing import MemoryBlobStore
from blogwriter.storage.media import (
    MEDIA_AREAS,
    IngestedImage,
    MediaRejected,
    MediaRole,
    ingest_image,
    media_name,
    sniff_image_type,
    store_media,
)

MAX_PIXELS = 12_000_000
MAX_BYTES = 10 * 1024 * 1024
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SHA = "a" * 64


def encode(image: Image.Image, image_format: str, **params: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=image_format, **params)
    return out.getvalue()


async def ingest(
    data: bytes, *, max_pixels: int = MAX_PIXELS, max_bytes: int = MAX_BYTES
) -> IngestedImage:
    return await ingest_image(data, max_pixels=max_pixels, max_bytes=max_bytes)


# --------------------------------------------------------------------------------------------
# Metadata is stripped
# --------------------------------------------------------------------------------------------


async def test_the_golden_jpeg_carries_metadata(golden_jpeg: bytes) -> None:
    with Image.open(io.BytesIO(golden_jpeg)) as image:
        exif = image.getexif()
        assert exif.get(0x0112) == 6
        assert exif.get_ifd(0x8825)  # GPS
        assert "icc_profile" in image.info
        assert "xmp" in image.info
        assert "comment" in image.info
    for marker in (b"Exif", b"GoldenCam", b"golden-author", b"golden-comment", b"ICC_PROFILE"):
        assert marker in golden_jpeg


async def test_exif_gps_xmp_icc_and_comments_are_stripped(golden_jpeg: bytes) -> None:
    result = await ingest(golden_jpeg)
    assert result.content_type == "image/jpeg"
    for marker in (
        b"Exif",
        b"GoldenCam",
        b"Model X",
        b"golden-author",
        b"xmpmeta",
        b"golden-comment",
        b"ICC_PROFILE",
        b"http://ns.adobe.com",
    ):
        assert marker not in result.data
    with Image.open(io.BytesIO(result.data)) as clean:
        assert dict(clean.getexif()) == {}
        assert not clean.getexif().get_ifd(0x8825)
        assert {"exif", "xmp", "comment", "icc_profile"}.isdisjoint(clean.info)
    assert result.sha256 == hashlib.sha256(result.data).hexdigest()


async def test_the_exif_orientation_is_applied_before_it_is_dropped(golden_jpeg: bytes) -> None:
    result = await ingest(golden_jpeg)
    # 64x32 stored with "rotate 90 CW": the reader sees 32x64, orange on top.
    assert (result.width, result.height) == (32, 64)
    with Image.open(io.BytesIO(result.data)) as clean:
        top = clean.convert("RGB").getpixel((16, 4))
        bottom = clean.convert("RGB").getpixel((16, 60))
    assert isinstance(top, tuple)
    assert isinstance(bottom, tuple)
    assert top[0] > 200  # orange
    assert bottom[0] < 100  # dark green


async def test_png_text_chunks_and_profiles_are_dropped() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "png-secret-text")
    info.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta>png-xmp</x:xmpmeta>")
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    source = encode(
        Image.new("RGBA", (10, 6), (10, 20, 30, 128)), "PNG", pnginfo=info, icc_profile=srgb
    )
    assert b"png-secret-text" in source
    result = await ingest(source)
    assert result.content_type == "image/png"
    assert b"png-secret-text" not in result.data
    assert b"png-xmp" not in result.data
    assert b"iCCP" not in result.data
    with Image.open(io.BytesIO(result.data)) as clean:
        assert clean.mode == "RGBA"
        assert clean.getpixel((0, 0)) == (10, 20, 30, 128)


async def test_webp_is_re_encoded_and_only_the_first_frame_survives() -> None:
    frames = [Image.new("RGB", (8, 8), color) for color in ("#ff0000", "#00ff00", "#0000ff")]
    out = io.BytesIO()
    frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:], lossless=True)
    result = await ingest(out.getvalue())
    assert result.content_type == "image/webp"
    with Image.open(io.BytesIO(result.data)) as clean:
        assert getattr(clean, "n_frames", 1) == 1
        pixel = clean.convert("RGB").getpixel((4, 4))
    assert isinstance(pixel, tuple)
    assert pixel[0] > 200


async def test_re_encoding_is_deterministic(golden_jpeg: bytes) -> None:
    first = await ingest(golden_jpeg)
    second = await ingest(golden_jpeg)
    assert first.data == second.data
    assert first.sha256 == second.sha256


# --------------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------------

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><html><body><script>alert(1)</script></body></html>"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _png_with_text(text: bytes) -> bytes:
    png = encode(Image.new("RGB", (4, 4), "#123456"), "PNG")
    iend = png.rindex(b"IEND") - 4
    return png[:iend] + _png_chunk(b"tEXt", b"Comment\x00" + text) + png[iend:]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(SVG, id="svg"),
        pytest.param(b'<?xml version="1.0"?>' + SVG, id="svg-xml-prolog"),
        pytest.param(HTML, id="html"),
        pytest.param(b"GIF89a" + b"\x00" * 32, id="gif"),
        pytest.param(b"%PDF-1.7\n", id="pdf"),
        pytest.param(b"", id="empty"),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "PNG") + b"<html><script>alert(1)</script>",
            id="png-with-trailing-html",
        ),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "PNG") + b"\x00trailing",
            id="png-with-trailing-bytes",
        ),
        pytest.param(_png_with_text(b"<script>alert(1)</script>"), id="png-script-in-text-chunk"),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "JPEG", comment=b"<svg onload=alert(1)>"),
            id="jpeg-svg-in-comment",
        ),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "JPEG") + b"<!DOCTYPE html><body>x</body>",
            id="jpeg-gifar-style",
        ),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "WEBP") + b"<html>", id="webp-with-trailing-html"
        ),
        pytest.param(
            encode(Image.new("RGB", (4, 4)), "JPEG", comment=b"javascript:alert(1)"),
            id="jpeg-javascript-url",
        ),
    ],
)
async def test_svg_html_and_polyglots_are_refused(data: bytes) -> None:
    with pytest.raises(MediaRejected) as caught:
        await ingest(data)
    assert caught.value.code == "unsupported_media_type"
    assert b"alert" not in caught.value.detail.encode()


async def test_images_over_the_pixel_cap_are_refused() -> None:
    big = encode(Image.new("L", (4000, 3001)), "PNG")  # 12,004,000 pixels
    with pytest.raises(MediaRejected) as caught:
        await ingest(big)
    assert caught.value.code == "too_large"
    ok = await ingest(encode(Image.new("L", (4000, 3000)), "PNG"))  # exactly 12 MP
    assert (ok.width, ok.height) == (4000, 3000)


async def test_a_decompression_bomb_header_is_refused() -> None:
    png = encode(Image.new("L", (1, 1)), "PNG")
    # Rewrite IHDR to claim 100,000 x 100,000 pixels (CRC fixed so the header parses).
    ihdr = png.index(b"IHDR")
    header = struct.pack(">II", 100_000, 100_000) + png[ihdr + 12 : ihdr + 17]
    bomb = (
        png[: ihdr - 4]
        + struct.pack(">I", 13)
        + b"IHDR"
        + header
        + struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xFFFFFFFF)
        + png[ihdr + 21 :]
    )
    with pytest.raises(MediaRejected) as caught:
        await ingest(bomb)
    assert caught.value.code == "too_large"


async def test_bytes_over_the_cap_are_refused() -> None:
    data = encode(Image.new("RGB", (64, 64), "#336699"), "PNG")
    with pytest.raises(MediaRejected) as caught:
        await ingest(data, max_bytes=len(data) - 1)
    assert caught.value.code == "too_large"


async def test_a_truncated_image_is_invalid() -> None:
    data = encode(Image.new("RGB", (64, 64), "#336699"), "JPEG")
    with pytest.raises(MediaRejected) as caught:
        await ingest(data[:40])
    assert caught.value.code == "invalid_image"


async def test_ingest_validates_its_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        await ingest_image(b"x", max_pixels=0, max_bytes=1)


def test_sniffing_reads_bytes_not_names() -> None:
    assert sniff_image_type(encode(Image.new("RGB", (2, 2)), "PNG")) == "image/png"
    assert sniff_image_type(encode(Image.new("RGB", (2, 2)), "JPEG")) == "image/jpeg"
    assert sniff_image_type(encode(Image.new("RGB", (2, 2)), "WEBP")) == "image/webp"
    assert sniff_image_type(SVG) is None
    assert sniff_image_type(b"RIFF\x00\x00\x00\x00WAVE") is None


# --------------------------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------------------------


def test_media_names_are_content_addressed_per_area() -> None:
    assert media_name("uploads", None, SHA, "png", now=NOW) == f"uploads/2026-11/{SHA}.png"
    assert media_name("posts", "post-123abc", SHA, "pdf", now=NOW) == f"posts/post-123abc/{SHA}.pdf"
    assert media_name("articles", "art-9f", SHA, "svg", now=NOW) == f"articles/art-9f/{SHA}.svg"
    assert media_name("brand", None, SHA, "webp", now=NOW) == f"brand/{SHA}.webp"
    assert MEDIA_AREAS == ("uploads", "posts", "articles", "brand")


def test_upload_months_are_utc() -> None:
    from zoneinfo import ZoneInfo

    late = datetime(2026, 11, 30, 23, 30, tzinfo=ZoneInfo("America/New_York"))  # Dec 1 UTC
    assert media_name("uploads", None, SHA, "png", now=late).startswith("uploads/2026-12/")


@pytest.mark.parametrize(
    ("area", "owner", "sha", "ext"),
    [
        ("posts", None, SHA, "png"),
        ("articles", "../x", SHA, "png"),
        ("posts", "Post-Upper", SHA, "png"),
        ("posts", "post/1", SHA, "png"),
        ("uploads", None, "A" * 64, "png"),
        ("uploads", None, SHA[:-1], "png"),
        ("uploads", None, SHA, "html"),
        ("uploads", None, SHA, "png/../x"),
    ],
)
def test_media_names_refuse_bad_parts(area: str, owner: str | None, sha: str, ext: str) -> None:
    assert area in MEDIA_AREAS
    with pytest.raises(ValueError):  # noqa: PT011 - several messages
        media_name(area, owner, sha, ext, now=NOW)  # pyright: ignore[reportArgumentType]


def test_media_name_needs_an_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        media_name("uploads", None, SHA, "png", now=datetime(2026, 11, 26))


def test_media_roles_match_the_media_ref_model() -> None:
    annotation = MediaRef.model_fields["role"].annotation
    assert annotation is MediaRole  # one definition: domain.common.MediaRole
    assert "headshot" in get_args(MediaRole.__value__)


# --------------------------------------------------------------------------------------------
# store_media
# --------------------------------------------------------------------------------------------


async def test_store_media_is_content_addressed_and_idempotent() -> None:
    blobs = MemoryBlobStore()
    data = encode(Image.new("RGB", (3, 2), "#abcdef"), "PNG")
    ref = await store_media(
        blobs,
        data,
        role="headshot",
        area="brand",
        owner_id=None,
        content_type="image/png",
        now=NOW,
        alt_text="Headshot",
        width=3,
        height=2,
    )
    digest = hashlib.sha256(data).hexdigest()
    assert ref.name == f"brand/{digest}.png"
    assert ref.sha256 == digest
    assert ref.container == "media"
    assert ref.size_bytes == len(data)
    assert ref.uploaded_at == "2026-11-26T12:00:00.000000Z"
    assert ref.url == f"/api/media/brand/{digest}.png"
    again = await store_media(
        blobs,
        data,
        role="headshot",
        area="brand",
        owner_id=None,
        content_type="image/png",
        now=NOW,
    )
    assert again.name == ref.name
    assert await blobs.get("media", ref.name) == data
    assert len(blobs.blobs) == 1


async def test_store_media_refuses_a_different_blob_under_the_same_name() -> None:
    blobs = MemoryBlobStore()
    data = b"%PDF-1.7 bytes"
    name = f"posts/post-1/{hashlib.sha256(data).hexdigest()}.pdf"
    await blobs.put("media", name, b"something else", content_type="application/pdf")
    with pytest.raises(ValueError, match="different blob"):
        await store_media(
            blobs,
            data,
            role="carousel_pdf",
            area="posts",
            owner_id="post-1",
            content_type="application/pdf",
            now=NOW,
        )


async def test_store_media_refuses_unknown_types_and_empty_data() -> None:
    blobs = MemoryBlobStore()
    with pytest.raises(ValueError, match="content type"):
        await store_media(
            blobs,
            b"x",
            role="image",
            area="brand",
            owner_id=None,
            content_type="text/html",
            now=NOW,
        )
    with pytest.raises(ValueError, match="empty"):
        await store_media(
            blobs,
            b"",
            role="image",
            area="brand",
            owner_id=None,
            content_type="image/png",
            now=NOW,
        )
    assert blobs.blobs == {}
