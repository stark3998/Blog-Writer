"""Plain-text field normalisation.

Every user-facing field in a slide spec or brand is *plain text*: it is never parsed as HTML or
Markdown, and Jinja2 autoescaping turns it into inert text. On top of that this module removes
characters that are invisible on the slide but change meaning elsewhere:

* C0/C1 control characters are rejected (they have no place in slide copy).
* Unicode format characters (category ``Cf``: zero-width spaces, bidi overrides such as U+202E,
  BOM) are stripped. They enable "Trojan Source"-style spoofing and would let a blocklisted
  client name slip past the confidentiality gate (``Cli\\u200bent`` renders exactly like
  ``Client``). So are the other *default-ignorable* code points that are not ``Cf`` (combining
  grapheme joiner, variation selectors, Hangul fillers, Khmer and Mongolian invisibles):
  ``Cli\\u034fent`` draws exactly like ``Client`` too. The zero-width joiner and VS16 are kept only
  where emoji sequences need them (not after a letter, digit or space); gates must still normalise
  with :func:`gate_normalise`.
* Whitespace (including newlines and tabs) collapses to single spaces; fields are single
  paragraphs.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator

__all__ = ["PlainText", "gate_normalise", "normalise_plain_text", "word_count"]

_ZWJ: Final = "\u200d"
_VS16: Final = "\ufe0f"  # emoji presentation selector
#: Default_Ignorable_Code_Point (Unicode DerivedCoreProperties.txt) members outside category Cf
#: (all of Cf is stripped anyway; the unassigned members are rejected as Cn).
_IGNORABLE_NON_CF: Final = frozenset(
    [
        *(0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F, 0x3164, 0xFFA0),
        *range(0xFE00, 0xFE10),  # VS1-VS16
        *range(0xE0100, 0xE01F0),  # VS17-VS256
    ]
)
_WS: Final = re.compile(r"\s+")
_WORD: Final = re.compile(r"\S+")


def normalise_plain_text(value: str) -> str:
    """NFC, invisible characters stripped, whitespace collapsed. Raises ``ValueError`` for
    control, surrogate, private-use and unassigned code points."""
    value = unicodedata.normalize("NFC", value)
    out: list[str] = []
    for ch in value:
        if ch in "\t\n\r\f\v":
            out.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat == "Cc":
            raise ValueError(f"control character U+{ord(ch):04X} is not allowed in slide text")
        if ch in (_ZWJ, _VS16):
            # Emoji sequences need these; after a letter, digit or space they can only hide a split.
            if out and unicodedata.category(out[-1])[0] not in "LNZ":
                out.append(ch)
            continue
        if cat == "Cf" or ord(ch) in _IGNORABLE_NON_CF:
            continue  # invisible: bidi overrides, zero-width spaces, BOM, variation selectors
        if cat in {"Cs", "Co", "Cn"}:
            raise ValueError(f"character U+{ord(ch):04X} is not allowed in slide text")
        out.append(ch)
    return _WS.sub(" ", "".join(out)).strip()


def gate_normalise(value: str) -> str:
    """Normalisation for matching (blocklist, near-duplicate): also drops emoji joiners and
    casefolds."""
    return normalise_plain_text(value).replace(_ZWJ, "").replace(_VS16, "").casefold()


def word_count(value: str) -> int:
    return len(_WORD.findall(value))


def _non_empty(value: str) -> str:
    value = normalise_plain_text(value)
    if not value:
        raise ValueError("must not be empty")
    return value


PlainText = Annotated[str, AfterValidator(_non_empty)]
"""A required, normalised, single-paragraph plain-text field (add ``Field(max_length=)``)."""
