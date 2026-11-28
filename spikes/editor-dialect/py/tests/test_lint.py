"""Dialect linter: the canonical corpus is clean; violations are reported with stable codes."""

from __future__ import annotations

from collections.abc import Collection

import pytest

from article_render.lint import lint_article

from .conftest import CORPUS_DIR, MANIFEST, NORMALIZED_DIR


def codes(
    source: str, *, require_frontmatter: bool = True, source_ids: Collection[str] | None = None
) -> set[str]:
    findings = lint_article(source, require_frontmatter=require_frontmatter, source_ids=source_ids)
    return {finding.code for finding in findings if finding.severity == "error"}


@pytest.mark.parametrize("name", sorted(MANIFEST["files"]))
def test_corpus(name: str) -> None:
    source = (CORPUS_DIR / name).read_text(encoding="utf-8")
    expected = set(MANIFEST["files"][name].get("lintErrors", []))
    assert codes(source, require_frontmatter=False) == expected


def test_full_article_binds_citations_to_brief_sources() -> None:
    source = (CORPUS_DIR / "17-full-article.md").read_text(encoding="utf-8")
    brief = {
        "owasp-llm-top-10",
        "nist-ai-rmf",
        "mitre-atlas",
        "entra-workload-id",
        "cisa-secure-ai",
        "mcp-spec",
    }
    assert codes(source, source_ids=brief) == set()
    assert codes(source, source_ids=brief - {"mcp-spec"}) == {"citation-unknown-source"}


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("Body.\n", "frontmatter-missing"),
        ("# Title\n", "heading-level"),
        ("- ## heading in a list\n", "heading-nested"),
        ("<div>raw</div>\n", "raw-html"),
        ("text <b>bold</b>\n", "raw-html"),
        ("an ![icon](/i.png) inline\n", "inline-image"),
        ("![](/i.png)\n", "image-alt"),
        ("![x](http://insecure.example/i.png)\n", "image-src"),
        ("[x](http://insecure.example)\n", "link-target"),
        ("[x](relative/path)\n", "link-target"),
        ("Claim [^missing].\n", "citation-undefined"),
        ("Claim.[^a]\n\n[^a]: One.\n[^a]: Two.\n", "citation-duplicate"),
        ("Claim.[^Bad_Id]\n\n[^Bad_Id]: Source.\n", "citation-id"),
        ("Claim.[^a]\n\n[^a]: Source.\n\n    Second paragraph.\n", "citation-definition-blocks"),
        ("[see [^a]](https://ok.example)\n\n[^a]: Source.\n", "citation-in-link"),
        (":::note Title\ntext\n:::\n", "callout-invalid"),
        (":::danger\ntext\n:::\n", "callout-invalid"),
        ("- item\n\n  :::note\n  inside\n  :::\n", "callout-nested"),
        ("- [ ] task\n- plain\n", "mixed-task-list"),
        (
            '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: p\nformat: f\ntags: []\n---\n'
            "\n---\n\ntext\n",
            "thematic-break-first",
        ),
        ("bad \u2028 separator\n", "forbidden-character"),
        ("```mermaid\n```\n", "mermaid-empty"),
        # Reviewer: markdown-it refuses these targets, so they rendered as literal text and
        # no rule ran; the spec lists them as link-target / image-src errors.
        ("[x](javascript:alert(1))\n", "link-target"),
        ("[x](JaVaScRiPt:alert(1))\n", "link-target"),
        ("[x](vbscript:msgbox(1))\n", "link-target"),
        ("[x](data:text/html,hi)\n", "link-target"),
        ("[x](file:///etc/passwd)\n", "link-target"),
        ("<javascript:alert(1)>\n", "link-target"),
        ("Claim.[^a]\n\n[^a]: Source. [site](javascript:alert(1))\n", "link-target"),
        ("![x](javascript:alert(1))\n", "image-src"),
        ("![x](data:image/svg+xml,<svg/onload=alert(1)>)\n", "image-src"),
    ],
)
def test_violation(source: str, code: str) -> None:
    require = code == "frontmatter-missing"
    assert code in codes(source, require_frontmatter=require)


def test_warnings() -> None:
    findings = lint_article(
        "Title\n=====\n\n    indented\n\n```TypeScript\nx\n```\n\n[^a]: Unused.\n",
        require_frontmatter=False,
    )
    warnings = {f.code for f in findings if f.severity == "warning"}
    assert {"setext-heading", "indented-code", "code-info", "citation-unused"} <= warnings


def test_refused_link_is_reported_once_on_its_line() -> None:
    findings = lint_article(
        "ok\n\n[x](javascript:alert(1)) and [y](https://ok.example)\n", require_frontmatter=False
    )
    assert [(f.code, f.line) for f in findings] == [("link-target", 3)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known gap (reviewer): normalization writes an undefined [^label] as escaped text "
        "(\\[^label\\]), which this linter cannot tell from deliberate prose such as the "
        "regex in 03-inline-escapes.md. The plan normalizes drafts before gate 5, so a "
        "hallucinated citation would publish as literal text. Fix in the normalizer: report "
        "unresolved labels instead of escaping them silently."
    ),
)
def test_undefined_citation_survives_normalization() -> None:
    source = (CORPUS_DIR / "19-normalize-citations.md").read_text(encoding="utf-8")
    normalized = (NORMALIZED_DIR / "19-normalize-citations.md").read_text(encoding="utf-8")
    assert "citation-undefined" in codes(source, require_frontmatter=False)
    assert "citation-undefined" in codes(normalized, require_frontmatter=False)


def test_oversized_source_is_one_finding_without_parsing() -> None:
    """Reviewer: the 512 KB body limit was enforced by render_article only."""
    findings = lint_article("a" * (512 * 1024 + 1), require_frontmatter=False)
    assert [(f.code, f.severity) for f in findings] == [("source-too-large", "error")]
