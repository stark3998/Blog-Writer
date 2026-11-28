"""The canonical writer's escaping: unit cases (twins of the editor's ``escape.test.ts``) and
seeded round-trip properties: text written in each inline context reads back as exactly that
text with the server's parser."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Final

import pytest
from markdown_it.token import Token

from blogwriter.publish.dialect import js_trim, js_trim_end
from blogwriter.publish.escape import (
    LINE_ENDING_RUN_RE,
    brackets_as_references,
    callout_fence_length,
    citation_label,
    code_fence_for,
    escape_heading_end,
    escape_inline_text,
    escape_line_start,
    fence_info,
    format_link_destination,
    format_link_title,
    shortest_unused_backtick_run,
)
from blogwriter.publish.parser import dialect_parser
from blogwriter.publish.tree import Mark, Node
from blogwriter.publish.writer import write_document

PLAIN: Final = {"line_start": False, "heading_end": False}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a*b", "a\\*b"),
        ("snake_case", "snake_case"),
        ("_lead and trail_", "\\_lead and trail\\_"),
        ("[sic]", "\\[sic\\]"),
        ("a\\b", "a\\\\b"),
        ("`x`", "\\`x\\`"),
        ("a < b > c", "a < b > c"),
        ("<T> and </p> and <!x> and <?x", "\\<T> and \\</p> and \\<!x> and \\<?x"),
        ("AT&T", "AT&T"),
        ("&copy; &#169; &#xA9;", "\\&copy; \\&#169; \\&#xA9;"),
        ("&copy without semicolon", "&copy without semicolon"),
        ("# mid-line", "# mid-line"),
        ("日本_語 and naïve_x", "日本_語 and naïve_x"),
        ("a\nb\r\nc\rd", "a b c d"),
        ("a  \n  b", "a b"),
        ("tab\there", "tab here"),
    ],
)
def test_escape_inline_text(text: str, expected: str) -> None:
    assert escape_inline_text(text, line_start=False, heading_end=False) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# heading", "\\# heading"),
        ("#hashtag", "#hashtag"),
        ("###### six", "\\###### six"),
        ("####### seven", "####### seven"),
        ("#\u00a0nbsp", "\\#\u00a0nbsp"),
        ("> quote", "\\> quote"),
        ("- item", "\\- item"),
        ("--flag", "\\--flag"),
        ("-|cell", "\\-|cell"),
        ("-", "\\-"),
        ("+ item", "\\+ item"),
        ("+1 for this", "+1 for this"),
        ("1. item", "1\\. item"),
        ("2024) year", "2024\\) year"),
        ("1234567890. ten digits", "1234567890. ten digits"),
        ("3.14 is pi", "3.14 is pi"),
        ("=== underline", "\\=== underline"),
        (":::note", "\\:::note"),
        (":-- delimiter", "\\:-- delimiter"),
        (": plain colon", ": plain colon"),
        ("| cell", "\\| cell"),
        ("~~~ fence", "\\~~~ fence"),
        ("~ tilde", "~ tilde"),
        ("   leading spaces", "leading spaces"),
    ],
)
def test_escape_at_line_start(text: str, expected: str) -> None:
    assert escape_inline_text(text, line_start=True, heading_end=False) == expected


def test_line_start_rules_apply_only_at_position_zero() -> None:
    assert escape_line_start("a # b") == "a # b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Item #", "Item \\#"),
        ("Ends ##", "Ends \\##"),
        ("#", "\\#"),
        ("C#", "C#"),
        ("Issue #42", "Issue #42"),
        ("tabbed\t# ", "tabbed\t\\# "),
    ],
)
def test_escape_heading_end(text: str, expected: str) -> None:
    assert escape_heading_end(text) == expected


def test_block_edges_trim_unicode_whitespace() -> None:
    text = "\u3000\u00a0 a \u00a0"
    assert (
        escape_inline_text(
            text, line_start=True, heading_end=False, block_start=True, block_end=True
        )
        == "a"
    )
    assert escape_inline_text(text, line_start=True, heading_end=False) == "\u3000\u00a0 a \u00a0"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://example.com/a_(b)", "https://example.com/a_(b)"),
        ("/blog-media/x/threat model.pdf", "</blog-media/x/threat model.pdf>"),
        ("https://example.com/a)b", "<https://example.com/a)b>"),
        ("https://example.com/a<b>", "<https://example.com/a\\<b\\>>"),
        ("https://example.com/a\\b", "<https://example.com/a\\\\b>"),
        ("https://example.com/a\nb\rc", "<https://example.com/a%0Ab%0Dc>"),
        ("https://example.com/?a=1&b=2", "https://example.com/?a=1&b=2"),
        ("https://example.com/&copy;", "https://example.com/\\&copy;"),
        ("", "<>"),
    ],
)
def test_format_link_destination(href: str, expected: str) -> None:
    assert format_link_destination(href) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Title", '"Title"'),
        ('The "best" guide', '"The \\"best\\" guide"'),
        ("back\\slash", '"back\\\\slash"'),
        ("one\n\n# two", '"one # two"'),
        ("&amp; entity", '"\\&amp; entity"'),
    ],
)
def test_format_link_title(title: str, expected: str) -> None:
    assert format_link_title(title) == expected


def test_small_helpers() -> None:
    assert brackets_as_references("\\[a\\] \\\\\\[ \\*") == "&#91;a&#93; \\\\&#91; \\*"
    assert [shortest_unused_backtick_run(t) for t in ("a", "`", "` ``", "``")] == [1, 2, 3, 1]
    assert code_fence_for("a\n```\n   ````x\n    `````indented") == "`````"
    assert code_fence_for("no fence") == "```"
    assert callout_fence_length("a\n:::\n  ::::") == 5
    assert callout_fence_length("text") == 3
    assert fence_info("ts") == "ts"
    assert fence_info("a`b") == ""
    assert fence_info("python `x`") == "python"
    assert fence_info("ts\n# Injected") == "ts # Injected"
    assert citation_label("two words]x") == "two-words-x"


# --- Round-trip properties ----------------------------------------------------------------------

ALPHABET: Final = (
    *"\\`*_[]<>&#-+=:|~!()^.,;?\"'{}$%@/",
    *"0123456789abcxyzABC",
    " ", "\u00a0", "\u3000", "\t", "\u200b", "é", "日", "😀", "ß",
    "&amp;", "&copy;", "&#35;", "&#x26;", "1.", "1)", "---", "===", ":::", "```", "~~~",
    "[^x]", "](", "![", "<a", "</", "<!--", "\n", "\r", "\r\n",
)  # fmt: skip
RUNS: Final = 400


def random_texts(seed: int) -> list[str]:
    r = random.Random(seed)
    texts: list[str] = []
    while len(texts) < RUNS:
        text = "".join(r.choice(ALPHABET) for _ in range(r.randint(1, 24)))
        if js_trim(_expected(text)):
            texts.append(text)
    return texts


def _expected(text: str) -> str:
    """What a reader sees: line endings and tabs become spaces (the writer folds them)."""
    return LINE_ENDING_RUN_RE.sub(" ", text).replace("\t", " ")


def _plain(children: Sequence[Token]) -> str:
    parts: list[str] = []
    for child in children:
        if child.type in ("text", "text_special", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
    return "".join(parts)


def _first_inline(markdown: str) -> list[Token]:
    tokens = dialect_parser().parse(markdown, {})
    inline = next(token for token in tokens if token.type == "inline")
    return inline.children or []


@pytest.mark.parametrize("seed", range(3))
def test_paragraph_text_round_trips(seed: int) -> None:
    for text in random_texts(seed):
        markdown = escape_inline_text(
            text, line_start=True, heading_end=False, block_start=True, block_end=True
        )
        tokens = dialect_parser().parse(markdown + "\n", {})
        assert [t.type for t in tokens] == ["paragraph_open", "inline", "paragraph_close"], text
        assert _plain(tokens[1].children or []) == js_trim(_expected(text)), text


@pytest.mark.parametrize("seed", range(3))
def test_text_after_a_hard_break_round_trips(seed: int) -> None:
    for text in random_texts(100 + seed):
        markdown = "before\\\n" + escape_inline_text(
            text, line_start=True, heading_end=False, block_end=True
        )
        tokens = dialect_parser().parse(markdown + "\n", {})
        assert [t.type for t in tokens] == ["paragraph_open", "inline", "paragraph_close"], text
        # After a hard break only spaces and tabs start the line; Unicode spaces are text.
        expected = js_trim_end(_expected(text).lstrip(" \t"))
        assert _plain(tokens[1].children or []) == "before\n" + expected, text


@pytest.mark.parametrize("seed", range(3))
def test_heading_text_round_trips(seed: int) -> None:
    for text in random_texts(200 + seed):
        escaped = escape_inline_text(
            text, line_start=False, heading_end=True, block_start=True, block_end=True
        )
        tokens = dialect_parser().parse(f"## {escaped}\n", {})
        assert tokens[0].type == "heading_open", text
        assert _plain(tokens[1].children or []) == js_trim(_expected(text)), text


@pytest.mark.parametrize("seed", range(3))
def test_link_text_round_trips(seed: int) -> None:
    for text in random_texts(300 + seed):
        escaped = brackets_as_references(escape_inline_text(text, **PLAIN))
        children = _first_inline(f"x [{escaped}](https://e.example)\n")
        assert [c.type for c in children].count("link_open") == 1, text
        start = next(i for i, c in enumerate(children) if c.type == "link_open")
        end = next(i for i, c in enumerate(children) if c.type == "link_close")
        assert _plain(children[start + 1 : end]) == _expected(text), text


@pytest.mark.parametrize("seed", range(3))
def test_inline_code_round_trips(seed: int) -> None:
    for text in random_texts(400 + seed):
        doc = Node(
            "doc",
            content=[
                Node(
                    "paragraph",
                    content=[
                        Node("text", text="x "),
                        Node("text", text=text, marks=(Mark("code"),)),
                    ],
                )
            ],
        )
        children = _first_inline(write_document(doc))
        code = [c.content for c in children if c.type == "code_inline"]
        expected = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        assert code == [expected], text
