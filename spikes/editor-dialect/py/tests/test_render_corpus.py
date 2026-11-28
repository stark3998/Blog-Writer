"""Golden HTML for every corpus file, plus cross-checks against the TipTap side.

- ``snapshots/corpus/<file>.html``: sanitized HTML (reviewed golden).
- ``snapshots/corpus/<file>.json``: TOC, citations, diagrams, word count.
- Parity: TipTap's first normalization (``corpus/normalized/<file>``) must
  render to the same document as the original source on the *server*
  renderer, i.e. the editor and the server share one dialect.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from article_render import render_article
from article_render.renderer import RenderResult

from .conftest import CORPUS_DIR, CORPUS_FILES, NORMALIZED_DIR, SNAPSHOT_DIR, assert_golden
from .html_audit import audit


def _render(name: str, *, normalized: bool = False) -> RenderResult:
    directory = NORMALIZED_DIR if normalized else CORPUS_DIR
    return render_article((directory / name).read_text(encoding="utf-8"))


def _metadata(result: RenderResult) -> str:
    data = {
        "frontmatter": dataclasses.asdict(result.frontmatter) if result.frontmatter else None,
        "toc": [dataclasses.asdict(entry) for entry in result.toc],
        "citations": [dataclasses.asdict(citation) for citation in result.citations],
        "diagrams": [diagram.sha256 for diagram in result.diagrams],
        "wordCount": result.word_count,
        "readingTimeMinutes": result.reading_time_minutes,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_html_golden(name: str) -> None:
    result = _render(name)
    assert audit(result.html) == []
    stem = name.removesuffix(".md")
    assert_golden(SNAPSHOT_DIR / "corpus" / f"{stem}.html", result.html)
    assert_golden(SNAPSHOT_DIR / "corpus" / f"{stem}.json", _metadata(result))


_PRE_RE = re.compile(r"(<pre[\s\S]*?</pre>)")


# Whitespace next to these tags is insignificant; next to inline tags (<em>, <a>, ...)
# it separates words, so it is compared (reviewer fix: any tag used to absorb it).
_BLOCK_TAG = (
    r"</?(?:p|li|ul|ol|h[1-6]|blockquote|table|thead|tbody|tr|th|td|aside|figure|section|hr|br|input)"
    r"\b[^>]*>"
)
_SPACE_AROUND_BLOCK_TAG_RE = re.compile(rf" ?({_BLOCK_TAG}) ?")


def _equivalent_html(html: str) -> str:
    """Collapse insignificant whitespace (outside <pre>) and list looseness."""
    parts = _PRE_RE.split(html)
    out = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            out.append(part)
            continue
        # Only the whitespace HTML collapses; Python's \s also matches NBSP, U+3000, ...
        part = re.sub(r"[ \t\n]+", " ", part)
        part = re.sub(r"<li( [^>]*)?> ?<p>", r"<li\1>", part)
        part = re.sub(r"</p> ?</li>", "</li>", part)
        part = re.sub(r"</p> ?(<(?:ul|ol)[ >])", r" \1", part)
        out.append(_SPACE_AROUND_BLOCK_TAG_RE.sub(r"\1", part))
    return "".join(out)


def test_equivalent_html_keeps_word_spacing() -> None:
    assert _equivalent_html("<p><em>a</em> b</p>") != _equivalent_html("<p><em>a</em>b</p>")
    assert _equivalent_html("<p>a\u00a0b</p>") != _equivalent_html("<p>a b</p>")
    assert _equivalent_html("<ul>\n<li>\n<p>a</p>\n</li>\n</ul>") == _equivalent_html(
        "<ul>\n<li>a</li>\n</ul>"
    )


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_tiptap_normalization_renders_the_same(name: str) -> None:
    original = _render(name)
    normalized = _render(name, normalized=True)
    assert _equivalent_html(normalized.html) == _equivalent_html(original.html)
    assert normalized.toc == original.toc
    assert normalized.citations == original.citations
    assert normalized.diagrams == original.diagrams
    assert normalized.word_count == original.word_count
    assert normalized.frontmatter == original.frontmatter


def test_canonical_files_are_unchanged_by_tiptap() -> None:
    from .conftest import MANIFEST

    for name, entry in MANIFEST["files"].items():
        if entry["canonical"]:
            assert (NORMALIZED_DIR / name).read_bytes() == (CORPUS_DIR / name).read_bytes(), name


def test_full_article_metadata() -> None:
    result = _render("17-full-article.md")
    assert result.frontmatter is not None
    assert result.frontmatter.series is not None and result.frontmatter.series.part == 2
    assert [c.id for c in result.citations] == [
        "owasp-llm-top-10",
        "nist-ai-rmf",
        "mitre-atlas",
        "entra-workload-id",
        "cisa-secure-ai",
        "mcp-spec",
    ]
    assert [entry.id for entry in result.toc][:3] == [
        "the-problem-with-the-agent-s-credentials",
        "the-pattern-in-one-diagram",
        "why-a-gateway-and-not-a-better-prompt",
    ]
    assert len(result.diagrams) == 1
    assert result.word_count > 1200
    assert result.reading_time_minutes >= 5


def test_image_sizes_from_the_asset_manifest() -> None:
    html = render_article(
        "![Diagram](/blog-media/a/diagram.png)\n",
        image_sizes={"/blog-media/a/diagram.png": (1200, 630)},
    ).html
    assert '<img src="/blog-media/a/diagram.png" alt="Diagram" width="1200" height="630">' in html


def test_citation_metadata_urls_follow_the_profile() -> None:
    """Reviewer: citations[] skips nh3, so the href rule is applied when it is built."""
    source = (
        "A.[^a] B.[^b] C.[^c] D.[^d]\n\n"
        "[^a]: Kept. [site](https://ok.example/a)\n"
        "[^b]: Insecure. [site](http://insecure.example)\n"
        "[^c]: Protocol-relative. [site](//evil.example)\n"
        "[^d]: Relative. [site](relative/path)\n"
    )
    citations = render_article(source).citations
    assert [(c.id, c.url) for c in citations] == [
        ("a", "https://ok.example/a"),
        ("b", None),
        ("c", None),
        ("d", None),
    ]


@pytest.mark.parametrize(
    ("source", "tiptap_output"),
    [
        # TipTap's canonical output (web/test/parity.test.ts) must render like the source.
        ("~~~a`b\n# not a heading\n~~~\n", "```\n# not a heading\n```\n"),
        ("~~~python `x`\nprint(1)\n~~~\n", "```python\nprint(1)\n```\n"),
        ("##\u00a0not a heading\n", "\\##\u00a0not a heading\n"),
        ("###\u3000not a heading\n", "\\###\u3000not a heading\n"),
    ],
)
def test_reviewer_parity_fixes_render_the_same(source: str, tiptap_output: str) -> None:
    assert render_article(tiptap_output).html == render_article(source).html
    assert "<h" not in render_article(source).html
