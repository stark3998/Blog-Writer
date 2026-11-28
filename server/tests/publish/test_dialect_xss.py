"""XSS corpus: hostile Markdown through the renderer, hostile HTML straight into the sanitizer.

The corpus is ``contracts/fixtures/xss`` (shared with the portfolio's rehype-sanitize twin). Each
output passes the contract's independent audit (tags, attributes and values inside the profile,
a self-contained fragment) and matches a reviewed golden in ``snapshots/xss`` and
``snapshots/xss-html`` (the Phase 0 goldens, unchanged under the frozen profile).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from blogwriter.publish.contract import PublishedContract
from blogwriter.publish.dialect import normalize_source
from blogwriter.publish.frontmatter import split_frontmatter
from blogwriter.publish.renderer import create_markdown, render_article
from blogwriter.publish.sanitizer import sanitize_html

REPO_DIR: Final = Path(__file__).resolve().parents[3]
XSS_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "xss"
SNAPSHOT_DIR: Final = Path(__file__).parent / "snapshots"
UPDATE_SNAPSHOTS: Final = os.environ.get("UPDATE_SNAPSHOTS") == "1"
MARKDOWN_CASES: Final = sorted((XSS_DIR / "markdown").glob("*.md"))
HTML_CASES: Final = sorted((XSS_DIR / "html").glob("*.html"))

DANGEROUS: Final = (
    "<script",
    "<iframe",
    "<object",
    "<embed",
    "<svg",
    "<math",
    "<form",
    "<base",
    "<meta",
    "<link",
    "<style",
    "<template",
)


def assert_golden(path: Path, actual: str) -> None:
    if UPDATE_SNAPSHOTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.exists(), f"missing golden {path}; run UPDATE_SNAPSHOTS=1 pytest"
    assert actual == path.read_text(encoding="utf-8"), f"{path.name} differs from its golden"


def test_corpus_is_present() -> None:
    assert len(MARKDOWN_CASES) == 24
    assert len(HTML_CASES) == 20


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda p: p.stem)
def test_markdown_case_is_neutralized(case: Path, contract: PublishedContract) -> None:
    result = render_article(case.read_text(encoding="utf-8"))
    assert contract.audit_html(result.html).findings == ()
    lowered = result.html.lower()
    for marker in DANGEROUS:
        assert marker not in lowered
    assert_golden(SNAPSHOT_DIR / "xss" / f"{case.stem}.html", result.html)


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda p: p.stem)
def test_renderer_alone_emits_no_author_markup(case: Path, contract: PublishedContract) -> None:
    """Defense in depth: with html=False markdown-it never emits author tags or event handlers,
    even before sanitizing. (URL policy, such as markdown-it's allowed ``data:image`` URLs or
    ``http:`` links, is the sanitizer's job.)"""
    _, body = split_frontmatter(normalize_source(case.read_text(encoding="utf-8")))
    raw = create_markdown().render(body, {})
    markup = [
        finding
        for finding in contract.audit_html(raw).findings
        if finding.startswith(("tag <", "end tag <", "comments", "attribute on"))
    ]
    assert markup == []


@pytest.mark.parametrize("case", HTML_CASES, ids=lambda p: p.stem)
def test_html_case_is_neutralized(case: Path, contract: PublishedContract) -> None:
    cleaned = sanitize_html(case.read_text(encoding="utf-8"))
    assert contract.audit_html(cleaned).findings == ()
    lowered = cleaned.lower()
    for marker in DANGEROUS:
        assert marker not in lowered
    for handler in ("onerror", "onclick", "onfocus", "onload", "formaction"):
        assert handler not in lowered
    assert_golden(SNAPSHOT_DIR / "xss-html" / f"{case.stem}.html", cleaned)


@pytest.mark.parametrize("case", HTML_CASES, ids=lambda p: p.stem)
def test_sanitizer_is_idempotent(case: Path) -> None:
    once = sanitize_html(case.read_text(encoding="utf-8"))
    assert sanitize_html(once) == once


@pytest.mark.parametrize(
    ("markdown", "absent"),
    [
        ("[x](javascript:alert(1))", "href"),
        ("[x](//evil.example)", "href"),
        ("[x](http://insecure.example)", "href"),
        ("![x](data:image/png;base64,iVBORw0KGgo=)", "src"),
        ("![x](//evil.example/x.png)", "src"),
        ("![x](http://insecure.example/x.png)", "src"),
    ],
)
def test_unsafe_urls_lose_the_attribute(markdown: str, absent: str) -> None:
    html = render_article(markdown).html
    assert f"{absent}=" not in html


def test_c0_controls_in_urls_are_refused() -> None:
    """New at the profile freeze: URL patterns exclude C0 controls and DEL."""
    html = sanitize_html('<a href="https://ok.example/a\x01b">x</a><img src="/a\x7fb.png" alt="y">')
    assert "href=" not in html
    assert "src=" not in html


def test_safe_urls_survive() -> None:
    html = render_article(
        "[a](https://ok.example/p?q=1) [b](mailto:a@b.example) [c](#intro) [d](/blog/x)\n\n"
        "![e](/blog-media/x/e.png) ![f](https://images.example/f.png)\n"
    ).html
    for value in (
        'href="https://ok.example/p?q=1"',
        'href="mailto:a@b.example"',
        'href="#intro"',
        'href="/blog/x"',
        'src="/blog-media/x/e.png"',
        'src="https://images.example/f.png"',
    ):
        assert value in html
    assert html.count('rel="noopener noreferrer"') == 4
