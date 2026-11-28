"""Heading ids: ASCII kebab-case, de-duplicated, never colliding with footnote ids.

The ids match the sanitizer profile's heading-id pattern (``^[a-z0-9]+(?:-[a-z0-9]+)*$``) and are
not prefixed (``clobberPrefix ''``), so ``toc[].id`` equals the ``id`` in ``htmlContent``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

__all__ = ["MAX_ID_LENGTH", "Slugger", "slugify"]

MAX_ID_LENGTH: Final = 64
_NON_ALNUM_RE: Final = re.compile(r"[^a-z0-9]+")
# Footnote anchors rendered by mdit_py_plugins.footnote use these ids.
_RESERVED_RE: Final = re.compile(r"fn(?:ref)?[0-9]+")


def slugify(text: str) -> str:
    """``"Ünïcödé — headings: résumé"`` -> ``"unicode-headings-resume"``.

    Accents are folded to ASCII (NFKD, combining marks dropped); everything that is not
    ``[a-z0-9]`` becomes a hyphen. Text without any ASCII letter or digit (for example a heading
    entirely in CJK) yields ``"section"``.
    """
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = _NON_ALNUM_RE.sub("-", ascii_text.lower()).strip("-")
    if len(slug) > MAX_ID_LENGTH:
        cut = slug[:MAX_ID_LENGTH]
        # Cut at a word boundary unless the limit already falls on one.
        if slug[MAX_ID_LENGTH] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        slug = cut.strip("-")
    return slug or "section"


class Slugger:
    """Hands out unique ids in document order, GitHub-style (``x``, ``x-1``, ``x-2``)."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def slug(self, text: str) -> str:
        base = slugify(text)
        candidate = base
        counter = 0
        while candidate in self._used or _RESERVED_RE.fullmatch(candidate):
            counter += 1
            candidate = f"{base}-{counter}"
        self._used.add(candidate)
        return candidate
