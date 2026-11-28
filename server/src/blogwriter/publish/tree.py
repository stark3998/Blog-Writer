"""The canonical writer's document tree: the editor's document model, in Python.

The normalizer reads Markdown into this tree (the same nodes and marks the editor's schema has),
applies the editor's normalizations to it, and writes it back with :mod:`.writer`. Keeping the
editor's model is what makes the two writers produce the same bytes: inline content is a flat
list of text runs carrying a set of marks (``link``, ``bold``, ``italic``, ``code``) plus inline
atoms (``hardBreak``, ``image``, ``citation``), and the writer re-derives the delimiters from the
mark sets exactly as the editor does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

__all__ = [
    "MARK_RANKS",
    "BlockType",
    "InlineType",
    "Mark",
    "MarkType",
    "Node",
    "NodeType",
    "is_empty_paragraph",
    "merge_adjacent_text",
    "sort_marks",
]

type MarkType = Literal["link", "bold", "italic", "code"]
type BlockType = Literal[
    "doc",
    "paragraph",
    "heading",
    "blockquote",
    "bulletList",
    "orderedList",
    "taskList",
    "listItem",
    "taskItem",
    "codeBlock",
    "mermaid",
    "horizontalRule",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
    "callout",
    "footnotes",
    "footnoteDefinition",
]
type InlineType = Literal["text", "hardBreak", "image", "citation"]
type NodeType = BlockType | InlineType

MARK_RANKS: Final[dict[MarkType, int]] = {"link": 0, "bold": 1, "italic": 2, "code": 3}
"""The editor schema's mark order (extension priority: link 1000, bold and italic 100, the
dialect's code mark 50). A lower rank nests outside a higher one when both open together."""


@dataclass(frozen=True, slots=True)
class Mark:
    type: MarkType
    href: str = ""
    """Link destination (``link`` only), decoded as markdown-it decodes it."""
    title: str = ""
    """Link title (``link`` only); empty when the link has none."""


def sort_marks(marks: list[Mark]) -> tuple[Mark, ...]:
    """A mark set in schema order, one mark per type (an inner mark of a type the text already
    has is dropped, as ProseMirror does)."""
    by_type: dict[MarkType, Mark] = {}
    for mark in marks:
        by_type.setdefault(mark.type, mark)
    return tuple(sorted(by_type.values(), key=lambda mark: MARK_RANKS[mark.type]))


@dataclass(slots=True, eq=False)
class Node:
    """A node of the tree. Only the fields of its type are meaningful."""

    type: NodeType
    content: list[Node] = field(default_factory=lambda: [])
    text: str = ""
    """``text`` nodes, and the code of ``codeBlock``/``mermaid``."""
    marks: tuple[Mark, ...] = ()
    level: int = 0
    """``heading`` level (1-6)."""
    start: int = 1
    """``orderedList`` first number."""
    checked: bool = False
    """``taskItem`` state."""
    language: str = ""
    """``codeBlock`` info string, as the editor keeps it (trimmed, backslash escapes removed)."""
    align: str = ""
    """``tableHeader``/``tableCell`` alignment: ``""``, ``left``, ``center`` or ``right``."""
    kind: str = ""
    """``callout`` type."""
    label: str = ""
    """``citation``/``footnoteDefinition`` id."""
    src: str = ""
    alt: str = ""
    title: str = ""
    """``image`` attributes (``title`` empty when the image has none)."""
    bullet: str = "-"
    """Serialization only: the marker of a ``bulletList``/``taskList`` (``-`` or ``*``)."""
    delimiter: str = "."
    """Serialization only: the delimiter of an ``orderedList`` (``.`` or ``)``)."""


def is_empty_paragraph(node: Node) -> bool:
    return node.type == "paragraph" and not node.content


def merge_adjacent_text(content: list[Node]) -> list[Node]:
    """Merge adjacent text nodes that carry the same marks; drop empty text nodes.

    Linear: a run of text is joined once (512 KB of escapes is 262,144 text tokens)."""
    merged: list[Node] = []
    run: list[Node] = []

    def flush() -> None:
        if len(run) == 1:
            merged.append(run[0])
        elif run:
            merged.append(Node("text", text="".join(n.text for n in run), marks=run[0].marks))
        run.clear()

    for node in content:
        if node.type != "text":
            flush()
            merged.append(node)
        elif node.text:
            if run and run[0].marks != node.marks:
                flush()
            run.append(node)
    flush()
    return merged
