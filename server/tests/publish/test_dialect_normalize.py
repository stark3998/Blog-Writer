"""The canonical normalizer: corpus goldens byte for byte, idempotence, the editor twin, the
errors normalization would otherwise hide, and the fail-closed paths."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Final

import pytest

from blogwriter.publish.dialect import MAX_SOURCE_BYTES, SourceTooLargeError
from blogwriter.publish.lint import lint_article
from blogwriter.publish.normalize import HIDDEN_BY_NORMALIZATION, normalize_markdown
from blogwriter.publish.renderer import render_article

REPO_DIR: Final = Path(__file__).resolve().parents[3]
CORPUS_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "corpus"
NORMALIZED_DIR: Final = CORPUS_DIR / "normalized"
MANIFEST: Final[dict[str, Any]] = json.loads(
    (CORPUS_DIR / "manifest.json").read_text(encoding="utf-8")
)
CORPUS_FILES: Final = sorted(MANIFEST["files"])
FRONTMATTER: Final = '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: p\nformat: f\ntags: []\n---\n'


def test_corpus_inventory() -> None:
    assert len(CORPUS_FILES) == 22
    assert sorted(path.name for path in NORMALIZED_DIR.glob("*.md")) == CORPUS_FILES


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_corpus_golden(name: str) -> None:
    """``normalized/<file>`` is the editor's reviewed output; the server writes the same bytes."""
    source = (CORPUS_DIR / name).read_bytes().decode("utf-8")
    expected = (NORMALIZED_DIR / name).read_bytes().decode("utf-8")
    assert normalize_markdown(source).text == expected


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_corpus_is_a_fixed_point(name: str) -> None:
    normalized = (NORMALIZED_DIR / name).read_text(encoding="utf-8")
    again = normalize_markdown(normalized)
    assert again.text == normalized
    assert again.errors == ()


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_corpus_errors(name: str) -> None:
    """Only the errors normalization hides come back, with the input's line numbers."""
    source = (CORPUS_DIR / name).read_text(encoding="utf-8")
    errors = normalize_markdown(source).errors
    expected = {
        "19-normalize-citations.md": [("citation-undefined", 12)],
        "22-tolerated-but-linted.md": [("raw-html", 7), ("raw-html", 9)],
    }.get(name, [])
    assert [(error.code, error.line) for error in errors] == expected
    assert all(error.severity == "error" for error in errors)


def test_line_endings_and_byte_order_mark() -> None:
    assert normalize_markdown("\ufeffa\r\nb\r\n\r\n- c\r- d").text == "a b\n\n- c\n- d\n"


def test_frontmatter_only_and_empty() -> None:
    head = FRONTMATTER.replace("tags: []", "tags: []")
    assert normalize_markdown(head).text == head
    assert normalize_markdown(head + "\n\n\n").text == head
    assert normalize_markdown(head + "Body.").text == head + "\nBody.\n"
    assert normalize_markdown("").text == ""
    assert normalize_markdown("\n\n").text == ""


# --- The editor twin ----------------------------------------------------------------------------
# Expected outputs computed with the editor's own codec (Phase 0 spike, ArticleCodec.normalize on
# TipTap 3.35.0); ``None`` means the input is already canonical.
EDITOR_TWIN: Final[list[tuple[str, str | None]]] = [
    ("***both***\n", None),
    ("**a *b***\n", None),
    ("*a **b***\n", None),
    ("**[x](https://e.example)**\n", "[**x**](https://e.example)\n"),
    ("[**x**](https://e.example)\n", None),
    ("**see [the docs](https://e.example/docs)**\n", None),
    ("*`inline` first*\n", None),
    ("**the `kubectl` command**\n", None),
    ("[`kubectl apply`](https://e.example/k)\n", None),
    ("a**b**c and un*frigging*believable\n", None),
    ("** a **\n", "\\*\\* a \\*\\*\n"),
    ("*a* *b* and **c** **d**\n", None),
    ("wow![x](https://e.example)\n", None),
    ("!*x*\n", None),
    ("` a `\n", "`a`\n"),
    ("`  a  `\n", None),
    ("`` ` ``\n", None),
    ("```a``b```\n", "`a``b`\n"),
    ("`a\nb`\n", "`a b`\n"),
    ("x ` ` y\n", None),
    ("a \\* b and \\_a\\_ and snake_case\n", None),
    ("a <b> < c\n", "a \\<b> < c\n"),
    ("&copy; \\&copy; &amp;copy; AT&T &#35; &nbsp;end\n", "© \\&copy; \\&copy; AT&T # \xa0end\n"),
    ("1\\. a\n", None),
    ("\\# a\n", None),
    ("#a\n", None),
    ("\\- a\n", None),
    ("+a\n", None),
    ("\\=\n", None),
    ("\\> a\n", None),
    ("\\| a\n", None),
    ("\\:::note\n", None),
    ("\\~~~\n", None),
    ("~~a~~\n", "\\~~a~~\n"),
    ("a\\\n\\# b\n", None),
    ("a\\\n- b\n", "a\\\\\n\n- b\n"),
    ("a  \nb\n", "a\\\nb\n"),
    ("line\\\n:-- x\n", "line\\\n\\:-- x\n"),
    ("## a #\n", "## a\n"),
    ("## \\#\n", None),
    ("##\n", None),
    ("## a ##\n", "## a\n"),
    ("### a#\n", None),
    ("## C#\n", None),
    ("Setext\n===\n", "# Setext\n"),
    ("Setext\n---\n", "## Setext\n"),
    ("#    Heading with extra spaces\n", "# Heading with extra spaces\n"),
    ("### Closed ATX heading ###\n", "### Closed ATX heading\n"),
    ('[a](https://e.example "t")\n', None),
    ("[a](https://e.example 't')\n", '[a](https://e.example "t")\n'),
    ("[a](<https://e.example/a b>)\n", None),
    ("[a](https://e.example/a(b))\n", None),
    ("[a](https://e.example/a\\)b)\n", "[a](<https://e.example/a)b>)\n"),
    ('[a](https://e.example "a \\"q\\" b")\n', None),
    ("[a]()\n", "[a](<>)\n"),
    ("[a [b] c](https://e.example)\n", "[a &#91;b&#93; c](https://e.example)\n"),
    ("![a *b*](/i.png)\n", "![a b](/i.png)\n"),
    ('![a](/i.png "t")\n', None),
    ("![a\\[b\\]](/i.png)\n", "![a&#91;b&#93;](/i.png)\n"),
    ("<https://e.example>\n", "[https://e.example](https://e.example)\n"),
    ("<a@b.example>\n", "[a@b.example](mailto:a@b.example)\n"),
    ('[x][r]\n\n[r]: https://e.example "T"\n', '[x](https://e.example "T")\n'),
    ("[![Alt](/b.png)](https://e.example)\n", "![Alt](/b.png)\n"),
    ("- a\n\n- b\n", "- a\n- b\n"),
    ("* a\n* b\n", "- a\n- b\n"),
    ("1) a\n2) b\n", "1. a\n2. b\n"),
    ("3. a\n4. b\n", None),
    ("0. a\n", None),
    ("1. a\n1. b\n", "1. a\n2. b\n"),
    ("- a\n* b\n", "- a\n\n* b\n"),
    ("- a\n\npara\n\n- b\n", None),
    ("- [X] a\n- [ ] b\n", "- [x] a\n- [ ] b\n"),
    ("- [ ] a\n  - [x] b\n", None),
    ("- a\n  ```\n  code\n  ```\n", None),
    ("- ```\n  code\n  ```\n", "- &nbsp;\n  ```\n  code\n  ```\n"),
    ("- > q\n", "- &nbsp;\n  > q\n"),
    ("- a\n  > q\n", None),
    ("- a\n\n  ---\n", None),
    ("- a\n  1. b\n", None),
    ("- a\n\n  2. b\n", None),
    ("10. a\n    - b\n", None),
    ("- a\n  b\n", "- a b\n"),
    ("- &nbsp;\n", None),
    ("-\n", "- &nbsp;\n"),
    ("- a\n\n      code\n", "- a\n  ```\n  code\n  ```\n"),
    ("1. a\n\n   b\n2. c\n", None),
    ("> a\n>\n> b\n", None),
    ("> > a\n", None),
    ("> - a\n> - b\n", None),
    ("> ```\n> x\n> ```\n", None),
    ("> a\\\n> b\n", None),
    ("~~~\na\n~~~\n", "```\na\n```\n"),
    ("```\n```\n", None),
    ("    a\n    b\n", "```\na\nb\n```\n"),
    ("````\n```\n````\n", None),
    ('```js title="x"\na\n```\n', None),
    ("~~~a`b\n# x\n~~~\n", "```\n# x\n```\n"),
    ("```Mermaid x=1\ngraph TD\n```\n", "```mermaid\ngraph TD\n```\n"),
    ("~~~mermaid\ngraph\n~~~\n", "```mermaid\ngraph\n```\n"),
    ("|a|b|\n|-|-|\n|1|2|\n", "| a | b |\n| --- | --- |\n| 1 | 2 |\n"),
    ("| a |\n| :-: |\n| x |\n", "| a |\n| :---: |\n| x |\n"),
    ("| a | b |\n| --- | --- |\n| 1 |\n", "| a | b |\n| --- | --- |\n| 1 | |\n"),
    ("| a |\n| - |\n| `x\\|y` |\n", "| a |\n| --- |\n| `x\\|y` |\n"),
    ("| a |\n| - |\n| x \\| y |\n", "| a |\n| --- |\n| x \\| y |\n"),
    ("| a |\n| - |\n| \\# x |\n", "| a |\n| --- |\n| # x |\n"),
    (":::  tip  \na\n:::\n", ":::tip\na\n:::\n"),
    (":::warning\na\n:::::\n", ":::warning\na\n:::\n"),
    (":::note\na\n\nb\n", ":::note\na\n\nb\n:::\n"),
    (":::note Title\nx\n:::\n", "\\:::note Title x :::\n"),
    ("::::note\n:::\n::::\n", ":::note\n\\:::\n:::\n"),
    (":::note\n```\n:::\n```\n:::\n", ":::note\n```\n```\n:::\n\n```\n:::\n```\n"),
    ("- a\n\n  :::note\n  x\n  :::\n", "- a\n\n  x\n"),
    ("Claim.[^a]\n\n[^a]: Source.\n", None),
    ("Claim.[^a]\n", "Claim.\\[^a\\]\n"),
    ("[^a](https://e.example)\n\n[^a]: S.\n", None),
    ("[^a]: S.\n\nClaim.[^a]\n", "Claim.[^a]\n\n[^a]: S.\n"),
    ("Claim.[^a]\n[^a]: S.\n", "Claim.[^a]\n\n[^a]: S.\n"),
    ("Claim.[^a]\n\n[^a]: S\ncontinued.\n", "Claim.[^a]\n\n[^a]: S continued.\n"),
    ("Claim.[^a]\n\n[^a]:\n    S.\n", "Claim.[^a]\n\n[^a]: S.\n"),
    ("Claim.[^a]\n\n[^a]: First.\n[^a]: Second.\n", "Claim.[^a]\n\n[^a]: Second.\n"),
    ("Text.\n\n[^a]: Unused.\n", None),
    ("C.[^a]\n\n[^a]:\n", None),
    ("C.[^b][^a]\n\n[^a]: A.\n[^b]: B.\n", "C.[^b][^a]\n\n[^b]: B.\n[^a]: A.\n"),
    ("C.[^a]\n\n[^a]: S.\\\n  x\n", "C.[^a]\n\n[^a]: S. x\n"),
    ("x[^a] y[^b]\n\n[^b]: B\n[^a]: A\n\nlast\n", "x[^a] y[^b]\n\nlast\n\n[^a]: A\n[^b]: B\n"),
    ("a\n***\nb\n", "a\n\n---\n\nb\n"),
    ("a\tb\n", "a b\n"),
    ("a\r\nb\r\n", "a b\n"),
    ("\ufeffa\n", "a\n"),
    ("a\n\n\n\nb", "a\n\nb\n"),
    ("<img src=x onerror=alert(1)>\n", "\\<img src=x onerror=alert(1)>\n"),
    ("a <b>x</b> b\n", "a \\<b>x\\</b> b\n"),
]


@pytest.mark.parametrize(("source", "expected"), EDITOR_TWIN)
def test_editor_twin(source: str, expected: str | None) -> None:
    result = normalize_markdown(source)
    assert result.text == (source if expected is None else expected)
    assert normalize_markdown(result.text).text == result.text


# --- Where the server's reading wins -------------------------------------------------------------
# markdown-it (the renderer) and the editor's tokenizer read these inputs differently. The
# normalizer keeps the server's reading, so the published page does not change; each is outside
# the dialect and the linter (or the normalizer's errors) reports it.
SERVER_READING: Final[list[tuple[str, str, list[str]]]] = [
    # A link destination's entity reference is decoded (the editor keeps it raw); same page.
    ("[a](https://e.example/a&amp;b)\n", "[a](https://e.example/a&b)\n", []),
    ("[a](https://e.example/&amp;copy;)\n", "[a](https://e.example/\\&copy;)\n", []),
    # "a" + "---" inside a list item is a setext h2 for CommonMark.
    ("- a\n  ---\n", "- ## a\n", []),
    # An ordered list starting at 2 cannot interrupt a paragraph: lazy continuation.
    ("- a\n  2. b\n", "- a 2. b\n", []),
    # A raw HTML block line does not end the paragraph when HTML is off.
    ("text\n<div>\nmore\n", "text \\<div> more\n", ["raw-html"]),
    # A citation inside link text makes markdown-it drop the link.
    (
        "[see [^a]](https://e.example)\n\n[^a]: S.\n",
        "\\[see [^a]\\](https://e.example)\n\n[^a]: S.\n",
        ["citation-in-link"],
    ),
    # Block content in a definition belongs to the definition; it is flattened into one line.
    (
        "C.[^a]\n\n[^a]: S.\n\n    Second.\n",
        "C.[^a]\n\n[^a]: S. Second.\n",
        ["citation-definition-blocks"],
    ),
    # Numeric references to C1 controls decode to U+FFFD (the editor maps them to Windows-1252).
    ("&#128;\n", "\ufffd\n", []),
    # Nested emphasis of one kind collapses (the editor cannot open these documents at all).
    ("*a *b* c*\n", "*a b c*\n", []),
    # A heading starting a list item stays a heading (the editor makes it a paragraph).
    ("- # h\n", "- # h\n", []),
    # Ordered task items keep their checkbox (the editor drops it).
    ("1) [X] a\n2) [ ] b\n", "1. [x] a\n2. [ ] b\n", []),
]


@pytest.mark.parametrize(("source", "expected", "error_codes"), SERVER_READING)
def test_server_reading_wins(source: str, expected: str, error_codes: list[str]) -> None:
    result = normalize_markdown(source)
    assert result.text == expected
    assert [error.code for error in result.errors] == error_codes
    assert normalize_markdown(expected).text == expected


# --- Errors normalization would hide, fail-closed paths ----------------------------------------


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("Claim [^missing].\n", "citation-undefined"),
        ("Claim.[^a]\n\n[^a]: One.\n[^a]: Two.\n", "citation-duplicate"),
        ("- item\n\n  :::note\n  inside\n  :::\n", "callout-nested"),
        (":::note Title\ntext\n:::\n", "callout-invalid"),
        ("- [ ] task\n- plain\n", "mixed-task-list"),
        ("[![Alt](/blog-media/a/b.png)](https://e.example)\n", "linked-image"),
        ("<div>raw</div>\n", "raw-html"),
        ("a <!-- comment --> b\n", "raw-html"),
    ],
)
def test_hidden_errors_are_reported(source: str, code: str) -> None:
    result = normalize_markdown(source)
    assert code in {error.code for error in result.errors}
    assert code in HIDDEN_BY_NORMALIZATION
    # The canonical text no longer shows it: that is why the normalizer reports it.
    visible = {f.code for f in lint_article(result.text, require_frontmatter=False)}
    assert code not in visible


def test_visible_errors_are_left_to_the_linter() -> None:
    result = normalize_markdown("# Title\n\n[x](http://insecure.example)\n")
    assert result.errors == ()
    assert result.ok
    visible = {f.code for f in lint_article(result.text, require_frontmatter=False)}
    assert {"heading-level", "link-target"} <= visible


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ('---\ntitle: "T"\n---\n\nBody   with  spaces.\n', "frontmatter-invalid"),
        ("---\nmissing: fence\n", "frontmatter-invalid"),
        ("> " * 25 + "deep\n", "nesting-too-deep"),
        ("***\n\n___\n", "thematic-break-first"),
        ("**t[*,*,]() ![]().**\n", "normalize-unstable"),
    ],
)
def test_unnormalizable_input_comes_back_unchanged(source: str, code: str) -> None:
    result = normalize_markdown(source)
    assert result.text == source
    assert code in {error.code for error in result.errors}
    assert not result.ok
    assert normalize_markdown(result.text).text == source


def test_formatting_without_a_one_pass_fixed_point_is_flagged() -> None:
    """The editor's writer has the same limit: emphasis whose delimiters cannot open or close
    where they land. The text is rewritten to its fixed point and the reviewer is told."""
    result = normalize_markdown("```\n```\n*[]() [^b]*\n[^b]:\n")
    assert [error.code for error in result.errors] == ["normalize-unstable"]
    again = normalize_markdown(result.text)
    assert (again.text, again.errors) == (result.text, ())


def test_thematic_break_first_after_frontmatter_is_kept_for_the_linter() -> None:
    source = FRONTMATTER + "\n***\n\nx\n"
    result = normalize_markdown(source)
    assert result.text == FRONTMATTER + "\n---\n\nx\n"
    assert "thematic-break-first" in {f.code for f in lint_article(result.text)}


def test_size_cap() -> None:
    with pytest.raises(SourceTooLargeError):
        normalize_markdown("a" * (MAX_SOURCE_BYTES + 1))
    almost = "a" * (MAX_SOURCE_BYTES - 1)
    assert normalize_markdown(almost).text == almost + "\n"
    # The canonical text (one more byte: the final newline) would break the cap.
    at_cap = normalize_markdown("a" * MAX_SOURCE_BYTES)
    assert at_cap.text == "a" * MAX_SOURCE_BYTES
    assert [error.code for error in at_cap.errors] == ["source-too-large"]


def test_editor_documents_that_need_an_empty_first_paragraph() -> None:
    """The editor starts every list item with a paragraph; ``- ---`` would read as a rule."""
    assert normalize_markdown("* ---\n").text == "- &nbsp;\n\n  ---\n"
    assert normalize_markdown("- &nbsp;\n\n  ---\n").text == "- &nbsp;\n\n  ---\n"


def test_adjacent_lists_keep_apart() -> None:
    assert normalize_markdown("- a\n\n* b\n\n- [ ] c\n\n1. d\n\n1) e\n").text == (
        "- a\n\n* b\n\n- [ ] c\n\n1. d\n\n1) e\n"
    )
    assert normalize_markdown("- a\n\np\n\n* b\n").text == "- a\n\np\n\n- b\n"


def test_definitions_follow_first_citation_then_source_order() -> None:
    source = "One.[^b] Two.[^a]\n\n[^c]: C.\n[^a]: A.\n[^b]: B.\n"
    assert normalize_markdown(source).text == (
        "One.[^b] Two.[^a]\n\n[^b]: B.\n[^a]: A.\n[^c]: C.\n"
    )


# --- Properties (seeded, deterministic) ---------------------------------------------------------

_WORDS: Final = (
    "agents", "use", "short-lived", "tokens", "C#", "snake_case", "2 * 3", "AT&T", "x < y",
    "R&D", "(see below)", '"quoted"', "e.g.", "50%", "#hashtag", "naïve", "日本語", "🔐",
    "\\*literal\\*", "\\[sic\\]", "\\_private", "1\\.5", "ok:", "dash -", "end.",
)  # fmt: skip
_SAFE: Final = ("agents", "tokens", "C#", "snake_case", "AT&T", "naïve", "日本語", "x < y", "e.g.")


def _span(r: random.Random) -> str:
    roll = r.random()
    if roll < 0.5:
        return " ".join(r.choice(_WORDS) for _ in range(r.randint(1, 5)))
    if roll < 0.6:
        return "*" + " ".join(r.choice(_SAFE) for _ in range(r.randint(1, 3))) + "*"
    if roll < 0.7:
        return "**" + " ".join(r.choice(_SAFE) for _ in range(r.randint(1, 3))) + "**"
    if roll < 0.78:
        return "`" + r.choice(["kubectl apply", "a | b", "x = 1", "`tick`", "<b>"]) + "`"
    if roll < 0.88:
        target = r.choice(["https://e.example/a", "/blog/x", "#sec", "mailto:me@e.example"])
        return f"[{r.choice(_SAFE)} {r.choice(_SAFE)}]({target})"
    if roll < 0.96:
        return r.choice(_SAFE) + "." + r.choice(["[^owasp]", "[^nist]", "[^mitre]"])
    return r.choice(_SAFE) + "\\\n" + r.choice(_SAFE)


def _paragraph(r: random.Random) -> str:
    return " ".join(_span(r) for _ in range(r.randint(1, 5))).strip()


def _block(r: random.Random, depth: int = 0) -> str:
    roll = r.random()
    if roll < 0.3 or depth > 1:
        return _paragraph(r)
    if roll < 0.4:
        return "#" * r.randint(2, 4) + " " + " ".join(r.choice(_SAFE) for _ in range(3))
    if roll < 0.55:
        marker = r.choice(["-", "1.", "- [ ]", "- [x]"])
        items: list[str] = []
        for index in range(r.randint(1, 4)):
            item_marker = f"{index + 1}." if marker == "1." else marker
            item = f"{item_marker} {_paragraph(r).replace(chr(10), ' ')}"
            if r.random() < 0.25:
                pad = " " * (len(item_marker.split()[0]) + 1)
                item += "\n" + pad + r.choice(["- nested", "1. nested", "> quoted"])
            items.append(item)
        return "\n".join(items)
    if roll < 0.63:
        return "> " + _paragraph(r).replace("\n", "\n> ")
    if roll < 0.71:
        language = r.choice(["ts", "python", "", "mermaid"])
        code = r.choice(["a = 1", "x\n```\ny", ":::\n", "  indented\n\n  x"])
        fence = "````" if "```" in code else "```"
        return f"{fence}{language}\n{code}\n{fence}"
    if roll < 0.8:
        columns = r.randint(2, 3)
        header = "| " + " | ".join(f"H{i}" for i in range(columns)) + " |"
        delimiter = (
            "| "
            + " | ".join(r.choice(["---", ":---", "---:", ":---:"]) for _ in range(columns))
            + " |"
        )
        row = "| " + " | ".join(r.choice(_SAFE) for _ in range(columns)) + " |"
        return f"{header}\n{delimiter}\n{row}"
    if roll < 0.9 and depth == 0:
        inner = _paragraph(r)
        return f":::{r.choice(['note', 'tip', 'warning'])}\n{inner}\n:::"
    if roll < 0.95:
        return "![" + r.choice(_SAFE) + "](/blog-media/post/fig.png)"
    return "---"


def _article(r: random.Random) -> str:
    blocks: list[str] = []
    for _ in range(r.randint(2, 8)):
        block = _block(r)
        if blocks and block[:1] in "-1" and blocks[-1][:1] in "-1":
            blocks.append("Between lists.")
        blocks.append(block)
    if blocks[0].startswith("---"):
        blocks[0] = "Lede."
    definitions = "\n".join(
        f"[^{s}]: Publisher. *Title {s}*. [site](https://{s}.example/)"
        for s in ("owasp", "nist", "mitre")
    )
    head = FRONTMATTER + "\n" if r.random() < 0.5 else ""
    return head + "\n\n".join(blocks) + "\n\n" + definitions + "\n"


_JUNK: Final = (
    "*", "**", "_", "__", "[", "]", "(", ")", "`", "\\", "<", "&", "#", "> ", "- ", "1. ", "|",
    ":::", "~~", "[^a]", "\n", "\n\n", "  \n", " ", "x", "y z", "![i](/i.png)", "[l](/l)",
)  # fmt: skip


def _junk(r: random.Random) -> str:
    return "".join(r.choice(_JUNK) for _ in range(r.randint(1, 40))) + "\n\n[^a]: S.\n"


@pytest.mark.parametrize("seed", range(4))
def test_idempotent_on_valid_articles(seed: int) -> None:
    r = random.Random(20261126 + seed)
    for _ in range(60):
        source = _article(r)
        once = normalize_markdown(source)
        assert once.errors == (), source
        assert normalize_markdown(once.text).text == once.text, source


@pytest.mark.parametrize("seed", range(4))
def test_idempotent_on_hostile_input(seed: int) -> None:
    r = random.Random(7 + seed)
    for _ in range(100):
        source = _junk(r)
        once = normalize_markdown(source)
        twice = normalize_markdown(once.text)
        assert twice.text == once.text, source


_PRE_RE: Final = re.compile(r"(<pre[\s\S]*?</pre>)")
_BLOCK_TAG_RE: Final = re.compile(
    r" ?(</?(?:p|li|ul|ol|h[1-6]|blockquote|table|thead|tbody|tr|th|td|aside|figure|section|hr|br"
    r"|input)\b[^>]*>) ?"
)


def _equivalent(html: str) -> str:
    parts = _PRE_RE.split(html)
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            out.append(part)
            continue
        part = re.sub(r"[ \t\n]+", " ", part)
        part = re.sub(r"<li( [^>]*)?> ?<p>", r"<li\1>", part)
        part = re.sub(r"</p> ?</li>", "</li>", part)
        part = re.sub(r"</p> ?(<(?:ul|ol|pre|blockquote|table)[ >])", r" \1", part)
        out.append(_BLOCK_TAG_RE.sub(r"\1", part))
    return "".join(out)


@pytest.mark.parametrize("seed", range(4))
def test_valid_articles_render_the_same_after_normalizing(seed: int) -> None:
    """Only syntax changes: the server renders the same page (list looseness aside)."""
    r = random.Random(1000 + seed)
    for _ in range(40):
        source = _article(r)
        before = render_article(source)
        after = render_article(normalize_markdown(source).text)
        assert _equivalent(after.html) == _equivalent(before.html), source
        assert (after.toc, after.citations, after.word_count) == (
            before.toc,
            before.citations,
            before.word_count,
        )
