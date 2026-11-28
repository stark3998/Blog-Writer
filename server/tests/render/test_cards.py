"""OG images (1200x630) and single-image cards (1080x1350) in Chromium, and the metadata-free
PNG guarantee for every raster the package produces."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from blogwriter.render import CarouselSpec, RenderBrand, Renderer
from blogwriter.render.spec import CoverSlide, DiagramSlide, FrameworkSlide
from blogwriter.render.testing import make_image, png_chunks, png_size, sample_brand

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

TITLE = "Give every AI agent its own identity"
SUBTITLE = "Four controls that turn anonymous tool calls into accountable actions"


def _count_rgb(png: bytes, box: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> int:
    with Image.open(io.BytesIO(png)) as im:
        raw = im.convert("RGB").crop(box).tobytes()
    pixels = (raw[i : i + 3] for i in range(0, len(raw), 3))
    return sum(1 for p in pixels if all(abs(a - b) < 40 for a, b in zip(p, rgb, strict=True)))


async def test_og_image_is_1200_by_630(renderer: Renderer, brand: RenderBrand) -> None:
    png = await renderer.render_og_image(TITLE, SUBTITLE, "AI agents and security", brand)
    assert png_size(png) == (1200, 630)
    bare = await renderer.render_og_image(TITLE, None, None, brand)
    assert png_size(bare) == (1200, 630)
    assert bare != png


async def test_og_image_shows_the_headshot(renderer: Renderer) -> None:
    red = make_image("PNG", size=(200, 200), color=(220, 20, 20))
    with_photo = await renderer.render_og_image(TITLE, None, None, sample_brand(headshot=red))
    without = await renderer.render_og_image(TITLE, None, None, sample_brand())
    byline = (92, 480, 400, 630)
    assert _count_rgb(with_photo, byline, (220, 20, 20)) > 500
    assert _count_rgb(without, byline, (220, 20, 20)) == 0


async def test_image_cards_are_1080_by_1350_for_any_slide(
    renderer: Renderer, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    kinds = (CoverSlide, FrameworkSlide, DiagramSlide)
    slides = [next(s for s in sample_carousel.slides if isinstance(s, kind)) for kind in kinds]
    pngs = [await renderer.render_image_card(slide, brand) for slide in slides]
    for png in pngs:
        assert png_size(png) == (1080, 1350)
    assert len(set(pngs)) == len(pngs)
    assert await renderer.render_image_card(slides[1], brand) == pngs[1]  # deterministic


async def test_rasters_carry_no_metadata_chunks(
    renderer: Renderer, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    """Confidentiality gate: nothing but pixels (no tEXt/iTXt/zTXt/eXIf/tIME/iCCP)."""
    photo = sample_brand(headshot=make_image("JPEG", exif_orientation=3, comment=b"client"))
    og = await renderer.render_og_image("Title", None, None, photo)
    card = await renderer.render_image_card(sample_carousel.slides[0], photo)
    deck = await renderer.render_carousel(sample_carousel, brand)
    diagram = await renderer.render_mermaid_png("flowchart TD\n  A --> B", 300)
    for png in (og, card, deck.pages[0], deck.pages[5], diagram):
        assert set(png_chunks(png)) == {"IHDR", "IDAT", "IEND"}
        assert b"client" not in png
