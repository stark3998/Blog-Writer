"""v1 documents into v2 documents (pure and synchronous: run them in a worker thread).

- ``published-blogs`` -> ``published-blogs`` (contract v2): same id and slug, re-rendered from
  the v1 Markdown; v1 images become ``/blog-media/<slug>/<sha256 prefix>.<ext>`` assets;
  ``data:`` placeholders and the v1 banner are dropped; the hero is the v1 hero image or a
  generated card; the OG image is a 1200x630 PNG.
- ``drafts`` -> ``articles`` (version 1, with its ``article-versions`` copy): the Markdown body
  is kept (normalized when possible), v1 images become ``media`` blobs served at
  ``/api/media/articles/<id>/...``; statuses follow :func:`draft_status`.
- ``feed-sources`` -> ``sources``: auto-publish flags, intervals and topics are dropped.
- ``crawled-articles`` -> ``signals``: the URL hash only (triaged; no title or URL text).
- ``keywords`` -> ``kb_seed`` and ``voice-profiles`` -> ``voice_seed``: proposals for the owner.

Every domain document carries ``migratedFrom`` (``{source, container, id, updatedAt}``): the
writer only replaces a document that still has the ``updatedAt`` the migration wrote, so a
document the owner changed in v2 is never overwritten by a re-run.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from blogwriter.backups.migrate_v1.images import (
    OG_HEIGHT,
    OG_WIDTH,
    CardStyle,
    V1Image,
    cover_png,
    png_of,
    title_card_png,
)
from blogwriter.backups.migrate_v1.markdown import (
    Image,
    clean_v1_markdown,
    rewrite_images,
    split_v1_frontmatter,
    take_leading_image,
    v1_image_id,
    v1_tags,
)
from blogwriter.backups.migrate_v1.seeds import KbSeed, VoiceSeed
from blogwriter.backups.migrate_v1.text import (
    contract_stamp,
    first_time,
    guess_format,
    guess_pillar,
    item_bool,
    item_str,
    item_strings,
    parse_v1_time,
    plain_line,
    ref_for,
    slug_or_none,
    tag_slug,
    text_line,
    unique_tags,
)
from blogwriter.db.base import CONTAINERS, partition_key_path
from blogwriter.domain.articles import Article, ArticleMeta, ArticleVersion
from blogwriter.domain.base import Document, check_https_url, derived_id, to_iso
from blogwriter.domain.common import BlobContainer
from blogwriter.domain.compose import FrontmatterError, MetaIncomplete, compose_document, word_stats
from blogwriter.domain.hashing import article_sha256, url_sha256
from blogwriter.domain.signals import Signal, Source
from blogwriter.domain.states import ArticleStatus, SourceStatus
from blogwriter.publish.contract import ContractUnavailableError, validate_published_doc
from blogwriter.publish.dialect import SourceTooLargeError
from blogwriter.publish.normalize import normalize_markdown
from blogwriter.publish.parser import dialect_parser
from blogwriter.publish.renderer import DiagramUrlError, render_article

__all__ = [
    "MIGRATED_FROM",
    "SIGNAL_WINDOW",
    "Conversion",
    "ConvertContext",
    "PendingBlob",
    "PendingDoc",
    "PostOverride",
    "Problem",
    "convert_crawled_article",
    "convert_draft",
    "convert_feed_source",
    "convert_keywords",
    "convert_published",
    "convert_voice_profile",
    "draft_status",
]

MIGRATED_FROM: Final = "migratedFrom"
SIGNAL_WINDOW: Final = timedelta(days=90)
"""Signals older than the container's TTL are not worth migrating (dedupe only)."""
_TOKEN_RE: Final = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_PUBLISHED_NOTE: Final = "Migrated from v1"
_GUESS_CHARS: Final = 4000
"""How much of the body the pillar guess reads (with the title and tags)."""


class PostOverride(BaseModel):
    """Owner corrections for one post (``--overrides``): its pillar and format ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pillar: str | None = None
    format: str | None = None


@dataclass(frozen=True, slots=True)
class ConvertContext:
    now: datetime
    pillars: tuple[str, ...]
    """The owner's pillar ids (v2 settings), in order."""
    formats: tuple[str, ...]
    """The owner's format-pack ids, in order."""
    default_pillar: str
    format_override: str | None = None
    overrides: Mapping[str, PostOverride] = field(default_factory=dict[str, PostOverride])
    """By published slug or v1 draft id."""
    card: CardStyle = field(default_factory=CardStyle)
    published_slugs: frozenset[str] = frozenset()
    """Slugs of v1's published-blogs documents (drafts that went live)."""
    feed_ids: frozenset[str] = frozenset()
    """Ids of v1's feed sources (signals of deleted feeds are skipped)."""


@dataclass(frozen=True, slots=True)
class PendingBlob:
    container: BlobContainer
    name: str
    data: bytes = field(repr=False)
    content_type: str


@dataclass(frozen=True, slots=True)
class PendingDoc:
    container: str
    body: dict[str, Any] = field(repr=False)

    @property
    def doc_id(self) -> str:
        return str(self.body["id"])

    @property
    def partition_key(self) -> str:
        path = partition_key_path(self.container)
        return str(self.body[path.lstrip("/")])


@dataclass(frozen=True, slots=True)
class Problem:
    """A reason a document cannot be migrated: a code and a JSON path, never a value."""

    code: str
    path: str = ""


@dataclass
class Conversion:
    """What one v1 document becomes (or why it does not)."""

    container: str
    ref: str
    docs: list[PendingDoc] = field(default_factory=list[PendingDoc])
    blobs: list[PendingBlob] = field(default_factory=list[PendingBlob])
    problems: list[Problem] = field(default_factory=list[Problem])
    notes: list[str] = field(default_factory=list[str])
    skip: str | None = None

    @property
    def ok(self) -> bool:
        return self.skip is None and not self.problems and bool(self.docs)

    def fail(self, code: str, path: str = "") -> Conversion:
        self.problems.append(Problem(code, path))
        return self

    def note(self, code: str) -> None:
        if code not in self.notes:
            self.notes.append(code)


# --------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------


def _marker(container: str, v1_id: str, updated_at: str) -> dict[str, str]:
    return {"source": "v1", "container": container, "id": v1_id, "updatedAt": updated_at}


def _validated[D: Document](
    conversion: Conversion, cls: type[D], fields: Mapping[str, object]
) -> D | None:
    try:
        return cls.model_validate(dict(fields))
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            path = "/" + "/".join(str(part) for part in error["loc"])
            conversion.fail(str(error["type"]), path)
        return None


def _add(conversion: Conversion, doc: Document) -> None:
    body = doc.to_document()
    if doc.CONTAINER not in CONTAINERS:  # pragma: no cover - every class names a real container
        raise ValueError(f"unknown container {doc.CONTAINER}")
    conversion.docs.append(PendingDoc(doc.CONTAINER, body))


def _https(value: str | None, conversion: Conversion) -> str | None:
    if value is None:
        return None
    url = value
    if url.lower().startswith("http://"):
        url = "https://" + url[len("http://") :]
        conversion.note("url_upgraded")
    try:
        return check_https_url(url)
    except ValueError:
        conversion.note("url_dropped")
        return None


def _first_paragraph(body: str) -> str | None:
    tokens = dialect_parser().parse(body, {})
    for index, token in enumerate(tokens[:-1]):
        if token.type != "paragraph_open" or token.level != 0:
            continue
        parts: list[str] = []
        for child in tokens[index + 1].children or []:
            if child.type in ("text", "code_inline", "text_special"):
                parts.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                parts.append(" ")
        text = "".join(parts).strip()
        if text:
            return text
    return None


def _normalized(body: str, conversion: Conversion) -> str:
    try:
        result = normalize_markdown(body)
    except SourceTooLargeError:
        conversion.fail("markdown_too_large", "/markdown")
        return body
    if result.ok:
        return result.text
    conversion.note("not_normalized")
    return body


def _pillar_and_format(
    ctx: ConvertContext, key: str, texts: Sequence[str], source_type: str | None
) -> tuple[str, str | None]:
    override = ctx.overrides.get(key)
    pillar = override.pillar if override is not None and override.pillar else None
    if pillar is None:
        pillar = guess_pillar(texts, ctx.pillars) or ctx.default_pillar
    chosen = override.format if override is not None and override.format else None
    return pillar, chosen or ctx.format_override or guess_format(source_type, ctx.formats)


def _tags(item: Mapping[str, Any], meta: Mapping[str, str]) -> list[str]:
    tags = item_strings(item, "tags")
    return unique_tags(tags if tags else v1_tags(meta.get("tags", "")))


type Resolver = Callable[[Image], str | None]


def _image_resolver(
    conversion: Conversion,
    images: Mapping[str, V1Image | None],
    asset: Callable[[V1Image, Image], str],
) -> Resolver:
    def resolve(image: Image) -> str | None:
        src = image.src
        if src.lower().startswith("data:"):
            conversion.note("placeholder_dropped")
            return None
        image_id = v1_image_id(src)
        if image_id is not None:
            found = images.get(image_id)
            if found is None:
                conversion.note("image_missing")
                return None
            return asset(found, image)
        if src.startswith("https://") or (src.startswith("/") and not src.startswith("//")):
            return src
        conversion.note("image_dropped")
        return None

    return resolve


# --------------------------------------------------------------------------------------------
# published-blogs
# --------------------------------------------------------------------------------------------


def convert_published(
    item: Mapping[str, Any], images: Mapping[str, V1Image | None], ctx: ConvertContext
) -> Conversion:
    """A v1 published post as a v2 ``published-blogs`` document (``status: published``)."""
    conversion = Conversion("published-blogs", ref_for("published-blogs", item.get("id")))
    slug = slug_or_none(item.get("slug"))
    if slug is None:
        return conversion.fail("invalid_slug", "/slug")
    if item.get("id") != slug:
        return conversion.fail("id_slug_mismatch", "/id")
    markdown: object = item.get("mdxContent")
    if not isinstance(markdown, str) or not markdown.strip():
        return conversion.fail("v1_markdown_missing", "/mdxContent")
    meta, body = split_v1_frontmatter(markdown)
    raw_title = item_str(item, "title") or meta.get("title")
    title, adjusted = plain_line(raw_title, 150)
    if title is None:
        return conversion.fail("title_missing", "/title")
    if adjusted:
        conversion.note("title_adjusted")

    assets: dict[str, dict[str, str]] = {}
    sizes: dict[str, tuple[int, int]] = {}

    def add_asset(data: bytes, name: str, content_type: str, size: tuple[int, int]) -> str:
        blob = f"{slug}/{name}"
        path = f"/blog-media/{blob}"
        if path not in assets:
            digest = hashlib.sha256(data).hexdigest()
            assets[path] = {"path": path, "blob": blob, "sha256": digest}
            conversion.blobs.append(PendingBlob("published", blob, data, content_type))
            sizes[path] = size
        return path

    def v1_asset(image: V1Image, _: Image) -> str:
        name = f"{image.sha256[:16]}.{image.ext}"
        return add_asset(image.data, name, image.content_type, (image.width, image.height))

    body = clean_v1_markdown(body, title=raw_title)
    hero: dict[str, object] | None = None
    hero_source: V1Image | None = None
    leading, rest = take_leading_image(body)
    if leading is not None:
        leading_id = v1_image_id(leading.src)
        if leading_id is not None:
            found = images.get(leading_id)
            body = rest
            if found is None:
                conversion.note("image_missing")
            else:
                alt, _ = plain_line(leading.alt or title, 300)
                hero = {
                    "url": v1_asset(found, leading),
                    "alt": alt or title,
                    "width": found.width,
                    "height": found.height,
                }
                hero_source = found
        elif leading.src.lower().startswith("data:"):
            body = rest
            conversion.note("placeholder_dropped")
    body = _normalized(
        rewrite_images(body, _image_resolver(conversion, images, v1_asset)), conversion
    )

    excerpt, adjusted = plain_line(item_str(item, "excerpt") or meta.get("excerpt"), 400)
    if excerpt is None:
        excerpt, _ = plain_line(_first_paragraph(body) or title, 400)
        conversion.note("excerpt_derived")
    elif adjusted:
        conversion.note("excerpt_adjusted")
    tags = _tags(item, meta)
    source_type = item_str(item, "sourceType")
    pillar, post_format = _pillar_and_format(
        ctx, slug, [title, *tags, body[:_GUESS_CHARS]], source_type
    )
    try:
        article_meta = ArticleMeta(
            title=title, slug=slug, excerpt=excerpt, pillar=pillar, format=post_format, tags=tags
        )
        document = compose_document(article_meta, body)
        rendered = render_article(document, image_sizes=sizes)
    except ValidationError:
        return conversion.fail("invalid_meta", "/meta")
    except MetaIncomplete as exc:
        return conversion.fail("meta_incomplete", "/" + ",".join(exc.missing))
    except FrontmatterError:
        return conversion.fail("invalid_frontmatter", "/mdxContent")
    except (SourceTooLargeError, DiagramUrlError):
        return conversion.fail("markdown_too_large", "/mdxContent")
    except ContractUnavailableError:
        return conversion.fail("contract_unavailable")

    og_png = cover_png(hero_source) if hero_source is not None else title_card_png(title, ctx.card)
    og_name = f"og-{hashlib.sha256(og_png).hexdigest()[:16]}.png"
    og_path = add_asset(og_png, og_name, "image/png", (OG_WIDTH, OG_HEIGHT))
    if hero is None:
        hero = {"url": og_path, "alt": title, "width": OG_WIDTH, "height": OG_HEIGHT}
        conversion.note("hero_generated")

    published = first_time(item, "publishedAt", "date", "updatedAt")
    updated = max(published, first_time(item, "updatedAt", "publishedAt"))
    published_at = contract_stamp(published)
    doc: dict[str, Any] = {
        "id": slug,
        "slug": slug,
        "schemaVersion": 2,
        "status": "published",
        "title": title,
        "excerpt": excerpt,
        "htmlContent": rendered.html,
        "mdxContent": document,
        "publishedAt": published_at,
        "updatedAt": contract_stamp(updated),
        "date": published_at[:10],
        "tags": tags,
        "pillar": pillar,
        "format": post_format,
        "heroImage": hero,
        "ogImage": {"url": og_path, "width": OG_WIDTH, "height": OG_HEIGHT},
        "assets": list(assets.values()),
        "readingTimeMinutes": rendered.reading_time_minutes,
        "wordCount": rendered.word_count,
        "toc": [
            {
                "id": entry.id,
                "text": text_line(entry.text, 300)[0] or entry.id,
                "level": entry.level,
            }
            for entry in rendered.toc
        ],
        "citations": [
            {
                "id": citation.id,
                "title": text_line(citation.text, 500)[0] or citation.url,
                "url": citation.url,
            }
            for citation in rendered.citations
            if citation.url is not None and slug_or_none(citation.id, 64) is not None
        ],
        "corrections": [],
    }
    source_url = _https(item_str(item, "sourceUrl") or meta.get("source_url"), conversion)
    if source_url is not None:
        doc["sourceUrl"] = source_url
    if source_type is not None:
        token = source_type.lower()
        if len(token) <= 40 and _TOKEN_RE.fullmatch(token):
            doc["sourceType"] = token
        else:
            conversion.note("source_type_dropped")
    try:
        errors = validate_published_doc(doc, now=ctx.now)
    except ContractUnavailableError:
        return conversion.fail("contract_unavailable")
    for error in errors:
        conversion.fail(error.code, error.path)
    if not errors:
        conversion.docs.append(PendingDoc("published-blogs", doc))
    return conversion


# --------------------------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------------------------


def draft_status(item: Mapping[str, Any], published_slugs: frozenset[str]) -> ArticleStatus:
    """``archived`` for drafts that went live in v1 (the migrated published-blogs document is
    the live copy) and for feed-generated drafts that never did; ``in_review`` for the owner's
    own drafts (editable in v2)."""
    went_live = (
        item_str(item, "publishedSlug") is not None
        or item_str(item, "publishedAt") is not None
        or item_str(item, "slug") in published_slugs
    )
    if went_live or item_str(item, "origin") == "rss_crawl":
        return ArticleStatus.ARCHIVED
    return ArticleStatus.IN_REVIEW


def convert_draft(
    item: Mapping[str, Any], images: Mapping[str, V1Image | None], ctx: ConvertContext
) -> Conversion:
    """A v1 draft as a v2 article (version 1, with its ``article-versions`` copy)."""
    conversion = Conversion("drafts", ref_for("drafts", item.get("id")))
    v1_id = item_str(item, "id")
    if v1_id is None:
        return conversion.fail("id_missing", "/id")
    article_id = derived_id("art", "v1", v1_id)
    content: object = item.get("content")
    markdown = content if isinstance(content, str) else ""
    meta, body = split_v1_frontmatter(markdown)
    raw_title = item_str(item, "title") or meta.get("title")
    title, adjusted = plain_line(raw_title, 150)
    if adjusted:
        conversion.note("title_adjusted")
    slug = slug_or_none(item.get("slug")) or (tag_slug(title, 80) if title else None)
    excerpt, adjusted = plain_line(item_str(item, "excerpt") or meta.get("excerpt"), 400)
    if adjusted:
        conversion.note("excerpt_adjusted")
    tags = _tags(item, meta)
    created = first_time(item, "createdAt", "updatedAt")
    updated = max(created, first_time(item, "updatedAt", "createdAt"))
    created_at, updated_at = to_iso(created), to_iso(updated)

    media: list[dict[str, object]] = []

    def media_asset(image: V1Image, reference: Image) -> str:
        data, content_type, ext = image.data, image.content_type, image.ext
        if image.ext == "gif":
            data, content_type, ext = png_of(image), "image/png", "png"
        digest = hashlib.sha256(data).hexdigest()
        name = f"articles/{article_id}/{digest}.{ext}"
        if all(ref["name"] != name for ref in media):
            conversion.blobs.append(PendingBlob("media", name, data, content_type))
            alt, _ = text_line(reference.alt, 1000)
            media.append(
                {
                    "role": "image",
                    "container": "media",
                    "name": name,
                    "sha256": digest,
                    "contentType": content_type,
                    "sizeBytes": len(data),
                    "width": image.width,
                    "height": image.height,
                    "altText": alt,
                    "uploadedAt": updated_at,
                }
            )
        return f"/api/media/{name}"

    body = clean_v1_markdown(body, title=raw_title)
    body = _normalized(
        rewrite_images(body, _image_resolver(conversion, images, media_asset)), conversion
    )
    if len(media) > 50:
        conversion.note("media_list_capped")
    pillar, post_format = _pillar_and_format(
        ctx, v1_id, [title or "", *tags, body[:_GUESS_CHARS]], item_str(item, "sourceType")
    )
    status = draft_status(item, ctx.published_slugs)
    meta_fields: dict[str, object] = {
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "pillar": pillar,
        "format": post_format,
        "tags": tags,
    }
    try:
        content_sha256 = article_sha256(
            ArticleMeta.model_validate(meta_fields).model_dump(mode="json", by_alias=True), body
        )
    except ValidationError:
        return conversion.fail("invalid_meta", "/meta")
    words, minutes = word_stats(body)
    marker = _marker("drafts", v1_id, updated_at)
    article = _validated(
        conversion,
        Article,
        {
            "id": article_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "status": status,
            "status_changed_at": updated_at,
            "updated_by": "system",
            "meta": meta_fields,
            "markdown": body,
            "version": 1,
            "content_sha256": content_sha256,
            "word_count": words,
            "reading_time_minutes": minutes,
            "media": media[:50],
            MIGRATED_FROM: marker,
        },
    )
    version = _validated(
        conversion,
        ArticleVersion,
        {
            "id": ArticleVersion.id_for(article_id, 1),
            "created_at": updated_at,
            "updated_at": updated_at,
            "article_id": article_id,
            "version": 1,
            "meta": meta_fields,
            "markdown": body,
            "content_sha256": content_sha256,
            "source": "restore",
            "author": "system",
            "status_at_save": status,
            "note": _PUBLISHED_NOTE,
            MIGRATED_FROM: marker,
        },
    )
    if article is not None and version is not None:
        _add(conversion, article)
        _add(conversion, version)
    return conversion


# --------------------------------------------------------------------------------------------
# feed-sources and crawled-articles
# --------------------------------------------------------------------------------------------


def convert_feed_source(item: Mapping[str, Any], ctx: ConvertContext) -> Conversion:
    """A v1 feed as a v2 ``source`` (type ``feed``). Auto-publish flags are dropped."""
    conversion = Conversion("feed-sources", ref_for("feed-sources", item.get("id")))
    v1_id = item_str(item, "id")
    if v1_id is None:
        return conversion.fail("id_missing", "/id")
    name = item_str(item, "name")
    url = _https(item_str(item, "feedUrl"), conversion)
    site = _https(item_str(item, "baseUrl"), conversion)
    status = SourceStatus.PAUSED if item.get("enabled") is False else SourceStatus.ACTIVE
    last_error: str | None = None
    if url is None:
        if site is None:
            return conversion.fail("feed_url_missing", "/feedUrl")
        url, status, last_error = site, SourceStatus.PAUSED, "v1_no_feed_url"
        conversion.note("no_feed_url")
    title, _ = plain_line(name or url, 200)
    created = first_time(item, "createdAt", "updatedAt")
    updated = max(created, first_time(item, "updatedAt", "createdAt"))
    updated_at = to_iso(updated)
    last_crawled = parse_v1_time(item.get("lastCrawledAt"))
    source = _validated(
        conversion,
        Source,
        {
            "id": derived_id("feed", "v1", v1_id),
            "created_at": to_iso(created),
            "updated_at": updated_at,
            "type": "feed",
            "title": title or "Feed",
            "url": url,
            "site_url": site,
            "pillar": guess_pillar([*item_strings(item, "topics"), name or ""], ctx.pillars),
            "status": status,
            "status_changed_at": updated_at,
            "last_crawled_at": to_iso(last_crawled) if last_crawled is not None else None,
            "last_error": last_error,
            MIGRATED_FROM: _marker("feed-sources", v1_id, updated_at),
        },
    )
    if source is not None:
        _add(conversion, source)
    return conversion


def convert_crawled_article(item: Mapping[str, Any], ctx: ConvertContext) -> Conversion:
    """A v1 crawled article as a ``signal``: the URL hash only (no title, no URL text)."""
    conversion = Conversion("crawled-articles", ref_for("crawled-articles", item.get("id")))
    v1_id = item_str(item, "id")
    feed = item_str(item, "feedSourceId")
    if v1_id is None:
        return conversion.fail("id_missing", "/id")
    if feed is None or feed not in ctx.feed_ids:
        conversion.skip = "orphan"
        return conversion
    url = item_str(item, "articleUrl") or item_str(item, "url")
    if url is None:
        conversion.skip = "no_url"
        return conversion
    try:
        digest = url_sha256(url)
    except ValueError:
        conversion.skip = "invalid_url"
        return conversion
    seen = first_time(item, "crawledAt")
    if ctx.now - seen > SIGNAL_WINDOW:
        conversion.skip = "stale"
        return conversion
    seen_at = to_iso(seen)
    source_id = derived_id("feed", "v1", feed)
    signal = _validated(
        conversion,
        Signal,
        {
            "id": Signal.id_for(source_id, digest),
            "created_at": seen_at,
            "updated_at": seen_at,
            "source_id": source_id,
            "url_sha256": digest,
            "seen_at": seen_at,
            "triaged": True,
            MIGRATED_FROM: _marker("crawled-articles", v1_id, seen_at),
        },
    )
    if signal is not None:
        _add(conversion, signal)
    return conversion


# --------------------------------------------------------------------------------------------
# keywords and voice-profiles (proposals for the owner)
# --------------------------------------------------------------------------------------------


def _block(value: str | None, limit: int, conversion: Conversion, note: str) -> str:
    if value is None:
        return ""
    if len(value) > limit:
        conversion.note(note)
        return value[:limit].rstrip()
    return value


def convert_keywords(item: Mapping[str, Any], ctx: ConvertContext) -> Conversion:
    """A v1 keyword list as a proposed ``kb_seed`` (pending owner approval)."""
    conversion = Conversion("keywords", ref_for("keywords", item.get("id")))
    v1_id = item_str(item, "id")
    if v1_id is None:
        return conversion.fail("id_missing", "/id")
    topic, _ = text_line(item_str(item, "topic") or v1_id, 200)
    keywords: list[str] = []
    for word in item_strings(item, "keywords"):
        cleaned, _ = text_line(word, 80)
        if cleaned is not None and cleaned not in keywords:
            keywords.append(cleaned)
    if len(keywords) > 200:
        conversion.note("keywords_capped")
    stamp = to_iso(first_time(item, "updatedAt", "createdAt"))
    seed = _validated(
        conversion,
        KbSeed,
        {
            "id": KbSeed.id_for(v1_id),
            "created_at": stamp,
            "updated_at": stamp,
            "status_changed_at": stamp,
            "topic": topic or v1_id[:200],
            "keywords": keywords[:200],
            "suggested_pillar": guess_pillar([topic or "", *keywords], ctx.pillars),
            MIGRATED_FROM: _marker("keywords", v1_id, stamp),
        },
    )
    if seed is not None:
        _add(conversion, seed)
    return conversion


def convert_voice_profile(item: Mapping[str, Any], ctx: ConvertContext) -> Conversion:
    """A v1 voice profile as a proposed ``voice_seed`` (pending owner approval)."""
    del ctx
    conversion = Conversion("voice-profiles", ref_for("voice-profiles", item.get("id")))
    v1_id = item_str(item, "id")
    if v1_id is None:
        return conversion.fail("id_missing", "/id")
    name, _ = text_line(item_str(item, "name"), 120)
    tone, _ = text_line(item_str(item, "tone"), 200)
    created = first_time(item, "createdAt", "updatedAt")
    updated = max(created, first_time(item, "updatedAt", "createdAt"))
    updated_at = to_iso(updated)
    seed = _validated(
        conversion,
        VoiceSeed,
        {
            "id": VoiceSeed.id_for(v1_id),
            "created_at": to_iso(created),
            "updated_at": updated_at,
            "status_changed_at": updated_at,
            "name": name or "Voice profile",
            "description": _block(item_str(item, "description"), 2000, conversion, "truncated"),
            "tone": tone or "",
            "style_notes": _block(item_str(item, "styleNotes"), 5000, conversion, "truncated"),
            "text": _block(item_str(item, "sampleText"), 20_000, conversion, "truncated"),
            "is_default": item_bool(item, "isDefault"),
            MIGRATED_FROM: _marker("voice-profiles", v1_id, updated_at),
        },
    )
    if seed is not None:
        _add(conversion, seed)
    return conversion
