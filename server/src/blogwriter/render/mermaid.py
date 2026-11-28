"""Mermaid diagrams, rendered in their own JavaScript-enabled context and sanitised afterwards.

Layers specific to Mermaid (on top of the session-wide isolation in
:mod:`blogwriter.render.browser`):

1. **Source neutralisation.** Init directives (``%%{init: ...}%%``), YAML front-matter config,
   interaction statements (``click``, ``link``, ``callback``) and style statements with ``url()``
   are removed before Mermaid sees the source; each removal becomes a warning the editor can show.
2. **Locked configuration.** ``securityLevel: 'strict'``, ``htmlLabels: false`` everywhere, and
   every sensitive key listed in ``secure`` so that nothing in the source can override it.
3. **Pinned bundle, pinned CSP.** Only ``@mermaid-js/tiny`` is shipped. Its SHA-256 is checked
   against :data:`~blogwriter.render.settings.MERMAID_TINY_SHA256` at start-up; it is injected
   with ``add_script_tag`` and the page's CSP allows exactly that script's hash and nothing else.
   Mindmap, architecture and KaTeX (``$$``) diagrams need the full build and are refused.
4. **Inert extraction.** The SVG string is parsed with ``DOMParser`` (an inert document: no
   script, no loads) and serialised as XML, then sanitised by
   :func:`blogwriter.render.svg_sanitize.sanitize_svg`; the finished SVG (fonts embedded) is
   checked again by :func:`~blogwriter.render.svg_sanitize.assert_svg_safe`.
5. The SVG is shown in slides through ``<img src="data:...">``: SVG-as-image never runs script
   or fetches anything, and the slide page itself has JavaScript off.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self, cast

from playwright.async_api import BrowserContext, Dialog, Page
from playwright.async_api import Error as PlaywrightError

from blogwriter.render._xml import new_element, parse_xml, to_bytes
from blogwriter.render.brand import BrandColors
from blogwriter.render.browser import RenderSession, wait_for_fonts
from blogwriter.render.errors import AssetIntegrityError, MermaidError, OutputLimitError
from blogwriter.render.fonts import TEXT_FAMILY, font_face_css
from blogwriter.render.html import STATIC_CSP, image_page_html
from blogwriter.render.svg_sanitize import SVG_NS, SanitizeReport, assert_svg_safe, sanitize_svg

__all__ = [
    "ARTICLE_FONT_SIZE",
    "MAX_RASTER_PIXELS",
    "MAX_RASTER_SIDE",
    "RENDER_JS",
    "SLIDE_FONT_SIZE",
    "MermaidBundle",
    "MermaidRenderer",
    "MermaidResult",
    "check_raster_size",
    "load_bundle",
    "measure_label_px",
    "mermaid_config",
    "neutralise_source",
    "rasterise_svg",
    "unsupported_reason",
]

#: Same OpenType policy as the slides: contextual alternates would reach the PDF text layer as
#: Private Use Area code points. Applied when Mermaid measures text and when the SVG is drawn.
_TEXT_FEATURES: Final = "svg,body{font-feature-settings:'kern' 1,'liga' 1,'calt' 0}"

#: Carousel diagrams are read on phones: labels render at 26px before any scaling.
SLIDE_FONT_SIZE: Final = 26
#: Article diagrams (blog) are drawn at their intrinsic size.
ARTICLE_FONT_SIZE: Final = 16

#: Raster budget for one PNG, in device pixels. Chromium's memory grows with the captured area
#: (reviewer measurement, macOS: 13 MP -> 0.73 GiB Chromium RSS, 49 MP -> 1.5 GiB, 153 MP ->
#: 3.3 GiB), and ~2 KB of sequence-diagram source can be 12000 px wide, so an unbounded raster
#: can take the whole 2 GiB container down. 12 MP is below the largest point measured as safe.
MAX_RASTER_PIXELS: Final = 12_000_000
MAX_RASTER_SIDE: Final = 16_384  # Chromium's capture limit per side

_DIRECTIVE: Final = re.compile(r"%%\{.*?\}%%", re.DOTALL)
_FRONTMATTER: Final = re.compile(r"\A\s*---[ \t]*\r?\n.*?\r?\n---[ \t]*(\r?\n|\Z)", re.DOTALL)
_INTERACTION_LINE: Final = re.compile(
    r"^[ \t]*(?:click[ \t]+\S+.*|(?:link|callback)[ \t]+\S+[ \t]+[\"'].*)$",
    re.IGNORECASE | re.MULTILINE,
)
#: Style statements that pull a resource (``fill:url(https://...)``) are dropped whole.
_URL_STYLE_LINE: Final = re.compile(
    r"^[ \t]*(?:style|classDef|linkStyle)\b.*url\s*\(.*$", re.IGNORECASE | re.MULTILINE
)
#: Diagram features only the full Mermaid build has (it is not shipped).
_NEEDS_FULL: Final = re.compile(r"(?im)^\s*(?:mindmap|architecture(?:-beta)?)\b|\$\$")

#: Keys a diagram can never override (directives are stripped anyway; this is the second lock).
_SECURE_KEYS: Final = [
    "secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "suppressErrorRendering",
    "htmlLabels", "flowchart", "class", "state", "sequence", "gantt", "journey", "er", "pie",
    "requirement", "mindmap", "timeline", "quadrantChart", "xyChart", "sankey", "block", "packet",
    "architecture", "kanban", "theme", "themeCSS", "themeVariables", "fontFamily", "altFontFamily",
    "look", "darkMode", "dompurifyConfig", "deterministicIds", "deterministicIDSeed", "elk",
    "layout", "markdownAutoWrap", "wrap", "fontSize", "icons",
]  # fmt: skip

RENDER_JS: Final = """async ([source, config, id, fontSize]) => {
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
    sha256: str
    """Hex SHA-256 of the file."""
    csp_hash: str
    """``sha256-<base64>`` of the script text, for the page CSP."""


def load_bundle(path: Path, expected_sha256: str) -> MermaidBundle:
    """Read and verify the pinned bundle (blocking I/O: call it from a worker thread)."""
    try:
        blob = path.read_bytes()
    except FileNotFoundError as exc:
        raise AssetIntegrityError(
            f"Mermaid bundle not found at {path}: run `npm ci --ignore-scripts` in server/vendor "
            "or set BW_RENDER_MERMAID_JS"
        ) from exc
    except OSError as exc:
        raise AssetIntegrityError(f"Mermaid bundle at {path} cannot be read") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected_sha256:
        raise AssetIntegrityError(
            f"Mermaid bundle sha256 mismatch at {path}: {digest} (pinned {expected_sha256})"
        )
    try:
        source = blob.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - the pinned file is UTF-8
        raise AssetIntegrityError("Mermaid bundle is not UTF-8") from exc
    csp_digest = hashlib.sha256(source.encode("utf-8")).digest()
    return MermaidBundle(
        source=source,
        sha256=digest,
        csp_hash="sha256-" + base64.b64encode(csp_digest).decode("ascii"),
    )


def unsupported_reason(source: str) -> str | None:
    """Why the shipped (tiny) build cannot render ``source``, or ``None``."""
    if _NEEDS_FULL.search(source):
        return (
            "mindmap, architecture and KaTeX ($$) diagrams are not supported "
            "(they need the full Mermaid build, which is not shipped)"
        )
    return None


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
        # Sequence diagrams size actors, messages and notes from this, not from themeVariables
        # (Mermaid's default is 16px).
        "fontSize": font_size,
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
        "secure": list(_SECURE_KEYS),
    }


@dataclass(frozen=True, slots=True)
class MermaidResult:
    svg: bytes
    """Sanitised, fonts embedded, explicit width and height."""
    width: int
    height: int
    font_size: int
    warnings: tuple[str, ...] = ()
    """What neutralisation removed from the source (for the editor)."""
    report: SanitizeReport = field(default_factory=SanitizeReport)

    @property
    def data_uri(self) -> str:
        return "data:image/svg+xml;base64," + base64.b64encode(self.svg).decode("ascii")


def _finish_svg(svg_xml: bytes, font_size: int) -> tuple[bytes, int, int]:
    """Fix the intrinsic size to the viewBox, scale arrowheads with the type size, embed the
    text font (SVG-as-image cannot see page fonts) and re-check the result. CPU-bound."""
    root = parse_xml(svg_xml)
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
    root.attrib.pop("style", None)  # Mermaid's max-width; the size is now explicit
    style = new_element(f"{{{SVG_NS}}}style")
    style.text = font_face_css(frozenset({TEXT_FAMILY})) + _TEXT_FEATURES
    root.insert(0, style)
    out = to_bytes(root)
    assert_svg_safe(out)
    return out, width, height


def _sanitize_and_finish(svg: str, font_size: int) -> tuple[bytes, int, int, SanitizeReport]:
    sanitized, report = sanitize_svg(svg)
    out, width, height = _finish_svg(sanitized, font_size)
    return out, width, height, report


class MermaidRenderer:
    """Renders any number of diagrams in one JS-enabled context of a :class:`RenderSession`."""

    def __init__(self, session: RenderSession, bundle: MermaidBundle) -> None:
        self._session = session
        self._bundle = bundle
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._count = 0
        #: Evidence that something executed in the Mermaid page. Must stay empty.
        self.dialogs: list[str] = []
        self.page_errors: list[str] = []

    @property
    def page(self) -> Page:
        if self._page is None:
            raise MermaidError("MermaidRenderer is not open")
        return self._page

    async def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(dialog.type)
        await dialog.dismiss()

    def _on_page_error(self, error: object) -> None:
        self.page_errors.append(type(error).__name__)

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def open(self) -> None:
        """Create the JS-enabled context, load the host page and inject the pinned bundle."""
        ctx = self._ctx = await self._session.new_context(width=1600, height=1200, javascript=True)
        try:
            page = await ctx.new_page()
            page.on("dialog", self._on_dialog)
            page.on("pageerror", self._on_page_error)
            csp = STATIC_CSP + f"; script-src '{self._bundle.csp_hash}'"
            fonts = font_face_css(frozenset({TEXT_FAMILY}))
            html = (
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
                f"<style>{fonts}{_TEXT_FEATURES}</style></head><body></body></html>"
            )
            await page.set_content(html)
            await page.add_script_tag(content=self._bundle.source)
            self._page = page
        except BaseException:
            self._ctx = None
            await ctx.close()
            raise

    async def close(self) -> None:
        ctx, self._ctx, self._page = self._ctx, None, None
        if ctx is not None:
            await ctx.close()

    async def render_raw(
        self, source: str, colors: BrandColors, font_size: int
    ) -> dict[str, object]:
        """Run Mermaid on ``source`` exactly as given (no neutralisation): ``{"svg": ...}`` or
        ``{"error": ...}``. Tests use it to exercise the locked configuration on its own."""
        self._count += 1
        config = mermaid_config(colors, font_size)
        try:
            result: object = await asyncio.wait_for(
                self.page.evaluate(RENDER_JS, [source, config, f"bw-d{self._count}", font_size]),
                timeout=self._session.settings.mermaid_timeout_s,
            )
        except TimeoutError as exc:
            raise MermaidError("diagram took too long to render") from exc
        except PlaywrightError as exc:
            raise MermaidError("diagram renderer failed (crashed or hit its memory limit)") from exc
        if not isinstance(result, dict):
            raise MermaidError("diagram renderer returned no result")
        return cast(dict[str, object], result)

    async def render(
        self, source: str, colors: BrandColors, *, font_size: int = SLIDE_FONT_SIZE
    ) -> MermaidResult:
        """Neutralise, render, sanitise and finish one diagram."""
        reason = unsupported_reason(source)
        if reason is not None:
            raise MermaidError(reason)
        clean, warnings = neutralise_source(source)
        result = await self.render_raw(clean, colors, font_size)
        svg = result.get("svg")
        if not isinstance(svg, str):
            error = result.get("error")
            raise MermaidError(f"invalid diagram: {error if isinstance(error, str) else 'unknown'}")
        out, width, height, report = await asyncio.to_thread(_sanitize_and_finish, svg, font_size)
        return MermaidResult(
            svg=out,
            width=width,
            height=height,
            font_size=font_size,
            warnings=tuple(warnings),
            report=report,
        )


_MIN_LABEL_JS: Final = """() => {
  const out = {};
  for (const box of document.querySelectorAll('[data-diagram-index]')) {
    let min = Infinity;
    for (const t of box.querySelectorAll('text, tspan')) {
      if (!t.textContent.trim() || t.getClientRects().length === 0) continue;
      const px = parseFloat(getComputedStyle(t).fontSize);
      if (px > 0 && px < min) min = px;
    }
    out[box.dataset.diagramIndex] = Number.isFinite(min) ? min : null;
  }
  return out;
}"""


async def measure_label_px(
    session: RenderSession, diagrams: Mapping[int, MermaidResult]
) -> dict[int, float]:
    """The smallest rendered text size of each diagram, in px at its intrinsic size.

    The sanitised SVGs are laid out inline in one JavaScript-disabled page (static CSP, route
    guard) and measured from the utility world. Diagrams without visible text are left out.
    """
    if not diagrams:
        return {}
    body = "".join(
        f'<div data-diagram-index="{i}">{r.svg.decode("utf-8")}</div>' for i, r in diagrams.items()
    )
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{STATIC_CSP}">'
        f"</head><body>{body}</body></html>"
    )
    async with session.static_page(width=1200, height=900) as page:
        await page.set_content(html, wait_until="load")
        raw: object = await page.evaluate(_MIN_LABEL_JS)
    sizes = cast(dict[str, float | None], raw)
    return {int(k): float(v) for k, v in sizes.items() if v is not None}


def check_raster_size(width: int, height: int) -> None:
    """Refuse (before any browser work) a raster of ``width`` x ``height`` device pixels that
    would exceed the memory budget."""
    if max(width, height) > MAX_RASTER_SIDE or width * height > MAX_RASTER_PIXELS:
        raise OutputLimitError(
            f"image is too large to rasterise: {width}x{height} device px (limits "
            f"{MAX_RASTER_SIDE} px per side, {MAX_RASTER_PIXELS:,} px in total); simplify or "
            "split the diagram, or use the SVG"
        )


async def rasterise_svg(
    session: RenderSession,
    svg: bytes,
    width: int,
    height: int,
    background: str,
    *,
    out_width: int,
) -> bytes:
    """PNG of an already-sanitised SVG, ``out_width`` pixels wide (height in proportion), drawn
    by a JavaScript-disabled page. The budget is checked before any browser work."""
    if out_width < 1:
        raise ValueError("out_width must be positive")
    out_height = max(1, round(height * out_width / width))
    check_raster_size(out_width, out_height)
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    async with session.static_page(width=out_width, height=out_height) as page:
        html = image_page_html(uri, out_width, out_height, background)
        await page.set_content(html, wait_until="load")
        await wait_for_fonts(page)
        return await page.screenshot(type="png", animations="disabled", caret="hide")
