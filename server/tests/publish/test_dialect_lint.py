"""Dialect linter (gate 5 dialect conformance): the corpus lints as its manifest says, and every
violation in ``docs/v2/article-dialect.md`` section 4 is reported with its stable code."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path
from typing import Any, Final

import pytest

from blogwriter.publish.dialect import MAX_SOURCE_BYTES
from blogwriter.publish.lint import Finding, lint_article
from blogwriter.publish.normalize import normalize_markdown

REPO_DIR: Final = Path(__file__).resolve().parents[3]
CORPUS_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "corpus"
NORMALIZED_DIR: Final = CORPUS_DIR / "normalized"
MANIFEST: Final[dict[str, Any]] = json.loads(
    (CORPUS_DIR / "manifest.json").read_text(encoding="utf-8")
)
FRONTMATTER: Final = (
    '---\ntitle: "T"\nslug: t\nexcerpt: "E"\npillar: p\nformat: f\ntags: []\n---\n\n'
)


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
        ('---\ntitle: "T"\n---\n\nBody.\n', "frontmatter-invalid"),
        ("# Title\n", "heading-level"),
        ("##### Five\n", "heading-level"),
        ("- ## heading in a list\n", "heading-nested"),
        (":::note\n## heading in a callout\n:::\n", "heading-nested"),
        ("##\n", "heading-empty"),
        ("<div>raw</div>\n", "raw-html"),
        ("text <b>bold</b>\n", "raw-html"),
        ("a <!-- comment --> b\n", "raw-html"),
        ("an ![icon](/i.png) inline\n", "inline-image"),
        ("![](/i.png)\n", "image-alt"),
        ("![x](http://insecure.example/i.png)\n", "image-src"),
        ("[![Alt](/blog-media/a/b.png)](https://example.com)\n", "linked-image"),
        ("[x](http://insecure.example)\n", "link-target"),
        ("[x](relative/path)\n", "link-target"),
        ("Claim [^missing].\n", "citation-undefined"),
        ("[see [^missing]](https://ok.example)\n", "citation-undefined"),
        ("Claim.[^a]\n\n[^a]: One.\n[^a]: Two.\n", "citation-duplicate"),
        ("Claim.[^Bad_Id]\n\n[^Bad_Id]: Source.\n", "citation-id"),
        ("Claim.[^a]\n\n[^a]: Source.\n\n    Second paragraph.\n", "citation-definition-blocks"),
        ("[see [^a]](https://ok.example)\n\n[^a]: Source.\n", "citation-in-link"),
        (":::note Title\ntext\n:::\n", "callout-invalid"),
        (":::danger\ntext\n:::\n", "callout-invalid"),
        ("- item\n\n  :::note\n  inside\n  :::\n", "callout-nested"),
        ("- [ ] task\n- plain\n", "mixed-task-list"),
        (f"{FRONTMATTER}---\n\ntext\n", "thematic-break-first"),
        ("bad \u2028 separator\n", "forbidden-character"),
        ("private \ue020 placeholder\n", "forbidden-character"),
        ("bell \x07 character\n", "control-character"),
        ("```mermaid\n```\n", "mermaid-empty"),
        ("> " * 25 + "deep\n", "nesting-too-deep"),
        # markdown-it refuses these targets, so they render as literal text; the spec lists them
        # as link-target / image-src errors.
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
    require = code in ("frontmatter-missing", "frontmatter-invalid")
    assert code in codes(source, require_frontmatter=require)


def test_a_lone_linked_image_is_not_also_an_inline_image() -> None:
    assert codes("[![Alt](/b.png)](https://e.example)\n", require_frontmatter=False) == {
        "linked-image"
    }
    assert codes("Text [![Alt](/b.png)](https://e.example)\n", require_frontmatter=False) == {
        "linked-image",
        "inline-image",
    }


def test_warnings() -> None:
    findings = lint_article(
        "Title\n=====\n\n    indented\n\n```TypeScript\nx\n```\n\n[^a]: Unused.\n",
        require_frontmatter=False,
    )
    warnings = {f.code for f in findings if f.severity == "warning"}
    assert {"setext-heading", "indented-code", "code-info", "citation-unused"} <= warnings


def test_findings_carry_lines_of_the_whole_article() -> None:
    findings = lint_article(
        f"{FRONTMATTER}ok\n\n[x](javascript:alert(1)) and [y](https://ok.example)\n"
    )
    assert [(f.code, f.severity, f.line) for f in findings] == [("link-target", "error", 12)]
    assert isinstance(findings[0], Finding)


def test_definitions_belong_at_the_end() -> None:
    findings = lint_article("[^a]: Early.\n\nClaim.[^a]\n", require_frontmatter=False)
    assert [(f.code, f.severity) for f in findings] == [("citation-placement", "warning")]


def test_oversized_source_is_one_finding_without_parsing() -> None:
    findings = lint_article("a" * (MAX_SOURCE_BYTES + 1), require_frontmatter=False)
    assert [(f.code, f.severity) for f in findings] == [("source-too-large", "error")]


def test_deep_frontmatter_is_invalid_not_a_crash() -> None:
    yaml_text = 'title: "T"\nslug: t\nexcerpt: "E"\npillar: p\nformat: f\ntags: []\n'
    yaml_text += "series: " + "[" * 3000 + "\n"
    assert [f.code for f in lint_article(f"---\n{yaml_text}---\n\nBody.\n")] == [
        "frontmatter-invalid"
    ]


def test_undefined_citation_survives_normalization_as_an_error() -> None:
    """Phase 0 open issue 1, closed: the editor writes an undefined [^label] as escaped text
    (``\\[^label\\]``), which lints clean, so the normalizer reports it instead."""
    source = (CORPUS_DIR / "19-normalize-citations.md").read_text(encoding="utf-8")
    normalized = (NORMALIZED_DIR / "19-normalize-citations.md").read_text(encoding="utf-8")
    assert "citation-undefined" in codes(source, require_frontmatter=False)
    assert "citation-undefined" not in codes(normalized, require_frontmatter=False)
    result = normalize_markdown(source)
    assert result.text == normalized
    gate5 = {f.code for f in result.errors} | codes(result.text, require_frontmatter=False)
    assert "citation-undefined" in gate5
