"""The post serializer: the display text and the escaped API payload, kept in lockstep.

A post stores two representations (plan: "LinkedIn Studio / Serializer"):

- ``displayText``: what readers see. The ready-to-post kit copies it and every count uses it.
- ``payload``: the escaped little-text ``commentary`` sent to ``POST /rest/posts``.

Both come from one token sequence (:func:`blogwriter.linkedin.littletext.parse_display`), so for
every saved post::

    payload == api_commentary(parse_display(displayText, post.hashtags))
    unescape(payload) == displayText

``post.hashtags`` is the post's approved list (a subset of its pillar's hashtags, canonical
spelling): only those become real hashtags; any other ``#word`` stays escaped text.

Links are never inferred: a URL in the text is escaped like any other text. The one per-post
link exception must stand on a line of its own (lint enforces it), which makes the payload
byte-identical to the serializer's ``Link`` token layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import regex

from blogwriter.domain.posts import Post
from blogwriter.linkedin.littletext import (
    LittleTextError,
    api_commentary,
    display_text,
    parse_display,
)

__all__ = [
    "LittleTextError",
    "clean_text",
    "payload_matches",
    "payload_units",
    "serialize",
]

# Control characters other than newline and tab (little text refuses them), and lone surrogates.
_CONTROL: Final = regex.compile(r"(?![\t\n])[\p{Cc}\p{Cs}]")
_TRAILING_SPACE: Final = regex.compile(r"[ \t]+(?=\n)")
_BLANK_RUN: Final = regex.compile(r"\n{3,}")


def serialize(display_text_in: str, approved_hashtags: Sequence[str]) -> tuple[str, str]:
    """``(display, payload)`` for ``display_text_in``.

    ``display`` is the normalized display text (line breaks as ``\\n``, approved hashtags in
    their approved spelling); store it as the post's ``displayText``. ``payload`` is
    ``api_commentary`` of the same tokens. Raises
    :class:`~blogwriter.linkedin.littletext.LittleTextError` for text little text cannot carry
    (control characters) or an invalid approved list.
    """
    tokens = parse_display(display_text_in, list(approved_hashtags))
    return display_text(tokens), api_commentary(tokens)


def payload_units(payload: str) -> int:
    """LinkedIn's length measure as lint counts it: UTF-16 code units of the escaped payload."""
    return len(payload.encode("utf-16-le")) // 2


def payload_matches(post: Post) -> bool:
    """Whether ``post.payload`` is exactly the serializer's payload of its display text."""
    try:
        expected = api_commentary(parse_display(post.display_text, list(post.hashtags)))
    except LittleTextError:
        return False
    return expected == post.payload


def clean_text(text: str) -> str:
    """Model or pasted text made safe for the serializer: line breaks normalized, control
    characters dropped, trailing spaces and runs of blank lines trimmed, ends stripped."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _CONTROL.sub("", normalized)
    normalized = _TRAILING_SPACE.sub("", normalized)
    normalized = _BLANK_RUN.sub("\n\n", normalized)
    return normalized.strip()
