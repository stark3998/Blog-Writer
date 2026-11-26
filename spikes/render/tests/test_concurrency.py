"""Three decks at once behind a semaphore of 2: no errors, never more than 2 in flight."""

from __future__ import annotations

import asyncio

import pytest

from render import RenderLimiter, render_carousel, render_og_card

from .conftest import png_size

pytestmark = pytest.mark.slow


async def test_three_decks_concurrently_behind_semaphore_two(sample_spec: dict[str, object]) -> None:
    limiter = RenderLimiter(2)
    results = await asyncio.gather(*(render_carousel(sample_spec, limiter=limiter) for _ in range(3)))
    assert len(results) == 3
    assert limiter.max_in_flight == 2
    assert limiter.completed == 3 and limiter.in_flight == 0
    pdfs = {r.pdf for r in results}
    assert len(pdfs) == 1  # deterministic across separate browsers running concurrently
    for r in results:
        assert png_size(r.cover_png) == (2160, 2700)


async def test_og_cards_share_the_same_limiter(sample_spec: dict[str, object]) -> None:
    limiter = RenderLimiter(2)
    og = {"title": "Give every AI agent its own identity", "kicker": "AI agents and security"}
    deck, one_x, two_x = await asyncio.gather(
        render_carousel(sample_spec, limiter=limiter),
        render_og_card(og, limiter=limiter, scale=1),
        render_og_card(og, limiter=limiter, scale=2),
    )
    assert deck.pdf.startswith(b"%PDF-")
    assert png_size(one_x) == (1200, 630)
    assert png_size(two_x) == (2400, 1260)
    assert limiter.max_in_flight == 2
