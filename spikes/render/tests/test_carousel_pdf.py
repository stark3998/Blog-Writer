"""PDF output: geometry, metadata, no active content, size, determinism, faithful text layer."""

from __future__ import annotations

import io
import json
import re
from typing import cast

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from render import DeckSpec, RenderedDeck, RenderSession, lint_deck, render_carousel, render_carousel_in
from render.lint import slide_text
from render.spec import DiagramSlide

from .conftest import pdf_pages_text, png_size, private_use_chars

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
async def rendered(session: RenderSession, sample_deck: DeckSpec) -> RenderedDeck:
    return await render_carousel_in(session, sample_deck)


def test_sample_deck_is_lint_clean(sample_deck: DeckSpec) -> None:
    assert lint_deck(sample_deck) == []
    assert len(sample_deck.slides) == 12
    assert {s.type for s in sample_deck.slides} == {
        "cover", "statement", "list", "steps", "framework", "diagram", "stat", "quote", "recap", "sources",
    }  # fmt: skip


async def test_every_page_is_exactly_1080_by_1350(rendered: RenderedDeck, sample_deck: DeckSpec) -> None:
    reader = PdfReader(io.BytesIO(rendered.pdf))
    assert len(reader.pages) == len(sample_deck.slides) == 12
    for page in reader.pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (1080.0, 1350.0)
        assert page.cropbox == page.mediabox


async def test_cover_png_is_first_slide_at_2x(rendered: RenderedDeck) -> None:
    assert png_size(rendered.cover_png) == (2160, 2700)


async def test_metadata_is_exactly_title_and_author(rendered: RenderedDeck, sample_deck: DeckSpec) -> None:
    reader = PdfReader(io.BytesIO(rendered.pdf))
    assert dict(reader.metadata or {}) == {
        "/Title": sample_deck.title,
        "/Author": sample_deck.brand.author_name,
    }
    for needle in (b"/Producer", b"/Creator", b"Skia", b"Chromium", b"pypdf", b"/CreationDate", b"/ModDate"):
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


async def test_twelve_slides_stay_far_below_10_mb(
    session: RenderSession, sample_spec: dict[str, object]
) -> None:
    # Worst realistic case: three diagram slides in a 12-slide deck.
    spec = json.loads(json.dumps(sample_spec))
    diagram = next(s for s in spec["slides"] if s["type"] == "diagram")  # type: ignore[index]
    spec["slides"][2] = diagram  # type: ignore[index]
    spec["slides"][9] = diagram  # type: ignore[index]
    deck = DeckSpec.model_validate(spec)
    assert sum(isinstance(s, DiagramSlide) for s in deck.slides) == 3
    out = await render_carousel_in(session, deck)
    assert len(PdfReader(io.BytesIO(out.pdf)).pages) == 12
    assert len(out.pdf) < 10 * 1024 * 1024
    assert len(out.pdf) < 1024 * 1024  # in practice ~0.1 MB; flag regressions early


async def test_same_spec_renders_byte_identical_pdf_in_a_fresh_browser(
    rendered: RenderedDeck, sample_deck: DeckSpec
) -> None:
    async with RenderSession() as fresh:
        again = await render_carousel_in(fresh, sample_deck)
    assert again.pdf == rendered.pdf  # approval bound to SHA-256 survives a re-render
    assert again.cover_png == rendered.cover_png


async def test_text_layer_carries_every_word_and_no_private_use_codepoints(
    rendered: RenderedDeck, sample_deck: DeckSpec
) -> None:
    pages = pdf_pages_text(rendered.pdf)
    for n, (slide, text) in enumerate(zip(sample_deck.slides, pages, strict=True), start=1):
        assert private_use_chars(text) == [], f"slide {n}: text layer has private-use glyphs"
        page_words = re.findall(r"\S+", text)
        for field_text in slide_text(slide):
            for word in field_text.split():
                assert word in page_words or word in text, f"slide {n}: {word!r} missing from text layer"
        assert f"{n} / {len(sample_deck.slides)}" in text


async def test_top_level_function_takes_json_and_returns_pdf_and_png(sample_spec: dict[str, object]) -> None:
    pdf, png = await render_carousel(json.dumps(sample_spec))
    assert pdf.startswith(b"%PDF-")
    assert png_size(png) == (2160, 2700)
