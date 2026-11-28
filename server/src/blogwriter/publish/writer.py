"""The canonical Markdown writer: a :mod:`.tree` document to dialect Markdown.

Python twin of the editor's serializer (Phase 0 spike: ``@tiptap/markdown`` 3.35.0's
``MarkdownManager`` mark-boundary algorithm plus the dialect's node renderers in
``extensions/overrides.ts``, ``callout.ts``, ``citation.ts`` and ``mermaid.ts``). For every
document the editor can hold it writes the editor's bytes; ``contracts/fixtures/corpus/normalized``
is the reference.

Inline content is a flat list of text runs with mark sets plus atoms; the delimiters (``**``,
``*``, backticks, ``[...](...)``) are re-derived from the mark sets: marks that end on a run nest
inside marks that continue, lower-ranked marks (link) nest outside higher-ranked ones (code), and
whitespace at a mark boundary moves outside the delimiters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from .dialect import JS_WHITESPACE, js_trim, js_trim_end, js_trim_start
from .escape import (
    brackets_as_references,
    callout_fence_length,
    citation_label,
    code_fence_for,
    escape_inline_text,
    fence_info,
    format_link_destination,
    format_link_title,
    shortest_unused_backtick_run,
)
from .tree import MARK_RANKS, Mark, MarkType, Node, is_empty_paragraph

__all__ = ["EMPTY_ITEM_TEXT", "write_document"]

EMPTY_ITEM_TEXT: Final = "&nbsp;"
"""How an empty list item is written: a bare nested ``-`` under a paragraph would be read as a
setext heading underline, and an empty item cannot interrupt a paragraph."""

_WS: Final = re.escape(JS_WHITESPACE)
_TRAILING_WS_RE: Final = re.compile(rf"[{_WS}]+\Z")
_LEADING_WS_RE: Final = re.compile(rf"\A[{_WS}]+")
_LINE_ENDING_RE: Final = re.compile(r"\r\n?|\n")
_UNESCAPED_BANG_RE: Final = re.compile(r"(\\*)!\Z")
_CELL_PIPE_RE: Final = re.compile(r"\\[^\n\r\u2028\u2029]|\|")
_CODE_PIPE_PLACEHOLDER: Final = "\ue011"
_LEADING_BLANKS_RE: Final = re.compile(r"\A[ \t]+")


def write_document(doc: Node) -> str:
    """The canonical Markdown of a document the normalizer has prepared (the editor's
    normalizations applied): blocks separated by one blank line, exactly one trailing newline,
    ``""`` when empty."""
    children = [child for child in doc.content if not is_empty_paragraph(child)]
    markdown = "\n\n".join(_block(child, doc, index) for index, child in enumerate(children))
    if js_trim(markdown.replace("&nbsp;", "").replace("\xa0", "")) == "":
        return ""
    body = markdown.rstrip("\n")
    return f"{body}\n" if body else ""


# --- blocks -------------------------------------------------------------------------------------


def _block(node: Node, parent: Node, index: int) -> str:
    match node.type:
        case "paragraph":
            return _trim_paragraph_lines(_inline(node.content, node)) if node.content else ""
        case "heading":
            text = _trim_paragraph_lines(_inline(node.content, node))
            return "#" * node.level if text == "" else f"{'#' * node.level} {text}"
        case "blockquote":
            return _blockquote(node)
        case "bulletList" | "taskList" | "orderedList":
            return "\n".join(
                _list_item(item, node, position) for position, item in enumerate(node.content)
            )
        case "listItem" | "taskItem":
            return _list_item(node, parent, index)
        case "codeBlock" | "mermaid":
            return _code_block(node)
        case "horizontalRule":
            return "---"
        case "table":
            return _table(node)
        case "callout":
            return _callout(node)
        case "footnotes":
            return "\n".join(
                _block(definition, node, position)
                for position, definition in enumerate(node.content)
            )
        case "footnoteDefinition":
            text = _inline(node.content, node)
            label = f"[^{citation_label(node.label)}]:"
            return label if text == "" else f"{label} {text}"
        case _:
            # Inline content where a block belongs cannot come out of the normalizer.
            return _inline([node], parent)


def _trim_paragraph_lines(markdown: str) -> str:
    """Trim what the parsers strip anyway: Unicode whitespace at the start and end of the block,
    spaces and tabs at the start of lines after a hard break."""
    lines = markdown.split("\n")
    trimmed = [
        js_trim_start(line) if position == 0 else _LEADING_BLANKS_RE.sub("", line)
        for position, line in enumerate(lines)
    ]
    return js_trim_end("\n".join(trimmed))


def _blockquote(node: Node) -> str:
    parts: list[str] = []
    for index, child in enumerate(node.content):
        lines = _block(child, node, index).split("\n")
        parts.append("\n".join(">" if js_trim(line) == "" else f"> {line}" for line in lines))
    return "\n>\n".join(parts)


def _code_block(node: Node) -> str:
    text = node.text
    language = "mermaid" if node.type == "mermaid" else fence_info(node.language)
    fence = code_fence_for(text)
    return f"{fence}{language}\n{fence}" if text == "" else f"{fence}{language}\n{text}\n{fence}"


def _callout(node: Node) -> str:
    inner = "\n\n".join(_block(child, node, index) for index, child in enumerate(node.content))
    fence = ":" * callout_fence_length(inner)
    return (
        f"{fence}{node.kind}\n{fence}" if inner == "" else f"{fence}{node.kind}\n{inner}\n{fence}"
    )


def _list_item(item: Node, parent: Node, index: int) -> str:
    if parent.type == "orderedList":
        number = f"{parent.start + index}{parent.delimiter} "
        checkbox = f"[{'x' if item.checked else ' '}] " if item.type == "taskItem" else ""
        marker = number + checkbox
        indent = " " * len(number)
    elif item.type == "taskItem":
        marker = f"{parent.bullet} [{'x' if item.checked else ' '}] "
        indent = "  "
    else:
        marker = f"{parent.bullet} "
        indent = " " * len(marker)

    children = item.content
    rendered = _block(children[0], item, 0) if children else ""
    main = EMPTY_ITEM_TEXT if js_trim(rendered) == "" else rendered
    output = marker + _indent_lines(main, indent, include_first=False)
    for position in range(1, len(children)):
        child = children[position]
        output += _block_separator(children[position - 1], child)
        output += _indent_lines(_block(child, item, position), indent, include_first=True)
    return output


def _block_separator(previous: Node, following: Node) -> str:
    """A single newline keeps a list item tight, but only where the second block can interrupt
    the first; everywhere else a blank line is required (a thematic break would turn the
    paragraph into a setext heading; a line after a list, quote or table would be a lazy
    continuation of it)."""
    if previous.type in ("codeBlock", "mermaid"):
        return "\n"
    if previous.type == "paragraph":
        if following.type in (
            "bulletList",
            "taskList",
            "codeBlock",
            "mermaid",
            "blockquote",
            "table",
        ):
            return "\n"
        if following.type == "orderedList":
            return "\n" if following.start == 1 else "\n\n"
    return "\n\n"


def _indent_lines(text: str, indent: str, *, include_first: bool) -> str:
    """Indent every non-empty line (optionally skipping the first); empty lines stay empty."""
    return "\n".join(
        f"{indent}{line}" if (position > 0 or include_first) and line != "" else line
        for position, line in enumerate(text.split("\n"))
    )


def _table(node: Node) -> str:
    rows = [row.content for row in node.content]
    if not rows:
        return ""
    column_count = max(len(cells) for cells in rows)
    if column_count == 0:
        return ""
    alignments: list[str] = []
    for column in range(column_count):
        align = ""
        for cells in rows:
            candidate = cells[column].align if column < len(cells) else ""
            if candidate in ("left", "center", "right"):
                align = candidate
                break
        alignments.append(align)

    def render_row(cells: list[Node]) -> str:
        texts = [
            _cell(cells[column] if column < len(cells) else None, node)
            for column in range(column_count)
        ]
        return "|" + "|".join(" " if text == "" else f" {text} " for text in texts) + "|"

    delimiters = {"left": " :--- ", "center": " :---: ", "right": " ---: "}
    delimiter = "|" + "|".join(delimiters.get(align, " --- ") for align in alignments) + "|"
    header, *body = rows
    return "\n".join([render_row(header), delimiter, *(render_row(cells) for cells in body)])


def _cell(cell: Node | None, table: Node) -> str:
    if cell is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in cell.content:
        inline = [_protect_code_pipes(child) for child in paragraph.content]
        paragraphs.append(js_trim(_inline(inline, table)))
    text = " ".join(part for part in paragraphs if part != "")
    escaped = _CELL_PIPE_RE.sub(
        lambda match: "\\|" if match.group(0) == "|" else match.group(0), text
    )
    return escaped.replace(_CODE_PIPE_PLACEHOLDER, "\\|")


def _protect_code_pipes(node: Node) -> Node:
    """GFM needs ``|`` escaped inside code spans in a table too; a hard break becomes a space."""
    if node.type == "text" and any(mark.type == "code" for mark in node.marks) and "|" in node.text:
        return Node("text", text=node.text.replace("|", _CODE_PIPE_PLACEHOLDER), marks=node.marks)
    if node.type == "hardBreak":
        return Node("text", text=" ")
    return node


# --- inline content -----------------------------------------------------------------------------


def _is_code(node: Node) -> bool:
    return any(mark.type == "code" for mark in node.marks)


def _encode_text(node: Node, index: int, siblings: Sequence[Node], parent: Node) -> str:
    """The escaped Markdown of one text run (``encodeDialectText`` in the editor).

    ``index`` is the run's position in ``parent.content``, ``-1`` when it is not a child of
    ``parent`` (table cells are written with the table as their parent, as in the editor).
    """
    if _is_code(node):
        # A line ending would end the Markdown line mid-span; inside a code span it reads as a
        # space. Edge whitespace stays inside the backticks (the code mark adds the padding
        # CommonMark strips).
        return _LINE_ENDING_RE.sub(" ", node.text)
    starts_lines = parent.type in ("paragraph", "footnoteDefinition")
    textblock = starts_lines or parent.type == "heading"
    previous = siblings[index - 1] if index > 0 else None
    block_start = textblock and index <= 0
    block_end = textblock and index == len(siblings) - 1
    line_start = starts_lines and (
        index <= 0 or (previous is not None and previous.type == "hardBreak")
    )
    heading_end = parent.type == "heading" and index == len(siblings) - 1
    escaped = escape_inline_text(
        node.text,
        line_start=line_start,
        heading_end=heading_end,
        block_start=block_start,
        block_end=block_end,
    )
    return brackets_as_references(escaped) if any(m.type == "link" for m in node.marks) else escaped


def _mark_opening(mark: Mark, mark_text: str) -> str:
    match mark.type:
        case "bold":
            return "**"
        case "italic":
            return "*"
        case "code":
            fence, padding = _code_delimiters(mark_text)
            return fence + padding
        case "link":
            return "["


def _mark_closing(mark: Mark, mark_text: str) -> str:
    match mark.type:
        case "bold":
            return "**"
        case "italic":
            return "*"
        case "code":
            fence, padding = _code_delimiters(mark_text)
            return padding + fence
        case "link":
            title = f" {format_link_title(mark.title)}" if mark.title else ""
            return f"]({format_link_destination(mark.href)}{title})"


def _code_delimiters(mark_text: str) -> tuple[str, str]:
    """Shortest unused backtick run, plus one space of padding when the code starts or ends
    with a backtick, or with a space on both sides (CommonMark strips one such space)."""
    text = _LINE_ENDING_RE.sub(" ", mark_text)
    fence = "`" * shortest_unused_backtick_run(text)
    space_padded = text.startswith(" ") and text.endswith(" ") and text.strip(" ") != ""
    padding = " " if text.startswith("`") or text.endswith("`") or space_padded else ""
    return fence, padding


def _mark_span_texts(nodes: Sequence[Node]) -> list[dict[MarkType, str]]:
    """Each text run's full mark-run text per mark type (a delimiter can depend on the whole
    span, not just one run)."""
    span_texts: list[dict[MarkType, str]] = [{} for _ in nodes]
    open_runs: dict[MarkType, tuple[list[int], list[str]]] = {}

    def flush(mark_type: MarkType) -> None:
        indexes, parts = open_runs.pop(mark_type)
        text = "".join(parts)
        for position in indexes:
            span_texts[position][mark_type] = text

    for position, node in enumerate(nodes):
        if node.type != "text":
            for mark_type in list(open_runs):
                flush(mark_type)
            continue
        types: set[MarkType] = {mark.type for mark in node.marks}
        ended: list[MarkType] = [t for t in open_runs if t not in types]
        for mark_type in ended:
            flush(mark_type)
        for mark in node.marks:
            indexes, parts = open_runs.setdefault(mark.type, ([], []))
            indexes.append(position)
            parts.append(node.text)
    for mark_type in list(open_runs):
        flush(mark_type)
    return span_texts


def _has_mark(node: Node | None, mark: Mark) -> bool:
    return node is not None and mark in node.marks


def _marks_to_open(
    active: dict[MarkType, Mark], current: dict[MarkType, Mark], following: Node | None
) -> list[Mark]:
    """Marks opening on this run, innermost first: marks that end here nest inside marks that
    continue into the next node; within each group the higher schema rank is inner."""
    opening = [mark for mark_type, mark in current.items() if active.get(mark_type) != mark]
    if len(opening) <= 1:
        return opening

    def inner_first(mark: Mark) -> tuple[int, str]:
        return (-MARK_RANKS[mark.type], mark.type)

    ending = sorted((m for m in opening if not _has_mark(following, m)), key=inner_first)
    continuing = sorted((m for m in opening if _has_mark(following, m)), key=inner_first)
    return [*ending, *continuing]


def _marks_to_close(current: dict[MarkType, Mark], following: Node | None) -> list[MarkType]:
    return [mark_type for mark_type, mark in current.items() if not _has_mark(following, mark)]


def _marks_to_close_at_end(
    active: dict[MarkType, Mark], current: dict[MarkType, Mark], following: Node | None
) -> list[MarkType]:
    if following is None or not following.marks:
        return list(reversed(active))
    if set(current.values()) == set(following.marks) and len(current) == len(following.marks):
        return []
    return [
        mark_type for mark_type, mark in reversed(active.items()) if mark not in following.marks
    ]


def _inline(nodes: Sequence[Node], parent: Node) -> str:
    """Inline content (``renderNodesWithMarkBoundaries`` in the editor)."""
    result: list[str] = []
    active: dict[MarkType, Mark] = {}
    span_texts = _mark_span_texts(nodes)
    siblings = parent.content
    own_children = nodes is siblings
    for position, node in enumerate(nodes):
        following = nodes[position + 1] if position + 1 < len(nodes) else None
        spans = span_texts[position]
        index = position if own_children else -1
        if node.type != "text":
            # Marks never stay open across an atom (atoms carry no marks), but close any that
            # are, the way the editor does: each closing goes in front of the previous one.
            previous_spans = span_texts[position - 1] if position > 0 else {}
            before = ""
            for mark_type in reversed(list(active)):
                closing_text = _mark_closing(active[mark_type], previous_spans.get(mark_type, ""))
                before = closing_text + before
            active.clear()
            result.append(before + _atom(node, index, siblings, parent))
            continue

        content = _encode_text(node, index, siblings, parent)
        protected = _is_code(node)
        current: dict[MarkType, Mark] = {mark.type: mark for mark in node.marks}
        opening = _marks_to_open(active, current, following)
        closing = _marks_to_close(current, following)

        # Whitespace-only text has nothing to wrap: drop marks that open and close here.
        if not protected and content and js_trim(content) == "" and current:
            transient = {mark_type for mark_type in closing if mark_type not in active}
            if transient:
                current = {t: m for t, m in current.items() if t not in transient}
                opening = _marks_to_open(active, current, following)
                closing = _marks_to_close(current, following)

        closing_active: list[MarkType] = [t for t in closing if t in active]
        crossed = bool(closing_active) and bool(opening)

        middle_trailing = ""
        if closing and not crossed and not protected:
            match = _TRAILING_WS_RE.search(content)
            if match:
                middle_trailing = match.group(0)
                content = content[: match.start()]

        if not crossed:
            for mark_type in reversed(closing):
                if mark_type not in active:
                    continue
                content += _mark_closing(current[mark_type], spans.get(mark_type, ""))
                del active[mark_type]

        leading = ""
        if opening and not protected:
            match = _LEADING_WS_RE.search(content)
            if match:
                leading = match.group(0)
                content = content[match.end() :]

        for mark in opening:
            content = _mark_opening(mark, spans.get(mark.type, "")) + content
        if not crossed:
            for mark in reversed(opening):
                active[mark.type] = mark
        content = leading + content

        close_at_end: list[MarkType]
        if crossed:
            # New marks (inner) close first, then the active ones ending here, last opened first.
            opened: list[MarkType] = [mark.type for mark in opening]
            outer: list[MarkType] = [t for t in reversed(active) if t in closing_active]
            close_at_end = opened + outer
        else:
            close_at_end = _marks_to_close_at_end(active, current, following)

        trailing = ""
        if close_at_end and not protected:
            match = _TRAILING_WS_RE.search(content)
            if match:
                trailing = match.group(0)
                content = content[: match.start()]
        for mark_type in close_at_end:
            mark = active.get(mark_type) or current.get(mark_type)
            if mark is not None:
                content += _mark_closing(mark, spans.get(mark_type, ""))
            active.pop(mark_type, None)
        result.append(content + trailing + middle_trailing)

    # A literal "!" directly before a link (or citation) would be read as an image.
    previous = -1
    for position, piece in enumerate(result):
        if not piece:
            continue
        if previous >= 0 and piece.startswith("["):
            match = _UNESCAPED_BANG_RE.search(result[previous])
            if match and len(match.group(1)) % 2 == 0:
                result[previous] = result[previous][:-1] + "\\!"
        previous = position
    return "".join(result)


def _atom(node: Node, index: int, siblings: Sequence[Node], parent: Node) -> str:
    match node.type:
        case "hardBreak":
            return "\\\n"
        case "citation":
            return f"[^{citation_label(node.label)}]"
        case "image":
            alt = js_trim(re.sub(f"[{_WS}]+", " ", node.alt))
            alt_markdown = brackets_as_references(
                escape_inline_text(alt, line_start=False, heading_end=False)
            )
            title = f" {format_link_title(node.title)}" if node.title else ""
            return f"![{alt_markdown}]({format_link_destination(node.src)}{title})"
        case _:
            return _block(node, parent, index)
