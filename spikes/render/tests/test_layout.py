"""Layout guarantees: longest allowed text fits, overflow fails closed, 28px floor, WCAG AA."""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from render import (
    BrandColors,
    BrandKit,
    DeckSpec,
    LayoutOverflowError,
    RenderSession,
    contrast_ratio,
    render_carousel_in,
)
from render import html as html_mod
from render.browser import find_overflows, wait_for_fonts
from render.html import OG_H, OG_W, SLIDE_H, SLIDE_W, deck_html, og_html
from render.mermaid import MermaidRenderer
from render.spec import DiagramSlide, OgSpec

pytestmark = pytest.mark.slow

# Realistic but wide vocabulary for the domain (long words, capitals, digits).
_WORDS = (
    "Authorization", "workload", "identities", "Governance", "privileged", "federation", "Okta", "Entra",
    "conditional", "access", "MITRE", "ATLAS", "evidence", "per-call", "token", "exchange", "Microsoft",
    "agentic", "boundary", "attestation", "2,000", "applications", "least-privilege", "reviews",
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


def worst_case_spec() -> dict[str, object]:
    s = itertools.count(1)

    def f(n: int) -> str:
        return filler(n, next(s))

    return {
        "title": f(100),
        "brand": {"author_name": f(48), "author_headline": f(64), "disclaimer": f(40)},
        "slides": [
            {"type": "cover", "kicker": f(40), "hook": f(80), "subtitle": f(110)},
            {"type": "statement", "statement": f(200), "note": f(140)},
            {"type": "list", "title": f(70), "items": [f(100) for _ in range(5)]},
            {"type": "steps", "title": f(70), "steps": [{"title": f(48), "detail": f(90)} for _ in range(4)]},
            {"type": "framework", "title": f(70), "layout": "2x2", "x_axis": f(28), "y_axis": f(28),
             "cells": [{"label": f(28), "text": f(64)} for _ in range(4)]},
            {"type": "framework", "title": f(70), "layout": "layered",
             "cells": [{"label": f(28), "text": f(64)} for _ in range(5)]},
            {"type": "diagram", "title": f(70), "alt": f(250), "caption": f(120),
             "mermaid": "flowchart TD\n  A[Agent] --> B[Gateway] --> C[Tool]"},
            {"type": "stat", "value": "$1,234.5M", "label": f(90), "context": f(140), "source": f(90)},
            {"type": "quote", "quote": f(200), "attribution": f(48), "role": f(64)},
            {"type": "recap", "title": f(60), "items": [f(90) for _ in range(5)]},
            {"type": "sources", "title": f(40),
             "sources": [{"publisher": f(40), "title": f(100), "date": f(16)} for _ in range(5)]},
        ],
    }  # fmt: skip


async def test_longest_allowed_text_in_every_field_still_fits(session: RenderSession) -> None:
    deck = DeckSpec.model_validate(worst_case_spec())
    out = await render_carousel_in(session, deck)  # raises LayoutOverflowError if anything clips
    assert out.pdf.startswith(b"%PDF-")


async def test_og_card_longest_title_fits(session: RenderSession) -> None:
    og = OgSpec(title=filler(110, 3), kicker=filler(40, 5))
    async with session.static_page(width=OG_W, height=OG_H) as page:
        await page.set_content(og_html(og))
        await wait_for_fonts(page)
        assert await find_overflows(page) == []


async def test_overflow_fails_closed_instead_of_clipping(
    session: RenderSession, sample_deck: DeckSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(html_mod._FIT, "statement", ((), 400))  # absurd size forces overflow
    with pytest.raises(LayoutOverflowError) as exc:
        await render_carousel_in(session, sample_deck)
    assert any(o.startswith("slide 2 body") for o in exc.value.overflows)


MIN_FONT_JS = """() => {
  const sizes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim()) continue;
    const el = node.parentElement;
    const px = parseFloat(getComputedStyle(el).fontSize);
    sizes.push([px, el.className, node.textContent.trim().slice(0, 20)]);
  }
  return sizes;
}"""


async def test_no_text_is_smaller_than_28px(session: RenderSession, sample_deck: DeckSpec) -> None:
    deck = DeckSpec.model_validate(worst_case_spec())
    async with MermaidRenderer(session) as mr:
        uris = {
            i: (await mr.render(s.mermaid, deck.brand.colors)).data_uri
            for i, s in enumerate(deck.slides)
            if isinstance(s, DiagramSlide)
        }
    for html, (w, h) in (
        (deck_html(deck, uris), (SLIDE_W, SLIDE_H)),
        (og_html(OgSpec(title="An article title", kicker="Pillar")), (OG_W, OG_H)),
    ):
        async with session.static_page(width=w, height=h) as page:
            await page.set_content(html)
            sizes = await page.evaluate(MIN_FONT_JS)
        assert sizes
        too_small = [s for s in sizes if s[0] < 28]
        assert too_small == [], too_small


def test_default_brand_meets_wcag_aa_on_every_text_pair() -> None:
    colors = BrandColors()
    for name, (fg, bg) in colors.text_pairs().items():
        assert contrast_ratio(fg, bg) >= 4.5, name


def test_contrast_math_matches_wcag_reference_values() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)
    assert contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)


def test_low_contrast_brand_kit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="WCAG AA"):
        BrandColors(muted="#9AA5A0")  # grey on paper: about 2.4:1


async def test_unbreakable_footer_text_fails_closed_instead_of_clipping(
    session: RenderSession, sample_deck: DeckSpec
) -> None:
    """Reviewer addition: the footer was not measured, so this name was silently clipped by 1,800px."""
    deck = sample_deck.model_copy(update={"brand": BrandKit(author_name="W" * 48, disclaimer="W" * 40)})
    with pytest.raises(LayoutOverflowError) as exc:
        await render_carousel_in(session, deck)
    assert any(" footer " in o for o in exc.value.overflows), exc.value.overflows
