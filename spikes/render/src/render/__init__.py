"""Isolated Chromium rendering for Blog Writer v2: carousels (PDF + cover PNG), OG cards, Mermaid.

Graduates into ``server/render`` (plan: browser, carousel, og, mermaid, pdf, templates/).
"""

from .brand import BrandColors, BrandKit, contrast_ratio
from .browser import RenderLimiter, RenderSession
from .carousel import RenderedDeck, render_carousel, render_carousel_in
from .errors import (
    AssetIntegrityError,
    LayoutOverflowError,
    MermaidError,
    OutputLimitError,
    RenderError,
    UnsafeSvgError,
)
from .lint import LintIssue, lint_deck
from .mermaid import MermaidRenderer, MermaidResult, render_mermaid
from .og import render_og_card, render_og_card_in
from .settings import RenderSettings
from .spec import DeckSpec, OgSpec

__all__ = [
    "AssetIntegrityError",
    "BrandColors",
    "BrandKit",
    "DeckSpec",
    "LayoutOverflowError",
    "LintIssue",
    "MermaidError",
    "MermaidRenderer",
    "MermaidResult",
    "OgSpec",
    "OutputLimitError",
    "RenderError",
    "RenderLimiter",
    "RenderSession",
    "RenderSettings",
    "RenderedDeck",
    "UnsafeSvgError",
    "contrast_ratio",
    "lint_deck",
    "render_carousel",
    "render_carousel_in",
    "render_mermaid",
    "render_og_card",
    "render_og_card_in",
]
