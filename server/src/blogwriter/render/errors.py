"""Exception hierarchy for :mod:`blogwriter.render`.

Messages never echo user content back beyond short excerpts (a Mermaid parse error names the
offending token), because they reach job events. They are never logged: the structured logger
records the exception type and frames only.

Suggested HTTP mapping for routes: :class:`RenderUnavailable` 503, :class:`RenderTimeout` 504,
:class:`AssetIntegrityError` 500, everything else 422 (the spec or the brand must change).
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "AssetIntegrityError",
    "DiagramLabelError",
    "HeadshotError",
    "LayoutOverflowError",
    "MermaidError",
    "OutputLimitError",
    "RenderError",
    "RenderTimeout",
    "RenderUnavailable",
    "UnsafeSvgError",
]


class RenderError(Exception):
    """Base class for every failure raised by :mod:`blogwriter.render`."""


class RenderUnavailable(RenderError):
    """Rendering is switched off in this process (no Chromium headless shell, or the Playwright
    driver's environment cannot be confined). The rest of the app keeps running."""


class RenderTimeout(RenderError):
    """A render job exceeded ``BW_RENDER_JOB_TIMEOUT_S`` (driver start and launch included)."""


class AssetIntegrityError(RenderError):
    """A bundled asset (font, Mermaid bundle) is missing or does not match its pinned SHA-256."""


class LayoutOverflowError(RenderError):
    """Text does not fit its box. The spec must be shortened; clipped slides never ship."""

    def __init__(self, overflows: Sequence[str]) -> None:
        self.overflows: tuple[str, ...] = tuple(overflows)
        super().__init__("content overflows its box: " + "; ".join(self.overflows))


class DiagramLabelError(RenderError):
    """A diagram would be drawn so small on its slide that its labels fall below the 18 px floor."""

    def __init__(self, slide: int, effective_px: float) -> None:
        self.slide = slide
        self.effective_px = effective_px
        super().__init__(
            f"slide {slide}: diagram labels would render at {effective_px:.1f}px "
            "(minimum 18px); simplify or split the diagram"
        )


class OutputLimitError(RenderError):
    """Rendered output breaks a hard limit (page size or count, byte size, raster budget)."""


class MermaidError(RenderError):
    """Mermaid rejected the diagram source (syntax error, size limit, unsupported type, timeout)."""


class UnsafeSvgError(RenderError):
    """SVG could not be parsed safely (DTD or entities, not SVG, too large) or failed the
    checker."""


class HeadshotError(RenderError):
    """The brand headshot is not a usable PNG, JPEG or WebP image."""
