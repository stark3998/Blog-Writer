"""Shared constants and small helpers of the article dialect (``docs/v2/article-dialect.md``).

Ported from the Phase 0 spike (``spikes/editor-dialect/py``). The web editor has twins of these
values; the shared corpus in ``contracts/fixtures/corpus`` keeps the two sides in step.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "BODY_HEADING_LEVELS",
    "CALLOUT_TYPES",
    "CITATION_ID_MAX_LENGTH",
    "CITATION_ID_RE",
    "JS_WHITESPACE",
    "LANGUAGE_RE",
    "MAX_SOURCE_BYTES",
    "MERMAID_LANGUAGE",
    "SLUG_RE",
    "TOC_HEADING_LEVELS",
    "SourceTooLargeError",
    "check_source_size",
    "info_language",
    "js_trim",
    "js_trim_end",
    "js_trim_start",
    "normalize_source",
]

CALLOUT_TYPES: Final = ("note", "tip", "warning")
"""Callout container types (``:::note``, ``:::tip``, ``:::warning``)."""

MERMAID_LANGUAGE: Final = "mermaid"

BODY_HEADING_LEVELS: Final = (2, 3, 4)
"""Heading levels allowed in an article body (the title is the page's h1)."""

TOC_HEADING_LEVELS: Final = (2, 3)
"""Headings that get an ``id`` and a table-of-contents entry."""

SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
"""Slug-like values: frontmatter slug/pillar/format/tags/series.id, citation labels."""

CITATION_ID_RE: Final = SLUG_RE
CITATION_ID_MAX_LENGTH: Final = 64

LANGUAGE_RE: Final = re.compile(r"^[a-z0-9][a-z0-9+#._-]{0,31}\Z")
"""A fenced code block info string must be exactly one lowercase language name."""

MAX_SOURCE_BYTES: Final = 512 * 1024
"""The server's cap on an article's Markdown (UTF-8 bytes). Bounds parsing time (markdown-it
DoS advisories); the editor caps documents at 128 KB."""

JS_WHITESPACE: Final = (
    "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
    "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
"""What JavaScript's ``\\s`` and ``String.prototype.trim`` treat as whitespace.

The editor (TipTap, marked) trims and splits with these semantics; Python's ``str.strip`` uses a
different set (it adds U+001C-U+001F and U+0085 and leaves out U+FEFF), so the canonical writer
uses these helpers wherever the editor uses ``trim``.
"""


class SourceTooLargeError(ValueError):
    """The article is larger than :data:`MAX_SOURCE_BYTES`; it is refused, not processed."""

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(f"the article is {size} bytes; the limit is {MAX_SOURCE_BYTES}")


def check_source_size(source: str) -> None:
    """Raise :class:`SourceTooLargeError` when ``source`` exceeds the server cap."""
    size = len(source.encode("utf-8", errors="surrogatepass"))
    if size > MAX_SOURCE_BYTES:
        raise SourceTooLargeError(size)


def info_language(info: str) -> str:
    """First word of a fence info string, lowercased (the CommonMark "language")."""
    parts = info.strip().split(maxsplit=1)
    return parts[0].lower() if parts else ""


def normalize_source(source: str) -> str:
    """UTF-8 text with ``\\n`` line endings and no byte-order mark."""
    return source.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def js_trim(text: str) -> str:
    """``String.prototype.trim``."""
    return text.strip(JS_WHITESPACE)


def js_trim_start(text: str) -> str:
    """``String.prototype.trimStart``."""
    return text.lstrip(JS_WHITESPACE)


def js_trim_end(text: str) -> str:
    """``String.prototype.trimEnd``."""
    return text.rstrip(JS_WHITESPACE)
