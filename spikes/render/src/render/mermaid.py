"""Mermaid diagrams, rendered in their own JavaScript-enabled context and sanitised afterwards.

Layers specific to Mermaid (on top of the session-wide isolation in :mod:`render.browser`):

1. **Source neutralisation.** Init directives (``%%{init: ...}%%``), YAML front-matter config and
   interaction statements (``click``, ``call``, ``callback``, ``link``, ``href``) are removed before
   Mermaid sees the source; each removal becomes a warning the editor can show.
2. **Locked configuration.** ``securityLevel: 'strict'``, ``htmlLabels: false`` everywhere, and every
   sensitive key listed in ``secure`` so that nothing in the source can override it.
3. **Pinned bundle, pinned CSP.** The bundle is checked against its SHA-256 and injected as a classic
   script; the page's CSP allows exactly that script's hash and nothing else. A script element that
   Mermaid (or an injected label) created would be refused by Chromium.
4. **Inert extraction.** The SVG string is parsed with ``DOMParser`` (an inert document: no script, no
   loads) and serialised as XML, then sanitised by :func:`render.svg_sanitize.sanitize_svg`.
5. The SVG is shown in slides through ``<img src="data:...">``: SVG-as-image never runs script or
   fetches anything, and the slide page itself has JavaScript off.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from lxml import etree
from playwright.async_api import BrowserContext, Dialog, Page
from playwright.async_api import Error as PlaywrightError

from .brand import BrandColors
from .browser import RenderSession, wait_for_fonts
from .errors import AssetIntegrityError, MermaidError, OutputLimitError, RenderError
from .fonts import TEXT_FAMILY, font_face_css
from .html import STATIC_CSP, image_page_html
from .svg_sanitize import SVG_NS, SanitizeReport, sanitize_svg

#: Same OpenType policy as the slides: contextual alternates would reach the PDF text layer as
#: Private Use Area code points. Applied when Mermaid measures text and when the SVG is drawn.
_TEXT_FEATURES = "svg,body{font-feature-settings:'kern' 1,'liga' 1,'calt' 0}"

#: Carousel diagrams are read on phones: labels render at >= 26px before any scaling.
SLIDE_FONT_SIZE = 26
ARTICLE_FONT_SIZE = 16

#: Raster budget for one diagram PNG, in device pixels. Chromium's memory grows with the captured
#: area (reviewer measurement, macOS: 13 MP -> 0.73 GiB Chromium RSS, 49 MP -> 1.5 GiB, 153 MP ->
#: 3.3 GiB), and ~2 KB of sequence-diagram source can be 12000 px wide, so an unbounded raster can
#: take the whole 2 GiB container down. 12 MP (e.g. 4000x3000 at 2x) is below the largest point
#: measured as safe. Slides are unaffected: they draw the SVG at frame size, not at intrinsic size.
MAX_RASTER_PIXELS = 12_000_000
MAX_RASTER_SIDE = 16_384  # Chromium's capture limit per side
RASTER_SCALE = 2

_DIRECTIVE = re.compile(r"%%\{.*?\}%%", re.DOTALL)
_FRONTMATTER = re.compile(r"\A\s*---[ \t]*\r?\n.*?\r?\n---[ \t]*(\r?\n|\Z)", re.DOTALL)
_INTERACTION_LINE = re.compile(
    r"^[ \t]*(?:click[ \t]+\S+.*|(?:link|callback)[ \t]+\S+[ \t]+[\"'].*)$",
    re.IGNORECASE | re.MULTILINE,
)
#: Style statements that pull a resource (``fill:url(https://...)``) are dropped whole.
_URL_STYLE_LINE = re.compile(
    r"^[ \t]*(?:style|classDef|linkStyle)\b.*url\s*\(.*$", re.IGNORECASE | re.MULTILINE
)

#: Diagram features that only the full Mermaid build has.
_NEEDS_FULL = re.compile(r"(?im)^\s*(?:mindmap|architecture(?:-beta)?)\b|\$\$")


def needs_full_bundle(source: str) -> bool:
    """True for mindmap / architecture diagrams and KaTeX ($$...$$), which the tiny build lacks."""
    return bool(_NEEDS_FULL.search(source))


#: Keys a diagram can never override (directives are stripped anyway; this is the second lock).
_SECURE_KEYS = [
    "secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "suppressErrorRendering",
    "htmlLabels", "flowchart", "class", "state", "sequence", "gantt", "journey", "er", "pie",
    "requirement", "mindmap", "timeline", "quadrantChart", "xyChart", "sankey", "block", "packet",
    "architecture", "kanban", "theme", "themeCSS", "themeVariables", "fontFamily", "altFontFamily",
    "look", "darkMode", "dompurifyConfig", "deterministicIds", "deterministicIDSeed", "elk",
    "layout", "markdownAutoWrap", "wrap", "fontSize", "icons",
]  # fmt: skip

_RENDER_JS = """async ([source, config, id, fontSize]) => {
  const m = globalThis.mermaid;
  if (!m || typeof m.render !== 'function') return {error: 'mermaid bundle is not loaded'};
  m.initialize(config);
  await Promise.all([
    document.fonts.load(`400 ${fontSize}px 'BW Text'`),
    document.fonts.load(`600 ${fontSize}px 'BW Text'`),
  ]);
  try {
    await m.parse(source);
  } catch (e) {
    return {error: String((e && e.message) || e).slice(0, 300)};
  }
  let svg;
  try {
    ({svg} = await m.render(id, source));
  } catch (e) {
    return {error: String((e && e.message) || e).slice(0, 300)};
  }
  // Inert parse (no script, no loads), then XML serialisation for the Python-side sanitiser.
  const doc = new DOMParser().parseFromString(svg, 'text/html');
  const el = doc.querySelector('svg');
  if (!el) return {error: 'mermaid returned no svg'};
  return {svg: new XMLSerializer().serializeToString(el)};
}"""


@dataclass(frozen=True, slots=True)
class MermaidBundle:
    source: str
    csp_hash: str  # 'sha256-...' for the page CSP


@cache
def load_bundle(path: Path, expected_sha256: str) -> MermaidBundle:
    try:
        blob = path.read_bytes()
    except FileNotFoundError as exc:
        raise AssetIntegrityError(f"Mermaid bundle not found at {path} (run npm ci in vendor/)") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected_sha256:
        raise AssetIntegrityError(f"Mermaid bundle sha256 mismatch: {digest}")
    source = blob.decode("utf-8")
    if "</script" in source.lower():  # pragma: no cover - would only matter if inlined into HTML
        raise AssetIntegrityError("Mermaid bundle contains '</script'")
    csp_hash = "sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode()
    return MermaidBundle(source=source, csp_hash=csp_hash)


def neutralise_source(source: str) -> tuple[str, list[str]]:
    """Strip configuration and interaction from Mermaid source. Returns (source, warnings)."""
    warnings: list[str] = []
    source = source.replace("\r\n", "\n")
    if _FRONTMATTER.search(source):
        source = _FRONTMATTER.sub("", source, count=1)
        warnings.append("removed front-matter configuration")
    n = len(_DIRECTIVE.findall(source))
    if n:
        source = _DIRECTIVE.sub("", source)
        warnings.append(f"removed {n} %%{{...}}%% directive(s)")
    n = len(_INTERACTION_LINE.findall(source))
    if n:
        source = _INTERACTION_LINE.sub("", source)
        warnings.append(f"removed {n} interaction statement(s) (click/link/callback)")
    n = len(_URL_STYLE_LINE.findall(source))
    if n:
        source = _URL_STYLE_LINE.sub("", source)
        warnings.append(f"removed {n} style statement(s) that reference a url()")
    return source.strip() + "\n", warnings


def mermaid_config(colors: BrandColors, font_size: int) -> dict[str, Any]:
    family = f"'{TEXT_FAMILY}', sans-serif"
    return {
        "startOnLoad": False,
        "securityLevel": "strict",
        "htmlLabels": False,
        "flowchart": {
            "htmlLabels": False,
            "useMaxWidth": False,
            "curve": "linear",
            "padding": 18,
            "nodeSpacing": 44,
            "rankSpacing": 56,
            "wrappingWidth": font_size * 11,
        },
        "sequence": {"useMaxWidth": False},
        "class": {"htmlLabels": False, "useMaxWidth": False},
        "state": {"useMaxWidth": False},
        "theme": "base",
        "look": "classic",
        "fontFamily": family,
        "themeVariables": {
            "fontFamily": family,
            "fontSize": f"{font_size}px",
            "background": colors.paper,
            "primaryColor": colors.paper,
            "primaryTextColor": colors.ink,
            "primaryBorderColor": colors.ink,
            "secondaryColor": colors.paper,
            "secondaryTextColor": colors.ink,
            "secondaryBorderColor": colors.muted,
            "tertiaryColor": colors.paper,
            "tertiaryTextColor": colors.ink,
            "tertiaryBorderColor": colors.rule,
            "mainBkg": colors.paper,
            "nodeBorder": colors.ink,
            "textColor": colors.ink,
            "titleColor": colors.ink,
            "lineColor": colors.muted,
            "edgeLabelBackground": colors.paper,
            "clusterBkg": colors.paper,
            "clusterBorder": colors.rule,
            "noteBkgColor": colors.paper,
            "noteTextColor": colors.ink,
            "noteBorderColor": colors.muted,
        },
        # Trusted CSS from us (never from the diagram): legible strokes, opaque edge-label plates.
        "themeCSS": (
            ".edgeLabel rect{opacity:1!important}"
            ".node rect,.node polygon,.node circle,.node ellipse,.node path{stroke-width:2.5px}"
            ".flowchart-link{stroke-width:2.5px}"
            ".cluster rect{stroke-width:2px}"
        ),
        "deterministicIds": True,
        "deterministicIDSeed": "bw",
        "maxTextSize": 4000,
        "maxEdges": 200,
        "suppressErrorRendering": True,
        "secure": _SECURE_KEYS,
    }


@dataclass(frozen=True, slots=True)
class MermaidResult:
    svg: bytes  # sanitised, fonts embedded, explicit width/height
    width: int
    height: int
    png: bytes | None  # 2x raster of the sanitised SVG, if requested
    warnings: tuple[str, ...] = ()
    report: SanitizeReport = field(default_factory=SanitizeReport)

    @property
    def data_uri(self) -> str:
        return "data:image/svg+xml;base64," + base64.b64encode(self.svg).decode("ascii")


def _finish_svg(svg_xml: bytes, font_size: int) -> tuple[bytes, int, int]:
    """Fix the intrinsic size to the viewBox, scale arrowheads with the type size, and embed the
    text font so the SVG renders identically as an image (SVG-as-image cannot see page fonts)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(svg_xml, parser=parser)
    marker_scale = max(1.0, font_size / ARTICLE_FONT_SIZE)
    for marker in root.iter(f"{{{SVG_NS}}}marker"):
        if marker.get("markerUnits") == "userSpaceOnUse":
            for attr in ("markerWidth", "markerHeight"):
                value = marker.get(attr)
                if value is not None:
                    marker.set(attr, f"{float(value) * marker_scale:g}")
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) != 4:
        raise MermaidError("diagram has no usable viewBox")
    width, height = (max(1, round(float(v) + 0.4999)) for v in vb[2:])
    root.set("width", str(width))
    root.set("height", str(height))
    root.attrib.pop("style", None)  # Mermaid's max-width; size is now explicit
    style = etree.Element(f"{{{SVG_NS}}}style")
    style.text = font_face_css(frozenset({TEXT_FAMILY})) + _TEXT_FEATURES
    root.insert(0, style)
    return etree.tostring(root, encoding="utf-8", xml_declaration=False), width, height


class MermaidRenderer:
    """Renders any number of diagrams in one JS-enabled context of a :class:`RenderSession`."""

    def __init__(self, session: RenderSession, *, full: bool = False) -> None:
        self._session = session
        self.full = full
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._count = 0
        #: Evidence that something executed in the Mermaid page. Must stay empty; alert on it.
        self.dialogs: list[str] = []
        self.page_errors: list[str] = []

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RenderError("MermaidRenderer is not open")
        return self._page

    async def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(dialog.type)
        await dialog.dismiss()

    async def __aenter__(self) -> MermaidRenderer:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def open(self) -> None:
        """Create the JS-enabled context, load the host page and inject the pinned bundle."""
        s = self._session.settings
        if self.full:
            bundle = load_bundle(s.mermaid_full_bundle, s.mermaid_full_sha256)
        else:
            bundle = load_bundle(s.mermaid_bundle, s.mermaid_sha256)
        ctx = self._ctx = await self._session.new_context(width=1600, height=1200, javascript=True)
        try:
            page = await ctx.new_page()
            page.on("dialog", self._on_dialog)
            page.on("pageerror", lambda err: self.page_errors.append(type(err).__name__))
            csp = STATIC_CSP + f"; script-src '{bundle.csp_hash}'"
            fonts = font_face_css(frozenset({TEXT_FAMILY}))
            html = (
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
                f"<style>{fonts}{_TEXT_FEATURES}</style></head><body></body></html>"
            )
            await page.set_content(html)
            await page.add_script_tag(content=bundle.source)
            self._page = page
        except BaseException:
            await ctx.close()
            raise

    async def close(self) -> None:
        if self._ctx is not None:
            await self._ctx.close()
        self._ctx = self._page = None

    async def render(
        self,
        source: str,
        colors: BrandColors,
        *,
        font_size: int = SLIDE_FONT_SIZE,
        png: bool = False,
    ) -> MermaidResult:
        if self._page is None:
            raise RenderError("MermaidRenderer is not open")
        clean, warnings = neutralise_source(source)
        self._count += 1
        config = mermaid_config(colors, font_size)
        try:
            result: dict[str, str] = await asyncio.wait_for(
                self._page.evaluate(_RENDER_JS, [clean, config, f"bw-d{self._count}", font_size]),
                timeout=self._session.settings.mermaid_timeout_s,
            )
        except TimeoutError as exc:
            raise MermaidError("diagram took too long to render") from exc
        except PlaywrightError as exc:
            raise MermaidError("diagram renderer failed (crashed or hit its memory limit)") from exc
        if "error" in result:
            raise MermaidError(f"invalid diagram: {result['error']}")
        sanitized, report = sanitize_svg(result["svg"])
        svg, width, height = _finish_svg(sanitized, font_size)
        raster = await rasterise_svg(self._session, svg, width, height, colors.paper) if png else None
        return MermaidResult(
            svg=svg, width=width, height=height, png=raster, warnings=tuple(warnings), report=report
        )


def check_raster_size(width: int, height: int, scale: int = RASTER_SCALE) -> None:
    """Refuse (before any browser work) a raster that would exceed the memory budget."""
    w, h = width * scale, height * scale
    if max(w, h) > MAX_RASTER_SIDE or w * h > MAX_RASTER_PIXELS:
        raise OutputLimitError(
            f"diagram is too large to rasterise: {w}x{h} device px (limits {MAX_RASTER_SIDE} px per side, "
            f"{MAX_RASTER_PIXELS:,} px in total); simplify or split it, or use the SVG"
        )


async def rasterise_svg(
    session: RenderSession, svg: bytes, width: int, height: int, background: str
) -> bytes:
    """2x PNG of an already-sanitised SVG, drawn by a JavaScript-disabled page."""
    check_raster_size(width, height)
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    async with session.static_page(width=width, height=height, scale=RASTER_SCALE) as page:
        await page.set_content(image_page_html(uri, width, height, background), wait_until="load")
        await wait_for_fonts(page)
        return await page.screenshot(type="png", animations="disabled", caret="hide")


async def render_mermaid(
    session: RenderSession,
    source: str,
    colors: BrandColors | None = None,
    *,
    font_size: int = SLIDE_FONT_SIZE,
    png: bool = True,
) -> MermaidResult:
    """Render one diagram: sanitised SVG (fonts embedded) and, by default, a 2x PNG."""
    async with MermaidRenderer(session, full=needs_full_bundle(source)) as renderer:
        return await renderer.render(source, colors or BrandColors(), font_size=font_size, png=png)
