"""Converters: v1 documents into valid v2 documents (synthetic v1 data from conftest)."""

from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from PIL import Image

from blogwriter.backups.migrate_v1.convert import (
    MIGRATED_FROM,
    Conversion,
    ConvertContext,
    PostOverride,
    convert_crawled_article,
    convert_draft,
    convert_feed_source,
    convert_keywords,
    convert_published,
    convert_voice_profile,
    draft_status,
)
from blogwriter.backups.migrate_v1.images import V1Image, decode_v1_image
from blogwriter.backups.migrate_v1.seeds import KbSeed, VoiceSeed
from blogwriter.domain.articles import Article, ArticleVersion
from blogwriter.domain.base import derived_id
from blogwriter.domain.hashing import url_sha256
from blogwriter.domain.signals import Signal, Source
from blogwriter.domain.states import ArticleStatus, ReviewStatus, SourceStatus
from blogwriter.publish.contract import validate_published_doc

type V1Data = dict[str, list[dict[str, Any]]]

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
PILLARS = ("agents", "identity", "governance")
FORMATS = ("case-study", "framework", "deep-explainer", "news-analysis")


def context(**changes: Any) -> ConvertContext:
    values: dict[str, Any] = {
        "now": NOW,
        "pillars": PILLARS,
        "formats": FORMATS,
        "default_pillar": "agents",
        "published_slugs": frozenset({"agent-identity-mcp", "okta-to-entra-lessons"}),
        "feed_ids": frozenset({"fs-1", "fs-2"}),
    }
    values.update(changes)
    return ConvertContext(**values)


def decoded(data: V1Data) -> dict[str, V1Image | None]:
    return {item["id"]: decode_v1_image(item) for item in data["images"]}


def item(data: V1Data, container: str, v1_id: str) -> dict[str, Any]:
    return next(entry for entry in data[container] if entry["id"] == v1_id)


def only_doc(conversion: Conversion) -> dict[str, Any]:
    assert conversion.ok, conversion.problems
    (doc,) = conversion.docs
    return doc.body


def blob_bytes(conversion: Conversion) -> Mapping[str, bytes]:
    return {blob.name: blob.data for blob in conversion.blobs}


# --------------------------------------------------------------------------------------------
# published-blogs
# --------------------------------------------------------------------------------------------


def test_a_published_post_becomes_a_valid_v2_document(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "agent-identity-mcp")
    conversion = convert_published(v1, decoded(v1_data), context())
    doc = only_doc(conversion)
    assert validate_published_doc(doc, now=NOW) == []
    assert doc["id"] == doc["slug"] == "agent-identity-mcp"
    assert doc["schemaVersion"] == 2
    assert doc["status"] == "published"
    assert doc["title"] == "Agent identity in MCP servers"
    assert doc["excerpt"] == "Why agents need their own identities."
    assert doc["publishedAt"] == "2026-03-01T12:34:56Z"
    assert doc["updatedAt"] == "2026-03-02T08:00:00Z"
    assert doc["date"] == "2026-03-01"
    assert doc["tags"] == ["ai-agents", "mcp"]
    assert doc["pillar"] == "agents"
    assert doc["format"] == "news-analysis"
    assert doc["sourceUrl"] == "https://example.com/source"
    assert doc["sourceType"] == "webpage"
    assert doc["corrections"] == []
    assert [entry["id"] for entry in doc["toc"]] == ["the-problem", "details", "what-to-do-monday"]
    assert doc["wordCount"] > 10
    assert doc["readingTimeMinutes"] == 1


def test_v1_images_become_listed_same_origin_assets(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "agent-identity-mcp")
    conversion = convert_published(v1, decoded(v1_data), context())
    doc = only_doc(conversion)
    blobs = blob_bytes(conversion)
    hero_bytes = base64.b64decode(item(v1_data, "images", "img-hero")["imageBase64"])
    hero_sha = hashlib.sha256(hero_bytes).hexdigest()
    hero_path = f"/blog-media/agent-identity-mcp/{hero_sha[:16]}.jpg"
    assert doc["heroImage"] == {
        "url": hero_path,
        "alt": "Agent identity in MCP servers",
        "width": 1536,
        "height": 1024,
    }
    assert blobs[f"agent-identity-mcp/{hero_sha[:16]}.jpg"] == hero_bytes
    assets = {asset["path"]: asset for asset in doc["assets"]}
    assert assets[hero_path] == {
        "path": hero_path,
        "blob": hero_path.removeprefix("/blog-media/"),
        "sha256": hero_sha,
    }
    for asset in doc["assets"]:
        assert hashlib.sha256(blobs[asset["blob"]]).hexdigest() == asset["sha256"]
    assert {blob.container for blob in conversion.blobs} == {"published"}
    diagram = next(path for path in assets if path.endswith(".png") and "/og-" not in path)
    assert f'src="{diagram}"' in doc["htmlContent"]
    assert 'width="400" height="300"' in doc["htmlContent"]
    og = doc["ogImage"]
    assert og["width"] == 1200
    assert og["height"] == 630
    assert og["url"].endswith(".png")
    with Image.open(io.BytesIO(blobs[og["url"].removeprefix("/blog-media/")])) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)


def test_placeholders_banners_and_mdx_are_gone(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "agent-identity-mcp")
    conversion = convert_published(v1, decoded(v1_data), context())
    doc = only_doc(conversion)
    html: str = doc["htmlContent"]
    markdown: str = doc["mdxContent"]
    assert "data:" not in html
    assert "data:" not in markdown
    assert 'class="frontmatter"' not in html
    assert "old render" not in html
    assert 'src="/api/images' not in html
    assert "](/api/images/" not in markdown.split("```")[0]
    assert "<Callout" not in markdown
    assert "Scope every grant." in markdown
    assert "import Callout" not in markdown
    assert markdown.count("Agent identity in MCP servers") == 1  # title in frontmatter only
    assert "https://example.com/chart.png" in html
    assert "/api/images/never-read" in markdown  # inside the code block, untouched
    assert "<h1" not in html
    assert "#### Deep detail" in markdown
    assert set(conversion.notes) >= {"placeholder_dropped"}


def test_a_post_without_a_hero_gets_a_card_and_a_derived_excerpt(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "okta-to-entra-lessons")
    conversion = convert_published(v1, decoded(v1_data), context())
    doc = only_doc(conversion)
    assert validate_published_doc(doc, now=NOW) == []
    assert doc["heroImage"]["url"] == doc["ogImage"]["url"]
    assert doc["heroImage"]["width"] == 1200
    assert doc["excerpt"].startswith("Migrate the identity provider last")
    assert doc["pillar"] == "identity"
    assert doc["format"] == "deep-explainer"
    assert "sourceUrl" not in doc
    assert doc["tags"] == []
    assert doc["date"] == "2026-04-10"
    assert {"hero_generated", "excerpt_derived"} <= set(conversion.notes)


def test_missing_and_unsafe_images_are_dropped(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "agent-identity-mcp")
    v1["mdxContent"] = v1["mdxContent"].replace("img-2", "img-svg")
    images = decoded(v1_data)
    assert images["img-svg"] is None  # SVG is never published from v1
    del images["img-hero"]
    conversion = convert_published(v1, images, context())
    doc = only_doc(conversion)
    assert validate_published_doc(doc, now=NOW) == []
    assert "image_missing" in conversion.notes
    assert "/api/images" not in doc["htmlContent"].split("<pre")[0]
    assert doc["heroImage"]["url"] == doc["ogImage"]["url"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"slug": "Not A Slug"}, "invalid_slug"),
        ({"id": "other-id"}, "id_slug_mismatch"),
        ({"mdxContent": ""}, "v1_markdown_missing"),
        ({"title": "", "mdxContent": "Body only.\n"}, "title_missing"),
    ],
)
def test_unusable_posts_fail_with_a_code(
    v1_data: V1Data, change: dict[str, Any], code: str
) -> None:
    v1 = {**item(v1_data, "published-blogs", "agent-identity-mcp"), **change}
    conversion = convert_published(v1, decoded(v1_data), context())
    assert not conversion.ok
    assert [problem.code for problem in conversion.problems] == [code]
    assert conversion.docs == []


def test_plain_text_fields_are_made_safe(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "okta-to-entra-lessons")
    v1["title"] = "Generics: <T> in\nAzure   functions"
    v1["excerpt"] = "x" * 450
    conversion = convert_published(v1, decoded(v1_data), context())
    doc = only_doc(conversion)
    assert doc["title"] == "Generics: \u2039T> in Azure functions"
    assert len(doc["excerpt"]) <= 400
    assert {"title_adjusted", "excerpt_adjusted"} <= set(conversion.notes)


def test_overrides_and_options_choose_pillar_and_format(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "okta-to-entra-lessons")
    overridden = context(
        overrides={"okta-to-entra-lessons": PostOverride(pillar="governance", format="framework")}
    )
    doc = only_doc(convert_published(v1, decoded(v1_data), overridden))
    assert (doc["pillar"], doc["format"]) == ("governance", "framework")
    forced = only_doc(
        convert_published(v1, decoded(v1_data), context(format_override="case-study"))
    )
    assert forced["format"] == "case-study"


def test_conversion_is_deterministic(v1_data: V1Data) -> None:
    v1 = item(v1_data, "published-blogs", "agent-identity-mcp")
    first = convert_published(v1, decoded(v1_data), context())
    second = convert_published(v1, decoded(v1_data), context())
    assert only_doc(first) == only_doc(second)
    assert blob_bytes(first) == blob_bytes(second)


# --------------------------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v1_id", "status"),
    [
        ("0f1e2d3c-1111-2222-3333-444455556666", ArticleStatus.ARCHIVED),  # went live in v1
        ("1a2b3c4d-0000-0000-0000-000000000002", ArticleStatus.ARCHIVED),  # feed-generated
        ("1a2b3c4d-0000-0000-0000-000000000003", ArticleStatus.IN_REVIEW),  # the owner's draft
    ],
)
def test_draft_status_mapping(v1_data: V1Data, v1_id: str, status: ArticleStatus) -> None:
    draft = item(v1_data, "drafts", v1_id)
    assert draft_status(draft, frozenset({"agent-identity-mcp"})) is status
    conversion = convert_draft(draft, decoded(v1_data), context())
    article = Article.from_document(conversion.docs[0].body)
    assert article.status is status


def test_a_published_slug_alone_marks_a_draft_as_live(v1_data: V1Data) -> None:
    draft = item(v1_data, "drafts", "1a2b3c4d-0000-0000-0000-000000000003")
    draft["slug"] = "okta-to-entra-lessons"
    assert draft_status(draft, frozenset({"okta-to-entra-lessons"})) is ArticleStatus.ARCHIVED


def test_a_draft_becomes_an_article_with_its_first_version(v1_data: V1Data) -> None:
    v1_id = "0f1e2d3c-1111-2222-3333-444455556666"
    conversion = convert_draft(item(v1_data, "drafts", v1_id), decoded(v1_data), context())
    assert conversion.ok, conversion.problems
    article_doc, version_doc = (doc.body for doc in conversion.docs)
    assert [doc.container for doc in conversion.docs] == ["articles", "article-versions"]
    article = Article.from_document(article_doc)
    version = ArticleVersion.from_document(version_doc)
    assert article.id == derived_id("art", "v1", v1_id)
    assert article.meta.title == "Agent identity in MCP servers"
    assert article.meta.slug == "agent-identity-mcp"
    assert article.meta.pillar == "agents"
    assert article.meta.tags == ["ai"]
    assert article.version == 1
    assert article.updated_by == "system"
    assert article.content_sha256 == article.compute_sha256()
    assert article.word_count > 10
    assert article.created_at == "2026-02-28T10:00:00.000000Z"
    assert article.updated_at == "2026-03-01T12:00:00.000000Z"
    assert not article.markdown.startswith("---")
    assert "data:" not in article.markdown
    assert article_doc[MIGRATED_FROM] == {
        "source": "v1",
        "container": "drafts",
        "id": v1_id,
        "updatedAt": "2026-03-01T12:00:00.000000Z",
    }
    assert version.id == f"{article.id}-v00001"
    assert version.markdown == article.markdown
    assert version.content_sha256 == article.content_sha256
    assert version.status_at_save is ArticleStatus.ARCHIVED
    assert "userId" not in article_doc


def test_draft_images_move_to_media_blobs(v1_data: V1Data) -> None:
    v1_id = "0f1e2d3c-1111-2222-3333-444455556666"
    conversion = convert_draft(item(v1_data, "drafts", v1_id), decoded(v1_data), context())
    article = Article.from_document(conversion.docs[0].body)
    names = [ref.name for ref in article.media]
    assert len(names) == 2
    assert all(name.startswith(f"articles/{article.id}/") for name in names)
    for ref in article.media:
        assert f"](/api/media/{ref.name})" in article.markdown
        data = blob_bytes(conversion)[ref.name]
        assert hashlib.sha256(data).hexdigest() == ref.sha256
        assert ref.role == "image"
    assert {blob.container for blob in conversion.blobs} == {"media"}
    assert "/api/images/img-" not in article.markdown


def test_gif_images_in_drafts_become_png(
    v1_data: V1Data, image_b64: Callable[[int, int, str, str], str]
) -> None:
    v1_data["images"].append({"id": "img-gif", "imageBase64": image_b64(8, 8, "#00ff00", "GIF")})
    draft = item(v1_data, "drafts", "1a2b3c4d-0000-0000-0000-000000000003")
    draft["content"] += "\n![anim](/api/images/img-gif)\n"
    conversion = convert_draft(draft, decoded(v1_data), context())
    article = Article.from_document(conversion.docs[0].body)
    (ref,) = article.media
    assert ref.content_type == "image/png"
    assert ref.name.endswith(".png")


def test_a_draft_without_a_slug_gets_one_from_its_title(v1_data: V1Data) -> None:
    draft = item(v1_data, "drafts", "1a2b3c4d-0000-0000-0000-000000000003")
    article = Article.from_document(convert_draft(draft, {}, context()).docs[0].body)
    assert article.meta.slug == "my-notes-on-agent-governance"
    assert article.meta.pillar == "governance"
    assert article.meta.excerpt is None


# --------------------------------------------------------------------------------------------
# feed-sources, crawled-articles
# --------------------------------------------------------------------------------------------


def test_a_feed_becomes_a_source_without_auto_publish_flags(v1_data: V1Data) -> None:
    body = only_doc(convert_feed_source(item(v1_data, "feed-sources", "fs-1"), context()))
    source = Source.from_document(body)
    assert source.id == derived_id("feed", "v1", "fs-1")
    assert source.type == "feed"
    assert source.title == "Azure blog"
    assert source.url == "https://azure.microsoft.com/blog/feed/"
    assert source.site_url == "https://azure.microsoft.com/blog"
    assert source.status is SourceStatus.ACTIVE
    assert source.last_crawled_at == "2026-11-20T00:00:00.000000Z"
    assert source.pillar == "agents"
    for dropped in (
        "autoPublishBlog",
        "autoPublishLinkedIn",
        "autoPublishTwitter",
        "crawlIntervalMinutes",
        "maxArticleAgeDays",
        "maxArticlesToGenerate",
        "topics",
        "feedType",
        "enabled",
    ):
        assert dropped not in body


def test_an_html_only_feed_is_paused_with_a_reason(v1_data: V1Data) -> None:
    conversion = convert_feed_source(item(v1_data, "feed-sources", "fs-2"), context())
    source = Source.from_document(only_doc(conversion))
    assert source.status is SourceStatus.PAUSED
    assert source.url == "https://identity.example.org"
    assert source.last_error == "v1_no_feed_url"
    assert {"url_upgraded", "no_feed_url"} <= set(conversion.notes)


def test_a_feed_without_any_url_fails(v1_data: V1Data) -> None:
    v1 = {**item(v1_data, "feed-sources", "fs-1"), "feedUrl": "", "baseUrl": ""}
    conversion = convert_feed_source(v1, context())
    assert [problem.code for problem in conversion.problems] == ["feed_url_missing"]


def test_a_crawled_article_becomes_a_hash_only_signal(
    v1_data: V1Data, canaries: tuple[str, str]
) -> None:
    v1 = item(v1_data, "crawled-articles", "c1")
    body = only_doc(convert_crawled_article(v1, context()))
    signal = Signal.from_document(body)
    source_id = derived_id("feed", "v1", "fs-1")
    digest = url_sha256("https://azure.microsoft.com/blog/post-1")
    assert signal.url_sha256 == digest  # the tracking parameter is normalized away
    assert signal.id == Signal.id_for(source_id, digest)
    assert signal.source_id == source_id
    assert signal.triaged
    text = repr(body)
    assert canaries[1] not in text
    assert "azure.microsoft.com" not in text
    assert "title" not in body
    assert "articleUrl" not in body


@pytest.mark.parametrize(
    ("change", "skip"),
    [
        ({"feedSourceId": "fs-deleted"}, "orphan"),
        ({"crawledAt": "2025-01-01T00:00:00+00:00"}, "stale"),
        ({"articleUrl": "ftp://example.com/x"}, "invalid_url"),
        ({"articleUrl": ""}, "no_url"),
    ],
)
def test_crawled_articles_that_are_skipped(
    v1_data: V1Data, change: dict[str, Any], skip: str
) -> None:
    v1 = {**item(v1_data, "crawled-articles", "c1"), **change}
    conversion = convert_crawled_article(v1, context())
    assert conversion.skip == skip
    assert conversion.docs == []


# --------------------------------------------------------------------------------------------
# keywords, voice-profiles
# --------------------------------------------------------------------------------------------


def test_keywords_become_a_proposed_kb_seed(v1_data: V1Data) -> None:
    body = only_doc(convert_keywords(item(v1_data, "keywords", "cloud security"), context()))
    seed = KbSeed.from_document(body)
    assert seed.id == KbSeed.id_for("cloud security")
    assert seed.status is ReviewStatus.PROPOSED
    assert seed.topic == "cloud security"
    assert seed.keywords == ["entra", "zero trust", "conditional access"]
    assert seed.suggested_pillar == "identity"
    assert body["kind"] == "kb_seed"


def test_a_voice_profile_becomes_a_proposed_voice_seed(v1_data: V1Data) -> None:
    body = only_doc(convert_voice_profile(item(v1_data, "voice-profiles", "vp-1"), context()))
    seed = VoiceSeed.from_document(body)
    assert seed.status is ReviewStatus.PROPOSED
    assert seed.name == "Default voice"
    assert seed.tone == "direct"
    assert seed.style_notes == "Short sentences.\nNo hype."
    assert seed.text.startswith("I write plainly.")
    assert seed.is_default
    assert "userId" not in body
    assert body["kind"] == "voice_seed"
