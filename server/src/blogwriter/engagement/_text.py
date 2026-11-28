"""Small text helpers shared by the engagement modules. Pure functions, no I/O.

Offsets are code-point indexes into the text they were computed on. Words are runs of letters
and digits with inner apostrophes (``don't``), case-folded, curly apostrophes read as ``'``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

import regex

__all__ = [
    "MAX_PASTED_CHARS",
    "clip",
    "normalize_model_text",
    "normalize_owner_text",
    "normalize_third_party",
    "relevance_terms",
    "word_spans",
    "words",
]

MAX_PASTED_CHARS: Final = 5000
"""Other members' text kept on an item (``EngagementItem.pastedText``)."""

_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIBLE: Final = regex.compile(r"\p{Default_Ignorable_Code_Point}")
"""Zero-width characters, soft hyphens, bidi controls, variation selectors and Unicode tag
characters: they hide text from a reader (and a blocklist) but not from a model."""
_BLANK_RUN: Final = re.compile(r"\n{3,}")
_WORD: Final = regex.compile(r"[\p{L}\p{N}]+(?:['\u2019][\p{L}\p{N}]+)*")
_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})

_STOPWORD_TEXT: Final = """
    a about above after again against all also am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do does
    doesn't doing don't down during each even ever every few for from further get gets got had
    hadn't has hasn't have haven't having he her here hers herself him himself his how i i'd i'll
    i'm i've if in into is isn't it it's its itself just let's like make makes many me more most
    much must my myself no nor not now of off on once one only or other our ours ourselves out
    over own really same say says she should shouldn't so some still such than that that's the
    their theirs them themselves then there there's these they they're this those through to too
    under until up us very was wasn't way we we're we've were weren't what what's when where
    which while who who's whom why will with won't would wouldn't yes yet you you're you've your
    yours yourself yourselves thing things think lot lots great post thanks thank good new
"""
_STOPWORDS: Final = frozenset(_STOPWORD_TEXT.split())


def normalize_third_party(text: str) -> str:
    """Other members' text as it is kept and sent to a model.

    NFKC folds styled "bold" letters and full-width forms into plain ones (so the blocklist sees
    what a reader sees), line endings become ``\\n``, control and invisible characters are
    removed, trailing spaces are trimmed per line, at most one blank line separates paragraphs,
    and the whole text is stripped.
    """
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    unified = unicodedata.normalize("NFKC", unified)
    unified = _CONTROL.sub("", unified)
    unified = _INVISIBLE.sub("", unified)
    unified = "\n".join(line.rstrip() for line in unified.split("\n"))
    return _BLANK_RUN.sub("\n\n", unified).strip()


def normalize_owner_text(text: str) -> str:
    """The owner's (or a model's) reply text: line endings ``\\n``, control characters removed,
    at most one blank line in a row, stripped. Letters are left as written."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    unified = _CONTROL.sub("", unified)
    unified = "\n".join(line.rstrip() for line in unified.split("\n"))
    return _BLANK_RUN.sub("\n\n", unified).strip()


def normalize_model_text(text: str) -> str:
    """Text a model wrote for the owner: :func:`normalize_owner_text` without invisible
    characters (a model has no reason to emit them)."""
    return normalize_owner_text(_INVISIBLE.sub("", text))


def clip(text: str, limit: int) -> str:
    """``text`` stripped and cut to at most ``limit`` characters, at a sentence end or a word
    boundary near the end when there is one."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    cut = stripped[:limit]
    sentence_end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    if sentence_end >= limit * 0.6:
        return cut[: sentence_end + 1].rstrip()
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip()


def word_spans(text: str) -> list[tuple[str, int, int]]:
    """``(word, start, end)`` for every word of ``text`` (case-folded, apostrophes unified)."""
    return [
        (match.group(0).casefold().translate(_APOSTROPHES), match.start(), match.end())
        for match in _WORD.finditer(text)
    ]


def words(text: str) -> list[str]:
    """The words of ``text`` (see :func:`word_spans`)."""
    return [word for word, _, _ in word_spans(text)]


def relevance_terms(texts: str | Iterable[str]) -> frozenset[str]:
    """Content words (three or more characters, not a stop word) for relevance ranking."""
    items = [texts] if isinstance(texts, str) else list(texts)
    return frozenset(
        word for item in items for word in words(item) if len(word) >= 3 and word not in _STOPWORDS
    )
