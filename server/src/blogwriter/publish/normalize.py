"""The Python canonical normalizer (plan: "canonical Markdown normalization (v0)").

:func:`normalize_markdown` rewrites any valid article Markdown into the dialect's canonical form
(``docs/v2/article-dialect.md`` sections 3, 5 and 6) without changing what the server renders:
the same bytes the editor (TipTap, ``ArticleCodec.normalize``) writes, so drafts produced on the
server and documents saved in the editor converge. ``contracts/fixtures/corpus/normalized`` is
the byte-for-byte reference, and the result is a fixed point (normalizing it again changes
nothing).

How: the body is read with the server's own parser (:mod:`.parser`, so the meaning kept is the
meaning the renderer sees) into the editor's document model (:mod:`.tree`), the editor's
normalizations are applied (soft breaks joined, callouts lifted to the top level, footnote
definitions moved to the end in order of first citation, empty paragraphs dropped, adjacent lists
given alternating markers, ...), and :mod:`.writer` writes it back. Frontmatter is rewritten in
its canonical form.

Some rewrites make a dialect error invisible in the canonical text: an undefined ``[^label]`` is
written as escaped text (``\\[^label\\]``, exactly as the editor writes it), raw HTML is escaped,
a nested callout loses its fence, a duplicate definition disappears. Those errors are found on the
input and returned in :attr:`NormalizeResult.errors` (lint findings, lines of the input), so gate 5
sees them: gate 5 is ``result.errors`` plus :func:`~.lint.lint_article` on ``result.text``.

When the input cannot be normalized faithfully (invalid frontmatter, nesting beyond markdown-it's
limit, no stable canonical form) the text comes back unchanged with the errors. Input above 512 KB
raises :class:`~.dialect.SourceTooLargeError`.

CPU-bound: call :func:`normalize_markdown` through ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from markdown_it.common.utils import unescapeAll
from markdown_it.token import Token

from .dialect import (
    CALLOUT_TYPES,
    MAX_SOURCE_BYTES,
    MERMAID_LANGUAGE,
    check_source_size,
    info_language,
    js_trim,
    normalize_source,
)
from .frontmatter import FrontmatterError, dump_frontmatter, parse_frontmatter, split_frontmatter
from .lint import Finding, lint_article, nesting_too_deep
from .parser import dialect_parser
from .tree import Mark, Node, is_empty_paragraph, merge_adjacent_text, sort_marks
from .writer import write_document

__all__ = ["HIDDEN_BY_NORMALIZATION", "NormalizeResult", "normalize_markdown"]

HIDDEN_BY_NORMALIZATION: Final = frozenset(
    {
        "callout-invalid",
        "callout-nested",
        "citation-definition-blocks",
        "citation-duplicate",
        "citation-in-link",
        "citation-undefined",
        "frontmatter-invalid",
        "linked-image",
        "mixed-task-list",
        "nesting-too-deep",
        "raw-html",
    }
)
"""Lint error codes the canonical text can no longer show (or that stop normalization); they are
reported in :attr:`NormalizeResult.errors`."""

_SOFT_BREAK_RE: Final = re.compile(r"[ \t]*\n[ \t]*")
_MARKED_UNESCAPE_RE: Final = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])")
_CONTAINER_OPEN: Final = {f"container_{kind}_open": kind for kind in CALLOUT_TYPES}
_TEXT_ALIGN: Final = {
    "text-align:left": "left",
    "text-align:center": "center",
    "text-align:right": "right",
}


_MAX_PASSES: Final = 4
_UNSTABLE: Final = Finding(
    "normalize-unstable",
    "error",
    None,
    "the Markdown has no stable canonical form (usually emphasis next to punctuation, a citation "
    "or an image); review the formatting",
)
_UNSUPPORTED: Final = Finding(
    "normalize-unsupported", "error", None, "unsupported Markdown construct"
)
_BREAK_FIRST: Final = Finding(
    "thematic-break-first",
    "error",
    1,
    "the body starts with a thematic break, which would read as frontmatter",
)
_TOO_LARGE: Final = Finding(
    "source-too-large",
    "error",
    None,
    f"the canonical text is larger than {MAX_SOURCE_BYTES} bytes",
)


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    """The canonical text and the gate-5 errors of the input that it hides (see the module
    doc). ``errors`` hold lint findings whose lines refer to the input."""

    text: str
    errors: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


class _Unsupported(Exception):
    """A token the dialect parser should never produce; normalization stops (fail closed)."""


class _BreakFirst(Exception):
    """The body would start with ``---``, which reads back as frontmatter."""


def normalize_markdown(text: str) -> NormalizeResult:
    """Canonical Markdown for ``text`` (an article with or without frontmatter).

    One canonical pass gives the editor's bytes. A few documents have no stable canonical form
    in one pass (the editor has the same limit): emphasis whose delimiters cannot open or close
    where the writer puts them, next to punctuation, a citation or an image. For those the pass
    is repeated until the text no longer changes, and the error ``normalize-unstable`` tells the
    reviewer that formatting was rewritten. Without a fixed point after a few passes, the input
    comes back unchanged with that error. Either way the result is idempotent.
    """
    check_source_size(text)
    errors = [
        finding
        for finding in lint_article(text, require_frontmatter=False)
        if finding.severity == "error" and finding.code in HIDDEN_BY_NORMALIZATION
    ]
    try:
        first = _canonical(text)
    except _Unsupported:
        errors.append(_UNSUPPORTED)
        return NormalizeResult(text, tuple(errors))
    except _BreakFirst:
        errors.append(_BREAK_FIRST)
        return NormalizeResult(text, tuple(errors))
    if first is None:
        return NormalizeResult(text, tuple(errors))
    if first == text:
        # Already canonical (every editor save): a fixed point, no second pass needed.
        return NormalizeResult(text, tuple(errors))

    current = first
    for _ in range(_MAX_PASSES):
        try:
            following = _canonical(current)
        except (_Unsupported, _BreakFirst):
            following = None
        if following == current:
            if current != first:
                errors.append(_UNSTABLE)
            if len(current.encode("utf-8", errors="surrogatepass")) > MAX_SOURCE_BYTES:
                errors.append(_TOO_LARGE)
                return NormalizeResult(text, tuple(errors))
            return NormalizeResult(current, tuple(errors))
        if following is None:
            break
        current = following
    errors.append(_UNSTABLE)
    return NormalizeResult(text, tuple(errors))


def _canonical(text: str) -> str | None:
    """One canonical pass; ``None`` when the text cannot be normalized faithfully (invalid
    frontmatter, or blocks nested beyond markdown-it's limit, which silently drops the rest)."""
    try:
        frontmatter_text, body = split_frontmatter(normalize_source(text))
        head = (
            dump_frontmatter(parse_frontmatter(frontmatter_text))
            if frontmatter_text is not None
            else None
        )
    except FrontmatterError:
        return None

    parser = dialect_parser(join_text=False, footnotes_to_end=False, permissive_links=True)
    tokens = parser.parse(body, {})
    if nesting_too_deep(tokens, parser):
        return None
    doc = Node("doc", content=_Reader(tokens).blocks())
    _normalize_parsed(doc)
    _prepare_for_writing(doc)
    body_markdown = write_document(doc)
    if head is None:
        if body_markdown.startswith("---"):
            raise _BreakFirst
        return body_markdown
    return head if body_markdown == "" else f"{head}\n{body_markdown}"


# --- tokens to the document tree -----------------------------------------------------------------


class _Reader:
    """Builds the editor's document model from markdown-it-py's (well nested) token stream."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def blocks(self) -> list[Node]:
        """Blocks up to the closing token of the current container (consumed) or the end."""
        nodes: list[Node] = []
        while self._pos < len(self._tokens):
            token = self._tokens[self._pos]
            if token.nesting == -1:
                self._pos += 1
                return nodes
            nodes.extend(self._block(token))
        return nodes

    def _block(self, token: Token) -> list[Node]:
        self._pos += 1
        match token.type:
            case "paragraph_open":
                inline = self._inline_token()
                self._pos += 1  # paragraph_close
                return [Node("paragraph", content=_inline(inline))]
            case "heading_open":
                inline = self._inline_token()
                self._pos += 1  # heading_close
                return [Node("heading", content=_inline(inline), level=int(token.tag[1]))]
            case "blockquote_open":
                return [Node("blockquote", content=self.blocks())]
            case "bullet_list_open":
                return _bullet_lists(self._items())
            case "ordered_list_open":
                # Ordered task items keep their checkbox, as the server renders it (the editor
                # drops it; the dialect's task lists are bullet lists).
                items = self._items()
                start_attr = token.attrGet("start")
                start = int(str(start_attr)) if start_attr is not None else 1
                return [Node("orderedList", content=items, start=start)]
            case "fence":
                return [_fence(token)]
            case "code_block":
                return [Node("codeBlock", text=token.content.rstrip("\n"))]
            case "hr":
                return [Node("horizontalRule")]
            case "table_open":
                return [self._table()]
            case "footnote_reference_open":
                return [
                    Node(
                        "footnoteDefinition",
                        content=_definition_content(self.blocks()),
                        label=str(token.meta["label"]),
                    )
                ]
            case kind if kind in _CONTAINER_OPEN:
                return [Node("callout", content=self.blocks(), kind=_CONTAINER_OPEN[kind])]
            case _:
                raise _Unsupported(token.type)

    def _inline_token(self) -> Token:
        token = self._tokens[self._pos]
        if token.type != "inline":
            raise _Unsupported(token.type)
        self._pos += 1
        return token

    def _items(self) -> list[Node]:
        """The items of the list whose open token was just consumed (and its close token)."""
        items: list[Node] = []
        while self._pos < len(self._tokens):
            token = self._tokens[self._pos]
            self._pos += 1
            if token.nesting == -1:
                return items
            if token.type != "list_item_open":
                raise _Unsupported(token.type)
            task = "task-list-item" in str(token.attrGet("class") or "")
            checked = task and self._checkbox_checked()
            content = self.blocks()
            items.append(
                Node("taskItem", content=content, checked=checked)
                if task
                else Node("listItem", content=content)
            )
        return items

    def _checkbox_checked(self) -> bool:
        """The state of a task item's checkbox (the ``html_inline`` token the task-list plugin
        puts first in the item's first paragraph; :func:`_inline` skips it)."""
        if self._pos + 1 >= len(self._tokens):
            return False
        children = self._tokens[self._pos + 1].children or []
        return (
            bool(children)
            and children[0].type == "html_inline"
            and "checked" in children[0].content
        )

    def _table(self) -> Node:
        rows: list[Node] = []
        while self._pos < len(self._tokens):
            token = self._tokens[self._pos]
            self._pos += 1
            if token.type == "table_close":
                break
            if token.type == "tr_open":
                rows.append(Node("tableRow", content=self._cells()))
        return Node("table", content=rows)

    def _cells(self) -> list[Node]:
        cells: list[Node] = []
        while self._pos < len(self._tokens):
            token = self._tokens[self._pos]
            self._pos += 1
            if token.type == "tr_close":
                break
            if token.type in ("th_open", "td_open"):
                inline = self._inline_token()
                self._pos += 1  # th_close / td_close
                cells.append(
                    Node(
                        "tableHeader" if token.type == "th_open" else "tableCell",
                        content=[Node("paragraph", content=_inline(inline))],
                        align=_TEXT_ALIGN.get(str(token.attrGet("style") or ""), ""),
                    )
                )
        return cells


def _bullet_lists(items: list[Node]) -> list[Node]:
    """A list mixing task and plain items (one list in CommonMark) becomes consecutive lists, as
    in the editor, whose schema cannot hold it (the linter reports ``mixed-task-list``)."""
    groups: list[Node] = []
    for item in items:
        kind = "taskList" if item.type == "taskItem" else "bulletList"
        if groups and groups[-1].type == kind:
            groups[-1].content.append(item)
        else:
            groups.append(Node(kind, content=[item]))
    return groups


def _fence(token: Token) -> Node:
    # The editor's code token: the info string trimmed, with backslash escapes removed (entity
    # references stay as written; both sides decode them when rendering).
    info = _MARKED_UNESCAPE_RE.sub(r"\1", js_trim(token.info))
    text = token.content.removesuffix("\n")
    if info_language(unescapeAll(token.info)) == MERMAID_LANGUAGE:
        return Node("mermaid", text=text)
    return Node("codeBlock", text=text, language=info)


def _definition_content(blocks: list[Node]) -> list[Node]:
    """A citation definition is one paragraph of inline text; other blocks are flattened into it
    (the linter reports ``citation-definition-blocks``)."""
    parts: list[list[Node]] = []
    for block in blocks:
        inline = _flatten_inline(block)
        if inline:
            parts.append(inline)
    content: list[Node] = []
    for position, part in enumerate(parts):
        if position:
            content.append(Node("text", text=" "))
        content.extend(part)
    return merge_adjacent_text(content)


def _flatten_inline(block: Node) -> list[Node]:
    if block.type in ("paragraph", "heading"):
        return list(block.content)
    if block.type in ("codeBlock", "mermaid"):
        return [Node("text", text=block.text)] if block.text else []
    flattened: list[Node] = []
    for child in block.content:
        part = _flatten_inline(child)
        if part:
            if flattened:
                flattened.append(Node("text", text=" "))
            flattened.extend(part)
    return flattened


def _inline(token: Token) -> list[Node]:
    """Inline tokens to text runs with mark sets plus atoms (citations, breaks, images)."""
    nodes: list[Node] = []
    stack: list[Mark] = []
    marks: tuple[Mark, ...] = ()
    for child in token.children or ():
        match child.type:
            case "text" | "text_special":
                nodes.append(Node("text", text=child.content, marks=marks))
            case "softbreak":
                nodes.append(Node("text", text="\n", marks=marks))
            case "hardbreak":
                nodes.append(Node("hardBreak"))
            case "code_inline":
                nodes.append(
                    Node("text", text=child.content, marks=sort_marks([*stack, Mark("code")]))
                )
            case "em_open" | "strong_open" | "link_open":
                stack.append(_mark(child))
                marks = sort_marks(stack)
            case "em_close" | "strong_close" | "link_close":
                if stack:
                    stack.pop()
                marks = sort_marks(stack)
            case "image":
                nodes.append(
                    Node(
                        "image",
                        src=str(child.attrGet("src") or ""),
                        alt=_plain_text(child.children or []),
                        title=str(child.attrGet("title") or ""),
                    )
                )
            case "footnote_ref":
                nodes.append(Node("citation", label=str(child.meta.get("label") or "")))
            case "html_inline":
                # Only the task-list checkbox: raw HTML is never recognised (html: False).
                continue
            case _:
                raise _Unsupported(child.type)
    return merge_adjacent_text(nodes)


def _mark(token: Token) -> Mark:
    if token.type == "em_open":
        return Mark("italic")
    if token.type == "strong_open":
        return Mark("bold")
    return Mark(
        "link", href=str(token.attrGet("href") or ""), title=str(token.attrGet("title") or "")
    )


def _plain_text(children: Sequence[Token]) -> str:
    """CommonMark's plain-text reading of inline tokens (image alt text)."""
    parts: list[str] = []
    for child in children:
        if child.type in ("text", "text_special", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "image":
            parts.append(_plain_text(child.children or []))
        elif child.type == "footnote_ref":
            parts.append(f"[^{child.meta.get('label') or ''}]")
    return "".join(parts)


# --- the editor's normalizations -------------------------------------------------------------


def _normalize_parsed(doc: Node) -> None:
    """``normalizeParsedDocument`` in the editor (``transforms.ts``)."""
    _lift_nested_callouts(doc, is_root=True)
    _normalize_soft_breaks(doc)
    definitions = _extract_definitions(doc)
    _remove_empty_paragraphs(doc)
    _attach_footnotes(doc, definitions)


def _prepare_for_writing(doc: Node) -> None:
    """``prepareForSerialization`` in the editor."""
    _remove_empty_paragraphs(doc)
    _drop_trailing_hard_breaks(doc)
    _flatten_heading_breaks(doc)
    _attach_footnotes(doc, _extract_definitions(doc))
    _alternate_adjacent_list_markers(doc)


def _lift_nested_callouts(node: Node, *, is_root: bool) -> None:
    """Callouts are top-level blocks only; a nested one is replaced by its content."""
    lifted: list[Node] = []
    for child in node.content:
        _lift_nested_callouts(child, is_root=False)
        if child.type == "callout" and not is_root:
            lifted.extend(child.content)
        else:
            lifted.append(child)
    node.content = lifted


def _normalize_soft_breaks(node: Node) -> None:
    """One paragraph per line: a line ending (with the blanks around it) becomes one space, and
    tabs in prose become spaces."""
    if node.type in ("codeBlock", "mermaid"):
        return
    for child in node.content:
        if child.type == "text":
            if ("\n" in child.text or "\t" in child.text) and not any(
                mark.type == "code" for mark in child.marks
            ):
                child.text = _SOFT_BREAK_RE.sub(" ", child.text).replace("\t", " ")
        else:
            _normalize_soft_breaks(child)


def _remove_empty_paragraphs(node: Node) -> None:
    for child in node.content:
        _remove_empty_paragraphs(child)
    # A quote left empty (its only content was a footnote definition, or nothing) writes as
    # nothing at all; the editor's schema cannot hold one either.
    node.content = [
        child for child in node.content if not (child.type == "blockquote" and not child.content)
    ]
    if node.type == "doc":
        node.content = [child for child in node.content if not is_empty_paragraph(child)]
    elif node.type in ("blockquote", "callout"):
        kept = [child for child in node.content if not is_empty_paragraph(child)]
        node.content = kept if kept else node.content[:1]
    elif node.type in ("listItem", "taskItem"):
        # The first paragraph is required; later empty ones are noise.
        node.content = [
            child
            for position, child in enumerate(node.content)
            if position == 0 or not is_empty_paragraph(child)
        ]
        if not node.content or node.content[0].type not in ("paragraph", "heading"):
            # The editor's schema starts an item with a paragraph and fills in an empty one
            # (written ``- &nbsp;``). It also keeps ``- ---`` from reading as a thematic break.
            node.content = [Node("paragraph"), *node.content]
        first = node.content[0]
        if (
            first.type == "paragraph"
            and len(first.content) == 1
            and first.content[0].type == "text"
            and first.content[0].text == "\xa0"
        ):
            # ``- &nbsp;`` is how an empty item is written.
            first.content = []


def _extract_definitions(node: Node) -> list[Node]:
    """Remove every footnote definition (wherever the parser put it), in source order."""
    found: list[Node] = []

    def visit(parent: Node) -> None:
        kept: list[Node] = []
        for child in parent.content:
            if child.type == "footnoteDefinition":
                found.append(child)
            elif child.type == "footnotes":
                found.extend(d for d in child.content if d.type == "footnoteDefinition")
            else:
                visit(child)
                kept.append(child)
        parent.content = kept

    visit(node)
    return found


def _attach_footnotes(doc: Node, definitions: list[Node]) -> None:
    """One footnotes section at the end: definitions in order of first citation (the numbering
    readers see), then unreferenced ones in source order; the last duplicate wins."""
    if not definitions:
        return
    by_label: dict[str, Node] = {}
    for definition in definitions:
        by_label[definition.label] = _sanitize_definition(definition)
    source_order = list(dict.fromkeys(definition.label for definition in definitions))

    referenced: list[str] = []
    seen: set[str] = set()

    def collect(node: Node) -> None:
        if node.type == "citation" and node.label in by_label and node.label not in seen:
            seen.add(node.label)
            referenced.append(node.label)
        for child in node.content:
            collect(child)

    collect(doc)
    ordered = [*referenced, *(label for label in source_order if label not in seen)]
    doc.content.append(Node("footnotes", content=[by_label[label] for label in ordered]))


def _sanitize_definition(definition: Node) -> Node:
    """A definition holds plain marked-up text only: no nested citations, breaks or images."""
    content: list[Node] = []
    for child in definition.content:
        if child.type == "citation":
            content.append(Node("text", text=f"[^{child.label}]", marks=child.marks))
        elif child.type == "hardBreak":
            content.append(Node("text", text=" "))
        elif child.type == "image":
            content.append(Node("text", text=child.alt))
        else:
            content.append(child)
    return Node("footnoteDefinition", content=merge_adjacent_text(content), label=definition.label)


def _drop_trailing_hard_breaks(node: Node) -> None:
    """A hard break at the end of a paragraph or heading is not a break in CommonMark."""
    for child in node.content:
        if child.type in ("paragraph", "heading") and child.content:
            while child.content and child.content[-1].type == "hardBreak":
                child.content.pop()
        else:
            _drop_trailing_hard_breaks(child)


def _flatten_heading_breaks(node: Node) -> None:
    """ATX headings are one line: a hard break inside a heading becomes a space."""
    for child in node.content:
        if child.type == "heading" and any(inline.type == "hardBreak" for inline in child.content):
            child.content = merge_adjacent_text(
                [
                    Node("text", text=" ") if inline.type == "hardBreak" else inline
                    for inline in child.content
                ]
            )
        else:
            _flatten_heading_breaks(child)


def _alternate_adjacent_list_markers(node: Node) -> None:
    """CommonMark merges adjacent lists of the same kind; a list right after another uses the
    other marker (bullets ``-``/``*``, ordered delimiters ``.``/``)``)."""
    previous: Node | None = None
    for child in node.content:
        if child.type in ("bulletList", "taskList"):
            follows_bullets = previous is not None and previous.type in ("bulletList", "taskList")
            child.bullet = (
                "*" if follows_bullets and previous is not None and previous.bullet == "-" else "-"
            )
        elif child.type == "orderedList":
            follows_ordered = previous is not None and previous.type == "orderedList"
            child.delimiter = (
                ")"
                if follows_ordered and previous is not None and previous.delimiter == "."
                else "."
            )
        _alternate_adjacent_list_markers(child)
        previous = child
