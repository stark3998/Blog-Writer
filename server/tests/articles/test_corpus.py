"""Golden: the 22 dialect corpus files saved through ``owner_save`` come back byte-identical to
``contracts/fixtures/corpus/normalized/`` (composed with their metadata where they have
frontmatter), plus the normalization rules behind it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from blogwriter.articles import compose_document, create_article, owner_save
from blogwriter.articles.content import PLACEHOLDER_HEAD, meta_findings, prepare_body
from blogwriter.db import Store
from blogwriter.domain.articles import ArticleMeta
from blogwriter.domain.compose import meta_from_frontmatter, word_stats
from blogwriter.publish.dialect import MAX_SOURCE_BYTES, normalize_source
from blogwriter.publish.frontmatter import parse_frontmatter, split_frontmatter
from blogwriter.publish.normalize import normalize_markdown
from blogwriter.publish.renderer import render_article

T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
CORPUS_DIR: Final = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "corpus"
MANIFEST: Final[dict[str, Any]] = json.loads(
    (CORPUS_DIR / "manifest.json").read_text(encoding="utf-8")
)
CORPUS_FILES: Final = sorted(MANIFEST["files"])


def test_the_corpus_is_all_there() -> None:
    assert len(CORPUS_FILES) == 22
    assert sorted(p.name for p in (CORPUS_DIR / "normalized").glob("*.md")) == CORPUS_FILES


@pytest.mark.parametrize("name", CORPUS_FILES)
async def test_corpus_file_saved_through_owner_save_is_byte_identical(
    store: Store, name: str
) -> None:
    source = (CORPUS_DIR / name).read_bytes().decode("utf-8")
    expected = (CORPUS_DIR / "normalized" / name).read_bytes().decode("utf-8")
    frontmatter, body = split_frontmatter(normalize_source(source))
    meta = (
        meta_from_frontmatter(parse_frontmatter(frontmatter))
        if frontmatter is not None
        else ArticleMeta()
    )
    created = await create_article(store, now=T0)
    saved = await owner_save(
        store,
        created.doc.id,
        if_match=created.etag,
        base_version=0,
        meta=meta,
        markdown=body,
        now=T0,
    )
    article = saved.stored.doc
    actual = (
        compose_document(article.meta, article.markdown)
        if frontmatter is not None
        else article.markdown
    )
    assert actual == expected
    assert (article.word_count, article.reading_time_minutes) == word_stats(article.markdown)
    errors = {"19-normalize-citations.md": ["citation-undefined"]}.get(name)
    if name == "22-tolerated-but-linted.md":
        errors = ["raw-html", "raw-html"]
    assert [finding.code for finding in saved.findings] == (errors or [])


def test_the_placeholder_head_is_canonical() -> None:
    assert normalize_markdown(PLACEHOLDER_HEAD).text == PLACEHOLDER_HEAD
    assert normalize_markdown(f"{PLACEHOLDER_HEAD}\nBody.").text == f"{PLACEHOLDER_HEAD}\nBody.\n"


def test_a_body_opening_with_a_rule_is_read_as_a_rule() -> None:
    body = "---\n\nText after a rule.\n"
    alone = normalize_markdown(body)
    assert [error.code for error in alone.errors] == ["frontmatter-invalid"]
    prepared = prepare_body(body)
    assert prepared.text == body
    assert prepared.findings == ()
    assert prepared.word_count == 4
    shipped = render_article(compose_document(full_meta(), prepared.text)).html
    assert shipped.startswith("<hr>\n<p>Text after a rule.</p>")


def test_empty_and_blank_bodies() -> None:
    assert prepare_body("").text == ""
    assert prepare_body("\n\n\n").text == ""
    assert prepare_body("\n\nText.").text == "Text.\n"
    assert prepare_body("").word_count == 0


def test_a_body_at_the_cap_is_normalized_alone() -> None:
    body = "word " * ((MAX_SOURCE_BYTES - 40) // 5)
    assert len(f"{PLACEHOLDER_HEAD}\n{body}".encode()) > MAX_SOURCE_BYTES
    prepared = prepare_body(body)
    assert prepared.text == body.strip() + "\n"
    assert prepared.word_count == len(body.split())


def test_normalizer_findings_point_into_the_body() -> None:
    body = "# A heading\n\nFine.\n\nBad <b>html</b> here.\n"
    prepared = prepare_body(body)
    assert [finding.code for finding in prepared.findings] == ["raw-html"]
    finding = prepared.findings[0]
    assert finding.start is not None
    assert finding.end is not None
    assert body[finding.start : finding.end] == "Bad <b>html</b> here."


def full_meta() -> ArticleMeta:
    return ArticleMeta(
        title="Least privilege for agents",
        slug="least-privilege-agents",
        excerpt="Scope every tool call.",
        pillar="agents",
        format="deep-explainer",
        tags=["identity"],
    )


def test_metadata_findings_only_for_values_that_break_the_dialect() -> None:
    assert meta_findings(ArticleMeta()) == []
    assert meta_findings(full_meta()) == []
    assert meta_findings(ArticleMeta(title="Partial")) == []
    broken = meta_findings(ArticleMeta(title=" padded", excerpt="line\nbreak"))
    assert [finding.message.split(":")[0] for finding in broken] == ["title", "excerpt"]
    assert all(finding.severity == "block" for finding in broken)
    assert all(finding.code == "frontmatter-invalid" for finding in broken)
