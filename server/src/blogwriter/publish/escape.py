"""Context-aware escaping for the canonical Markdown writer (``docs/v2/article-dialect.md``
section 5).

Python twin of the editor's ``escape.ts`` (Phase 0 spike, ``web/src/dialect/escape.ts``). Text
written with :func:`escape_inline_text` parses back to exactly the same characters in markdown-it
(the server) and in the editor, while staying readable: only the characters that could start
Markdown syntax in their position are escaped.

The editor runs on JavaScript, so its regular expressions have JavaScript semantics: ``\\s`` and
``trim`` use :data:`~.dialect.JS_WHITESPACE`, ``\\d`` is ASCII, ``$`` is the end of the string
and multiline ``^`` also starts a line after CR, U+2028 and U+2029. The patterns here spell
those out, so both writers produce the same bytes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from .dialect import JS_WHITESPACE, js_trim, js_trim_end, js_trim_start

__all__ = [
    "LINE_ENDING_RUN_RE",
    "brackets_as_references",
    "callout_fence_length",
    "citation_label",
    "code_fence_for",
    "escape_heading_end",
    "escape_inline_text",
    "escape_line_start",
    "fence_info",
    "format_link_destination",
    "format_link_title",
    "shortest_unused_backtick_run",
]

_WS: Final = re.escape(JS_WHITESPACE)

LINE_ENDING_RUN_RE: Final = re.compile(r"[ \t]*(?:(?:\r\n?|\n)[ \t]*)+")
"""A run of CommonMark line endings (LF, CR or CRLF) with the spaces and tabs around them."""

_ENTITY_LIKE_RE: Final = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{0,31});"
)
"""Something CommonMark would read as an entity or numeric character reference."""
_TAG_START_RE: Final = re.compile(r"[A-Za-z/!?]")
"""A character that turns ``<`` into an autolink or (in other renderers) an HTML tag."""
_ALWAYS_ESCAPED: Final = frozenset("\\`*[]")

_ATX_RE: Final = re.compile(rf"#{{1,6}}(?=[{_WS}]|\Z)")
_BULLET_RE: Final = re.compile(r"-(?=[ \t|:-]|\Z)")
_PLUS_RE: Final = re.compile(r"\+(?=[ \t]|\Z)")
_ORDERED_RE: Final = re.compile(r"([0-9]{1,9})([.)])(?=[ \t]|\Z)")
_COLON_RE: Final = re.compile(r":(?=[:-])")
_TILDE_RE: Final = re.compile(r"~(?=~)")
_HEADING_END_RE: Final = re.compile(r"(^|[ \t])(#+)[ \t]*\Z")
_ESCAPE_PAIR_RE: Final = re.compile(r"\\([^\n\r\u2028\u2029])")
_BACKTICK_RUN_RE: Final = re.compile(r"`+")
# JavaScript's multiline ``^`` also starts a line after CR, U+2028 and U+2029.
_JS_LINE_START: Final = r"(?:^|(?<=[\n\r\u2028\u2029]))"
_FENCE_RUN_RE: Final = re.compile(_JS_LINE_START + r" {0,3}(`+)")
_COLON_RUN_RE: Final = re.compile(_JS_LINE_START + r" {0,3}(:{3,})")
_FENCE_INFO_UNSAFE_RE: Final = re.compile(r"[`\r\n]")
_WS_RUN_RE: Final = re.compile(rf"[{_WS}]+")
_BRACKETS_NEEDED_RE: Final = re.compile(rf"[{_WS}<>\\\x00-\x1f\x7f]")
_POINTY_ESCAPED_RE: Final = re.compile(r"[<>\\]")
_TITLE_ESCAPED_RE: Final = re.compile(r'["\\]')
_LABEL_UNSAFE_RE: Final = re.compile(rf"[\]{_WS}]")


def _is_word_char(ch: str) -> bool:
    """A letter or digit in any script (JavaScript ``/[\\p{L}\\p{N}]/u``)."""
    return bool(ch) and unicodedata.category(ch)[0] in ("L", "N")


def escape_inline_text(
    text: str,
    *,
    line_start: bool,
    heading_end: bool,
    block_start: bool = False,
    block_end: bool = False,
) -> str:
    """Escape one text run.

    - ``line_start``: the run begins a Markdown line (the first inline of a paragraph or a
      definition, or the text after a hard break). Block syntax (``#``, ``>``, ``-``, ``1.``,
      ``:::``, ...) is neutralised there and leading spaces and tabs are dropped.
    - ``heading_end``: the run ends an ATX heading (a trailing ``#`` run would be eaten).
    - ``block_start``/``block_end``: the run starts/ends a paragraph or heading, where parsers
      trim Unicode whitespace; it is dropped instead of written and lost on parse.

    A line ending inside text (edited, pasted or generated text; parsing never produces one) is
    written as one space, a run of them with surrounding blanks too, so text can never start a
    new Markdown line. Tabs become spaces.
    """
    source = LINE_ENDING_RUN_RE.sub(" ", text).replace("\t", " ")
    if block_start:
        source = js_trim_start(source)
    elif line_start:
        source = source.lstrip(" \t")
    if block_end:
        source = js_trim_end(source)

    chars = list(source)
    out: list[str] = []
    for index, ch in enumerate(chars):
        if ch in _ALWAYS_ESCAPED:
            out.append("\\" + ch)
        elif ch == "_":
            previous = chars[index - 1] if index > 0 else ""
            following = chars[index + 1] if index + 1 < len(chars) else ""
            out.append("_" if _is_word_char(previous) and _is_word_char(following) else "\\_")
        elif ch == "<":
            following = chars[index + 1] if index + 1 < len(chars) else ""
            out.append("\\<" if following and _TAG_START_RE.fullmatch(following) else "<")
        elif ch == "&":
            ahead = "".join(chars[index : index + 40])
            out.append("\\&" if _ENTITY_LIKE_RE.match(ahead) else "&")
        else:
            out.append(ch)
    escaped = "".join(out)

    if line_start:
        escaped = escape_line_start(escaped)
    if heading_end:
        escaped = escape_heading_end(escaped)
    return escaped


def escape_line_start(line: str) -> str:
    """Neutralise block syntax at the start of a line. ``line`` is already inline-escaped, so
    ``*``, `````, ``[`` and ``\\`` never appear unescaped here."""
    if _ATX_RE.match(line):
        # ATX heading: CommonMark only counts space or tab after the # run, but the editor's
        # tokenizer also accepts Unicode whitespace; escaping it either way is safe.
        return "\\" + line
    if line.startswith(">"):
        return "\\" + line
    if _BULLET_RE.match(line) or _PLUS_RE.match(line):
        # A bullet marker, a thematic break or setext underline (---), or a table delimiter row.
        return "\\" + line
    if line.startswith("="):
        return "\\" + line
    ordered = _ORDERED_RE.match(line)
    if ordered:
        digits = ordered.group(1)
        return f"{digits}\\{line[len(digits) :]}"
    if _COLON_RE.match(line):
        # A callout fence (:::note) or a table delimiter cell (:--).
        return "\\" + line
    if line.startswith("|"):
        return "\\" + line
    if _TILDE_RE.match(line):
        return "\\" + line
    return line


def escape_heading_end(text: str) -> str:
    """A trailing ``#`` run after whitespace (or making up the whole heading) is an optional ATX
    closing sequence the parser would drop; escaping its first ``#`` keeps it as text."""
    match = _HEADING_END_RE.search(text)
    if match is None:
        return text
    run_start = match.start() + len(match.group(1))
    return f"{text[:run_start]}\\{text[run_start:]}"


def brackets_as_references(escaped: str) -> str:
    """Inside link text and image descriptions, write brackets as ``&#91;``/``&#93;``.

    The editor's tokenizer unescapes ``\\[`` and ``\\]`` in link text *before* tokenizing it, so
    an escaped ``[^x]`` or ``[a](b)`` there would turn back into a citation or a nested link.
    """

    def replace(match: re.Match[str]) -> str:
        ch = match.group(1)
        if ch == "[":
            return "&#91;"
        if ch == "]":
            return "&#93;"
        return match.group(0)

    return _ESCAPE_PAIR_RE.sub(replace, escaped)


def shortest_unused_backtick_run(text: str) -> int:
    """Length of the shortest backtick run that does not occur in ``text`` (at least 1)."""
    runs = {len(match.group(0)) for match in _BACKTICK_RUN_RE.finditer(text)}
    length = 1
    while length in runs:
        length += 1
    return length


def code_fence_for(text: str) -> str:
    """Backtick fence for a code block: longer than any backtick run that could close it."""
    longest = max((len(match.group(1)) for match in _FENCE_RUN_RE.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


def callout_fence_length(inner: str) -> int:
    """Colon fence length for a callout whose rendered content is ``inner``: three, or one more
    than the longest colon run that starts a line of the content (a shorter or equal run would
    close the callout early)."""
    longest = max((len(match.group(1)) for match in _COLON_RUN_RE.finditer(inner)), default=0)
    return max(3, longest + 1)


def fence_info(info: str) -> str:
    """Info string for a backtick fence.

    It cannot hold a backtick (the line would not be a fence, and the code would be read as
    Markdown) or a line break, but a tilde fence (``~~~a`b``) can carry one. Keep the words
    before the first word with a backtick: the server only reads the first word, and a first
    word with a backtick names no language, so the rendered code block is unchanged.
    """
    if not _FENCE_INFO_UNSAFE_RE.search(info):
        return info
    words = _WS_RUN_RE.split(js_trim(info))
    cut = next((index for index, word in enumerate(words) if "`" in word), len(words))
    return " ".join(words[:cut])


def _escape_entity_like(value: str) -> str:
    """Backslash-escape ``&`` where CommonMark would read an entity or character reference.

    Destinations and titles here hold the decoded value (markdown-it decodes references there),
    so a literal ``&copy;`` must be written ``\\&copy;`` to survive the next parse."""
    out: list[str] = []
    for index, ch in enumerate(value):
        if ch == "&" and _ENTITY_LIKE_RE.match(value, index):
            out.append("\\&")
        else:
            out.append(ch)
    return "".join(out)


def format_link_destination(href: str) -> str:
    """A link or image destination: bare when that parses back unchanged, otherwise in pointy
    brackets with ``<``, ``>`` and ``\\`` escaped (CR and LF percent-encoded: a raw line ending
    would end the link and the line)."""
    if href == "":
        return "<>"
    if not _BRACKETS_NEEDED_RE.search(href) and _parentheses_balanced(href):
        return _escape_entity_like(href)
    inner = _POINTY_ESCAPED_RE.sub(lambda match: "\\" + match.group(0), href)
    inner = inner.replace("\n", "%0A").replace("\r", "%0D")
    return f"<{_escape_entity_like(inner)}>"


def format_link_title(title: str) -> str:
    """A link title, always double-quoted and on one line (a blank line would end the
    paragraph)."""
    one_line = LINE_ENDING_RUN_RE.sub(" ", title)
    escaped = _TITLE_ESCAPED_RE.sub(lambda match: "\\" + match.group(0), one_line)
    return f'"{_escape_entity_like(escaped)}"'


def citation_label(label: str) -> str:
    """The ``[^label]`` text for a citation or definition: whitespace and ``]`` become ``-``,
    the same way for references and definitions so they stay paired."""
    return _LABEL_UNSAFE_RE.sub("-", label)


def _parentheses_balanced(value: str) -> bool:
    depth = 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
