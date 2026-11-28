"""Illustrations travel to the portfolio like every article image: the chosen hero becomes
``heroImage`` (and an asset) instead of the typographic card, an inserted image is copied to
``published/<slug>/`` and rewritten to ``/blog-media/<slug>/...``, and ``og:image`` stays the
1200x630 card. Approval covers both: the insert changes the content hash the gates and the
approval use, and the hero choice changes the etag the approve route takes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from blogwriter.articles import (
    StaleArticle,
    attach_illustrations,
    choose_hero,
    insert_illustration,
)
from blogwriter.core.errors import ProblemError
from blogwriter.domain.articles import Article, Illustration
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.compose import compose_document
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.settings import BrandKit, PillarSettings
from blogwriter.domain.states import ArticleStatus, WriterConflict
from blogwriter.domain.testing import FakeServices, make_services
from blogwriter.illustrations.steps import reencode_illustration
from blogwriter.illustrations.testing import generated_png
from blogwriter.publish.approval import approve
from blogwriter.publish.assets import hero_alt, prepare_assets, render_published
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.document import PublishedImage, build_published_document
from blogwriter.publish.models import ApproveIn
from blogwriter.publish.renderer import render_article
from blogwriter.publish.testing import SITE, seed_article
from blogwriter.storage import store_media

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SLUG = "scope-every-tool-call"


@pytest.fixture
def services() -> FakeServices:
    return make_services()


async def candidate(
    services: FakeServices, article: Article, color: str, size: tuple[int, int], alt: str
) -> Illustration:
    assert services.blobs is not None
    data, width, height = reencode_illustration(generated_png(color, size), max_pixels=12_000_000)
    ref = await store_media(
        services.blobs,
        data,
        role="image",
        area="articles",
        owner_id=article.id,
        content_type="image/webp",
        now=services.clock.now(),
        width=width,
        height=height,
        alt_text=alt,
    )
    return Illustration(
        id=Illustration.id_for(ref.sha256),
        role="hero" if size[0] > size[1] else "inline",
        media=ref,
        alt_text=alt,
        prompt_summary="Each tool call gets its own small key",
        job_id="illustrations-test-1",
        created_at=to_iso(T0),
    )


async def illustrated(services: FakeServices) -> tuple[Article, Illustration, Illustration]:
    """A publishable article whose hero and one inline image are illustrations."""
    seeded = await seed_article(services)
    hero = await candidate(services, seeded, "#335577", (96, 64), "Small glass keys in a row")
    inline = await candidate(services, seeded, "#446688", (64, 64), "One lit step in the dark")
    attached = await attach_illustrations(
        services.store, seeded.id, [hero, inline], now=services.clock.now()
    )
    chosen = await choose_hero(
        services.store, seeded.id, hero.id, if_match=attached.etag, now=services.clock.now()
    )
    saved = await insert_illustration(
        services.store,
        seeded.id,
        inline.id,
        heading="What to do next",
        if_match=chosen.etag,
        now=services.clock.now(),
    )
    return saved.stored.doc, hero, inline


async def published(services: FakeServices, article: Article) -> dict[str, Any]:
    assert services.blobs is not None
    assert services.render is not None
    document = compose_document(article.meta, article.markdown)
    prepared = await prepare_assets(
        article,
        first_pass=render_article(document),
        blobs=services.blobs,
        render=services.render,
        brand=BrandKit.default(to_iso(T0)).model_copy(update={"author_name": "Test Author"}),
        pillars=PillarSettings.default(to_iso(T0)),
    )
    rendered = render_published(document, prepared)
    hero = prepared.hero
    doc = build_published_document(
        article,
        rendered,
        prepared.assets,
        now=T0,
        site_origin=SITE,
        hero=PublishedImage(hero.url, hero.width, hero.height, hero_alt(article, prepared)),
        markdown=document,
    )
    assert validate_published_doc(doc, now=T0) == []
    return doc


async def test_the_hero_and_an_inserted_image_are_published_as_assets(
    services: FakeServices,
) -> None:
    article, hero, inline = await illustrated(services)
    doc = await published(services, article)
    assert services.blobs is not None
    hero_bytes, _ = services.blobs.blobs[("media", hero.media.name)]
    hero_file = f"hero-{sha256_hex(hero_bytes)[:16]}.webp"
    assert doc["heroImage"] == {
        "url": f"/blog-media/{SLUG}/{hero_file}",
        "alt": "Small glass keys in a row",
        "width": 96,
        "height": 64,
    }
    og = doc["ogImage"]
    assert og["url"].startswith(f"/blog-media/{SLUG}/og-")
    assert (og["width"], og["height"]) == (1200, 630)
    assert og["url"] != doc["heroImage"]["url"]
    inline_bytes, _ = services.blobs.blobs[("media", inline.media.name)]
    inline_file = f"img-{sha256_hex(inline_bytes)[:16]}.webp"
    assets = {asset["path"]: asset for asset in doc["assets"]}
    for file, data in ((hero_file, hero_bytes), (inline_file, inline_bytes)):
        asset = assets[f"/blog-media/{SLUG}/{file}"]
        assert asset["blob"] == f"{SLUG}/{file}"
        assert asset["sha256"] == sha256_hex(data)
        copied, _ = services.blobs.blobs[("published", asset["blob"])]
        assert copied == data
    html = doc["htmlContent"]
    assert (
        f'<img src="/blog-media/{SLUG}/{inline_file}" alt="One lit step in the dark" '
        'width="64" height="64">' in html
    )
    assert "/api/media/" not in html


async def test_approval_covers_the_inserted_image_and_the_hero(services: FakeServices) -> None:
    seeded = await seed_article(services)
    hero = await candidate(services, seeded, "#335577", (96, 64), "Small glass keys in a row")
    inline = await candidate(services, seeded, "#446688", (64, 64), "One lit step in the dark")
    attached = await attach_illustrations(
        services.store, seeded.id, [hero, inline], now=services.clock.now()
    )
    chosen = await choose_hero(
        services.store, seeded.id, hero.id, if_match=attached.etag, now=services.clock.now()
    )
    with pytest.raises(StaleArticle):  # an approval of the page before the hero choice
        await approve(
            services, seeded.id, if_match=attached.etag, request=ApproveIn(), mode="approve"
        )
    saved = await insert_illustration(
        services.store,
        seeded.id,
        inline.id,
        heading="What to do next",
        if_match=chosen.etag,
        now=services.clock.now(),
    )
    article = saved.stored.doc
    assert article.version == seeded.version + 1
    assert article.compute_sha256() != seeded.compute_sha256()  # the image is content
    with pytest.raises(ProblemError) as stale:  # the gates ran on the body without it
        await approve(
            services, seeded.id, if_match=saved.stored.etag, request=ApproveIn(), mode="approve"
        )
    assert stale.value.code == "report_stale"


async def test_a_hero_choice_is_refused_once_the_article_is_approved(
    services: FakeServices,
) -> None:
    seeded = await seed_article(services, status=ArticleStatus.APPROVED)
    hero = await candidate(services, seeded, "#335577", (96, 64), "Small glass keys in a row")
    attached = await attach_illustrations(
        services.store, seeded.id, [hero], now=services.clock.now()
    )
    with pytest.raises(WriterConflict):
        await choose_hero(
            services.store, seeded.id, hero.id, if_match=attached.etag, now=services.clock.now()
        )
    doc = (await read_doc(services.store, Article, seeded.id)).doc
    assert doc.hero_media() is None
