"""Carousel rendering: slide spec JSON in, (PDF bytes, first slide as a 2x PNG) out."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, NamedTuple

from playwright.async_api import Playwright

from .browser import RenderLimiter, RenderSession, default_limiter, find_overflows, wait_for_fonts
from .errors import LayoutOverflowError, RenderError
from .html import SLIDE_H, SLIDE_W, deck_html
from .mermaid import SLIDE_FONT_SIZE, MermaidRenderer, needs_full_bundle
from .pdf import finalize_pdf
from .settings import RenderSettings
from .spec import DeckSpec, DiagramSlide

#: Chromium sizes PDF pages in inches (96 CSS px = 72 pt). Printing the 1080px layout on a
#: 15in x 18.75in sheet at scale 4/3 yields pages of exactly 1080 x 1350 pt.
PDF_WIDTH, PDF_HEIGHT, PDF_SCALE = "15in", "18.75in", 4 / 3


class RenderedDeck(NamedTuple):
    pdf: bytes
    cover_png: bytes  # first slide, 2160 x 2700


def parse_deck(spec: DeckSpec | Mapping[str, Any] | str | bytes) -> DeckSpec:
    if isinstance(spec, DeckSpec):
        return spec
    if isinstance(spec, str | bytes):
        return DeckSpec.model_validate_json(spec)
    return DeckSpec.model_validate(dict(spec))


async def render_carousel(
    spec: DeckSpec | Mapping[str, Any] | str | bytes,
    *,
    settings: RenderSettings | None = None,
    limiter: RenderLimiter | None = None,
    playwright: Playwright | None = None,
) -> RenderedDeck:
    """Render one carousel job end to end: its own Chromium, at most ``limit`` jobs at once."""
    deck = parse_deck(spec)
    settings = settings or RenderSettings.from_env()
    limiter = limiter or default_limiter(settings)
    async with limiter.slot():
        try:
            # The deadline covers driver start and browser launch too: a hung launch must not hold the
            # job slot (and so every later render) forever. It is lifted before teardown, so a deadline
            # that lands during cleanup cannot skip it.
            async with (
                asyncio.timeout(settings.job_timeout_s) as deadline,
                RenderSession(settings, playwright) as session,
            ):
                deck_out = await render_carousel_in(session, deck)
                deadline.reschedule(None)
                return deck_out
        except TimeoutError as exc:
            raise RenderError(f"render job exceeded {settings.job_timeout_s:g}s") from exc


async def render_carousel_in(session: RenderSession, deck: DeckSpec) -> RenderedDeck:
    """Render inside an existing session (tests, batch jobs)."""
    timer = session.timer
    diagram_uris: dict[int, str] = {}
    diagrams = [(i, s) for i, s in enumerate(deck.slides) if isinstance(s, DiagramSlide)]
    if diagrams:
        renderer = MermaidRenderer(session, full=any(needs_full_bundle(s.mermaid) for _, s in diagrams))
        with timer.stage("mermaid_context"):
            await renderer.open()
        try:
            with timer.stage("mermaid_render"):
                for i, slide in diagrams:
                    result = await renderer.render(
                        slide.mermaid, deck.brand.colors, font_size=SLIDE_FONT_SIZE
                    )
                    diagram_uris[i] = result.data_uri
        finally:
            await renderer.close()

    with timer.stage("html"):
        html = deck_html(deck, diagram_uris)
    async with session.static_page(width=SLIDE_W, height=SLIDE_H, scale=2) as page:
        with timer.stage("set_content"):
            await page.set_content(html, wait_until="load")
            await wait_for_fonts(page)
        with timer.stage("fit_check"):
            overflows = await find_overflows(page)
        if overflows:
            raise LayoutOverflowError(overflows)
        with timer.stage("cover_png"):
            cover_png = await page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                animations="disabled",
                caret="hide",
            )
        with timer.stage("pdf"):
            raw_pdf = await page.pdf(
                width=PDF_WIDTH,
                height=PDF_HEIGHT,
                scale=PDF_SCALE,
                print_background=True,
                prefer_css_page_size=False,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
    with timer.stage("pdf_finalize"):
        pdf = finalize_pdf(
            raw_pdf,
            title=deck.title,
            author=deck.brand.author_name,
            expected_pages=len(deck.slides),
            max_bytes=session.settings.max_pdf_bytes,
        )
    return RenderedDeck(pdf=pdf, cover_png=cover_png)


def deck_from_file(path: str) -> DeckSpec:
    with open(path, encoding="utf-8") as fh:  # noqa: PTH123 - tiny helper for scripts
        return DeckSpec.model_validate(json.load(fh))
