"""The canonical article document and word counts (gates, publish and export share them)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from blogwriter.domain.articles import ArticleMeta, SeriesRef
from blogwriter.domain.common import from_linkedin_finding, from_publish_finding
from blogwriter.domain.compose import (
    WORDS_PER_MINUTE,
    FrontmatterError,
    MetaIncomplete,
    compose_document,
    frontmatter_length,
    meta_from_frontmatter,
    word_stats,
)
from blogwriter.linkedin.lint import lint_post
from blogwriter.publish import renderer
from blogwriter.publish.frontmatter import parse_frontmatter, split_frontmatter
from blogwriter.publish.lint import Finding as PublishFinding
from blogwriter.publish.lint import lint_article
from blogwriter.publish.normalize import normalize_markdown

REPO_DIR: Final = Path(__file__).resolve().parents[3]
NORMALIZED_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "corpus" / "normalized"
NORMALIZED: Final = sorted(NORMALIZED_DIR.glob("*.md"))
WITH_FRONTMATTER: Final = [p for p in NORMALIZED if p.read_text().startswith("---\n")]

META: Final = ArticleMeta(
    title='Least-privilege "tool" calls',
    slug="least-privilege-tool-calls",
    excerpt="Scope every MCP tool call.",
    pillar="agents",
    format="deep-explainer",
    tags=["mcp", "identity"],
    series=SeriesRef(id="agent-identity", title="Agent identity", part=2, total=3),
)


def test_the_corpus_has_documents_with_frontmatter() -> None:
    assert len(NORMALIZED) == 22
    assert len(WITH_FRONTMATTER) >= 3


@pytest.mark.parametrize("path", WITH_FRONTMATTER, ids=[p.name for p in WITH_FRONTMATTER])
def test_compose_gives_back_the_normalized_bytes(path: Path) -> None:
    """Golden: meta + body of every normalized corpus article compose to its exact bytes."""
    text = path.read_text(encoding="utf-8")
    head, body = split_frontmatter(text)
    assert head is not None
    meta = meta_from_frontmatter(parse_frontmatter(head))
    assert compose_document(meta, body) == text
    assert compose_document(meta, body)[frontmatter_length(meta) :] == body


@pytest.mark.parametrize("path", NORMALIZED, ids=[p.name for p in NORMALIZED])
def test_word_stats_match_the_publish_renderer(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    _head, body = split_frontmatter(text)
    rendered = renderer.render_article(text)
    assert word_stats(body) == (rendered.word_count, rendered.reading_time_minutes)


def test_reading_speed_is_the_renderer_s() -> None:
    assert WORDS_PER_MINUTE == renderer.WORDS_PER_MINUTE
    assert word_stats("") == (0, 0)
    assert word_stats(" ".join(["word"] * 231)) == (231, 2)
    assert word_stats("```\ncode words do not count\n```\n\nTwo words") == (2, 1)


def test_composed_documents_are_canonical_and_lint_clean() -> None:
    body = "A lede with a claim.\n\n## Why it matters\n\nBecause scopes leak.\n"
    document = compose_document(META, body)
    assert document.startswith('---\ntitle: "Least-privilege \\"tool\\" calls"\n')
    assert "series:\n  id: agent-identity\n" in document
    assert normalize_markdown(document).text == document
    assert [f.code for f in lint_article(document) if f.severity == "error"] == []
    assert compose_document(META, "") == document[: frontmatter_length(META) - 1]


def test_incomplete_or_invalid_meta_is_refused() -> None:
    with pytest.raises(MetaIncomplete) as caught:
        compose_document(ArticleMeta(title="T"), "Body")
    assert caught.value.missing == ("slug", "excerpt", "pillar", "format")
    assert caught.value.code == "meta_incomplete"
    with pytest.raises(FrontmatterError):
        compose_document(META.model_copy(update={"title": "A <b>bold</b> title"}), "Body")
    with pytest.raises(FrontmatterError):
        compose_document(META.model_copy(update={"excerpt": " padded"}), "Body")


def test_publish_lint_findings_map_to_body_spans() -> None:
    body = "Intro paragraph.\n\n<div>raw html</div>\n\nDone."
    document = compose_document(META, body)
    start = frontmatter_length(META)
    raw = next(f for f in lint_article(document) if f.code == "raw-html")
    finding = from_publish_finding(raw, document, body_start=start)
    assert (finding.code, finding.severity, finding.overridable) == ("raw-html", "block", False)
    assert finding.start is not None
    assert finding.end is not None
    assert body[finding.start : finding.end] == "<div>raw html</div>"
    in_head = PublishFinding("frontmatter-invalid", "error", 2, "x" * 900)
    whole = from_publish_finding(in_head, document)
    assert (whole.start, whole.end) == (4, 4 + len(document.splitlines()[1]))
    in_body_only = from_publish_finding(in_head, document, body_start=start)
    assert (in_body_only.start, in_body_only.end) == (None, None)
    assert len(in_body_only.message) == 500
    warning = from_publish_finding(PublishFinding("setext-heading", "warning", None, " "), body)
    assert (warning.severity, warning.message, warning.start) == ("warn", "setext-heading", None)
    odd = from_publish_finding(PublishFinding("Odd Code", "error", 99, "m"), body)
    assert (odd.code, odd.start) == ("dialect-lint", None)


def test_linkedin_lint_findings_keep_their_spans() -> None:
    display = "Let's dive in. We leverage scoped tokens for every agent."
    findings = [from_linkedin_finding(f) for f in lint_post(display, approved_hashtags=[])]
    tells = [f for f in findings if f.code == "ai-tell"]
    assert tells
    assert all(f.severity == "warn" for f in tells)
    assert {display[f.start or 0 : f.end or 0].casefold() for f in tells} >= {"leverage"}
