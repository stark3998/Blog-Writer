"""What the gates read out of an article-dialect body: links, citations, diagrams, raw HTML.

The publish parser's tokens decide what is a link (so links in code are never links), and
spans are found in the token's source lines, so findings point at the body the editor shows.
Pure and CPU-bound.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from blogwriter.publish.dialect import MERMAID_LANGUAGE, info_language
from blogwriter.publish.parser import dialect_parser

__all__ = [
    "CitationRef",
    "Diagram",
    "HtmlFragment",
    "Link",
    "body_links",
    "citation_refs",
    "line_offsets",
    "mermaid_diagrams",
    "raw_html",
]


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    start: int
    end: int
    kind: Literal["link", "image"]


@dataclass(frozen=True, slots=True)
class CitationRef:
    label: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Diagram:
    source: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class HtmlFragment:
    tag: str | None
    """The first tag name, lowercase (``iframe``, ``form``...), or ``None`` (a comment...)."""
    start: int
    end: int


def line_offsets(body: str) -> list[int]:
    """Offset of the start of every line (plus one past the end)."""
    offsets = [0]
    for index, char in enumerate(body):
        if char == "\n":
            offsets.append(index + 1)
    offsets.append(len(body) + 1)
    return offsets


def _line_span(offsets: Sequence[int], body: str, first: int, last: int) -> tuple[int, int]:
    start = offsets[min(first, len(offsets) - 1)]
    end = offsets[min(last, len(offsets) - 1)] - 1
    return min(start, len(body)), max(min(end, len(body)), min(start, len(body)))


def _inline_tokens(tokens: Sequence[Token]) -> list[Token]:
    return [token for token in tokens if token.type == "inline"]


def body_links(body: str) -> list[Link]:
    """Every link and image destination in ``body`` (citation definitions included), in order.

    Destinations are as written (the permissive parser keeps refused ones, so ``javascript:``
    is reported, not hidden).
    """
    offsets = line_offsets(body)
    parser = dialect_parser(footnotes_to_end=False, permissive_links=True)
    found: list[Link] = []
    cursor_by_line: dict[int, int] = {}
    for token in _inline_tokens(parser.parse(body, {})):
        if token.map is None:
            continue
        line_start, line_end = _line_span(offsets, body, token.map[0], token.map[1])
        for child in token.children or ():
            if child.type == "link_open":
                url = str(child.attrGet("href") or "")
                kind: Literal["link", "image"] = "link"
            elif child.type == "image":
                url = str(child.attrGet("src") or "")
                kind = "image"
            else:
                continue
            cursor = cursor_by_line.get(token.map[0], line_start)
            position = body.find(url, cursor, line_end) if url else -1
            if position == -1:
                start, end = line_start, line_end
            else:
                start, end = position, position + len(url)
                cursor_by_line[token.map[0]] = end
            found.append(Link(url=url, start=start, end=end, kind=kind))
    return found


_CITATION_RE: Final = re.compile(r"\[\^([^\]\s]+)\]")


def citation_refs(body: str) -> list[CitationRef]:
    """Every ``[^label]`` citation in the text (not the definitions), with its span."""
    tokens = dialect_parser(footnotes_to_end=False).parse(body, {})
    offsets = line_offsets(body)
    refs: list[CitationRef] = []
    for token in _inline_tokens(tokens):
        if token.map is None:
            continue
        labels = [
            str(child.meta.get("label"))
            for child in token.children or ()
            if child.type == "footnote_ref"
        ]
        if not labels:
            continue
        line_start, line_end = _line_span(offsets, body, token.map[0], token.map[1])
        cursor = line_start
        for label in labels:
            match = _find_citation(body, label, cursor, line_end)
            if match is None:
                refs.append(CitationRef(label, line_start, line_end))
                continue
            refs.append(CitationRef(label, match[0], match[1]))
            cursor = match[1]
    return refs


def _find_citation(body: str, label: str, start: int, end: int) -> tuple[int, int] | None:
    for match in _CITATION_RE.finditer(body, start, end):
        if match.group(1) == label and body[match.end() : match.end() + 1] != ":":
            return match.start(), match.end()
    return None


def mermaid_diagrams(body: str) -> list[Diagram]:
    """Mermaid fences in document order (the sources the publish job renders)."""
    offsets = line_offsets(body)
    diagrams: list[Diagram] = []
    for token in dialect_parser().parse(body, {}):
        if token.type == "fence" and info_language(token.info) == MERMAID_LANGUAGE:
            if token.map is None:
                continue
            start, end = _line_span(offsets, body, token.map[0], token.map[1])
            diagrams.append(Diagram(source=token.content, start=start, end=end))
    return diagrams


@functools.cache
def _html_probe() -> MarkdownIt:
    """The dialect's block structure with HTML recognition on (finds raw HTML, not code)."""
    return MarkdownIt("commonmark", {"html": True}).enable("table")


_TAG_RE: Final = re.compile(r"<\s*/?\s*([A-Za-z][A-Za-z0-9-]*)")


def raw_html(body: str) -> list[HtmlFragment]:
    """Raw HTML blocks and inline tags outside code, with the first tag name of each."""
    offsets = line_offsets(body)
    fragments: list[HtmlFragment] = []
    for token in _html_probe().parse(body):
        if token.map is None:
            continue
        line_start, line_end = _line_span(offsets, body, token.map[0], token.map[1])
        if token.type == "html_block":
            fragments.extend(_fragments(token.content, body, line_start, line_end))
        elif token.type == "inline":
            for child in token.children or ():
                if child.type == "html_inline":
                    fragments.extend(_fragments(child.content, body, line_start, line_end))
    return fragments


def _fragments(content: str, body: str, line_start: int, line_end: int) -> list[HtmlFragment]:
    position = body.find(content.strip(), line_start, max(line_end, line_start)) if content else -1
    start, end = (
        (position, position + len(content.strip())) if position != -1 else (line_start, line_end)
    )
    tags = [match.group(1).lower() for match in _TAG_RE.finditer(content)]
    if not tags:
        return [HtmlFragment(tag=None, start=start, end=end)]
    return [HtmlFragment(tag=tag, start=start, end=end) for tag in dict.fromkeys(tags)]
