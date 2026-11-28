"""The published-blogs v2 documents: built from an article, its second render pass and its
assets; redirect stubs and tombstones; every one passes the contract. Includes the golden
published document of the sample article (``goldens/job-published-document.json``; regenerate
deliberately with ``UPDATE_GOLDENS=1``)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from blogwriter.domain.articles import Article, Correction, PublishState
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import Citation
from blogwriter.domain.compose import compose_document
from blogwriter.domain.settings import BrandKit, PillarSettings
from blogwriter.domain.testing import FakeServices, make_services
from blogwriter.publish.assets import (
    AssetError,
    PreparedAssets,
    hero_alt,
    prepare_assets,
    render_published,
)
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.document import (
    PublishedImage,
    build_published_document,
    contract_time,
    image_sources,
    plain_line,
    redirect_stub,
    swap_image_sources,
    tombstone,
)
from blogwriter.publish.renderer import render_article
from blogwriter.publish.testing import IMAGE_SRC, SITE, article_body, owner_hero, seed_article

GOLDEN = Path(__file__).parent / "goldens" / "job-published-document.json"
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


async def prepared_for(services: FakeServices, article: Article) -> PreparedAssets:
    document = compose_document(article.meta, article.markdown)
    assert services.blobs is not None
    assert services.render is not None
    return await prepare_assets(
        article,
        first_pass=render_article(document),
        blobs=services.blobs,
        render=services.render,
        brand=BrandKit.default(to_iso(T0)).model_copy(update={"author_name": "Test Author"}),
        pillars=PillarSettings.default(to_iso(T0)),
    )


def document_for(
    article: Article,
    prepared: PreparedAssets,
    *,
    now: datetime = T0,
    sources: dict[str, Citation] | None = None,
) -> dict[str, object]:
    document = compose_document(article.meta, article.markdown)
    rendered = render_published(document, prepared)
    hero = prepared.hero
    return build_published_document(
        article,
        rendered,
        prepared.assets,
        now=now,
        site_origin=SITE,
        hero=PublishedImage(hero.url, hero.width, hero.height, hero_alt(article, prepared)),
        sources=sources,
        markdown=document,
    )


@pytest.fixture
def services() -> FakeServices:
    return make_services()


async def test_the_sample_document_matches_its_golden_and_the_contract(
    services: FakeServices,
) -> None:
    article = await seed_article(services)
    prepared = await prepared_for(services, article)
    source = Citation(
        id="src-owasp",
        url="https://genai.owasp.org/llm-top-10/",
        title="OWASP Top 10 for LLM Applications",
        publisher="OWASP",
        date="2025",
        domain="genai.owasp.org",
    )
    doc = document_for(article, prepared, sources={"src-owasp": source})
    assert validate_published_doc(doc, now=T0) == []
    if os.environ.get("UPDATE_GOLDENS") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    golden: Any = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert doc == golden


async def test_assets_are_content_hashed_under_the_slug(services: FakeServices) -> None:
    article = await seed_article(services)
    prepared = await prepared_for(services, article)
    assert services.blobs is not None
    names = sorted(name for container, name in services.blobs.blobs if container == "published")
    assert names == sorted(asset.blob for asset in prepared.assets)
    for asset in prepared.assets:
        assert asset.blob.startswith("scope-every-tool-call/")
        assert asset.path == f"/blog-media/{asset.blob}"
        data, _ = services.blobs.blobs[("published", asset.blob)]
        assert sha256_hex(data) == asset.sha256
        assert asset.sha256[:16] in asset.blob
    kinds = sorted(asset.blob.split("/")[1].split("-")[0] for asset in prepared.assets)
    assert kinds == ["diagram", "img", "og"]
    again = await prepared_for(services, article)
    assert again.assets == prepared.assets  # same bytes, same names: nothing new written


async def test_the_html_points_at_published_media_only(services: FakeServices) -> None:
    article = await seed_article(services)
    prepared = await prepared_for(services, article)
    doc = document_for(article, prepared)
    html = doc["htmlContent"]
    assert isinstance(html, str)
    assert IMAGE_SRC not in html
    assert "/api/media/" not in html
    sources = image_sources(html)
    assert len(sources) == 2  # the diagram and the image
    assert all(src.startswith("/blog-media/scope-every-tool-call/") for src in sources)
    assert 'width="64" height="32"' in html  # the copied image's size from the manifest
    assert doc["mdxContent"] == compose_document(article.meta, article.markdown)


async def test_times_are_contract_formatted_and_never_in_the_future(
    services: FakeServices,
) -> None:
    article = await seed_article(services)
    prepared = await prepared_for(services, article)
    first = document_for(article, prepared, now=T0.replace(microsecond=987654))
    assert first["publishedAt"] == "2026-11-26T12:00:00Z"
    assert first["updatedAt"] == "2026-11-26T12:00:00Z"
    assert first["date"] == "2026-11-26"
    published = article.model_copy(
        update={"publish": PublishState(published_at=to_iso(T0 - timedelta(days=3)))}
    )
    later = document_for(published, prepared, now=T0)
    assert later["publishedAt"] == "2026-11-23T12:00:00Z"
    assert later["updatedAt"] == "2026-11-26T12:00:00Z"
    assert later["date"] == "2026-11-23"
    assert validate_published_doc(later, now=T0) == []


async def test_hero_defaults_to_the_og_png_with_the_title_as_alt(services: FakeServices) -> None:
    article = await seed_article(services)
    prepared = await prepared_for(services, article)
    rendered = render_published(compose_document(article.meta, article.markdown), prepared)
    doc = build_published_document(article, rendered, prepared.assets, now=T0, site_origin=SITE)
    og: Any = doc["ogImage"]
    hero: Any = doc["heroImage"]
    assert og == {"url": prepared.og.url, "width": 1200, "height": 630}
    assert hero["url"] == og["url"]
    assert hero["alt"] == article.meta.title
    assert validate_published_doc(doc, now=T0) == []
    with pytest.raises(ValueError, match="Open Graph"):
        build_published_document(article, rendered, [], now=T0, site_origin=SITE)


async def test_citations_corrections_and_plain_text(services: FakeServices) -> None:
    body = (
        "Text with two sources.[^src-a] And another.[^src-b] And a mail.[^src-c]\n"
        "\n"
        "[^src-a]: A Publisher. *A title*. [a](https://a.example/x)\n"
        "[^src-b]: Unlinked definition only.\n"
        "[^src-c]: Write to [us](mailto:someone@example.com).\n"
    )
    article = await seed_article(services, body=body)
    article = article.model_copy(
        update={
            "corrections": [
                Correction(date="2026-11-26", note="Second\nnote.", created_at=to_iso(T0)),
                Correction(
                    date="2026-11-26", note="First note.", created_at=to_iso(T0 - timedelta(1))
                ),
            ],
            "publish": PublishState(published_at=to_iso(T0 - timedelta(days=2))),
        }
    )
    prepared = await prepared_for(services, article)
    doc = document_for(article, prepared)
    assert doc["citations"] == [
        {"id": "src-a", "title": "A Publisher. A title. a", "url": "https://a.example/x"}
    ]
    assert doc["corrections"] == [
        {"date": "2026-11-26", "note": "First note."},
        {"date": "2026-11-26", "note": "Second note."},
    ]
    assert validate_published_doc(doc, now=T0) == []


def test_stubs_and_tombstones_pass_the_contract() -> None:
    stub = redirect_stub("old-slug", "new-slug", now=T0)
    assert stub == {
        "id": "old-slug",
        "slug": "old-slug",
        "schemaVersion": 2,
        "status": "redirect",
        "redirectTo": "new-slug",
        "updatedAt": "2026-11-26T12:00:00Z",
    }
    assert validate_published_doc(stub, now=T0) == []
    gone = tombstone("old-slug", now=T0)
    assert set(gone) == {"id", "slug", "schemaVersion", "status", "updatedAt"}
    assert gone["status"] == "unpublished"
    assert validate_published_doc(gone, now=T0) == []
    with pytest.raises(ValueError, match="another slug"):
        redirect_stub("same", "same", now=T0)


def test_contract_time_and_plain_line() -> None:
    assert contract_time("2026-11-26T12:00:05.999999Z") == "2026-11-26T12:00:05Z"
    with pytest.raises(ValueError, match="aware"):
        contract_time(datetime(2026, 11, 26))
    assert plain_line("  a\tb\n\u2028c  ", 50) == "a b c"
    clipped = plain_line("word " * 40, 30)
    assert len(clipped) <= 30
    assert clipped.endswith("…")


def test_only_img_sources_are_swapped() -> None:
    html = (
        '<p><img src="/api/media/uploads/a.png" alt="x"><a href="/api/media/uploads/a.png">'
        'link</a><img src="https://cdn.example/b.png" alt="y"></p>'
    )
    swapped = swap_image_sources(html, {"/api/media/uploads/a.png": "/blog-media/s/img-1.png"})
    assert swapped == (
        '<p><img src="/blog-media/s/img-1.png" alt="x"><a href="/api/media/uploads/a.png">'
        'link</a><img src="https://cdn.example/b.png" alt="y"></p>'
    )
    assert image_sources(swapped) == ["/blog-media/s/img-1.png", "https://cdn.example/b.png"]


async def test_the_owners_hero_image_replaces_the_default_card(services: FakeServices) -> None:
    ref, data = owner_hero(alt="A hand-drawn gateway")
    draft_og = ref.model_copy(
        update={"role": "og_image", "sha256": "1" * 64, "name": "articles/x/og.png"}
    )
    default_hero = draft_og.model_copy(update={"role": "hero_image"})
    assert services.blobs is not None
    await services.blobs.put("media", ref.name, data, content_type="image/png")
    article = await seed_article(services, media=[draft_og, default_hero, ref])
    prepared = await prepared_for(services, article)
    assert prepared.hero.url != prepared.og.url
    assert prepared.hero.url.startswith("/blog-media/scope-every-tool-call/hero-")
    assert (prepared.hero.width, prepared.hero.height) == (1600, 900)
    doc = document_for(article, prepared)
    hero: Any = doc["heroImage"]
    assert hero == {
        "url": prepared.hero.url,
        "alt": "A hand-drawn gateway",
        "width": 1600,
        "height": 900,
    }
    assert validate_published_doc(doc, now=T0) == []


async def test_a_pipeline_default_hero_is_not_mistaken_for_the_owners(
    services: FakeServices,
) -> None:
    ref, _ = owner_hero()
    draft_og = ref.model_copy(update={"role": "og_image"})
    article = await seed_article(services, media=[draft_og, ref])  # same bytes: the default card
    prepared = await prepared_for(services, article)
    assert prepared.hero == prepared.og


async def test_a_missing_media_image_is_refused(services: FakeServices) -> None:
    article = await seed_article(
        services, body=article_body(image_src="/api/media/uploads/2026-11/gone.png")
    )
    with pytest.raises(AssetError) as caught:
        await prepared_for(services, article)
    assert caught.value.code == "media_missing"
