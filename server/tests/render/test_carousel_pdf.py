"""Carousel output in Chromium: PDF geometry, metadata, active content, size, determinism, the
text layer, page previews, the preview HTML and the module-level API."""

from __future__ import annotations

import copy
import hashlib
import io
import re
from typing import Any, cast

import pytest
import pytest_asyncio
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from blogwriter.render import (
    CarouselSpec,
    DiagramSlide,
    RenderBrand,
    RenderedDeck,
    Renderer,
    render_carousel,
    set_default_renderer,
)
from blogwriter.render.browser import RenderSession, wait_for_fonts
from blogwriter.render.html import SLIDE_H, SLIDE_W
from blogwriter.render.lint import slide_text
from blogwriter.render.pdf import finish_pdf, private_use_chars
from blogwriter.render.service import PDF_HEIGHT, PDF_SCALE, PDF_WIDTH
from blogwriter.render.testing import make_image, pdf_pages_text, png_size, sample_brand

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def rendered(
    renderer: Renderer, sample_carousel: CarouselSpec, brand: RenderBrand
) -> RenderedDeck:
    return await renderer.render_carousel(sample_carousel, brand)


def _rgb(png: bytes, xy: tuple[int, int]) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(png)) as im:
        return cast(tuple[int, int, int], im.convert("RGB").getpixel(xy))


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


async def test_every_page_is_exactly_1080_by_1350_pt(
    rendered: RenderedDeck, sample_carousel: CarouselSpec
) -> None:
    reader = PdfReader(io.BytesIO(rendered.pdf))
    assert len(reader.pages) == len(sample_carousel.slides) == 12
    for page in reader.pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (1080.0, 1350.0)
        assert page.cropbox == page.mediabox


async def test_page_previews_show_the_right_slides(
    rendered: RenderedDeck, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    """Each preview is its own slide: the top margin shows that slide's surface colour."""
    assert len(rendered.pages) == 12
    ink, paper = _hex_rgb(brand.colors.ink), _hex_rgb(brand.colors.paper)
    for png, slide in zip(rendered.pages, sample_carousel.slides, strict=True):
        assert png_size(png) == (SLIDE_W, SLIDE_H)
        expected = ink if slide.type in {"cover", "statement", "stat"} else paper
        assert _rgb(png, (540, 40)) == expected, slide.type
    assert len(set(rendered.pages)) == 12


async def test_2x_previews_leave_the_pdf_unchanged(
    renderer: Renderer, rendered: RenderedDeck, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    hi = await renderer.render_carousel(sample_carousel, brand, preview_scale=2)
    assert png_size(hi.pages[0]) == (2 * SLIDE_W, 2 * SLIDE_H)
    assert hi.pdf == rendered.pdf


async def test_metadata_is_exactly_title_and_author(
    rendered: RenderedDeck, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    reader = PdfReader(io.BytesIO(rendered.pdf))
    assert dict(reader.metadata or {}) == {
        "/Title": sample_carousel.title,
        "/Author": brand.name_mark,
    }
    for needle in (b"/Producer", b"/Creator", b"Skia", b"Chromium", b"pypdf", b"/CreationDate"):
        assert needle not in rendered.pdf, needle
    assert rendered.pdf.startswith(b"%PDF-1.4")


async def test_pdf_has_no_links_or_active_content(rendered: RenderedDeck) -> None:
    reader = PdfReader(io.BytesIO(rendered.pdf))
    root = cast(DictionaryObject, reader.trailer["/Root"].get_object())
    for key in ("/OpenAction", "/AA", "/AcroForm", "/Metadata", "/Names", "/URI"):
        assert key not in root
    for page in reader.pages:
        assert "/Annots" not in page
        assert "/AA" not in page


async def test_sha256_and_warnings(rendered: RenderedDeck) -> None:
    assert rendered.sha256 == hashlib.sha256(rendered.pdf).hexdigest()
    assert rendered.warnings == ()  # the sample deck is lint-clean and its diagram is legible


async def test_twelve_slides_with_three_diagrams_stay_far_below_10_mb(
    renderer: Renderer, sample_spec: dict[str, Any], brand: RenderBrand
) -> None:
    spec = copy.deepcopy(sample_spec)
    diagram = next(s for s in spec["slides"] if s["type"] == "diagram")
    spec["slides"][2] = diagram
    spec["slides"][9] = diagram
    deck = CarouselSpec.model_validate(spec)
    assert sum(isinstance(s, DiagramSlide) for s in deck.slides) == 3
    out = await renderer.render_carousel(deck, brand)
    assert len(PdfReader(io.BytesIO(out.pdf)).pages) == 12
    assert len(out.pdf) < 10 * 1024 * 1024
    assert len(out.pdf) < 1024 * 1024  # in practice ~0.1 MB; flag regressions early


async def test_same_spec_renders_byte_identical_output_in_a_fresh_browser(
    renderer: Renderer, rendered: RenderedDeck, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    """Every job launches its own Chromium; an approval bound to the SHA-256 survives."""
    again = await renderer.render_carousel(sample_carousel, brand)
    assert again.pdf == rendered.pdf
    assert again.sha256 == rendered.sha256
    assert again.pages == rendered.pages


async def test_text_layer_carries_every_word_and_no_private_use_codepoints(
    rendered: RenderedDeck, sample_carousel: CarouselSpec
) -> None:
    assert list(rendered.text) == pdf_pages_text(rendered.pdf)
    n_slides = len(sample_carousel.slides)
    for n, (slide, text) in enumerate(zip(sample_carousel.slides, rendered.text, strict=True), 1):
        assert private_use_chars(text) == [], f"slide {n}: text layer has private-use glyphs"
        page_words = re.findall(r"\S+", text)
        for field_text in slide_text(slide):
            for word in field_text.split():
                assert word in page_words or word in text, f"slide {n}: {word!r} missing"
        assert f"{n} / {n_slides}" in text


async def test_preview_html_is_the_document_the_pdf_is_printed_from(
    renderer: Renderer,
    session: RenderSession,
    rendered: RenderedDeck,
    sample_carousel: CarouselSpec,
    brand: RenderBrand,
) -> None:
    html = await renderer.carousel_preview_html(sample_carousel, brand)
    assert await renderer.carousel_preview_html(sample_carousel, brand) == html  # deterministic
    async with session.static_page(width=SLIDE_W, height=SLIDE_H, scale=2) as page:
        await page.set_content(html, wait_until="load")
        await wait_for_fonts(page)
        raw = await page.pdf(
            width=PDF_WIDTH,
            height=PDF_HEIGHT,
            scale=PDF_SCALE,
            print_background=True,
            prefer_css_page_size=False,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
    pdf, _ = finish_pdf(raw, title=sample_carousel.title, author=brand.name_mark, expected_pages=12)
    assert pdf == rendered.pdf


async def test_headshot_is_drawn_on_the_cover_only(
    renderer: Renderer, rendered: RenderedDeck, sample_carousel: CarouselSpec
) -> None:
    red = make_image("PNG", size=(300, 300), color=(220, 20, 20))
    out = await renderer.render_carousel(sample_carousel, sample_brand(headshot=red))
    assert out.pages[0] != rendered.pages[0]
    assert out.pages[1:] == rendered.pages[1:]
    # The photo sits at the left of the byline, at the foot of the cover's content box.
    reds = 0
    with Image.open(io.BytesIO(out.pages[0])) as im:
        rgb = im.convert("RGB")
        for y in range(900, 1200, 4):
            for x in range(132, 260, 4):
                r, g, b = cast(tuple[int, int, int], rgb.getpixel((x, y)))
                reds += r > 180 and g < 60 and b < 60
    assert reds > 100
    assert b"/Subtype /Image" in out.pdf or b"/Subtype/Image" in out.pdf


async def test_module_level_api_uses_the_default_renderer(
    sample_carousel: CarouselSpec, brand: RenderBrand, rendered: RenderedDeck
) -> None:
    set_default_renderer(None)  # the first call creates and starts one from BW_RENDER_*
    try:
        deck = await render_carousel(sample_carousel, brand)
    finally:
        set_default_renderer(None)
    assert deck.pdf == rendered.pdf
