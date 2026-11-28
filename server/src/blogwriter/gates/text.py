"""Prose views of a text for the slop detectors: blocks, sentences and words with exact offsets.

A :class:`TextView` keeps the length of the text it was built from: everything that is not
prose (Markdown syntax, inline code, code blocks, tables, link targets, citation markers,
footnote definitions) is replaced by spaces, newlines are kept. Inline code is remembered by
position: each span counts as one specific. So any offset into ``view.text`` is the
same offset into the original, and findings computed on the view point at the article body or
the post text the editor shows.

- :func:`markdown_view` reads an article-dialect body with the publish parser's block structure
  (``markdown-it`` line maps) and masks the inline syntax of the canonical dialect.
- :func:`plain_view` reads plain text (LinkedIn posts, voice samples, pasted comments): blank
  lines separate paragraphs, bullet-like lines are list items, and a block of short lines that
  each end a sentence is read line by line (LinkedIn renders every line break).

Everything here is pure and CPU-bound: call it from a thread for long texts.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from markdown_it.token import Token

from blogwriter.publish.parser import dialect_parser

__all__ = [
    "Block",
    "BlockKind",
    "Sentence",
    "TextView",
    "Word",
    "fold",
    "markdown_view",
    "plain_view",
    "sentences",
    "words",
]

type BlockKind = Literal["paragraph", "list_item", "heading", "quote", "callout", "other"]
"""``other``: a block that is not prose (code, table, rule, image, footnote definitions). It
breaks runs of paragraphs but is never read."""


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class TextView:
    """Prose of a text with the original offsets (see the module docs)."""

    text: str
    blocks: tuple[Block, ...]
    code_spans: tuple[tuple[int, int], ...] = ()
    """Inline code in the prose (each one counts as a specific)."""

    def block_text(self, block: Block) -> str:
        return self.text[block.start : block.end]

    def prose_blocks(self) -> list[Block]:
        return [block for block in self.blocks if block.kind != "other"]


@dataclass(frozen=True, slots=True)
class Sentence:
    start: int
    end: int
    text: str
    words: int


@dataclass(frozen=True, slots=True)
class Word:
    start: int
    end: int
    text: str


# --------------------------------------------------------------------------------------------
# Words and sentences
# --------------------------------------------------------------------------------------------

_WORD_RE: Final = re.compile(r"[$€£]?\w(?:(?:[\w'\u2019\-/:@+%#]|[.,](?=\w))*[\w%])?")
"""A word: letters or digits, with inner punctuation that keeps identifiers, versions,
numbers (``2,000``, ``v1.2``), host names and contractions together."""
_HAS_ALNUM: Final = re.compile(r"[^\W_]")

_TERMINATOR_RE: Final = re.compile(r"(?:[.!?]+|…)[\"'”\u2019)\]]*(?=\s|$)")
_ABBREVIATIONS: Final = frozenset(
    {
        "e.g",
        "i.e",
        "etc",
        "vs",
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "inc",
        "ltd",
        "co",
        "corp",
        "jr",
        "sr",
        "st",
        "fig",
        "approx",
        "u.s",
        "u.k",
        "a.m",
        "p.m",
        "cf",
        "al",
        "dept",
        "mt",
        "vol",
    }
)
_LAST_TOKEN_RE: Final = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")


def words(text: str, *, offset: int = 0) -> list[Word]:
    """The words of ``text`` (offsets shifted by ``offset``)."""
    found: list[Word] = []
    for match in _WORD_RE.finditer(text):
        token = match.group(0)
        if _HAS_ALNUM.search(token):
            found.append(Word(match.start() + offset, match.end() + offset, token))
    return found


def _word_count(text: str) -> int:
    return sum(1 for match in _WORD_RE.finditer(text) if _HAS_ALNUM.search(match.group(0)))


def _is_abbreviation(text: str, end: int) -> bool:
    """Whether the period ending at ``end`` closes an abbreviation or an initial."""
    match = _LAST_TOKEN_RE.search(text[max(0, end - 12) : end])
    if match is None:
        return False
    token = match.group(1).lower().rstrip(".")
    if token in _ABBREVIATIONS:
        return True
    return len(token) == 1 and token.isalpha()  # initials: "A. B. Testing"


def sentences(text: str, *, offset: int = 0) -> list[Sentence]:
    """Sentences of one block of prose, in order, with offsets shifted by ``offset``.

    A sentence ends at ``.``, ``!``, ``?`` or ``…`` (closing quotes and brackets included)
    before whitespace or the end, except after a known abbreviation or an initial. A line break
    also ends a sentence when the line ends with terminal punctuation (plain text).
    """
    found: list[Sentence] = []
    start = 0
    for match in _TERMINATOR_RE.finditer(text):
        single_period = len(match.group(0).rstrip("\"'”\u2019)]")) == 1
        if (
            match.group(0).startswith(".")
            and single_period
            and _is_abbreviation(text, match.start() + 1)
        ):
            continue
        _append_sentence(found, text, start, match.end(), offset)
        start = match.end()
    _append_sentence(found, text, start, len(text), offset)
    return found


def _append_sentence(found: list[Sentence], text: str, start: int, end: int, offset: int) -> None:
    piece = text[start:end]
    stripped = piece.strip()
    if not stripped or not _HAS_ALNUM.search(stripped):
        return
    lead = len(piece) - len(piece.lstrip())
    first = start + lead
    last = first + len(stripped)
    found.append(Sentence(first + offset, last + offset, stripped, _word_count(stripped)))


def fold(text: str) -> tuple[str, list[int]]:
    """``text`` lowercased with whitespace runs collapsed to one space, and the offset in
    ``text`` of every character kept (the folding of ``domain.research.verbatim_spans``)."""
    out: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if in_space:
                continue
            out.append(" ")
            in_space = True
        else:
            lowered = char.lower()
            out.append(lowered if len(lowered) == 1 else char)
            in_space = False
        offsets.append(index)
    return "".join(out), offsets


# --------------------------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------------------------

_BULLET_RE: Final = re.compile(
    r"[ \t]*(?:[-*+•·‣▪▸►→➜➤✓✔✅❌✗👉🔹🔸▶◆◇■□●○]|\d{1,2}[.)]|[a-z][.)])[ \t]+"
)
_LINE_END_RE: Final = re.compile(r"(?:[.!?…:]|[\"'”\u2019)\]])\s*$|[^\x00-\u2fff]\s*$")
"""A line that ends a sentence (terminal punctuation, a closing quote, or an emoji)."""


def plain_view(text: str) -> TextView:
    """The prose view of plain text (nothing is masked; blocks come from the line structure)."""
    lines: list[tuple[int, int]] = []
    position = 0
    for line in text.split("\n"):
        lines.append((position, position + len(line)))
        position += len(line) + 1
    blocks: list[Block] = []
    group: list[tuple[int, int]] = []

    def flush() -> None:
        if group:
            blocks.extend(_plain_group(text, list(group)))
            group.clear()

    for start, end in lines:
        if text[start:end].strip():
            group.append((start, end))
        else:
            flush()
    flush()
    return TextView(text=text, blocks=tuple(blocks))


def _plain_group(text: str, group: list[tuple[int, int]]) -> list[Block]:
    """Blocks of a run of non-blank lines."""
    out: list[Block] = []
    paragraph: list[tuple[int, int]] = []

    def close_paragraph() -> None:
        if not paragraph:
            return
        line_texts = [text[s:e] for s, e in paragraph]
        separate = len(paragraph) > 1 and all(_LINE_END_RE.search(t) for t in line_texts)
        if separate:
            out.extend(_trimmed(text, "paragraph", s, e) for s, e in paragraph)
        else:
            out.append(_trimmed(text, "paragraph", paragraph[0][0], paragraph[-1][1]))
        paragraph.clear()

    for start, end in group:
        bullet = _BULLET_RE.match(text, start, end)
        if bullet is not None:
            close_paragraph()
            out.append(_trimmed(text, "list_item", bullet.end(), end))
        else:
            paragraph.append((start, end))
    close_paragraph()
    return [block for block in out if block.end > block.start]


def _trimmed(text: str, kind: BlockKind, start: int, end: int) -> Block:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Block(kind, start, end)


# --------------------------------------------------------------------------------------------
# Markdown (article dialect body)
# --------------------------------------------------------------------------------------------

_PREFIX_RE: Final = re.compile(
    r"[ \t]*(?:>[ \t]?)*[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?"
)
_HEADING_PREFIX_RE: Final = re.compile(r"[ \t]*#{1,6}[ \t]+")
_HEADING_SUFFIX_RE: Final = re.compile(r"[ \t]+#+[ \t]*$")
_LINK_DEST: Final = r"(?:<[^<>\n]*>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:[ \t]+\"[^\"\n]*\")?"
# Link text and alt never hold a raw bracket in the canonical dialect (they are written as
# &#91; / &#93;), so a bracket ends the text: linear on any input, even "[[[[...".
_IMAGE_RE: Final = re.compile(rf"!\[(?:\\.|[^\[\]\\\n])*\]\({_LINK_DEST}\)")
_LINK_RE: Final = re.compile(rf"\[((?:\\.|[^\[\]\\\n])*)\]\({_LINK_DEST}\)")
_FOOTNOTE_REF_RE: Final = re.compile(r"\[\^[^\]\s]+\]")
_ESCAPE_RE: Final = re.compile(r"\\(?=[!-/:-@\[-`{-~])")
_ENTITY_RE: Final = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_EMPHASIS_RE: Final = re.compile(r"(?<!\\)(?:\*+|(?<![\w])_+|_+(?![\w]))")
_HARD_BREAK_RE: Final = re.compile(r"\\$")
_BACKTICKS_RE: Final = re.compile(r"`+")

_CONTAINER_KINDS: Final[dict[str, BlockKind]] = {
    "bullet_list_open": "list_item",
    "ordered_list_open": "list_item",
    "blockquote_open": "quote",
    "footnote_reference_open": "other",
}
_OTHER_BLOCKS: Final = frozenset({"fence", "code_block", "html_block", "hr", "table_open"})


def markdown_view(body: str) -> TextView:
    """The prose view of an article-dialect body (see the module docs)."""
    chars = ["\n" if char == "\n" else " " for char in body]
    line_starts = [0]
    for index, char in enumerate(body):
        if char == "\n":
            line_starts.append(index + 1)
    blocks: list[Block] = []
    code_spans: list[tuple[int, int]] = []
    tokens = dialect_parser(footnotes_to_end=False).parse(body, {})
    for kind, line_range in _block_lines(tokens):
        first_line, last_line = line_range
        if kind == "other":
            start = line_starts[first_line] if first_line < len(line_starts) else len(body)
            blocks.append(Block("other", start, start))
            continue
        start, end = len(body), 0
        for line_number in range(first_line, min(last_line, len(line_starts))):
            line_start = line_starts[line_number]
            line_end = body.find("\n", line_start)
            line_end = len(body) if line_end == -1 else line_end
            span = _unmask_line(
                body, line_start, line_end, chars, code_spans, heading=kind == "heading"
            )
            if span is not None:
                start, end = min(start, span[0]), max(end, span[1])
        if end > start and _HAS_ALNUM.search("".join(chars[start:end])):
            blocks.append(Block(kind, start, end))
        else:
            anchor = line_starts[first_line] if first_line < len(line_starts) else len(body)
            blocks.append(Block("other", anchor, anchor))
    return TextView(text="".join(chars), blocks=tuple(blocks), code_spans=tuple(code_spans))


def _block_lines(tokens: Sequence[Token]) -> Iterator[tuple[BlockKind, tuple[int, int]]]:
    """``(kind, (first line, end line))`` of every block in document order."""
    stack: list[BlockKind] = []
    skip_until: str | None = None
    for token in tokens:
        if skip_until is not None:
            if token.type == skip_until and token.level == 0:
                skip_until = None
            continue
        if token.type in _OTHER_BLOCKS and token.map:
            yield "other", (token.map[0], token.map[1])
            if token.type == "table_open":
                skip_until = "table_close"
            continue
        if token.type in _CONTAINER_KINDS:
            stack.append(_CONTAINER_KINDS[token.type])
        elif token.type.startswith("container_") and token.type.endswith("_open"):
            stack.append("callout")
        elif (
            token.type in ("bullet_list_close", "ordered_list_close", "blockquote_close")
            or token.type == "footnote_reference_close"
            or (token.type.startswith("container_") and token.type.endswith("_close"))
        ):
            if stack:
                stack.pop()
        elif token.type == "heading_open" and token.map:
            yield ("other" if "other" in stack else "heading"), (token.map[0], token.map[1])
        elif token.type == "paragraph_open" and token.map:
            yield _paragraph_kind(stack), (token.map[0], token.map[1])


def _paragraph_kind(stack: Sequence[BlockKind]) -> BlockKind:
    if "other" in stack:
        return "other"
    if not stack:
        return "paragraph"
    return stack[-1]


def _unmask_line(
    body: str,
    line_start: int,
    line_end: int,
    chars: list[str],
    code_spans: list[tuple[int, int]],
    *,
    heading: bool,
) -> tuple[int, int] | None:
    """Copy the prose of one line into ``chars``; return the copied span (or ``None``)."""
    line = body[line_start:line_end]
    prefix = (_HEADING_PREFIX_RE if heading else _PREFIX_RE).match(line)
    begin = prefix.end() if prefix else 0
    finish = len(line)
    if heading:
        suffix = _HEADING_SUFFIX_RE.search(line, begin)
        if suffix is not None:
            finish = suffix.start()
    segment = line[begin:finish]
    blank = [False] * len(segment)
    protected = [False] * len(segment)
    line_code: list[tuple[int, int]] = []

    for open_start, open_end, close_start, close_end in _code_spans(segment):
        for index in range(open_start, open_end):
            blank[index] = True
        for index in range(close_start, close_end):
            blank[index] = True
        for index in range(open_end, close_start):
            protected[index] = True
            blank[index] = True  # code is a specific, never prose (it is counted by position)
        span = (line_start + begin + open_end, line_start + begin + close_start)
        code_spans.append(span)
        line_code.append(span)

    def mask(start: int, end: int) -> None:
        for index in range(start, end):
            if not protected[index]:
                blank[index] = True

    for match in _IMAGE_RE.finditer(segment):
        if not protected[match.start()]:
            mask(match.start(), match.end())
    for match in _LINK_RE.finditer(segment):
        if protected[match.start()] or blank[match.start()]:
            continue
        mask(match.start(), match.start(1))  # "["
        mask(match.end(1), match.end())  # "](target)"
    for pattern in (_FOOTNOTE_REF_RE, _ENTITY_RE, _ESCAPE_RE, _EMPHASIS_RE, _HARD_BREAK_RE):
        for match in pattern.finditer(segment):
            if not protected[match.start()]:
                mask(match.start(), match.end())

    first: int | None = None
    last = 0
    for index, char in enumerate(segment):
        if blank[index] or char == "\n":
            continue
        position = line_start + begin + index
        chars[position] = char
        if not char.isspace():
            first = position if first is None else first
            last = position + 1
    for start, end in line_code:
        first = start if first is None else min(first, start)
        last = max(last, end)
    return None if first is None else (first, last)


def _code_spans(segment: str) -> list[tuple[int, int, int, int]]:
    """Inline code spans: ``(open start, open end, close start, close end)``."""
    spans: list[tuple[int, int, int, int]] = []
    position = 0
    while True:
        opening = _BACKTICKS_RE.search(segment, position)
        if opening is None:
            return spans
        if opening.start() > 0 and segment[opening.start() - 1] == "\\":
            position = opening.end()
            continue
        width = opening.end() - opening.start()
        search = opening.end()
        closing: re.Match[str] | None = None
        while True:
            candidate = _BACKTICKS_RE.search(segment, search)
            if candidate is None:
                break
            if candidate.end() - candidate.start() == width:
                closing = candidate
                break
            search = candidate.end()
        if closing is None:
            position = opening.end()
            continue
        spans.append((opening.start(), opening.end(), closing.start(), closing.end()))
        position = closing.end()
