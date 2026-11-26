"""OG card (1200x630 PNG) and the metadata-free PNG guarantee for every raster we produce."""

from __future__ import annotations

import struct

import pytest
from pydantic import ValidationError

from render import DeckSpec, OgSpec, RenderSession, render_carousel_in, render_og_card_in
from render.mermaid import render_mermaid

from .conftest import png_size

pytestmark = pytest.mark.slow


def png_chunks(png: bytes) -> list[str]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    i, chunks = 8, []
    while i < len(png):
        (length,) = struct.unpack(">I", png[i : i + 4])
        chunks.append(png[i + 4 : i + 8].decode("latin-1"))
        i += 12 + length
    return chunks


async def test_og_card_is_1200x630_and_2x_on_request(session: RenderSession) -> None:
    og = OgSpec(title="Give every AI agent its own identity", kicker="AI agents and security")
    assert png_size(await render_og_card_in(session, og)) == (1200, 630)
    assert png_size(await render_og_card_in(session, og, scale=2)) == (2400, 1260)


async def test_rasters_carry_no_metadata_chunks(session: RenderSession, sample_deck: DeckSpec) -> None:
    """Confidentiality gate: nothing but pixels (no tEXt/iTXt/zTXt/eXIf/tIME)."""
    og = await render_og_card_in(session, OgSpec(title="Title"))
    deck = await render_carousel_in(session, sample_deck)
    diagram = await render_mermaid(session, "flowchart TD\n  A --> B")
    assert diagram.png is not None
    for png in (og, deck.cover_png, diagram.png):
        assert set(png_chunks(png)) == {"IHDR", "IDAT", "IEND"}


def test_og_spec_limits() -> None:
    with pytest.raises(ValidationError):
        OgSpec(title="x" * 111)
    with pytest.raises(ValidationError):
        OgSpec(title="ok", kicker="k" * 41)
