"""Exception hierarchy for the render package.

Messages never echo user content back verbatim beyond short, escaped excerpts, because they end up in
job events and structured logs (plan: never log prompts, completions or fetched content).
"""

from __future__ import annotations


class RenderError(Exception):
    """Base class for every failure raised by :mod:`render`."""


class AssetIntegrityError(RenderError):
    """A bundled asset (font, Mermaid bundle) is missing or does not match its pinned SHA-256."""


class LayoutOverflowError(RenderError):
    """Text does not fit its box. The spec must be shortened; we never ship clipped slides."""

    def __init__(self, overflows: list[str]) -> None:
        self.overflows = overflows
        super().__init__("content overflows its box: " + "; ".join(overflows))


class OutputLimitError(RenderError):
    """Rendered output breaks a hard limit (page size/count, byte size)."""


class MermaidError(RenderError):
    """Mermaid rejected the diagram source (syntax error, size limit, timeout)."""


class UnsafeSvgError(RenderError):
    """SVG could not be parsed safely (DTD/entities, not SVG, too large)."""
