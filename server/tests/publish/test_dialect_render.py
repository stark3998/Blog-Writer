"""The article renderer: corpus goldens, metadata, the diagram hook and linked images.

Goldens live in ``snapshots/corpus``: ``<file>.html`` is the sanitized HTML (the Phase 0 spike's
reviewed goldens, byte for byte) and ``<file>.json`` the metadata. Regenerate deliberately with
``UPDATE_SNAPSHOTS=1`` and review the diff. Every output also passes the contract's independent
HTML audit (``html.parser``, not ammonia).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Final

import pytest

from blogwriter.publish.contract import PublishedContract
from blogwriter.publish.dialect import MAX_SOURCE_BYTES, SourceTooLargeError
from blogwriter.publish.frontmatter import FrontmatterError
from blogwriter.publish.normalize import normalize_markdown
from blogwriter.publish.renderer import (
    DEFAULT_DIAGRAM_ALT,
    DiagramUrlError,
    Heading,
    RenderedArticle,
    TocEntry,
    diagram_id,
    render_article,
)

REPO_DIR: Final = Path(__file__).resolve().parents[3]
CORPUS_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "corpus"
NORMALIZED_DIR: Final = CORPUS_DIR / "normalized"
SNAPSHOT_DIR: Final = Path(__file__).parent / "snapshots" / "corpus"
UPDATE_SNAPSHOTS: Final = os.environ.get("UPDATE_SNAPSHOTS") == "1"
MANIFEST: Final[dict[str, Any]] = json.loads(
    (CORPUS_DIR / "manifest.json").read_text(encoding="utf-8")
)
CORPUS_FILES: Final = sorted(MANIFEST["files"])


def assert_golden(path: Path, actual: str) -> None:
    if UPDATE_SNAPSHOTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.exists(), f"missing golden {path}; run UPDATE_SNAPSHOTS=1 pytest"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{path.name} differs from its golden (UPDATE_SNAPSHOTS=1 to accept)"


def render(name: str, *, normalized: bool = False) -> RenderedArticle:
    directory = NORMALIZED_DIR if normalized else CORPUS_DIR
    return render_article((directory / name).read_text(encoding="utf-8"))


def metadata(result: RenderedArticle) -> str:
    data = {
        "frontmatter": dataclasses.asdict(result.frontmatter) if result.frontmatter else None,
        "title": result.title,
        "excerpt": result.excerpt,
        "toc": [dataclasses.asdict(entry) for entry in result.toc],
        "headings": [dataclasses.asdict(heading) for heading in result.headings],
        "citations": [dataclasses.asdict(citation) for citation in result.citations],
        "diagrams": [diagram_id(source) for source in result.mermaid_sources],
        "wordCount": result.word_count,
        "readingTimeMinutes": result.reading_time_minutes,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_html_golden(name: str, contract: PublishedContract) -> None:
    result = render(name)
    assert contract.audit_html(result.html).findings == ()
    stem = name.removesuffix(".md")
    assert_golden(SNAPSHOT_DIR / f"{stem}.html", result.html)
    assert_golden(SNAPSHOT_DIR / f"{stem}.json", metadata(result))


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_toc_equals_the_audited_headings(name: str, contract: PublishedContract) -> None:
    """published-blogs invariant: toc lists exactly the h2/h3 of htmlContent that carry an id."""
    result = render(name)
    audit = contract.audit_html(result.html)
    assert [(entry.level, entry.id) for entry in result.toc] == list(audit.headings)
    assert [(h.level, h.id) for h in result.headings if h.id is not None] == list(audit.headings)


_PRE_RE: Final = re.compile(r"(<pre[\s\S]*?</pre>)")
# Whitespace next to these tags is insignificant; next to inline tags (<em>, <a>, ...) it
# separates words, so it is compared.
_BLOCK_TAG: Final = (
    r"</?(?:p|li|ul|ol|h[1-6]|blockquote|table|thead|tbody|tr|th|td|aside|figure|section|hr|br|input)"
    r"\b[^>]*>"
)
_SPACE_AROUND_BLOCK_TAG_RE: Final = re.compile(rf" ?({_BLOCK_TAG}) ?")


def equivalent_html(html: str) -> str:
    """Collapse insignificant whitespace (outside <pre>) and list looseness."""
    parts = _PRE_RE.split(html)
    out: list[str] = []
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
    assert equivalent_html("<p><em>a</em> b</p>") != equivalent_html("<p><em>a</em>b</p>")
    assert equivalent_html("<p>a\u00a0b</p>") != equivalent_html("<p>a b</p>")
    assert equivalent_html("<ul>\n<li>\n<p>a</p>\n</li>\n</ul>") == equivalent_html(
        "<ul>\n<li>a</li>\n</ul>"
    )


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_editor_normalization_renders_the_same(name: str) -> None:
    """The editor's first normalization (``normalized/<file>``) is the same document on the
    server: the editor and the server share one dialect."""
    original = render(name)
    normalized = render(name, normalized=True)
    assert equivalent_html(normalized.html) == equivalent_html(original.html)
    assert normalized.toc == original.toc
    assert normalized.citations == original.citations
    assert normalized.mermaid_sources == original.mermaid_sources
    assert normalized.word_count == original.word_count
    assert normalized.frontmatter == original.frontmatter


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_python_normalization_renders_the_same(name: str) -> None:
    source = (CORPUS_DIR / name).read_text(encoding="utf-8")
    normalized = render_article(normalize_markdown(source).text)
    original = render_article(source)
    assert equivalent_html(normalized.html) == equivalent_html(original.html)
    assert (normalized.toc, normalized.citations) == (original.toc, original.citations)


def test_canonical_files_are_unchanged_by_the_editor() -> None:
    for name, entry in MANIFEST["files"].items():
        if entry["canonical"]:
            assert (NORMALIZED_DIR / name).read_bytes() == (CORPUS_DIR / name).read_bytes(), name


def test_full_article_metadata() -> None:
    result = render("17-full-article.md")
    assert result.frontmatter is not None
    assert result.frontmatter.series is not None
    assert result.frontmatter.series.part == 2
    assert result.title == "Least-privilege tool calls for AI agents: a field guide"
    assert result.excerpt is not None
    assert result.excerpt.startswith("How to give an agent")
    assert [c.id for c in result.citations] == [
        "owasp-llm-top-10",
        "nist-ai-rmf",
        "mitre-atlas",
        "entra-workload-id",
        "cisa-secure-ai",
        "mcp-spec",
    ]
    assert [c.number for c in result.citations] == [1, 2, 3, 4, 5, 6]
    assert [entry.id for entry in result.toc][:3] == [
        "the-problem-with-the-agent-s-credentials",
        "the-pattern-in-one-diagram",
        "why-a-gateway-and-not-a-better-prompt",
    ]
    assert len(result.mermaid_sources) == 1
    assert result.mermaid_sources[0].startswith("flowchart LR\n")
    assert result.word_count > 1200
    assert result.reading_time_minutes >= 5


def test_headings_cover_every_level_and_ids_only_h2_h3() -> None:
    result = render_article("## Two\n\n### Three\n\n#### Four\n\n## Two\n")
    assert result.headings == (
        Heading(2, "Two", "two"),
        Heading(3, "Three", "three"),
        Heading(4, "Four", None),
        Heading(2, "Two", "two-1"),
    )
    assert result.toc == (
        TocEntry(2, "two", "Two"),
        TocEntry(3, "three", "Three"),
        TocEntry(2, "two-1", "Two"),
    )
    assert "<h4>Four</h4>" in result.html


def test_fragment_without_frontmatter() -> None:
    result = render_article("Just a fragment.\n")
    assert (result.frontmatter, result.title, result.excerpt) == (None, None, None)
    assert result.html == "<p>Just a fragment.</p>\n"
    assert (result.word_count, result.reading_time_minutes) == (3, 1)


def test_empty_article() -> None:
    result = render_article("")
    assert (result.html, result.word_count, result.reading_time_minutes) == ("", 0, 0)


def test_image_sizes_from_the_asset_manifest() -> None:
    html = render_article(
        "![Diagram](/blog-media/a/diagram.png)\n",
        image_sizes={"/blog-media/a/diagram.png": (1200, 630)},
    ).html
    assert '<img src="/blog-media/a/diagram.png" alt="Diagram" width="1200" height="630">' in html


def test_citation_metadata_urls_follow_the_profile() -> None:
    """citations[] skips nh3, so the profile's href rule is applied when it is built."""
    source = (
        "A.[^a] B.[^b] C.[^c] D.[^d] E.[^e]\n\n"
        "[^a]: Kept. [site](https://ok.example/a)\n"
        "[^b]: Insecure. [site](http://insecure.example)\n"
        "[^c]: Protocol-relative. [site](//evil.example)\n"
        "[^d]: Relative. [site](relative/path)\n"
        "[^e]: First allowed wins. [x](http://no.example) [y](https://yes.example)\n"
    )
    citations = render_article(source).citations
    assert [(c.id, c.url) for c in citations] == [
        ("a", "https://ok.example/a"),
        ("b", None),
        ("c", None),
        ("d", None),
        ("e", "https://yes.example"),
    ]


@pytest.mark.parametrize(
    ("source", "editor_output"),
    [
        # The editor's canonical output (spike web/test/parity.test.ts) renders like the source.
        ("~~~a`b\n# not a heading\n~~~\n", "```\n# not a heading\n```\n"),
        ("~~~python `x`\nprint(1)\n~~~\n", "```python\nprint(1)\n```\n"),
        ("##\u00a0not a heading\n", "\\##\u00a0not a heading\n"),
        ("###\u3000not a heading\n", "\\###\u3000not a heading\n"),
    ],
)
def test_parity_fixes_render_the_same(source: str, editor_output: str) -> None:
    assert render_article(editor_output).html == render_article(source).html
    assert "<h" not in render_article(source).html
    assert normalize_markdown(source).text == editor_output


# --- Mermaid diagrams ---------------------------------------------------------------------------

DIAGRAM = "flowchart LR\n  A --> B\n"


def test_mermaid_without_a_hook_renders_escaped_source() -> None:
    result = render_article(f"```mermaid\n{DIAGRAM}```\n\n```mermaid\nx<y & z\n```\n")
    digest = hashlib.sha256(DIAGRAM.encode()).hexdigest()
    assert result.html.startswith(
        f'<figure class="mermaid" data-diagram="{digest}"><pre class="mermaid-source"><code>'
        "flowchart LR\n  A --&gt; B\n</code></pre></figure>\n"
    )
    assert "x&lt;y &amp; z" in result.html
    assert result.mermaid_sources == (DIAGRAM, "x<y & z\n")
    assert diagram_id(DIAGRAM) == digest


def test_mermaid_with_a_hook_renders_an_image() -> None:
    seen: list[str] = []

    def diagram_url(source: str) -> str:
        seen.append(source)
        return f"/blog-media/post/diagram-{diagram_id(source)[:12]}.svg"

    url = f"/blog-media/post/diagram-{diagram_id(DIAGRAM)[:12]}.svg"
    result = render_article(
        f"Intro.\n\n```Mermaid\n{DIAGRAM}```\n",
        diagram_url=diagram_url,
        image_sizes={url: (800, 400)},
    )
    assert seen == [DIAGRAM]
    assert (
        f'<figure class="mermaid" data-diagram="{diagram_id(DIAGRAM)}">'
        f'<img src="{url}" alt="{DEFAULT_DIAGRAM_ALT}" width="800" height="400"></figure>'
    ) in result.html
    assert "mermaid-source" not in result.html
    assert result.mermaid_sources == (DIAGRAM,)


def test_mermaid_accessible_title_becomes_the_alt_text(contract: PublishedContract) -> None:
    source = 'flowchart LR\n  accTitle: Token "exchange" <flow>\n  A --> B\n'
    html = render_article(
        f"```mermaid\n{source}```\n", diagram_url=lambda _: "https://cdn.example/d.svg"
    ).html
    assert 'alt="Token &quot;exchange&quot;  flow&gt;"' in html
    assert contract.audit_html(html).findings == ()


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "http://insecure.example/d.svg", "//evil.example/d.svg", "d.svg", ""],
)
def test_diagram_hook_must_return_an_allowed_url(url: str) -> None:
    with pytest.raises(DiagramUrlError):
        render_article(f"```mermaid\n{DIAGRAM}```\n", diagram_url=lambda _: url)


# --- Linked images, limits ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "html"),
    [
        (
            "[![Alt](/blog-media/a/b.png)](https://example.com)\n",
            '<p><img src="/blog-media/a/b.png" alt="Alt"></p>\n',
        ),
        (
            '[see ![x](/i.png) here](https://example.com "T")\n',
            '<p><a href="https://example.com" title="T" rel="noopener noreferrer">see </a>'
            '<img src="/i.png" alt="x"><a href="https://example.com" title="T" '
            'rel="noopener noreferrer"> here</a></p>\n',
        ),
    ],
)
def test_linked_images_lose_the_link(source: str, html: str, contract: PublishedContract) -> None:
    rendered = render_article(source).html
    assert rendered == html
    assert contract.audit_html(rendered).findings == ()


def test_oversized_article_is_refused() -> None:
    with pytest.raises(SourceTooLargeError):
        render_article("a" * (MAX_SOURCE_BYTES + 1))


def test_invalid_frontmatter_is_refused() -> None:
    with pytest.raises(FrontmatterError):
        render_article('---\ntitle: "T"\n---\n\nBody.\n')
