"""Small text helpers shared by the voice modules: normalization, words, passages, prompt data.

Pure functions, no I/O. Offsets are code-point indexes into the text they were computed on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

import regex

__all__ = [
    "MAX_SAMPLE_CHARS",
    "clip",
    "dedupe_casefold",
    "leading_excerpt",
    "neutralize_tags",
    "normalize_text",
    "paragraph_spans",
    "verbatim_span",
    "word_count",
    "words",
]

MAX_SAMPLE_CHARS: Final = 20_000
"""``VoiceSample.text`` holds at most this many characters."""

_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD: Final = regex.compile(r"[\p{L}\p{N}]+(?:['\u2019][\p{L}\p{N}]+)*")
_PARAGRAPH_BREAK: Final = re.compile(r"\n[ \t]*\n\s*")
_SINGLE_QUOTES: Final = "['\u2018\u2019]"
_DOUBLE_QUOTES: Final = '["\u201c\u201d]'
_QUOTE_VARIANTS: Final = {
    "'": _SINGLE_QUOTES,
    "\u2018": _SINGLE_QUOTES,
    "\u2019": _SINGLE_QUOTES,
    '"': _DOUBLE_QUOTES,
    "\u201c": _DOUBLE_QUOTES,
    "\u201d": _DOUBLE_QUOTES,
}
_SENTENCE_END: Final = re.compile("[.!?][\"'\u201d\u2019)]?(?=\\s)")


def normalize_text(text: str) -> str:
    """Line endings as ``\\n``, C0 control characters (except tab and newline) removed, and
    surrounding whitespace stripped. Scores and spans always index the normalized text."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", unified).strip()


def clip(text: str, limit: int) -> str:
    """``text`` stripped and cut to at most ``limit`` characters (at a word boundary if one is
    near the end)."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    cut = stripped[:limit]
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip()


def words(text: str) -> list[str]:
    """The words of ``text``, case-folded (letters and digits, inner apostrophes kept)."""
    return [match.group(0).casefold() for match in _WORD.finditer(text)]


def word_count(text: str) -> int:
    """How many words ``text`` has (same tokenization as :func:`words`)."""
    return sum(1 for _ in _WORD.finditer(text))


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every paragraph (blocks separated by blank lines), trimmed."""
    spans: list[tuple[int, int]] = []
    position = 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        spans.append((position, match.start()))
        position = match.end()
    spans.append((position, len(text)))
    trimmed: list[tuple[int, int]] = []
    for start, end in spans:
        block = text[start:end]
        if not block.strip():
            continue
        lead = len(block) - len(block.lstrip())
        trail = len(block) - len(block.rstrip())
        trimmed.append((start + lead, end - trail))
    return trimmed


def verbatim_span(text: str, excerpt: str) -> tuple[int, int] | None:
    """Where ``excerpt`` occurs in ``text``, ignoring differences in whitespace and in the
    straight or curly form of quotes; ``None`` if it does not occur (or is blank)."""
    tokens = excerpt.split()
    if not tokens:
        return None
    parts: list[str] = []
    for token in tokens:
        parts.append("".join(_QUOTE_VARIANTS.get(char, re.escape(char)) for char in token))
    found = re.search(r"\s+".join(parts), text)
    if found is None:
        return None
    return found.start(), found.end()


def leading_excerpt(text: str, limit: int, *, minimum: int = 40) -> str | None:
    """A verbatim excerpt from the start of ``text``: whole paragraphs up to ``limit``
    characters, else the first paragraph cut at its last sentence end before ``limit``."""
    spans = paragraph_spans(text)
    if not spans:
        return None
    first_start = spans[0][0]
    end: int | None = None
    for _start, stop in spans:
        if stop - first_start > limit:
            break
        end = stop
    if end is not None and end - first_start >= minimum:
        return text[first_start:end]
    window = text[first_start : first_start + limit]
    cut = None
    for match in _SENTENCE_END.finditer(window):
        cut = match.end()
    if cut is None or cut < minimum:
        space = window.rfind(" ")
        cut = space if space >= minimum else None
    if cut is None:
        return None
    return window[:cut].rstrip()


def neutralize_tags(text: str, tags: Iterable[str]) -> str:
    """Break the closing (and opening) forms of our prompt delimiters inside data, so text
    supplied as data cannot end the section it sits in (``</sample>`` -> ``<\\/sample>``)."""
    out = text
    for tag in tags:
        out = re.sub(rf"<(/?)({re.escape(tag)})\b", r"<\\\1\2", out, flags=re.IGNORECASE)
    return out


def dedupe_casefold(items: Iterable[str]) -> list[str]:
    """``items`` stripped, blanks dropped, case-insensitive duplicates removed (first wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = " ".join(item.split())
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
