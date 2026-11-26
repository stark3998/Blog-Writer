"""Render a deck spec to out/ for visual review: deck.pdf, cover@2x.png, slide-NN.png, og.png.

uv run python bench/render_sample.py [tests/fixtures/sample_deck.json]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".ms-playwright"))

from render import OgSpec, RenderSession, lint_deck, render_carousel_in, render_og_card_in  # noqa: E402
from render.browser import wait_for_fonts  # noqa: E402
from render.carousel import parse_deck  # noqa: E402
from render.html import SLIDE_H, SLIDE_W, deck_html  # noqa: E402
from render.mermaid import MermaidRenderer  # noqa: E402
from render.spec import DiagramSlide  # noqa: E402


async def main(spec_json: str, out: Path) -> None:
    deck = parse_deck(spec_json)
    for issue in lint_deck(deck):
        print(f"lint {issue.code} slide={issue.slide}: {issue.message}")
    async with RenderSession() as session:
        t0 = time.perf_counter()
        rendered = await render_carousel_in(session, deck)
        took = time.perf_counter() - t0
        print(f"carousel: {took:.2f}s pdf={len(rendered.pdf)}B cover={len(rendered.cover_png)}B")
        (out / "deck.pdf").write_bytes(rendered.pdf)
        (out / "cover@2x.png").write_bytes(rendered.cover_png)

        # Review-only: every slide as a 1x PNG, from the same HTML the PDF used.
        uris: dict[int, str] = {}
        async with MermaidRenderer(session) as mr:
            for i, s in enumerate(deck.slides):
                if isinstance(s, DiagramSlide):
                    res = await mr.render(s.mermaid, deck.brand.colors, png=True)
                    uris[i] = res.data_uri
                    (out / f"diagram-{i + 1:02d}.svg").write_bytes(res.svg)
                    if res.png:
                        (out / f"diagram-{i + 1:02d}@2x.png").write_bytes(res.png)
                    print(f"diagram {i + 1}: {res.width}x{res.height} warnings={list(res.warnings)}")
        async with session.static_page(width=SLIDE_W, height=SLIDE_H, scale=1) as page:
            await page.set_content(deck_html(deck, uris), wait_until="load")
            await wait_for_fonts(page)
            for i in range(len(deck.slides)):
                png = await page.screenshot(
                    full_page=True, clip={"x": 0, "y": i * SLIDE_H, "width": SLIDE_W, "height": SLIDE_H}
                )
                (out / f"slide-{i + 1:02d}.png").write_bytes(png)

        og = OgSpec(
            title="Give every AI agent its own identity before it gets a key", kicker="AI agents and security"
        )
        (out / "og.png").write_bytes(await render_og_card_in(session, og))
    print(f"blocked requests: {[b.redacted for b in session.guard.blocked]}")
    print(f"wrote {out}")


if __name__ == "__main__":
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests/fixtures/sample_deck.json"
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    asyncio.run(main(spec_path.read_text(encoding="utf-8"), out_dir))
