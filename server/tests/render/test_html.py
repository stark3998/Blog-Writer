"""The self-contained documents (no browser): CSP, markup structure, the XSS corpus, the OpenType
policy, image cards, OG images and the raster page."""

from __future__ import annotations

import pytest

from blogwriter.render import PREVIEW_CSP, CarouselSpec, OgImageSpec
from blogwriter.render.html import (
    deck_html,
    fit,
    image_card_html,
    image_page_html,
    is_dense,
    og_html,
)
from blogwriter.render.spec import (
    CoverSlide,
    DiagramSlide,
    FrameworkSlide,
    ListSlide,
    StatementSlide,
)
from blogwriter.render.testing import (
    hostile_brand,
    hostile_spec,
    markup_problems,
    markup_tags,
    sample_brand,
)


def check_structure(html: str, *, images: int) -> None:
    assert markup_problems(html, images=images) == []


def test_hostile_text_in_every_field_stays_inert_markup() -> None:
    spec = CarouselSpec.model_validate(hostile_spec(diagram=False))
    html = deck_html(spec, hostile_brand())
    check_structure(html, images=0)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html  # escaped, not dropped
    assert "&amp;lt;script&amp;gt;" in html  # an already-escaped payload is escaped again


def test_document_carries_the_no_script_csp() -> None:
    spec = CarouselSpec.model_validate(hostile_spec(diagram=False))
    tags = markup_tags(deck_html(spec, sample_brand()))
    csp = [a for t, a in tags if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"]
    assert [a["content"] for a in csp] == [PREVIEW_CSP]
    assert "script-src" not in PREVIEW_CSP
    assert PREVIEW_CSP.startswith("default-src 'none'")


def test_opentype_policy_keeps_calt_off_and_tnum_out() -> None:
    """Spike finding: calt and tnum reach the PDF text layer as Private Use Area code points."""
    spec = CarouselSpec.model_validate(hostile_spec(diagram=False))
    for html in (
        deck_html(spec, sample_brand()),
        og_html(OgImageSpec(title="t"), sample_brand()),
    ):
        assert '"calt" 0' in html
        assert '"tnum"' not in html
        assert "'tnum'" not in html
        assert "tabular-nums" not in html


def test_diagram_slides_need_a_rendered_data_uri() -> None:
    spec = CarouselSpec.model_validate(hostile_spec(diagram=True))
    with pytest.raises(ValueError, match="not been rendered"):
        deck_html(spec, sample_brand())
    with pytest.raises(ValueError, match="not been rendered"):
        deck_html(spec, sample_brand(), diagram_uris={6: "https://evil.example/x.svg"})
    html = deck_html(spec, sample_brand(), diagram_uris={6: "data:image/svg+xml;base64,PHN2Zy8+"})
    check_structure(html, images=1)


def test_headshot_uri_must_be_a_png_data_uri() -> None:
    spec = CarouselSpec.model_validate({"title": "t", "slides": [{"type": "cover", "hook": "h"}]})
    with pytest.raises(ValueError, match="headshot_uri"):
        deck_html(spec, sample_brand(), headshot_uri="https://evil.example/me.png")
    html = deck_html(spec, sample_brand(), headshot_uri="data:image/png;base64,AAAA")
    check_structure(html, images=1)
    assert 'class="byline__photo" src="data:image/png;base64,AAAA" alt=""' in html


def test_slides_are_numbered_and_image_cards_are_not() -> None:
    spec = CarouselSpec.model_validate(
        {"title": "t", "slides": [{"type": "statement", "statement": "s"}] * 3}
    )
    html = deck_html(spec, sample_brand())
    for n in (1, 2, 3):
        assert f'<p class="foot__num">{n} / 3</p>' in html
    card = image_card_html(StatementSlide(statement="s"), sample_brand(disclaimer="Views mine"))
    assert 'class="foot__num"' not in card
    assert 'style="height:100.000%"' in card
    assert "Views mine" in card


def test_image_card_accepts_any_slide_type() -> None:
    card = image_card_html(CoverSlide(hook="Hook"), sample_brand(), lang="en-GB")
    assert '<html lang="en-GB">' in card
    diagram = DiagramSlide(title="t", mermaid="flowchart TD\n A-->B", alt="a")
    with pytest.raises(ValueError, match="not been rendered"):
        image_card_html(diagram, sample_brand())


def test_og_document_has_title_subtitle_pillar_and_byline() -> None:
    spec = OgImageSpec(title="Title", subtitle="Subtitle", pillar_label="Pillar")
    html = og_html(spec, sample_brand(), headshot_uri="data:image/png;base64,AAAA")
    for text in ("Title", "Subtitle", "Pillar", "Avery Quinn", "example.com"):
        assert text in html
    assert f"font-size:{fit('Title', 'og_title_sub')}px" in html
    assert 'class="og__photo"' in html
    bare = og_html(OgImageSpec(title="Title"), sample_brand(site_name=None))
    assert 'class="og__subtitle"' not in bare
    assert 'class="og__photo"' not in bare
    assert 'class="og__site"' not in bare
    assert f"font-size:{fit('Title', 'og_title')}px" in bare


def test_raster_page_accepts_only_data_images_and_hex_backgrounds() -> None:
    with pytest.raises(ValueError, match="data:"):
        image_page_html("https://evil.example/x.svg", 10, 10, "#FFFFFF")
    with pytest.raises(ValueError, match="RRGGBB"):
        image_page_html("data:image/svg+xml;base64,AA", 10, 10, "red;background:url(x)")
    assert "data:image/svg+xml;base64,AA" in image_page_html(
        "data:image/svg+xml;base64,AA", 10, 10, "#FFFFFF"
    )


def test_density_steps_only_long_list_steps_layered_and_sources_slides(
    sample_carousel: CarouselSpec,
) -> None:
    assert not any(is_dense(s) for s in sample_carousel.slides)  # typical decks: full size
    long_list = ListSlide(title="t", items=["x " * 50] * 5)
    assert is_dense(long_list)
    assert not is_dense(ListSlide(title="t" * 70, items=["short item"] * 5))
    two_by_two = FrameworkSlide.model_validate(
        {"title": "t", "layout": "2x2", "cells": [{"label": "l" * 28, "text": "t" * 64}] * 4}
    )
    assert not is_dense(two_by_two)  # its cells are measured boxes of their own
    spec = CarouselSpec(title="t", slides=[long_list])
    assert 'class="slide slide--list slide--dense on-paper"' in deck_html(spec, sample_brand())


@pytest.mark.parametrize(
    ("text", "role", "px"),
    [
        ("x" * 28, "hook", 112),
        ("x" * 29, "hook", 96),
        ("x" * 80, "hook", 80),
        ("x" * 200, "statement", 56),
        ("x" * 4, "stat", 280),
        ("x" * 110, "og_title", 58),
        ("x" * 110, "og_title_sub", 52),
    ],
)
def test_type_steps_down_with_length(text: str, role: str, px: int) -> None:
    assert fit(text, role) == px
    assert px >= 28
