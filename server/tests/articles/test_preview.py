"""The portfolio-like preview: headers, media and diagrams inlined as ``data:`` URIs (alt text
otherwise), sanitized HTML against the XSS corpus, and a golden page."""

from __future__ import annotations

import base64
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

import httpx
import pytest

from blogwriter.articles.preview import MAX_INLINE_IMAGE_BYTES, build_preview, media_name
from blogwriter.core.testing import OWNER_OID, principal_headers
from blogwriter.db import Store
from blogwriter.domain.api import PREVIEW_CSP
from blogwriter.domain.articles import Article, ArticleMeta, ArticleVersion, PublishState
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import MediaRef
from blogwriter.domain.compose import meta_from_frontmatter
from blogwriter.domain.docstore import create_doc, save_singleton
from blogwriter.domain.ports import RenderFailed
from blogwriter.domain.settings import EmployerPolicy
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeRenderService,
    FakeServices,
    MemoryBlobStore,
    png_bytes,
    sample_document,
)
from blogwriter.publish.dialect import MAX_SOURCE_BYTES, normalize_source
from blogwriter.publish.frontmatter import parse_frontmatter, split_frontmatter

READ: Final = principal_headers(OWNER_OID)
REPO_DIR: Final = Path(__file__).resolve().parents[3]
CORPUS_DIR: Final = REPO_DIR / "contracts" / "fixtures" / "corpus"
XSS_CASES: Final = sorted((REPO_DIR / "contracts" / "fixtures" / "xss" / "markdown").glob("*.md"))
GOLDEN_DIR: Final = Path(__file__).parent / "goldens"
UPDATE_GOLDENS: Final = os.environ.get("UPDATE_GOLDENS") == "1"
XSS_TEXTS: Final = {case.stem: case.read_text(encoding="utf-8") for case in XSS_CASES}
FULL_ARTICLE: Final = (CORPUS_DIR / "17-full-article.md").read_text(encoding="utf-8")
BODY_TAGS: Final = frozenset(
    {
        "a",
        "aside",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "figure",
        "h2",
        "h3",
        "h4",
        "hr",
        "img",
        "input",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "span",
        "strong",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
SAFE_HREF: Final = re.compile(r"^(?:https://|mailto:|#|/(?![/\\]))")
IMG_SRC: Final = re.compile(r'<img[^>]*\ssrc="([^"]*)"')


class BodyAudit(HTMLParser):
    """Structural checks of the preview body: profile tags only, no event handlers, safe link
    targets, images only as data: URIs (text that merely looks like markup is fine)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in BODY_TAGS:
            self.problems.append(f"tag <{tag}>")
        for name, value in attrs:
            risky = name.startswith("on") or name in ("style", "srcset", "formaction")
            if risky and not (name == "style" and tag in ("td", "th")):
                self.problems.append(f"attribute {name} on <{tag}>")
            if name == "href" and not SAFE_HREF.match(value or ""):
                self.problems.append(f"href on <{tag}>")
            if name == "src" and not (value or "").startswith("data:image/"):
                self.problems.append(f"src on <{tag}>")


def audit(body: str) -> list[str]:
    parser = BodyAudit()
    parser.feed(body)
    parser.close()
    return parser.problems


DIAGRAM = """```mermaid
flowchart LR
  accTitle: Token flow
  Agent --> Gateway
```"""


def post_body(page: str) -> str:
    start = page.index('<div class="post-body">')
    return page[start : page.index("</article>", start)]


async def seed(store: Store, markdown: str, **fields: object) -> Article:
    article = sample_document(Article, status=ArticleStatus.IN_REVIEW, markdown=markdown, **fields)
    await create_doc(store, article)
    return article


async def put_media(blobs: MemoryBlobStore | None, name: str, data: bytes, kind: str) -> None:
    assert blobs is not None
    await blobs.put("media", name, data, content_type=kind)


async def test_the_preview_inlines_media_and_diagrams(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    image = png_bytes(4, 4)
    await put_media(services.blobs, "uploads/2026-11/abc.png", image, "image/png")
    markdown = (
        "## First section\n\nIntro.\n\n"
        "![A diagram we drew](/api/media/uploads/2026-11/abc.png)\n\n"
        "![A remote image](https://images.example/x.png)\n\n"
        f"{DIAGRAM}\n\n### Detail\n\nMore.\n"
    )
    article = await seed(
        store,
        markdown,
        meta=ArticleMeta(title="Tokens & <agents>", excerpt="Scope it.", pillar="agents"),
    )
    response = await client.get(f"/api/articles/{article.id}/preview", headers=READ)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["content-security-policy"] == PREVIEW_CSP
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["cache-control"] == "no-store"
    page = response.text
    body = post_body(page)
    uploaded = f'src="data:image/png;base64,{base64.b64encode(image).decode()}"'
    assert uploaded in body
    assert 'alt="A diagram we drew"' in body
    assert "https://images.example" not in page
    assert (
        '<span class="missing-image" title="Not shown in the preview">A remote image</span>' in body
    )
    svg = base64.b64encode(FakeRenderService.SVG).decode()
    assert f'<img src="data:image/svg+xml;base64,{svg}" alt="Token flow">' in body
    assert "mermaid-source" not in body
    assert all(src.startswith("data:image/") for src in IMG_SRC.findall(page))
    assert "<h1>Tokens &amp; &lt;agents&gt;</h1>" in page
    assert '<a href="#first-section">First section</a>' in page
    assert "Preview of the draft. Not published." in page
    assert services.render is not None
    assert services.render.calls == ["render_mermaid_svg"]


async def test_large_missing_and_foreign_images_show_their_alt_text(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    big = b"\x89PNG" + b"0" * MAX_INLINE_IMAGE_BYTES
    await put_media(services.blobs, "uploads/2026-11/big.png", big, "image/png")
    await put_media(services.blobs, "uploads/2026-11/doc.pdf", b"%PDF-1.7", "application/pdf")
    markdown = (
        "![Too big](/api/media/uploads/2026-11/big.png)\n\n"
        "![Gone](/api/media/uploads/2026-11/gone.png)\n\n"
        "![Not an image](/api/media/uploads/2026-11/doc.pdf)\n\n"
        "![Escape](/api/media/../secrets.png)\n\n"
        "![Published asset](/blog-media/some-post/hero.png)\n\n"
        '![Quote " and <tag>](/x.png)\n'
    )
    article = await seed(store, markdown)
    page = (await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text
    body = post_body(page)
    for alt in ("Too big", "Gone", "Not an image", "Escape", "Published asset"):
        assert f'title="Not shown in the preview">{alt}</span>' in body
    assert "Quote &quot; and &lt;tag&gt;</span>" in body
    assert "<img" not in body


async def test_without_storage_or_rendering_the_preview_still_works(store: Store) -> None:
    article = await seed(store, f"![Pic](/api/media/uploads/2026-11/a.png)\n\n{DIAGRAM}\n")
    page = await build_preview(
        article_id=article.id,
        meta=article.meta,
        body=article.markdown,
        media=[],
        blobs=None,
        render=None,
    )
    body = post_body(page)
    assert "Pic</span>" in body
    assert '<pre class="mermaid-source"><code>flowchart LR' in body


async def test_a_failed_diagram_keeps_its_source_and_renders_are_cached(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    article = await seed(store, f"{DIAGRAM}\n")
    render = services.render
    assert render is not None
    render.fail_with = RenderFailed("mermaid_error")
    failed = post_body((await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text)
    assert '<pre class="mermaid-source">' in failed
    render.fail_with = None
    for _ in range(2):
        page = (await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text
        assert "data:image/svg+xml;base64," in page
    assert render.calls == ["render_mermaid_svg", "render_mermaid_svg"]  # once failed, once ok


async def test_preview_of_a_version_and_of_a_live_article(
    client: httpx.AsyncClient, store: Store
) -> None:
    article = await seed(
        store, "Current text.\n", version=2, publish=PublishState(live=True, slug="live-post")
    )
    version = ArticleVersion(
        id=ArticleVersion.id_for(article.id, 1),
        article_id=article.id,
        version=1,
        meta=ArticleMeta(title="Old title"),
        markdown="Old text.\n",
        content_sha256=sha256_hex("old"),
        source="pipeline",
        author="pipeline",
        status_at_save=ArticleStatus.DRAFTING,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
    )
    await create_doc(store, version)
    old = (await client.get(f"/api/articles/{article.id}/preview?version=1", headers=READ)).text
    assert "Old text." in old
    assert "<h1>Old title</h1>" in old
    assert "Preview of version 1. The published page stays as it is" in old
    current = (await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text
    assert "Current text." in current
    assert "<h1>Untitled draft</h1>" in current
    missing = await client.get(f"/api/articles/{article.id}/preview?version=7", headers=READ)
    assert missing.status_code == 404


async def test_the_hero_and_the_disclaimer_appear(
    client: httpx.AsyncClient, store: Store, services: FakeServices
) -> None:
    hero = png_bytes(12, 6)
    await put_media(services.blobs, "articles/x/hero.png", hero, "image/png")
    ref = MediaRef(
        role="hero_image",
        name="articles/x/hero.png",
        sha256=sha256_hex(hero),
        content_type="image/png",
        size_bytes=len(hero),
        alt_text="A gateway between agents",
    )
    article = await seed(store, "Text.\n", media=[ref])
    policy = EmployerPolicy.default(SAMPLE_NOW).model_copy(
        update={"disclaimer_long": "Views are my own, not my employer's."}
    )
    await save_singleton(store, policy, if_match=None)
    page = (await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text
    assert '<figure class="hero"><img src="data:image/png;base64,' in page
    assert 'alt="A gateway between agents"' in page
    assert '<p class="disclaimer">Views are my own, not my employer&#39;s.</p>' in page


async def test_a_body_that_cannot_render_shows_a_notice(store: Store) -> None:
    body = "---\n" + "a" * (MAX_SOURCE_BYTES - 4)
    page = await build_preview(
        article_id="art-x", meta=ArticleMeta(), body=body, media=[], blobs=None, render=None
    )
    assert "This text cannot be previewed." in page
    assert store is not None


def test_only_same_origin_media_names_are_read() -> None:
    assert media_name("/api/media/uploads/2026-11/a.png") == "uploads/2026-11/a.png"
    assert media_name("/api/media/brand/a.png") == "brand/a.png"
    for src in (
        "/api/media/../x.png",
        "/api/media/uploads/../../x.png",
        "/api/media/other/a.png",
        "/api/media/",
        "https://example.com/api/media/uploads/a.png",
        "/blog-media/x/a.png",
    ):
        assert media_name(src) is None, src


@pytest.mark.parametrize("case", sorted(XSS_TEXTS))
async def test_hostile_markdown_is_neutralized_in_the_preview(
    client: httpx.AsyncClient, store: Store, case: str
) -> None:
    article = await seed(
        store,
        XSS_TEXTS[case],
        meta=ArticleMeta(title="<script>alert(1)</script>", excerpt='" onmouseover="x'),
    )
    response = await client.get(f"/api/articles/{article.id}/preview", headers=READ)
    assert response.status_code == 200
    page = response.text
    assert audit(post_body(page)) == []
    assert all(src.startswith("data:image/") for src in IMG_SRC.findall(page))
    assert "<h1>&lt;script&gt;alert(1)&lt;/script&gt;</h1>" in page
    assert '<p class="excerpt">&#34; onmouseover=&#34;x</p>' in page
    assert page.lower().count("<script") == 0


def test_xss_corpus_is_present() -> None:
    assert len(XSS_CASES) == 24


def check_golden(name: str, actual: str) -> None:
    golden = GOLDEN_DIR / name
    if UPDATE_GOLDENS:
        golden.write_text(actual, encoding="utf-8", newline="\n")
    assert golden.exists(), "missing golden; run UPDATE_GOLDENS=1 pytest tests/articles"
    assert actual == golden.read_text(encoding="utf-8"), f"{name} differs from its golden"


async def test_golden_preview_of_the_full_article(client: httpx.AsyncClient, store: Store) -> None:
    frontmatter, body = split_frontmatter(normalize_source(FULL_ARTICLE))
    assert frontmatter is not None
    article = sample_document(
        Article,
        id="art-0000000000000000000000000017",
        status=ArticleStatus.IN_REVIEW,
        meta=meta_from_frontmatter(parse_frontmatter(frontmatter)),
        markdown=body,
    )
    await create_doc(store, article)
    page = (await client.get(f"/api/articles/{article.id}/preview", headers=READ)).text
    assert audit(post_body(page)) == []
    check_golden("preview-17-full-article.html", page)
