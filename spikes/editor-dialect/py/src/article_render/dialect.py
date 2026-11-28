"""Shared constants of the article dialect.

Twin of ``web/src/dialect/constants.ts``; the cross-language tests keep them
in step.
"""

from __future__ import annotations

import re
from typing import Final

CALLOUT_TYPES: Final = ("note", "tip", "warning")
"""Callout container types (``:::note``, ``:::tip``, ``:::warning``)."""

MERMAID_LANGUAGE: Final = "mermaid"

BODY_HEADING_LEVELS: Final = (2, 3, 4)
"""Heading levels allowed in an article body (the title is the page's h1)."""

TOC_HEADING_LEVELS: Final = (2, 3)
"""Headings that get an ``id`` and a table-of-contents entry."""

SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
"""Slug-like values: frontmatter slug/pillar/format/tags/series.id, heading ids."""

CITATION_ID_RE: Final = SLUG_RE
CITATION_ID_MAX_LENGTH: Final = 64

LANGUAGE_RE: Final = re.compile(r"^[a-z0-9][a-z0-9+#._-]{0,31}$")
"""A fenced code block info string must be exactly one lowercase language name."""

MAX_SOURCE_BYTES: Final = 512 * 1024
"""Upper bound on an article's Markdown, to bound rendering time (markdown-it DoS advisories)."""


def info_language(info: str) -> str:
    """First word of a fence info string, lowercased (the CommonMark "language")."""
    parts = info.strip().split(maxsplit=1)
    return parts[0].lower() if parts else ""


def normalize_source(source: str) -> str:
    """UTF-8 text with ``\\n`` line endings and no byte-order mark."""
    return source.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
