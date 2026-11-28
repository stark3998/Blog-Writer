"""XSS corpus: hostile Markdown through the renderer, hostile HTML straight into the sanitizer.

The corpus lives in ``spikes/editor-dialect/xss`` so the portfolio's
rehype-sanitize twin can run the same files. Each output must pass the
independent audit (tags, attributes and values inside the profile, nothing
executable) and matches a reviewed golden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from article_render import render_article, sanitize_html

from .conftest import SNAPSHOT_DIR, XSS_DIR, assert_golden
from .html_audit import audit

MARKDOWN_CASES = sorted((XSS_DIR / "markdown").glob("*.md"))
HTML_CASES = sorted((XSS_DIR / "html").glob("*.html"))

DANGEROUS = (
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


def test_corpus_is_present() -> None:
    assert len(MARKDOWN_CASES) >= 20
    assert len(HTML_CASES) >= 20


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda p: p.stem)
def test_markdown_case_is_neutralized(case: Path) -> None:
    result = render_article(case.read_text(encoding="utf-8"))
    assert audit(result.html) == []
    lowered = result.html.lower()
    for marker in DANGEROUS:
        assert marker not in lowered
    assert_golden(SNAPSHOT_DIR / "xss" / f"{case.stem}.html", result.html)


@pytest.mark.parametrize("case", MARKDOWN_CASES, ids=lambda p: p.stem)
def test_renderer_alone_emits_no_author_markup(case: Path) -> None:
    """Defense in depth: with html=False markdown-it never emits author tags or
    event handlers, even before sanitizing. (URL policy, e.g. markdown-it's
    allowed data:image URLs or http links, is the sanitizer's job.)"""
    raw = render_article(case.read_text(encoding="utf-8")).unsanitized_html
    markup_problems = [p for p in audit(raw) if p.startswith(("tag ", "event handler", "comment"))]
    assert markup_problems == []


@pytest.mark.parametrize("case", HTML_CASES, ids=lambda p: p.stem)
def test_html_case_is_neutralized(case: Path) -> None:
    cleaned = sanitize_html(case.read_text(encoding="utf-8"))
    assert audit(cleaned) == []
    lowered = cleaned.lower()
    for marker in DANGEROUS:
        assert marker not in lowered
    assert "onerror" not in lowered and "onclick" not in lowered and "onfocus" not in lowered
    assert_golden(SNAPSHOT_DIR / "xss-html" / f"{case.stem}.html", cleaned)


def test_sanitizer_is_idempotent() -> None:
    for case in HTML_CASES:
        once = sanitize_html(case.read_text(encoding="utf-8"))
        assert sanitize_html(once) == once, case.name


@pytest.mark.parametrize(
    ("markdown", "absent"),
    [
        ("[x](javascript:alert(1))", "href"),
        ("[x](//evil.example)", "href"),
        ("[x](http://insecure.example)", "href"),
        ("![x](data:image/png;base64,iVBORw0KGgo=)", "src"),
        ("![x](//evil.example/x.png)", "src"),
    ],
)
def test_unsafe_urls_lose_the_attribute(markdown: str, absent: str) -> None:
    html = render_article(markdown).html
    assert f"{absent}=" not in html


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
