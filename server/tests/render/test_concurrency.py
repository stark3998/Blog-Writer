"""Jobs behind the limiter: never more in flight than allowed, identical output, shared slots."""

from __future__ import annotations

import asyncio

import pytest

from blogwriter.render import CarouselSpec, RenderBrand, Renderer, RenderSettings
from blogwriter.render.browser import RenderLimiter
from blogwriter.render.testing import png_size

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]


async def test_three_decks_behind_a_limit_of_two(
    render_settings: RenderSettings, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    limiter = RenderLimiter(2)
    renderer = Renderer(render_settings, limiter=limiter)
    await renderer.start()
    results = await asyncio.gather(
        *(renderer.render_carousel(sample_carousel, brand) for _ in range(3))
    )
    assert limiter.max_in_flight == 2
    assert limiter.completed == 3
    assert limiter.in_flight == 0
    assert len({r.pdf for r in results}) == 1  # deterministic across concurrent browsers


async def test_default_concurrency_runs_one_job_at_a_time(
    render_settings: RenderSettings, brand: RenderBrand
) -> None:
    assert render_settings.concurrency == 1
    limiter = RenderLimiter(render_settings.concurrency)
    renderer = Renderer(render_settings, limiter=limiter)
    await renderer.start()
    pngs = await asyncio.gather(
        renderer.render_og_image("One", None, None, brand),
        renderer.render_og_image("Two", None, None, brand),
        renderer.render_mermaid_png("flowchart TD\n  A --> B", 200),
    )
    assert limiter.max_in_flight == 1
    assert limiter.completed == 3
    assert png_size(pngs[0]) == (1200, 630)
    assert png_size(pngs[2])[0] == 200


async def test_og_images_and_carousels_share_one_limiter(
    render_settings: RenderSettings, sample_carousel: CarouselSpec, brand: RenderBrand
) -> None:
    limiter = RenderLimiter(2)
    renderer = Renderer(render_settings, limiter=limiter)
    await renderer.start()
    deck, og = await asyncio.gather(
        renderer.render_carousel(sample_carousel, brand),
        renderer.render_og_image("Title", None, "Pillar", brand),
    )
    assert deck.pdf.startswith(b"%PDF-")
    assert png_size(og) == (1200, 630)
    assert limiter.completed == 2
    assert limiter.max_in_flight == 2
