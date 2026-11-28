"""The portfolio-like preview page (``GET /api/articles/{id}/preview``).

The body goes through the publish renderer (``blogwriter.publish.renderer.render_article``:
markdown-it with ``html: False`` plus the shared nh3 sanitizer profile), the same HTML the
published page carries. The page is served with ``domain.api.PREVIEW_HEADERS`` into a sandboxed
iframe whose CSP allows only ``img-src data:``, so after sanitizing:

- images stored in the app (``/api/media/<name>``, Blob container ``media``) are inlined as
  ``data:`` URIs; anything else (remote URLs, missing blobs, images over 2 MB, past the page
  budget) shows its alt text instead;
- Mermaid diagrams are rendered by ``services.render`` (``render_mermaid_svg``) and inlined as
  SVG ``data:`` URIs; without the render service, or when a render fails, the figure keeps the
  escaped source the renderer wrote.

Only sanitized HTML is touched: ``src`` values are swapped for ``data:`` URIs built from our own
bytes (base64), and an image that is not inlined becomes its escaped alt text. Rendering,
encoding and templating run in a worker thread; nothing is stored (no GET changes state).
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final, cast

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup
from pygments.formatters import HtmlFormatter

from blogwriter.core.logging import register_log_fields
from blogwriter.domain.articles import ArticleMeta
from blogwriter.domain.common import MediaRef
from blogwriter.domain.ports import BlobNotFound, BlobStore, RenderFailed, RenderService
from blogwriter.publish.dialect import MAX_SOURCE_BYTES, SourceTooLargeError
from blogwriter.publish.frontmatter import FrontmatterError
from blogwriter.publish.renderer import TocEntry, diagram_id, render_article

__all__ = [
    "DIAGRAM_TIMEOUT_SECONDS",
    "INLINE_CONTENT_TYPES",
    "MAX_INLINE_IMAGE_BYTES",
    "MAX_INLINE_TOTAL_BYTES",
    "MAX_PREVIEW_DIAGRAMS",
    "build_preview",
    "clear_diagram_cache",
    "media_name",
]

MAX_INLINE_IMAGE_BYTES: Final = 2 * 1024 * 1024
"""An image or diagram larger than this shows its alt text (contract W-A7)."""
MAX_INLINE_TOTAL_BYTES: Final = 16 * 1024 * 1024
"""Raw image bytes one preview page inlines at most; later images show their alt text."""
MAX_PREVIEW_DIAGRAMS: Final = 20
"""Distinct diagrams rendered per preview; later ones keep their source."""
DIAGRAM_TIMEOUT_SECONDS: Final = 60.0
INLINE_CONTENT_TYPES: Final = frozenset({"image/png", "image/jpeg", "image/webp", "image/svg+xml"})

_MEDIA_PREFIX: Final = "/api/media/"
_MEDIA_NAME_RE: Final = re.compile(r"(?:uploads|posts|articles|brand)/[a-z0-9][a-z0-9/._-]*")
_SVG: Final = "image/svg+xml"
# Sanitized HTML (nh3/html5ever) double-quotes every attribute value and escapes the quotes in it.
_IMG_TAG_RE: Final = re.compile(r'<img((?:\s+[a-z][a-z0-9-]*(?:="[^"]*")?)*)\s*/?>')
_ATTRIBUTE_RE: Final = re.compile(r'\s+([a-z][a-z0-9-]*)(?:="([^"]*)")?')
_FIGURE_RE: Final = re.compile(
    r'<figure class="mermaid" data-diagram="([0-9a-f]{64})">(.*?)</figure>', re.DOTALL
)
_ACC_TITLE_RE: Final = re.compile(r"^[ \t]*accTitle[ \t]*:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
_NOT_PLAIN_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029<]")

_logger: Final = logging.getLogger("blogwriter.articles.preview")
register_log_fields("article_id", "diagrams", "images")


# --------------------------------------------------------------------------------------------
# Diagram cache (an optimisation only: renders are deterministic and slow)
# --------------------------------------------------------------------------------------------


class _DiagramCache:
    """SVG renders by diagram id, least recently used out, bounded by count and bytes."""

    def __init__(self, *, max_entries: int = 64, max_bytes: int = 8 * 1024 * 1024) -> None:
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: str) -> bytes | None:
        data = self._items.get(key)
        if data is not None:
            self._items.move_to_end(key)
        return data

    def put(self, key: str, data: bytes) -> None:
        if key in self._items:
            return
        self._items[key] = data
        self._bytes += len(data)
        while self._items and (
            len(self._items) > self._max_entries or self._bytes > self._max_bytes
        ):
            _, dropped = self._items.popitem(last=False)
            self._bytes -= len(dropped)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


_DIAGRAMS: Final = _DiagramCache()


def clear_diagram_cache() -> None:
    """Forget cached diagram renders (tests)."""
    _DIAGRAMS.clear()


# --------------------------------------------------------------------------------------------
# Rendering (sync, in a worker thread)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rendered:
    html: str | None
    """Sanitized body HTML; ``None`` when the body cannot be rendered at all."""
    toc: tuple[TocEntry, ...] = ()
    word_count: int = 0
    reading_time_minutes: int = 0
    mermaid_sources: tuple[str, ...] = ()
    image_sources: tuple[str, ...] = ()


def _render(body: str) -> _Rendered:
    # A leading blank line keeps a body that opens with "---" from reading as frontmatter
    # (CommonMark skips it), the same reading as behind the published frontmatter.
    source = "\n" + body
    if len(source.encode("utf-8", errors="surrogatepass")) > MAX_SOURCE_BYTES:
        source = body
    try:
        rendered = render_article(source)
    except (FrontmatterError, SourceTooLargeError):
        return _Rendered(html=None)
    images = tuple(
        html.unescape(attributes.get("src", ""))
        for attributes in (_attributes(m.group(1)) for m in _IMG_TAG_RE.finditer(rendered.html))
    )
    return _Rendered(
        html=rendered.html,
        toc=rendered.toc,
        word_count=rendered.word_count,
        reading_time_minutes=rendered.reading_time_minutes,
        mermaid_sources=rendered.mermaid_sources,
        image_sources=tuple(src for src in images if src),
    )


def _attributes(raw: str) -> dict[str, str]:
    """Attribute name -> value as written (still HTML-escaped)."""
    return {match.group(1): match.group(2) or "" for match in _ATTRIBUTE_RE.finditer(raw)}


def _data_uri(content_type: str, data: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


def _missing_image(alt: str) -> str:
    text = html.escape(alt.strip() or "Image")
    return f'<span class="missing-image" title="Not shown in the preview">{text}</span>'


def _inline_images(body: str, images: Mapping[str, tuple[str, bytes]]) -> str:
    def swap(match: re.Match[str]) -> str:
        attributes = _attributes(match.group(1))
        image = images.get(html.unescape(attributes.get("src", "")))
        if image is None:
            return _missing_image(html.unescape(attributes.get("alt", "")))
        attributes["src"] = _data_uri(*image)
        return "<img" + "".join(f' {name}="{value}"' for name, value in attributes.items()) + ">"

    return _IMG_TAG_RE.sub(swap, body)


def diagram_alt(source: str) -> str:
    """The diagram's alt text as the publish renderer writes it: Mermaid's ``accTitle`` or
    ``Diagram``."""
    match = _ACC_TITLE_RE.search(source)
    if match is None:
        return "Diagram"
    title = _NOT_PLAIN_RE.sub(" ", match.group(1)).strip()
    return title[:300] if title else "Diagram"


def _inline_diagrams(body: str, diagrams: Mapping[str, tuple[str, bytes]]) -> str:
    def swap(match: re.Match[str]) -> str:
        found = diagrams.get(match.group(1))
        if found is None:
            return match.group(0)
        alt, svg = found
        return (
            f'<figure class="mermaid" data-diagram="{match.group(1)}">'
            f'<img src="{_data_uri(_SVG, svg)}" alt="{html.escape(alt)}"></figure>'
        )

    return _FIGURE_RE.sub(swap, body)


@cache
def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("blogwriter.articles", "templates"),
        autoescape=True,  # every template, every expression
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )


@cache
def _code_css() -> str:
    formatter: HtmlFormatter[str] = HtmlFormatter(style="default")
    style_defs = cast("Callable[[str], str]", formatter.get_style_defs)  # pyright: ignore[reportUnknownMemberType]
    return style_defs(".highlight")


@dataclass(frozen=True, slots=True)
class _Page:
    meta: ArticleMeta
    rendered: _Rendered
    images: Mapping[str, tuple[str, bytes]]
    diagrams: Mapping[str, tuple[str, bytes]]
    hero: tuple[str, bytes, str] | None
    """content type, bytes, alt text."""
    version: int | None
    live: bool
    disclaimer: str | None


def _banner(page: _Page) -> str:
    what = f"Preview of version {page.version}" if page.version else "Preview of the draft"
    if page.live:
        return f"{what}. The published page stays as it is until you publish again."
    return f"{what}. Not published."


def _byline(rendered: _Rendered) -> str:
    if not rendered.word_count:
        return "No text yet"
    return f"{rendered.reading_time_minutes} min read · {rendered.word_count:,} words"


def _assemble(page: _Page) -> str:
    rendered = page.rendered
    if rendered.html is None:
        body = Markup('<p class="preview-error">This text cannot be previewed.</p>')
    else:
        inlined = _inline_diagrams(_inline_images(rendered.html, page.images), page.diagrams)
        body = Markup(inlined)  # noqa: S704 - nh3-sanitized HTML; only data: URIs swapped in
    hero: Markup | None = None
    if page.hero is not None:
        content_type, data, alt = page.hero
        tag = f'<img src="{_data_uri(content_type, data)}" alt="{html.escape(alt)}">'
        hero = Markup(tag)  # noqa: S704 - base64 data URI and escaped alt text
    meta = page.meta
    eyebrow = " · ".join(part for part in (meta.pillar, meta.format) if part)
    template = _environment().get_template("preview.html.j2")
    return template.render(
        title=meta.title or "Untitled draft",
        excerpt=meta.excerpt,
        eyebrow=eyebrow,
        byline=_byline(rendered),
        banner=_banner(page),
        hero=hero,
        toc=rendered.toc,
        body=body,
        disclaimer=page.disclaimer,
        code_css=Markup(_code_css()),  # noqa: S704 - Pygments' own static stylesheet
    )


# --------------------------------------------------------------------------------------------
# Media and diagrams (async I/O)
# --------------------------------------------------------------------------------------------


def _valid_media_name(name: str) -> bool:
    return bool(_MEDIA_NAME_RE.fullmatch(name)) and ".." not in name.split("/")


def media_name(src: str) -> str | None:
    """The Blob name in container ``media`` behind a same-origin ``/api/media/<name>`` URL."""
    if not src.startswith(_MEDIA_PREFIX):
        return None
    name = src[len(_MEDIA_PREFIX) :]
    return name if _valid_media_name(name) else None


async def _fetch_media(blobs: BlobStore, name: str) -> tuple[str, bytes] | None:
    info = await blobs.head("media", name)
    if (
        info is None
        or info.size_bytes > MAX_INLINE_IMAGE_BYTES
        or info.content_type not in INLINE_CONTENT_TYPES
    ):
        return None
    try:
        data = await blobs.get("media", name)
    except BlobNotFound:
        return None
    if len(data) > MAX_INLINE_IMAGE_BYTES:
        return None
    return info.content_type, data


async def _load_images(
    sources: Sequence[str], blobs: BlobStore | None
) -> dict[str, tuple[str, bytes]]:
    loaded: dict[str, tuple[str, bytes]] = {}
    if blobs is None:
        return loaded
    budget = MAX_INLINE_TOTAL_BYTES
    for src in dict.fromkeys(sources):
        name = media_name(src)
        if name is None:
            continue
        found = await _fetch_media(blobs, name)
        if found is None or len(found[1]) > budget:
            continue
        budget -= len(found[1])
        loaded[src] = found
    return loaded


async def _render_diagrams(
    sources: Sequence[str], render: RenderService | None, *, article_id: str
) -> dict[str, tuple[str, bytes]]:
    rendered: dict[str, tuple[str, bytes]] = {}
    if render is None:
        return rendered
    for source in list(dict.fromkeys(sources))[:MAX_PREVIEW_DIAGRAMS]:
        key = diagram_id(source)
        svg = _DIAGRAMS.get(key)
        if svg is None:
            try:
                async with asyncio.timeout(DIAGRAM_TIMEOUT_SECONDS):
                    svg = await render.render_mermaid_svg(source)
            except (RenderFailed, TimeoutError):
                _logger.info("article_preview_diagram_failed", extra={"article_id": article_id})
                continue
            _DIAGRAMS.put(key, svg)
        if len(svg) <= MAX_INLINE_IMAGE_BYTES:
            rendered[key] = (diagram_alt(source), svg)
    return rendered


def _hero_media(media: Sequence[MediaRef]) -> MediaRef | None:
    for role in ("hero_image", "og_image"):
        for item in media:
            if item.role == role and item.container == "media":
                return item
    return None


async def build_preview(
    *,
    article_id: str,
    meta: ArticleMeta,
    body: str,
    media: Sequence[MediaRef],
    blobs: BlobStore | None,
    render: RenderService | None,
    version: int | None = None,
    live: bool = False,
    disclaimer: str | None = None,
) -> str:
    """The preview page (a complete HTML document) of ``meta`` and ``body``."""
    rendered = await asyncio.to_thread(_render, body)
    diagrams = await _render_diagrams(rendered.mermaid_sources, render, article_id=article_id)
    images = await _load_images(rendered.image_sources, blobs)
    hero: tuple[str, bytes, str] | None = None
    hero_ref = _hero_media(media)
    if hero_ref is not None and blobs is not None and _valid_media_name(hero_ref.name):
        found = await _fetch_media(blobs, hero_ref.name)
        if found is not None:
            hero = (found[0], found[1], hero_ref.alt_text or meta.title or "Hero image")
    page = _Page(
        meta=meta,
        rendered=rendered,
        images=images,
        diagrams=diagrams,
        hero=hero,
        version=version,
        live=live,
        disclaimer=disclaimer,
    )
    _logger.debug(
        "article_preview",
        extra={"article_id": article_id, "diagrams": len(diagrams), "images": len(images)},
    )
    return await asyncio.to_thread(_assemble, page)
