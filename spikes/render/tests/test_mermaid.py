"""Mermaid: neutralised source, strict config, pinned bundle, sanitised SVG, 2x PNG, slide embedding."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from lxml import etree
from playwright.async_api import Error as PlaywrightError

from render import (
    BrandColors,
    MermaidError,
    MermaidRenderer,
    OutputLimitError,
    RenderSession,
    render_carousel_in,
    render_mermaid,
)
from render.errors import AssetIntegrityError
from render.html import deck_html
from render.mermaid import (
    MAX_RASTER_PIXELS,
    check_raster_size,
    load_bundle,
    needs_full_bundle,
    neutralise_source,
)
from render.settings import MERMAID_FULL_SHA256, MERMAID_TINY_SHA256, RenderSettings
from render.spec import MAX_MERMAID_SOURCE, DeckSpec
from render.svg_sanitize import assert_svg_safe, sanitize_svg

from .conftest import png_size

pytestmark = pytest.mark.slow

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


def test_neutralise_removes_config_and_interaction_and_reports_it() -> None:
    clean, warnings = neutralise_source(HOSTILE)
    assert "config:" not in clean and "securityLevel" not in clean
    assert "%%{" not in clean
    assert not re.search(r"(?im)^\s*click\b", clean)
    assert "url(" not in clean
    assert len(warnings) == 4
    assert clean.splitlines()[0] == "flowchart TD"


def test_neutralise_keeps_ordinary_diagrams_intact() -> None:
    src = "flowchart LR\n  linkStyle default stroke:#333\n  A[clicker] --> B[link to docs]\n"
    clean, warnings = neutralise_source(src)
    assert warnings == []
    assert clean == src


def test_class_diagram_link_and_callback_lines_are_removed() -> None:
    src = 'classDiagram\n  class Agent\n  link Agent "https://evil.example"\n  callback Agent "fn" "tip"\n'
    clean, warnings = neutralise_source(src)
    assert "evil" not in clean and "callback" not in clean
    assert warnings


async def test_hostile_source_is_neutralised_end_to_end(session: RenderSession) -> None:
    before = len(session.guard.blocked)
    async with MermaidRenderer(session) as mr:
        result = await mr.render(HOSTILE, BrandColors(), png=True)
        # Nothing ran in the JS-enabled Mermaid page: no dialogs, no page errors.
        assert mr.dialogs == []
        assert mr.page_errors == []
    assert result.warnings
    assert_svg_safe(result.svg)
    svg = result.svg.decode()
    assert "foreignObject" not in svg and "<a " not in svg and "javascript:" not in svg
    assert "evil.example" not in svg
    assert session.guard.blocked[before:] == []  # and nothing even tried to load
    assert result.png is not None


async def test_bundle_is_the_only_script_the_mermaid_page_will_run(session: RenderSession) -> None:
    async with MermaidRenderer(session) as mr:
        with pytest.raises(PlaywrightError, match="Content Security Policy"):
            await mr.page.add_script_tag(content="window.pwned = true")
        assert await mr.page.evaluate("typeof window.pwned") == "undefined"
        assert await mr.page.evaluate("typeof globalThis.mermaid.render") == "function"


async def test_svg_and_2x_png_have_matching_sizes_and_embedded_font(session: RenderSession) -> None:
    result = await render_mermaid(session, "flowchart TD\n  A[Agent] --> B[Gateway]\n  B --> C[Tool]")
    root = etree.fromstring(result.svg)
    assert root.get("width") == str(result.width) and root.get("height") == str(result.height)
    assert result.png is not None
    assert png_size(result.png) == (result.width * 2, result.height * 2)
    assert b"@font-face" in result.svg and b"data:font/woff2;base64," in result.svg
    assert b"'calt' 0" in result.svg  # same OpenType policy as slides: clean PDF text layer


async def test_invalid_source_raises_a_clean_error(session: RenderSession) -> None:
    with pytest.raises(MermaidError, match="invalid diagram"):
        await render_mermaid(session, "flowchart TD\n  A -->", png=False)


async def test_diagram_is_embedded_in_the_slide_as_a_data_uri_img(
    session: RenderSession, sample_deck: DeckSpec
) -> None:
    idx, slide = next((i, s) for i, s in enumerate(sample_deck.slides) if s.type == "diagram")
    result = await render_mermaid(session, slide.mermaid, sample_deck.brand.colors, png=False)  # type: ignore[union-attr]
    html = deck_html(sample_deck, {idx: result.data_uri})
    m = re.search(
        r'<img class="figure__img" src="(data:image/svg\+xml;base64,[A-Za-z0-9+/=]+)" alt="([^"]+)">', html
    )
    assert m, "diagram slide must embed the SVG as a data: URI image"
    assert_svg_safe(base64.b64decode(m.group(1).split(",", 1)[1]))
    assert m.group(2).startswith("Flow chart:")


def test_bundle_integrity_mismatch_is_refused(tmp_path: Path) -> None:
    fake = tmp_path / "mermaid.min.js"
    fake.write_text("globalThis.mermaid = {render(){}}")
    with pytest.raises(AssetIntegrityError, match="sha256 mismatch"):
        load_bundle(fake, MERMAID_TINY_SHA256)
    with pytest.raises(AssetIntegrityError, match="not found"):
        load_bundle(tmp_path / "missing.js", MERMAID_TINY_SHA256)


def test_pinned_bundles_match_settings() -> None:
    s = RenderSettings()
    assert s.mermaid_sha256 == MERMAID_TINY_SHA256 and s.mermaid_full_sha256 == MERMAID_FULL_SHA256
    tiny = load_bundle(s.mermaid_bundle, s.mermaid_sha256)
    full = load_bundle(s.mermaid_full_bundle, s.mermaid_full_sha256)
    for bundle in (tiny, full):
        assert bundle.csp_hash.startswith("sha256-")
        assert 'globalThis["mermaid"]' in bundle.source
    assert len(tiny.source) < len(full.source) * 0.6


@pytest.mark.parametrize(
    ("source", "full"),
    [
        ("flowchart TD\n  A --> B", False),
        ("sequenceDiagram\n  A->>B: hi", False),
        ("mindmap\n  root((Identity))", True),
        ("architecture-beta\n  service api(server)[API]", True),
        ("flowchart TD\n  A[$$x^2$$] --> B", True),
    ],
)
def test_full_bundle_is_selected_only_when_needed(source: str, full: bool) -> None:
    assert needs_full_bundle(source) is full


async def test_mindmap_renders_with_the_auto_selected_full_bundle(session: RenderSession) -> None:
    source = "mindmap\n  root((Agent identity))\n    Workload IDs\n    Token exchange"
    with pytest.raises(MermaidError):
        async with MermaidRenderer(session) as tiny:  # the tiny build has no mindmap
            await tiny.render(source, BrandColors())
    result = await render_mermaid(session, source, png=False)
    assert_svg_safe(result.svg)


async def test_locked_config_holds_even_if_a_directive_reaches_mermaid(session: RenderSession) -> None:
    """Second lock, tested alone: skip neutralisation and let Mermaid see hostile directives."""
    from render.mermaid import _RENDER_JS, mermaid_config

    raw = (
        '%%{init: {"securityLevel": "loose", "htmlLabels": true, "flowchart": {"htmlLabels": true},'
        ' "themeCSS": ".node rect { fill: url(https://evil.example/x.png) }"}}%%\n'
        'flowchart TD\n  A["<b>bold</b> label"] --> B\n  click A call alert(1)\n'
    )
    async with MermaidRenderer(session) as mr:
        out = await mr.page.evaluate(_RENDER_JS, [raw, mermaid_config(BrandColors(), 26), "lock", 26])
        assert "svg" in out, out
        svg = out["svg"]
        assert "foreignObject" not in svg  # htmlLabels override refused
        assert "evil.example" not in svg  # themeCSS override refused
        assert await mr.page.evaluate("globalThis.mermaid.mermaidAPI.getConfig().securityLevel") == "strict"
        assert mr.dialogs == []


# --- Reviewer additions -----------------------------------------------------------------------------


async def test_locked_config_holds_even_if_frontmatter_config_reaches_mermaid(session: RenderSession) -> None:
    """Second lock, front-matter variant (the directive variant is tested above)."""
    from render.mermaid import _RENDER_JS, mermaid_config

    raw = (
        "---\nconfig:\n  securityLevel: loose\n  htmlLabels: true\n  flowchart:\n    htmlLabels: true\n"
        '  themeCSS: ".node rect { fill: url(https://evil.example/x.png) }"\n---\n'
        'flowchart TD\n  A["<b>bold</b> label"] --> B\n'
    )
    async with MermaidRenderer(session) as mr:
        out = await mr.page.evaluate(_RENDER_JS, [raw, mermaid_config(BrandColors(), 26), "fm", 26])
        assert "svg" in out, out
        assert "foreignObject" not in out["svg"]
        assert "evil.example" not in out["svg"]
        assert await mr.page.evaluate("globalThis.mermaid.mermaidAPI.getConfig().securityLevel") == "strict"
        assert mr.dialogs == [] and mr.page_errors == []


#: Link forms that Mermaid still renders as real <a xlink:href> in strict mode and that the line-based
#: neutraliser does not catch (it only matches click/link/callback at the start of a line).
LINKS_PAST_THE_NEUTRALISER = {
    "semicolon-separated click": (
        'flowchart TD\n  A[Start] --> B[End]; click A "https://evil.example/c" _blank'
    ),
    "sequence link": (
        "sequenceDiagram\n  participant A\n  link A: Dashboard @ https://evil.example/d\n  A->>A: hi"
    ),
    "sequence links": (
        'sequenceDiagram\n  participant A\n  links A: {"Docs": "https://evil.example/d"}\n  A->>A: hi'
    ),
}


@pytest.mark.parametrize("source", LINKS_PAST_THE_NEUTRALISER.values(), ids=LINKS_PAST_THE_NEUTRALISER.keys())
async def test_sanitiser_alone_removes_links_mermaid_emits_in_strict_mode(
    session: RenderSession, source: str
) -> None:
    from render.mermaid import _RENDER_JS, mermaid_config

    async with MermaidRenderer(session) as mr:
        out = await mr.page.evaluate(_RENDER_JS, [source, mermaid_config(BrandColors(), 26), "lnk", 26])
    raw = out["svg"]
    assert "evil.example" in raw and "<a " in raw  # strict mode really does emit the link
    clean, report = sanitize_svg(raw)
    assert b"evil.example" not in clean and b"<a " not in clean
    assert report.unwrapped_links >= 1


async def test_link_that_reaches_mermaid_never_reaches_the_pdf(
    session: RenderSession, sample_spec: dict[str, object]
) -> None:
    spec = json.loads(json.dumps(sample_spec))
    diagram = next(s for s in spec["slides"] if s["type"] == "diagram")  # type: ignore[index]
    diagram["mermaid"] = LINKS_PAST_THE_NEUTRALISER["semicolon-separated click"]  # type: ignore[index]
    out = await render_carousel_in(session, DeckSpec.model_validate(spec))
    assert b"/Annots" not in out.pdf and b"/URI" not in out.pdf and b"evil.example" not in out.pdf


def _sequence(actors: int, messages: int) -> str:
    lines = ["sequenceDiagram", *(f"  participant P{i}" for i in range(actors))]
    lines += [f"  P{i % actors}->>P{(i * 7 + 3) % actors}: m{i}" for i in range(messages)]
    return "\n".join(lines)


def test_raster_budget_boundaries() -> None:
    check_raster_size(2000, 1500)  # 4000 x 3000 device px = 12 MP: allowed
    with pytest.raises(OutputLimitError, match="too large to rasterise"):
        check_raster_size(2001, 1500)
    with pytest.raises(OutputLimitError, match="per side"):
        check_raster_size(8193, 10)  # 16386 px wide, tiny area


async def test_oversized_diagram_is_refused_before_rasterising(session: RenderSession) -> None:
    """~2 KB of source, 12050 x 3171 px: its unbounded 2x PNG took Chromium to about 3.3 GiB."""
    source = _sequence(60, 60)
    assert len(source) < MAX_MERMAID_SOURCE
    with pytest.raises(OutputLimitError, match="too large to rasterise"):
        await render_mermaid(session, source, png=True)
    small = await render_mermaid(session, _sequence(4, 4), png=True)  # normal diagrams still rasterise
    assert small.png is not None and png_size(small.png) == (small.width * 2, small.height * 2)
    assert small.width * 2 * small.height * 2 <= MAX_RASTER_PIXELS
