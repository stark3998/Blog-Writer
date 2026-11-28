"""blogwriter.render: isolated Chromium rendering of carousels, image cards, OG images and Mermaid.

Public API (all async, one isolated render job per call; see ``docs/v2/build/render.md``):

* :func:`render_carousel` ``(spec, brand) -> RenderedDeck`` (PDF, page PNGs, text layer, SHA-256,
  warnings) and :func:`carousel_preview_html` (the same self-contained HTML, for a sandboxed
  iframe);
* :func:`render_image_card` ``(slide, brand) -> PNG 1080x1350``;
* :func:`render_og_image` ``(title, subtitle, pillar_label, brand) -> PNG 1200x630``;
* :func:`render_mermaid_svg` ``(source) -> str`` (sanitised SVG), :func:`render_mermaid_png`
  ``(source, width) -> PNG`` and :func:`render_mermaid` (both, plus source warnings);
* :class:`RenderBrand`, :class:`CarouselSpec` and the slide models, :func:`lint_carousel`;
* :class:`RenderSettings` (``BW_RENDER_*``), :class:`Renderer`, and the lifecycle
  (:func:`startup`, :func:`shutdown`, :class:`RenderLifecycle`, :func:`get_renderer`).

This module is itself a lifecycle: ``create_app(lifecycles=[..., blogwriter.render])``.
"""

from blogwriter.render.brand import BrandColors, FontChoice, RenderBrand, contrast_ratio
from blogwriter.render.errors import (
    AssetIntegrityError,
    DiagramLabelError,
    HeadshotError,
    LayoutOverflowError,
    MermaidError,
    OutputLimitError,
    RenderError,
    RenderTimeout,
    RenderUnavailable,
    UnsafeSvgError,
)
from blogwriter.render.html import PREVIEW_CSP
from blogwriter.render.lifecycle import RenderLifecycle, get_renderer, shutdown, startup
from blogwriter.render.lint import RenderWarning, lint_carousel
from blogwriter.render.service import (
    MermaidDiagram,
    RenderedDeck,
    Renderer,
    RenderStatus,
    carousel_preview_html,
    get_default_renderer,
    render_carousel,
    render_image_card,
    render_mermaid,
    render_mermaid_png,
    render_mermaid_svg,
    render_og_image,
    set_default_renderer,
)
from blogwriter.render.settings import MERMAID_TINY_SHA256, MERMAID_VERSION, RenderSettings
from blogwriter.render.spec import (
    CarouselSpec,
    Cell,
    CoverSlide,
    DiagramSlide,
    FrameworkSlide,
    ListSlide,
    OgImageSpec,
    QuoteSlide,
    RecapSlide,
    Slide,
    Source,
    SourcesSlide,
    StatementSlide,
    StatSlide,
    Step,
    StepsSlide,
)
from blogwriter.render.svg_sanitize import assert_svg_safe, sanitize_svg

__all__ = [
    "MERMAID_TINY_SHA256",
    "MERMAID_VERSION",
    "PREVIEW_CSP",
    "AssetIntegrityError",
    "BrandColors",
    "CarouselSpec",
    "Cell",
    "CoverSlide",
    "DiagramLabelError",
    "DiagramSlide",
    "FontChoice",
    "FrameworkSlide",
    "HeadshotError",
    "LayoutOverflowError",
    "ListSlide",
    "MermaidDiagram",
    "MermaidError",
    "OgImageSpec",
    "OutputLimitError",
    "QuoteSlide",
    "RecapSlide",
    "RenderBrand",
    "RenderError",
    "RenderLifecycle",
    "RenderSettings",
    "RenderStatus",
    "RenderTimeout",
    "RenderUnavailable",
    "RenderWarning",
    "RenderedDeck",
    "Renderer",
    "Slide",
    "Source",
    "SourcesSlide",
    "StatSlide",
    "StatementSlide",
    "Step",
    "StepsSlide",
    "UnsafeSvgError",
    "assert_svg_safe",
    "carousel_preview_html",
    "contrast_ratio",
    "get_default_renderer",
    "get_renderer",
    "lint_carousel",
    "render_carousel",
    "render_image_card",
    "render_mermaid",
    "render_mermaid_png",
    "render_mermaid_svg",
    "render_og_image",
    "sanitize_svg",
    "set_default_renderer",
    "shutdown",
    "startup",
]
