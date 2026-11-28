"""Text helpers: cleaning untrusted feed text, prompt-safe JSON, slop checks on model text.

Feed titles, publisher names and search-result titles are untrusted third-party text. They are
reduced to one line of plain text (no markup, no control or bidirectional formatting
characters) before they are stored on an idea or placed in a prompt.

Model-written angles go into the owner's inbox and may later seed a draft, so they follow the
plan's slop rules: a formula opener, an AI tell, a link or a hashtag discards the angle (the
idea itself is kept). AI tells are :data:`blogwriter.linkedin.lint.AI_TELLS`, matched with the
same recipe as ``linkedin.lint`` (``regex`` patterns, curly apostrophes folded, letter and digit
boundaries), never plain substring matching.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from typing import Final

import regex

from blogwriter.linkedin.lint import AI_TELLS

__all__ = ["clean_text", "prompt_json", "slop_codes"]

_TAG_RE: Final = re.compile(r"<[^<>]{0,500}>")
_SPACE_RE: Final = re.compile(r"\s+")
_ELLIPSIS: Final = "\u2026"

_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})


def _phrase(pattern: str) -> regex.Pattern[str]:
    return regex.compile(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])")


#: The plan's banned formula openers ("Unpopular opinion", "Here's the thing", "Let that sink
#: in", "X is dead", "Most people get X wrong", "Nobody talks about"), checked at the start.
_OPENERS: Final = tuple(
    regex.compile(rf"(?i)^[\s\W]{{0,4}}{pattern}")
    for pattern in (
        r"unpopular\s+opinion\b",
        r"here(?:'s|\s+is)\s+the\s+thing\b",
        r"nobody\s+(?:is\s+)?talk(?:s|ing)\s+about\b",
        r"most\s+people\s+get\s+.{1,60}?\s+wrong\b",
        r"[\w .#+-]{1,40}?\s+is\s+dead(?=\s*(?:[.!:,]|$))",
        r"let\s+that\s+sink\s+in\b",
    )
)
_ANYWHERE: Final = (_phrase(r"let\s+that\s+sink\s+in\b"),)
_AI_TELLS: Final = tuple(_phrase(pattern) for pattern in AI_TELLS)
_LINK: Final = regex.compile(r"(?i)\b(?:https?://|www\.)\S+")
_HASHTAG: Final = regex.compile(r"(?<![\p{L}\p{N}&])#\p{L}[\p{L}\p{N}_]*")


def _drop_invisible(text: str) -> str:
    # Control characters become spaces (they may separate words); format characters such as
    # bidirectional overrides and zero-width joiners are removed.
    out: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cc":
            out.append(" ")
        elif category in ("Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(char)
    return "".join(out)


def clean_text(value: object, *, max_chars: int) -> str | None:
    """One line of plain text from untrusted ``value``, at most ``max_chars`` long, or ``None``.

    Entities are decoded twice (feeds often double-encode), tags removed, control and
    formatting characters dropped, whitespace collapsed. Longer text is cut at a word boundary
    and ends with an ellipsis.
    """
    if not isinstance(value, str) or max_chars < 2:
        return None
    text = html.unescape(html.unescape(value))
    text = _TAG_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", _drop_invisible(text)).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    if text[max_chars - 1] != " ":  # the cut lands inside a word: end at the last space
        space = cut.rfind(" ")
        if space >= max_chars // 2:
            cut = cut[:space]
    return cut.rstrip(" ,;:-") + _ELLIPSIS


def prompt_json(value: object) -> str:
    """JSON for a delimited prompt section: ``<`` and ``>`` are escaped, so untrusted strings
    can never close or open the section's tags."""
    text = json.dumps(value, ensure_ascii=False, indent=1, sort_keys=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def slop_codes(text: str) -> list[str]:
    """Why ``text`` breaks the slop rules: ``formula-opener``, ``ai-tell``, ``link``,
    ``hashtag`` (empty when it is clean)."""
    folded = text.translate(_APOSTROPHES)
    codes: list[str] = []
    if any(pattern.match(folded) for pattern in _OPENERS) or any(
        pattern.search(folded) for pattern in _ANYWHERE
    ):
        codes.append("formula-opener")
    if any(pattern.search(folded) for pattern in _AI_TELLS):
        codes.append("ai-tell")
    if _LINK.search(folded):
        codes.append("link")
    if _HASHTAG.search(folded):
        codes.append("hashtag")
    return codes
