"""Layout guarantees in Chromium: the longest allowed text fits, overflow fails closed, a 28px
type floor for HTML text, and the diagram label floor (warn below 22px, block below 18px)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from blogwriter.render import (
    CarouselSpec,
    DiagramLabelError,
    LayoutOverflowError,
    OgImageSpec,
    RenderBrand,
    Renderer,
)
from blogwriter.render import html as html_mod
from blogwriter.render.browser import RenderSession, find_overflows, wait_for_fonts
from blogwriter.render.html import (
    OG_H,
    OG_W,
    SLIDE_H,
    SLIDE_W,
    deck_html,
    image_card_html,
    is_dense,
    og_html,
)
from blogwriter.render.mermaid import MermaidBundle, MermaidRenderer
from blogwriter.render.spec import DiagramSlide, StepsSlide
from blogwriter.render.testing import make_image, sample_brand

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

# Realistic but wide vocabulary for the domain (long words, capitals, digits).
_WORDS = (
    "Authorization", "workload", "identities", "Governance", "privileged", "federation", "Okta",
    "Entra", "conditional", "access", "MITRE", "ATLAS", "evidence", "per-call", "token",
    "exchange", "Microsoft", "agentic", "boundary", "attestation", "2,000", "applications",
    "least-privilege", "reviews",
)  # fmt: skip


def filler(n: int, seed: int) -> str:
    words = itertools.cycle(_WORDS[seed % len(_WORDS) :] + _WORDS[: seed % len(_WORDS)])
    out = ""
    for w in words:
        nxt = f"{out} {w}".strip()
        if len(nxt) > n:
            break
        out = nxt
    return out if len(out) >= n - 12 else (out + " " + "x" * (n - len(out) - 1))[:n]


def worst_case_spec(offset: int = 0) -> dict[str, Any]:
    """Every field at its maximum length; ``offset`` rotates the vocabulary (line breaks move)."""
    s = itertools.count(1 + offset)

    def f(n: int) -> str:
        return filler(n, next(s))

    return {
        "title": f(100),
        "slides": [
            {"type": "cover", "kicker": f(40), "hook": f(80), "subtitle": f(110)},
            {"type": "statement", "statement": f(200), "note": f(140)},
            {"type": "list", "title": f(70), "items": [f(100) for _ in range(5)]},
            {"type": "steps", "title": f(70),
             "steps": [{"title": f(48), "detail": f(90)} for _ in range(4)]},
            {"type": "framework", "title": f(70), "layout": "2x2", "x_axis": f(28), "y_axis": f(28),
             "cells": [{"label": f(28), "text": f(64)} for _ in range(4)]},
            {"type": "framework", "title": f(70), "layout": "layered",
             "cells": [{"label": f(28), "text": f(64)} for _ in range(5)]},
            {"type": "diagram", "title": f(70), "alt": f(250), "caption": f(120),
             "mermaid": "flowchart TD\n  A[Agent] --> B[Gateway] --> C[Tool]"},
            {"type": "stat", "value": "$1,234.5M", "label": f(90), "context": f(140),
             "source": f(90)},
            {"type": "quote", "quote": f(200), "attribution": f(48), "role": f(64)},
            {"type": "recap", "title": f(60), "items": [f(90) for _ in range(5)]},
            {"type": "sources", "title": f(40),
             "sources": [{"publisher": f(40), "title": f(100), "date": f(16)} for _ in range(5)]},
        ],
    }  # fmt: skip


def below_density_spec(offset: int = 0) -> dict[str, Any]:
    """The longest body text that is still set at full size (just under DENSE_BODY_CHARS)."""
    s = itertools.count(1 + offset)

    def f(n: int) -> str:
        return filler(n, next(s))

    return {
        "title": f(100),
        "slides": [
            {"type": "list", "title": f(70), "items": [f(100), f(100), f(95), "a", "b"]},
            {"type": "steps", "title": f(70), "steps": [
                {"title": f(48), "detail": f(90)}, {"title": f(48), "detail": f(90)},
                {"title": f(42)}, {"title": "x"}]},
            {"type": "framework", "title": f(70), "layout": "layered", "cells": [
                {"label": f(28), "text": f(64)}, {"label": f(28), "text": f(64)},
                {"label": f(28), "text": f(24)}, {"label": "a", "text": "b"},
                {"label": "c", "text": "d"}]},
            {"type": "sources", "title": f(40), "sources": [
                {"publisher": f(40), "title": f(100), "date": f(16)},
                {"publisher": f(40), "title": f(100), "date": f(16)},
                {"publisher": f(40), "title": f(24)}, {"publisher": "a", "title": "b"},
                {"publisher": "c", "title": "d"}]},
        ],
    }  # fmt: skip


def worst_case_brand() -> RenderBrand:
    return sample_brand(
        name_mark=filler(48, 40),
        headline=filler(64, 41),
        disclaimer=filler(40, 42),
        site_name=filler(40, 43),
        headshot=make_image("JPEG", size=(800, 600)),
    )


def chain(n: int) -> str:
    edges = (f"  N{i}[Step {i}] --> N{i + 1}[Step {i + 1}]" for i in range(n - 1))
    return "flowchart LR\n" + "\n".join(edges)


def one_diagram(mermaid: str) -> CarouselSpec:
    slide = {"type": "diagram", "title": "One call, three checks", "mermaid": mermaid, "alt": "a"}
    return CarouselSpec.model_validate({"title": "t", "slides": [slide]})


async def test_longest_allowed_text_in_every_field_still_fits(renderer: Renderer) -> None:
    deck = CarouselSpec.model_validate(worst_case_spec())
    out = await renderer.render_carousel(deck, worst_case_brand())  # raises if anything clips
    assert out.pdf.startswith(b"%PDF-")


#: Rotations that overflowed by 15-56px before dense slides existed (list, steps, layered
#: framework, sources), plus neighbours.
ROTATIONS = (0, 9, 10, 16, 18, 20, 42)


@pytest.mark.parametrize(
    ("make", "dense"),
    [(worst_case_spec, True), (below_density_spec, False)],
    ids=["maximum lengths (dense)", "just below the density threshold"],
)
async def test_longest_text_fits_whatever_the_line_breaks(
    session: RenderSession, make: Any, dense: bool
) -> None:
    brand = worst_case_brand()
    async with session.static_page(width=SLIDE_W, height=SLIDE_H, scale=2) as page:
        for offset in ROTATIONS:
            spec: dict[str, Any] = make(offset)
            spec["slides"] = [s for s in spec["slides"] if s["type"] != "diagram"]
            deck = CarouselSpec.model_validate(spec)
            if not dense:
                assert not any(is_dense(s) for s in deck.slides)
            await page.set_content(deck_html(deck, brand), wait_until="load")
            await wait_for_fonts(page)
            assert await find_overflows(page) == [], offset


async def test_longest_og_text_still_fits(renderer: Renderer) -> None:
    png = await renderer.render_og_image(
        filler(110, 3), filler(140, 4), filler(40, 5), worst_case_brand()
    )
    assert png.startswith(b"\x89PNG")
    png = await renderer.render_og_image(filler(110, 6), None, filler(40, 7), worst_case_brand())
    assert png.startswith(b"\x89PNG")


async def test_longest_text_fits_every_image_card(renderer: Renderer) -> None:
    deck = CarouselSpec.model_validate(worst_case_spec())
    for slide in deck.slides:
        if isinstance(slide, StepsSlide | DiagramSlide):
            png = await renderer.render_image_card(slide, worst_case_brand())
            assert png.startswith(b"\x89PNG")


async def test_overflow_fails_closed_instead_of_clipping(
    renderer: Renderer,
    sample_carousel: CarouselSpec,
    brand: RenderBrand,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(html_mod.FIT_STEPS, "statement", ((), 400))  # absurd size: overflow
    with pytest.raises(LayoutOverflowError) as exc:
        await renderer.render_carousel(sample_carousel, brand)
    assert any(o.startswith("slide 2 body") for o in exc.value.overflows)


async def test_unbreakable_footer_text_fails_closed_instead_of_clipping(
    renderer: Renderer, sample_carousel: CarouselSpec
) -> None:
    """Spike finding: the footer was not measured, so this name was silently clipped."""
    brand = sample_brand(name_mark="W" * 48, disclaimer="W" * 40)
    with pytest.raises(LayoutOverflowError) as exc:
        await renderer.render_carousel(sample_carousel, brand)
    assert any(" footer " in o for o in exc.value.overflows), exc.value.overflows


MIN_FONT_JS = """() => {
  const sizes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim()) continue;
    const el = node.parentElement;
    sizes.push([parseFloat(getComputedStyle(el).fontSize), el.className,
                node.textContent.trim().slice(0, 20)]);
  }
  return sizes;
}"""


async def test_no_html_text_is_smaller_than_28px(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    deck = CarouselSpec.model_validate(worst_case_spec())
    brand = worst_case_brand()
    async with MermaidRenderer(session, bundle) as mr:
        uris = {
            i: (await mr.render(s.mermaid, brand.colors)).data_uri
            for i, s in enumerate(deck.slides)
            if isinstance(s, DiagramSlide)
        }
    documents = [
        (deck_html(deck, brand, diagram_uris=uris), (SLIDE_W, SLIDE_H)),
        (image_card_html(deck.slides[1], brand), (SLIDE_W, SLIDE_H)),
        (
            og_html(OgImageSpec(title="Title", subtitle="Sub", pillar_label="P"), brand),
            (OG_W, OG_H),
        ),
    ]
    for html, (w, h) in documents:
        async with session.static_page(width=w, height=h) as page:
            await page.set_content(html)
            sizes: list[list[Any]] = await page.evaluate(MIN_FONT_JS)
        assert sizes
        too_small = [s for s in sizes if s[0] < 28]
        assert too_small == []


async def test_og_fit_check_sees_the_whole_card(session: RenderSession) -> None:
    og = OgImageSpec(title=filler(110, 3), subtitle=filler(140, 9), pillar_label=filler(40, 5))
    async with session.static_page(width=OG_W, height=OG_H) as page:
        await page.set_content(og_html(og, worst_case_brand()))
        await wait_for_fonts(page)
        assert await find_overflows(page) == []


async def test_small_diagram_labels_warn(renderer: Renderer, brand: RenderBrand) -> None:
    """A 5-node LR chain is scaled to ~0.71 on a slide: 26px labels end up at ~18.4px."""
    out = await renderer.render_carousel(one_diagram(chain(5)), brand)
    labels = [w for w in out.warnings if w.code == "diagram_label_size"]
    assert len(labels) == 1
    assert labels[0].slide == 1
    assert "18." in labels[0].message


async def test_illegible_diagram_labels_block(renderer: Renderer, brand: RenderBrand) -> None:
    with pytest.raises(DiagramLabelError) as exc:
        await renderer.render_carousel(one_diagram(chain(6)), brand)
    assert exc.value.slide == 1
    assert exc.value.effective_px < 18
    gantt = "gantt\n  title Plan\n  dateFormat YYYY-MM-DD\n  section A\n  Task :a1, 2024-01-01, 3d"
    with pytest.raises(DiagramLabelError):  # small text by design: measured, not assumed
        await renderer.render_carousel(one_diagram(gantt), brand)
    card = one_diagram(chain(6)).slides[0]
    with pytest.raises(DiagramLabelError):  # image cards follow the same floor
        await renderer.render_image_card(card, brand)


async def test_legible_diagram_has_no_label_warning(renderer: Renderer, brand: RenderBrand) -> None:
    out = await renderer.render_carousel(one_diagram(chain(3)), brand)
    assert [w for w in out.warnings if w.code == "diagram_label_size"] == []
