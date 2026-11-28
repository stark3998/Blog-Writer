"""Mermaid in Chromium: neutralised hostile source, the locked configuration and the pinned CSP,
sanitised SVG and PNG output, slide embedding, the raster budget and label measurement."""

from __future__ import annotations

import base64
import copy
import re
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from blogwriter.render import (
    BrandColors,
    CarouselSpec,
    DiagramSlide,
    MermaidError,
    OutputLimitError,
    RenderBrand,
    Renderer,
)
from blogwriter.render._xml import parse_xml
from blogwriter.render.browser import RenderSession
from blogwriter.render.html import deck_html
from blogwriter.render.mermaid import (
    MAX_RASTER_PIXELS,
    MermaidBundle,
    MermaidRenderer,
    measure_label_px,
    mermaid_config,
    rasterise_svg,
)
from blogwriter.render.spec import MAX_MERMAID_SOURCE
from blogwriter.render.svg_sanitize import assert_svg_safe, sanitize_svg
from blogwriter.render.testing import png_size

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

HOSTILE = """---
config:
  securityLevel: loose
  flowchart:
    htmlLabels: true
---
%%{init: {"securityLevel": "loose", "htmlLabels": true, "themeCSS": "* {background: url(https://evil.example/x.png)}"}}%%
flowchart TD
  A["<script>alert(1)</script>"] --> B["<img src=x onerror=alert(2)>"]
  B --> C[Callback target]
  C --> D["<a href='javascript:alert(3)'>link</a>"]
  click A call alert(4)
  click B callback "tooltip"
  click C href "javascript:alert(5)" "Tooltip" _blank
  click D "https://evil.example/" _blank
  style D fill:url(https://evil.example/fill.png)
"""
FLOW = "flowchart TD\n  A[Agent] --> B[Gateway]\n  B --> C[Tool]"


def _sequence(actors: int, messages: int) -> str:
    lines = ["sequenceDiagram", *(f"  participant P{i}" for i in range(actors))]
    lines += [f"  P{i % actors}->>P{(i * 7 + 3) % actors}: m{i}" for i in range(messages)]
    return "\n".join(lines)


async def test_hostile_source_is_neutralised_end_to_end(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    before = len(session.guard.blocked)
    async with MermaidRenderer(session, bundle) as mr:
        result = await mr.render(HOSTILE, BrandColors())
        png = await rasterise_svg(
            session, result.svg, result.width, result.height, "#FFFFFF", out_width=result.width
        )
        assert mr.dialogs == []  # nothing ran in the JS-enabled Mermaid page
        assert mr.page_errors == []
    assert result.warnings
    assert_svg_safe(result.svg)
    svg = result.svg.decode()
    assert "foreignObject" not in svg
    assert "<a " not in svg
    assert "javascript:" not in svg
    assert "evil.example" not in svg
    assert session.guard.blocked[before:] == []  # and nothing even tried to load
    assert png_size(png)[0] == result.width


async def test_bundle_is_the_only_script_the_mermaid_page_will_run(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    async with MermaidRenderer(session, bundle) as mr:
        with pytest.raises(PlaywrightError, match="Content Security Policy"):
            await mr.page.add_script_tag(content="window.pwned = true")
        assert await mr.page.evaluate("typeof window.pwned") == "undefined"
        assert await mr.page.evaluate("typeof globalThis.mermaid.render") == "function"


async def test_svg_has_explicit_size_and_the_embedded_font(renderer: Renderer) -> None:
    diagram = await renderer.render_mermaid(FLOW, png_width=None)
    root = parse_xml(diagram.svg.encode())
    assert root.get("width") == str(diagram.width)
    assert root.get("height") == str(diagram.height)
    assert "@font-face" in diagram.svg
    assert "data:font/woff2;base64," in diagram.svg
    assert "'calt' 0" in diagram.svg  # same OpenType policy as slides: clean PDF text layer
    assert diagram.warnings == ()
    assert_svg_safe(diagram.svg)
    assert await renderer.render_mermaid_svg(FLOW) == diagram.svg


async def test_png_is_exactly_the_requested_width(renderer: Renderer) -> None:
    diagram = await renderer.render_mermaid(FLOW, png_width=2 * 200)
    assert diagram.png is not None
    width, height = png_size(diagram.png)
    assert width == 400
    assert height == round(diagram.height * 400 / diagram.width)
    png = await renderer.render_mermaid_png(FLOW, 640)
    assert png_size(png)[0] == 640


async def test_invalid_source_raises_a_clean_error(renderer: Renderer) -> None:
    with pytest.raises(MermaidError, match="invalid diagram"):
        await renderer.render_mermaid_svg("flowchart TD\n  A -->")


async def test_types_the_tiny_build_lacks_are_refused(
    renderer: Renderer, session: RenderSession, bundle: MermaidBundle
) -> None:
    source = "mindmap\n  root((Agent identity))\n    Workload IDs\n    Token exchange"
    with pytest.raises(MermaidError, match="not supported"):
        await renderer.render_mermaid_svg(source)
    async with MermaidRenderer(session, bundle) as mr:  # and the tiny build really lacks it
        raw = await mr.render_raw(source, BrandColors(), 26)
        assert "svg" not in raw


async def test_diagram_is_embedded_in_the_slide_as_a_data_uri_img(
    session: RenderSession,
    bundle: MermaidBundle,
    sample_carousel: CarouselSpec,
    brand: RenderBrand,
) -> None:
    idx, slide = next(
        (i, s) for i, s in enumerate(sample_carousel.slides) if isinstance(s, DiagramSlide)
    )
    async with MermaidRenderer(session, bundle) as mr:
        result = await mr.render(slide.mermaid, brand.colors)
    html = deck_html(sample_carousel, brand, diagram_uris={idx: result.data_uri})
    m = re.search(
        r'<img class="figure__img" data-diagram src="(data:image/svg\+xml;base64,[A-Za-z0-9+/=]+)"'
        r' alt="([^"]+)">',
        html,
    )
    assert m, "diagram slide must embed the SVG as a data: URI image"
    assert_svg_safe(base64.b64decode(m.group(1).split(",", 1)[1]))
    assert m.group(2).startswith("Flow chart:")


async def test_locked_config_holds_even_if_a_directive_reaches_mermaid(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    """Second lock, tested alone: skip neutralisation and let Mermaid see hostile directives."""
    raw = (
        '%%{init: {"securityLevel": "loose", "htmlLabels": true, "flowchart": {"htmlLabels": true},'
        ' "themeCSS": ".node rect { fill: url(https://evil.example/x.png) }"}}%%\n'
        'flowchart TD\n  A["<b>bold</b> label"] --> B\n  click A call alert(1)\n'
    )
    async with MermaidRenderer(session, bundle) as mr:
        out = await mr.render_raw(raw, BrandColors(), 26)
        svg = out.get("svg")
        assert isinstance(svg, str), out
        assert "foreignObject" not in svg  # htmlLabels override refused
        assert "evil.example" not in svg  # themeCSS override refused
        level = await mr.page.evaluate("globalThis.mermaid.mermaidAPI.getConfig().securityLevel")
        assert level == "strict"
        assert mr.dialogs == []


async def test_locked_config_holds_even_if_frontmatter_config_reaches_mermaid(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    raw = (
        "---\nconfig:\n  securityLevel: loose\n  htmlLabels: true\n  flowchart:\n"
        "    htmlLabels: true\n"
        '  themeCSS: ".node rect { fill: url(https://evil.example/x.png) }"\n---\n'
        'flowchart TD\n  A["<b>bold</b> label"] --> B\n'
    )
    async with MermaidRenderer(session, bundle) as mr:
        out = await mr.render_raw(raw, BrandColors(), 26)
        svg = out.get("svg")
        assert isinstance(svg, str), out
        assert "foreignObject" not in svg
        assert "evil.example" not in svg
        level = await mr.page.evaluate("globalThis.mermaid.mermaidAPI.getConfig().securityLevel")
        assert level == "strict"
        assert mr.dialogs == []
        assert mr.page_errors == []


#: Link forms Mermaid still renders as real <a xlink:href> in strict mode and that the line-based
#: neutraliser does not catch (it only matches click/link/callback at the start of a line).
LINKS_PAST_THE_NEUTRALISER = {
    "semicolon-separated click": (
        'flowchart TD\n  A[Start] --> B[End]; click A "https://evil.example/c" _blank'
    ),
    "sequence link": (
        "sequenceDiagram\n  participant A\n  link A: Dashboard @ https://evil.example/d\n"
        "  A->>A: hi"
    ),
    "sequence links": (
        "sequenceDiagram\n  participant A\n"
        '  links A: {"Docs": "https://evil.example/d"}\n  A->>A: hi'
    ),
}


@pytest.mark.parametrize(
    "source", LINKS_PAST_THE_NEUTRALISER.values(), ids=LINKS_PAST_THE_NEUTRALISER.keys()
)
async def test_sanitiser_alone_removes_links_mermaid_emits_in_strict_mode(
    session: RenderSession, bundle: MermaidBundle, source: str
) -> None:
    async with MermaidRenderer(session, bundle) as mr:
        out = await mr.render_raw(source, BrandColors(), 26)
    raw = out.get("svg")
    assert isinstance(raw, str)
    assert "evil.example" in raw  # strict mode really does emit the link
    assert "<a " in raw
    clean, report = sanitize_svg(raw)
    assert b"evil.example" not in clean
    assert b"<a " not in clean
    assert report.unwrapped_links >= 1


async def test_link_that_reaches_mermaid_never_reaches_the_pdf(
    renderer: Renderer, sample_spec: dict[str, Any], brand: RenderBrand
) -> None:
    spec = copy.deepcopy(sample_spec)
    diagram = next(s for s in spec["slides"] if s["type"] == "diagram")
    diagram["mermaid"] = LINKS_PAST_THE_NEUTRALISER["semicolon-separated click"]
    out = await renderer.render_carousel(CarouselSpec.model_validate(spec), brand)
    assert b"/Annots" not in out.pdf
    assert b"/URI" not in out.pdf
    assert b"evil.example" not in out.pdf


async def test_oversized_raster_is_refused_before_rasterising(renderer: Renderer) -> None:
    """~2 KB of source, ~12000 px wide: its unbounded 2x PNG once took Chromium to 3.3 GiB."""
    source = _sequence(60, 60)
    assert len(source) < MAX_MERMAID_SOURCE
    big = await renderer.render_mermaid(source)
    assert big.width > 8000
    with pytest.raises(OutputLimitError, match="too large to rasterise"):
        await renderer.render_mermaid_png(source, big.width)
    small = await renderer.render_mermaid_png(_sequence(4, 4), 800)  # normal diagrams rasterise
    width, height = png_size(small)
    assert width == 800
    assert width * height <= MAX_RASTER_PIXELS


async def test_label_measurement_sees_every_diagram_type_at_its_real_size(
    session: RenderSession, bundle: MermaidBundle
) -> None:
    sources = {
        0: FLOW,
        1: _sequence(3, 3),  # sequence diagrams follow the top-level fontSize, not the theme
        2: "gantt\n  title Plan\n  dateFormat YYYY-MM-DD\n  section A\n  Task :a1, 2024-01-01, 3d",
    }
    async with MermaidRenderer(session, bundle) as mr:
        results = {
            i: await mr.render(src, BrandColors(), font_size=26) for i, src in sources.items()
        }
    sizes = await measure_label_px(session, results)
    assert sizes[0] == 26
    assert sizes[1] == 26
    assert sizes[2] < 18  # gantt axis labels are small by design: the check must see them


async def test_article_size_is_the_default_for_standalone_diagrams(
    session: RenderSession, bundle: MermaidBundle, renderer: Renderer
) -> None:
    async with MermaidRenderer(session, bundle) as mr:
        result = await mr.render(FLOW, BrandColors(), font_size=16)
    assert (await measure_label_px(session, {0: result}))[0] == 16
    assert mermaid_config(BrandColors(), 16)["fontSize"] == 16
    assert (await renderer.render_mermaid(FLOW)).width == result.width
