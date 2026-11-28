"""Published assets: diagrams, the Open Graph PNG, the hero image and copied article images.

Everything goes to the private Blob container ``published`` under ``<slug>/`` with
content-hashed names (the SHA-256 of the bytes, so a name never points at changed bytes and a
re-render of the same content writes nothing new):

==================  ==================================================  =================
asset               source                                              name
==================  ==================================================  =================
Mermaid diagram     ``services.render.render_mermaid_svg`` per source   ``diagram-<h>.svg``
Open Graph image    ``services.render.render_og_image`` (1200x630 PNG)  ``og-<h>.png``
hero                the owner's ``hero_image`` media, else the OG PNG   ``hero-<h>.<ext>``
article image       an ``/api/media/<name>`` image copied from ``media`` ``img-<h>.<ext>``
==================  ==================================================  =================

The HTML references them as ``/blog-media/<slug>/<file>`` (the portfolio copies every listed
blob into ``public/blog-media/`` and checks its hash). :class:`PreparedAssets` is the ``render``
step's output: references, sizes and hashes only (never bytes or HTML), from which the
``upsert`` step renders the same HTML again (:func:`render_published`).
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, cast

from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue

from blogwriter.domain.articles import Article, PublishedAsset
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import BlobContainer, MediaRef
from blogwriter.domain.ports import BlobExists, BlobNotFound, BlobStore, RenderService
from blogwriter.domain.settings import BrandKit, PillarSettings
from blogwriter.publish.document import (
    OG_HEIGHT,
    OG_WIDTH,
    image_sources,
    plain_line,
    published_path,
    swap_image_sources,
)
from blogwriter.publish.renderer import RenderedArticle, diagram_id, render_article

__all__ = [
    "MAX_ASSET_BYTES",
    "AssetError",
    "AssetImage",
    "PreparedAssets",
    "hero_alt",
    "media_blob_name",
    "prepare_assets",
    "render_published",
]

MAX_ASSET_BYTES: Final = 10 * 1024 * 1024
"""A copied image or a render larger than this is refused (the portfolio serves it as is)."""
MAX_SIDE: Final = 16_384
"""The contract's largest image dimension."""
MAX_DIAGRAMS: Final = 50
MAX_IMAGES: Final = 100
OG_TITLE_CHARS: Final = 110
OG_SUBTITLE_CHARS: Final = 140
OG_PILLAR_CHARS: Final = 40
PUBLISHED: Final = "published"

_MEDIA_PREFIX: Final = "/api/media/"
_MEDIA_NAME_RE: Final = re.compile(r"(?:uploads|posts|articles|brand)/[a-z0-9][a-z0-9/._-]*")
_EXTENSIONS: Final[Mapping[str, str]] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}
_RASTER: Final = frozenset({"image/png", "image/jpeg", "image/webp"})
_SVG_ROOT_RE: Final = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE)
_SVG_SIZE_RE: Final = re.compile(rb'\b(width|height)="([0-9]+(?:\.[0-9]+)?)(?:px)?"')


class AssetError(Exception):
    """An asset cannot be published (permanent). ``code``: ``media_missing``,
    ``media_type``, ``media_too_large``, ``media_integrity``, ``asset_integrity``,
    ``og_invalid``, ``assets_stale``, ``too_many_assets``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetImage:
    """A published image: its ``/blog-media/<slug>/<file>`` path and size (``0`` = unknown)."""

    url: str
    width: int = 0
    height: int = 0

    @property
    def size(self) -> tuple[int, int] | None:
        return (self.width, self.height) if self.width > 0 and self.height > 0 else None

    def to_json(self) -> dict[str, JsonValue]:
        return {"url": self.url, "width": self.width, "height": self.height}

    @classmethod
    def from_json(cls, value: JsonValue) -> AssetImage:
        if not isinstance(value, dict):
            raise ValueError("an asset image is a JSON object")
        url, width, height = value.get("url"), value.get("width"), value.get("height")
        if not isinstance(url, str) or not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("an asset image has url, width and height")
        return cls(url, width, height)


@dataclass(frozen=True, slots=True)
class PreparedAssets:
    """The ``render`` step's output (references only)."""

    slug: str
    content_sha256: str
    version: int
    assets: tuple[PublishedAsset, ...]
    og: AssetImage
    hero: AssetImage
    diagrams: Mapping[str, AssetImage] = field(default_factory=dict[str, AssetImage])
    """``diagram_id(source)`` -> the rendered SVG."""
    images: Mapping[str, AssetImage] = field(default_factory=dict[str, AssetImage])
    """The article's ``/api/media/...`` image ``src`` -> its published copy."""

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "slug": self.slug,
            "contentSha256": self.content_sha256,
            "version": self.version,
            "assets": [cast(JsonValue, a.model_dump(mode="json")) for a in self.assets],
            "og": self.og.to_json(),
            "hero": self.hero.to_json(),
            "diagrams": {key: image.to_json() for key, image in self.diagrams.items()},
            "images": {key: image.to_json() for key, image in self.images.items()},
        }

    @classmethod
    def from_json(cls, value: JsonValue) -> PreparedAssets:
        if not isinstance(value, dict):
            raise ValueError("prepared assets are a JSON object")
        slug, sha, version = value.get("slug"), value.get("contentSha256"), value.get("version")
        raw_assets, diagrams, images = (
            value.get("assets"),
            value.get("diagrams"),
            value.get("images"),
        )
        if not isinstance(slug, str) or not isinstance(sha, str) or not isinstance(version, int):
            raise ValueError("prepared assets need slug, contentSha256 and version")
        if not isinstance(raw_assets, list) or not isinstance(diagrams, dict):
            raise ValueError("prepared assets need assets and diagrams")
        if not isinstance(images, dict):
            raise ValueError("prepared assets need images")
        return cls(
            slug=slug,
            content_sha256=sha,
            version=version,
            assets=tuple(PublishedAsset.model_validate(item) for item in raw_assets),
            og=AssetImage.from_json(value.get("og")),
            hero=AssetImage.from_json(value.get("hero")),
            diagrams={key: AssetImage.from_json(item) for key, item in diagrams.items()},
            images={key: AssetImage.from_json(item) for key, item in images.items()},
        )


# --------------------------------------------------------------------------------------------
# Helpers (sync helpers run in worker threads)
# --------------------------------------------------------------------------------------------


def media_blob_name(src: str) -> str | None:
    """The Blob name in container ``media`` behind a same-origin ``/api/media/<name>`` image."""
    if not src.startswith(_MEDIA_PREFIX):
        return None
    name = src[len(_MEDIA_PREFIX) :]
    if not _MEDIA_NAME_RE.fullmatch(name) or ".." in name.split("/"):
        return None
    return name


def _raster_size(data: bytes) -> tuple[str, int, int]:
    """``(content type, width, height)`` read from the image header (no full decode)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = (image.format or "").upper()
            width, height = image.size
    except Image.DecompressionBombError:
        raise AssetError("media_too_large") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise AssetError("media_type") from None
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise AssetError("media_too_large")
    content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(kind)
    if content_type is None:
        raise AssetError("media_type")
    return content_type, width, height


def _svg_size(data: bytes) -> tuple[int, int]:
    root = _SVG_ROOT_RE.search(data[:4096])
    if root is None:
        return 0, 0
    sizes = {name.decode(): float(value) for name, value in _SVG_SIZE_RE.findall(root.group(0))}
    width, height = round(sizes.get("width", 0)), round(sizes.get("height", 0))
    if not (0 < width <= 16_384 and 0 < height <= 16_384):
        return 0, 0
    return width, height


async def _put(
    blobs: BlobStore, slug: str, prefix: str, data: bytes, content_type: str
) -> tuple[PublishedAsset, str]:
    """Upload ``data`` as ``<slug>/<prefix>-<sha16>.<ext>`` (content-addressed; an existing
    blob must hold the same bytes). Returns the asset and the file name."""
    if len(data) > MAX_ASSET_BYTES:
        raise AssetError("media_too_large")
    digest = sha256_hex(data)
    file = f"{prefix}-{digest[:16]}.{_EXTENSIONS[content_type]}"
    name = f"{slug}/{file}"
    try:
        await blobs.put(PUBLISHED, name, data, content_type=content_type)
    except BlobExists:
        info = await blobs.head(PUBLISHED, name)
        if info is None or info.sha256 != digest:
            raise AssetError("asset_integrity") from None
    asset = PublishedAsset(path=published_path(slug, file), blob=name, sha256=digest)
    return asset, file


async def _read_media(blobs: BlobStore, container: BlobContainer, name: str) -> bytes:
    try:
        return await blobs.get(container, name)
    except BlobNotFound:
        raise AssetError("media_missing") from None


def _owner_hero(media: Sequence[MediaRef]) -> MediaRef | None:
    """The owner's hero image: a ``hero_image`` media that is not a copy of the draft OG PNG
    (the pipeline stores the OG PNG as the default hero), raster only."""
    og_hashes = {item.sha256 for item in media if item.role == "og_image"}
    for item in media:
        if (
            item.role == "hero_image"
            and item.sha256 not in og_hashes
            and item.content_type in _RASTER
            and item.container == "media"
        ):
            return item
    return None


# --------------------------------------------------------------------------------------------
# The render step's work
# --------------------------------------------------------------------------------------------


async def prepare_assets(
    article: Article,
    *,
    first_pass: RenderedArticle,
    blobs: BlobStore,
    render: RenderService,
    brand: BrandKit,
    pillars: PillarSettings,
) -> PreparedAssets:
    """Render and upload every asset of ``article`` (whose first render pass, without asset
    URLs, is ``first_pass``). Raises :class:`AssetError` and lets ``RenderFailed`` through."""
    meta = article.meta
    slug, title = meta.slug, meta.title
    if slug is None or title is None:
        raise ValueError("the article's frontmatter is incomplete")
    assets: dict[str, PublishedAsset] = {}

    def keep(asset: PublishedAsset) -> None:
        assets.setdefault(asset.path, asset)

    diagrams: dict[str, AssetImage] = {}
    sources = list(dict.fromkeys(first_pass.mermaid_sources))
    if len(sources) > MAX_DIAGRAMS:
        raise AssetError("too_many_assets")
    for source in sources:
        svg = await render.render_mermaid_svg(source, brand)
        asset, _ = await _put(blobs, slug, "diagram", svg, "image/svg+xml")
        keep(asset)
        width, height = _svg_size(svg)
        diagrams[diagram_id(source)] = AssetImage(asset.path, width, height)

    pillar = pillars.get(meta.pillar) if meta.pillar is not None else None
    og_png = await render.render_og_image(
        title=plain_line(title, OG_TITLE_CHARS),
        subtitle=plain_line(meta.excerpt, OG_SUBTITLE_CHARS) if meta.excerpt else None,
        pillar_label=plain_line(pillar.name, OG_PILLAR_CHARS) if pillar is not None else None,
        brand=brand,
    )
    content_type, width, height = await asyncio.to_thread(_raster_size, og_png)
    if content_type != "image/png" or (width, height) != (OG_WIDTH, OG_HEIGHT):
        raise AssetError("og_invalid")
    og_asset, _ = await _put(blobs, slug, "og", og_png, "image/png")
    keep(og_asset)
    og = AssetImage(og_asset.path, OG_WIDTH, OG_HEIGHT)

    hero = og
    owner_hero = _owner_hero(article.media)
    if owner_hero is not None:
        data = await _read_media(blobs, owner_hero.container, owner_hero.name)
        if sha256_hex(data) != owner_hero.sha256:
            raise AssetError("media_integrity")
        hero_type, hero_width, hero_height = await asyncio.to_thread(_raster_size, data)
        hero_asset, _ = await _put(blobs, slug, "hero", data, hero_type)
        keep(hero_asset)
        hero = AssetImage(hero_asset.path, hero_width, hero_height)

    images: dict[str, AssetImage] = {}
    media_sources = list(dict.fromkeys(await asyncio.to_thread(image_sources, first_pass.html)))
    copied = [(src, name) for src in media_sources if (name := media_blob_name(src)) is not None]
    if len(copied) > MAX_IMAGES:
        raise AssetError("too_many_assets")
    for src, name in copied:
        info = await blobs.head("media", name)
        if info is None:
            raise AssetError("media_missing")
        if info.content_type not in _EXTENSIONS:
            raise AssetError("media_type")
        if info.size_bytes > MAX_ASSET_BYTES:
            raise AssetError("media_too_large")
        data = await _read_media(blobs, "media", name)
        if info.content_type == "image/svg+xml":
            kind, (img_width, img_height) = "image/svg+xml", _svg_size(data)
        else:
            kind, img_width, img_height = await asyncio.to_thread(_raster_size, data)
        asset, _ = await _put(blobs, slug, "img", data, kind)
        keep(asset)
        images[src] = AssetImage(asset.path, img_width, img_height)

    if len(assets) > 200:
        raise AssetError("too_many_assets")
    return PreparedAssets(
        slug=slug,
        content_sha256=article.compute_sha256(),
        version=article.version,
        assets=tuple(assets.values()),
        og=og,
        hero=hero,
        diagrams=diagrams,
        images=images,
    )


def hero_alt(article: Article, prepared: PreparedAssets) -> str:
    """The hero's alt text: the owner's alt text for their own hero image, else the title (the
    default hero is the typographic OG card, which shows the title). Not stored in the job."""
    title = article.meta.title or ""
    owner = _owner_hero(article.media)
    if owner is not None and prepared.hero.url != prepared.og.url:
        return plain_line(owner.alt_text or title, 300) or title
    return title


def render_published(document: str, prepared: PreparedAssets) -> RenderedArticle:
    """The second render pass (sync, CPU-bound: run it in a thread): diagrams as images at
    their published paths, copied images swapped to theirs, sizes from the manifest.
    :class:`AssetError` ``assets_stale`` when the document has a diagram the assets lack."""
    sizes: dict[str, tuple[int, int]] = {}
    for src, image in prepared.images.items():
        if image.size is not None:
            sizes[src] = image.size
    for image in prepared.diagrams.values():
        if image.size is not None:
            sizes[image.url] = image.size

    def diagram_url(source: str) -> str:
        found = prepared.diagrams.get(diagram_id(source))
        if found is None:
            raise AssetError("assets_stale")
        return found.url

    rendered = render_article(document, diagram_url=diagram_url, image_sizes=sizes)
    replacements = {src: image.url for src, image in prepared.images.items()}
    missing = [
        src
        for src in image_sources(rendered.html)
        if media_blob_name(src) is not None and src not in replacements
    ]
    if missing:
        raise AssetError("assets_stale")
    return replace(rendered, html=swap_image_sources(rendered.html, replacements))
