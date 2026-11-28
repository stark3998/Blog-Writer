"""The published-blogs v2 documents the publish job writes (``contracts/published-blogs.v2``).

- :func:`build_published_document`: a ``status: published`` document from an article, its
  rendered HTML and its assets.
- :func:`redirect_stub`: what a renamed slug's old id becomes (``status: redirect``).
- :func:`tombstone`: what an unpublished slug becomes (``status: unpublished``; the slug stays
  reserved and nothing stale stays readable).

Every document is validated with ``contract.validate_published_doc`` before it is written
(:mod:`blogwriter.publish.published`); these builders only shape it. Timestamps use the
contract's second-precision format ``YYYY-MM-DDTHH:MM:SSZ`` (:func:`contract_time`), not the
domain's microsecond format.

Also here: the two HTML helpers of the second render pass. Images the article references from
the app's media store (``/api/media/<name>``) are copied to ``published/<slug>/`` and their
``src`` swapped for the same-origin ``/blog-media/<slug>/<file>`` path in the **sanitized** HTML
(nh3 writes every attribute double-quoted, so a tag-level rewrite is exact). Nothing else in the
HTML is touched.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from blogwriter.domain.articles import Article, Correction, PublishedAsset
from blogwriter.domain.base import parse_iso
from blogwriter.domain.common import Citation as SourceCitation
from blogwriter.publish.contract import MEDIA_PREFIX, SCHEMA_VERSION
from blogwriter.publish.renderer import RenderedArticle

__all__ = [
    "MAX_CITATION_TITLE",
    "MAX_DISCLAIMER",
    "MAX_TOC_TEXT",
    "PublishedImage",
    "build_published_document",
    "contract_time",
    "disclaimer_html",
    "image_sources",
    "is_og_asset",
    "plain_line",
    "published_path",
    "redirect_stub",
    "swap_image_sources",
    "tombstone",
]

MAX_TOC_TEXT: Final = 300
MAX_CITATION_TITLE: Final = 500
MAX_PUBLISHER: Final = 200
MAX_NOTE: Final = 1000
MAX_ALT: Final = 300
MAX_DISCLAIMER: Final = 300
"""``EmployerPolicy.disclaimer_long`` is at most this long."""
OG_WIDTH: Final = 1200
OG_HEIGHT: Final = 630

# Sanitized HTML (nh3/html5ever) double-quotes every attribute value.
_IMG_TAG_RE: Final = re.compile(r'<img((?:\s+[a-z][a-z0-9-]*(?:="[^"]*")?)*)\s*/?>')
_ATTRIBUTE_RE: Final = re.compile(r'\s+([a-z][a-z0-9-]*)(?:="([^"]*)")?')
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]")
_URL_RE: Final = re.compile(r"(?:https://[^\s\\\x00-\x1f\x7f]+|/(?![/\\])[^\s\\\x00-\x1f\x7f]*)")


@dataclass(frozen=True, slots=True)
class PublishedImage:
    """``heroImage``/``ogImage``: a site-relative path (one of ``assets[].path``) or an https
    URL, its pixel size and (hero only) its alt text."""

    url: str
    width: int
    height: int
    alt: str | None = None


def contract_time(moment: datetime | str) -> str:
    """``2026-11-30T14:00:05Z``: the contract's fixed-width UTC instant (seconds, floored).

    Accepts an aware datetime or a domain timestamp (``...T..:..:...ffffffZ``)."""
    value = parse_iso(moment) if isinstance(moment, str) else moment
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plain_line(text: str, limit: int) -> str:
    """One line of text for a contract text field: control characters and line breaks become
    spaces, whitespace runs collapse, the ends are trimmed, and it is cut to ``limit``
    characters (at a word boundary when one is near, with an ellipsis)."""
    flat = " ".join(_CONTROL_RE.sub(" ", text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def disclaimer_html(text: str | None) -> str:
    """The employer policy's article footer disclaimer as HTML inside the sanitizer profile: a
    rule, then the escaped text in an emphasized paragraph (``""`` without one). The publish job
    appends it to ``htmlContent``; the preview shows the same text below the body."""
    line = plain_line(text, MAX_DISCLAIMER) if text else ""
    if not line:
        return ""
    return f"<hr>\n<p><em>{html_lib.escape(line, quote=False)}</em></p>\n"


def published_path(slug: str, file: str) -> str:
    """``/blog-media/<slug>/<file>``: where the portfolio serves a published asset."""
    return f"{MEDIA_PREFIX}{slug}/{file}"


def is_og_asset(asset: PublishedAsset) -> bool:
    """Whether ``asset`` is the rendered Open Graph PNG (``og-<sha>.png``)."""
    file = asset.path.rsplit("/", 1)[-1]
    return file.startswith("og-") and file.endswith(".png")


def _attributes(raw: str) -> list[tuple[str, str | None]]:
    return [(match.group(1), match.group(2)) for match in _ATTRIBUTE_RE.finditer(raw)]


def image_sources(html: str) -> list[str]:
    """The ``src`` of every ``<img>`` in sanitized HTML, unescaped, in order (duplicates kept)."""
    sources: list[str] = []
    for match in _IMG_TAG_RE.finditer(html):
        for name, value in _attributes(match.group(1)):
            if name == "src" and value:
                sources.append(html_lib.unescape(value))
    return sources


def swap_image_sources(html: str, replacements: Mapping[str, str]) -> str:
    """Sanitized HTML with every ``<img src>`` found in ``replacements`` (unescaped original ->
    new URL) rewritten; other tags and attributes are kept as written."""
    if not replacements:
        return html

    def swap(match: re.Match[str]) -> str:
        changed = False
        parts: list[str] = []
        for name, value in _attributes(match.group(1)):
            if name == "src" and value is not None:
                target = replacements.get(html_lib.unescape(value))
                if target is not None:
                    value = html_lib.escape(target, quote=True)
                    changed = True
            parts.append(f' {name}="{value}"' if value is not None else f" {name}")
        return "<img" + "".join(parts) + ">" if changed else match.group(0)

    return _IMG_TAG_RE.sub(swap, html)


def _citations(
    rendered: RenderedArticle, sources: Mapping[str, SourceCitation]
) -> list[dict[str, object]]:
    """``citations[]`` in footnote order: the brief's metadata when the id is one of its
    sources, else the definition's text and first allowed link. A citation without an https
    or site-relative URL is left out (the footnote itself stays in ``htmlContent``)."""
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for citation in sorted(rendered.citations, key=lambda item: item.number):
        if citation.id in seen:
            continue
        source = sources.get(citation.id)
        url = source.url if source is not None else citation.url
        if url is None or not _URL_RE.fullmatch(url) or len(url) > 2048:
            continue
        title = plain_line(
            source.title if source is not None else citation.text, MAX_CITATION_TITLE
        )
        if not title:
            continue
        entry: dict[str, object] = {"id": citation.id, "title": title, "url": url}
        if source is not None and source.publisher:
            publisher = plain_line(source.publisher, MAX_PUBLISHER)
            if publisher:
                entry["publisher"] = publisher
        if source is not None and source.date:
            entry["date"] = source.date
        out.append(entry)
        seen.add(citation.id)
    return out


def _corrections(corrections: Sequence[Correction]) -> list[dict[str, object]]:
    ordered = sorted(corrections, key=lambda item: (item.date, item.created_at))
    out: list[dict[str, object]] = []
    for item in ordered:
        note = plain_line(item.note, MAX_NOTE)
        if note:
            out.append({"date": item.date, "note": note})
    return out


def build_published_document(
    article: Article,
    rendered: RenderedArticle,
    assets: Sequence[PublishedAsset],
    *,
    now: datetime,
    site_origin: str,
    hero: PublishedImage | None = None,
    sources: Mapping[str, SourceCitation] | None = None,
    markdown: str | None = None,
    corrections: Sequence[Correction] | None = None,
    disclaimer: str | None = None,
) -> dict[str, object]:
    """A ``status: published`` document for ``article`` (validate it before the upsert).

    ``rendered`` is the second render pass (diagram and image URLs already pointing at
    ``/blog-media/<slug>/``). ``assets`` must contain the Open Graph PNG (``og-<sha>.png``,
    1200x630); ``hero`` defaults to it with the title as alt text (the plan's typographic
    card). ``sources`` maps citation ids to the brief's citations (titles, publishers, dates).
    ``markdown`` becomes ``mdxContent`` (the composed canonical document). ``corrections``
    defaults to the article's. ``publishedAt`` is the first publication (``now`` the first
    time) and ``updatedAt`` is ``now``; ``canonicalUrl`` is ``<site_origin>/blog/<slug>``.
    ``disclaimer`` (the employer policy's article footer) ends ``htmlContent``
    (:func:`disclaimer_html`), so the live page carries what the preview shows.
    """
    meta = article.meta
    slug = meta.slug
    if slug is None or meta.title is None or meta.excerpt is None:
        raise ValueError("the article's frontmatter is incomplete")
    if meta.pillar is None or meta.format is None:
        raise ValueError("the article's frontmatter is incomplete")
    og_asset = next((asset for asset in assets if is_og_asset(asset)), None)
    if og_asset is None:
        raise ValueError("the Open Graph PNG is not among the assets")
    first = article.publish.published_at
    published_at = contract_time(first) if first is not None else contract_time(now)
    updated_at = max(contract_time(now), published_at)
    image = hero or PublishedImage(og_asset.path, OG_WIDTH, OG_HEIGHT, meta.title)
    doc: dict[str, object] = {
        "id": slug,
        "slug": slug,
        "schemaVersion": SCHEMA_VERSION,
        "status": "published",
        "title": meta.title,
        "excerpt": meta.excerpt,
        "htmlContent": rendered.html + disclaimer_html(disclaimer),
    }
    if markdown is not None:
        doc["mdxContent"] = markdown
    doc.update(
        {
            "publishedAt": published_at,
            "updatedAt": updated_at,
            "date": published_at[:10],
            "tags": list(meta.tags),
            "pillar": meta.pillar,
            "format": meta.format,
        }
    )
    if meta.series is not None:
        series: dict[str, object] = {
            "id": meta.series.id,
            "title": meta.series.title,
            "part": meta.series.part,
        }
        if meta.series.total is not None:
            series["total"] = meta.series.total
        doc["series"] = series
    doc["heroImage"] = {
        "url": image.url,
        "alt": plain_line(image.alt or meta.title, MAX_ALT),
        "width": image.width,
        "height": image.height,
    }
    doc["ogImage"] = {"url": og_asset.path, "width": OG_WIDTH, "height": OG_HEIGHT}
    doc["assets"] = [
        {"path": asset.path, "blob": asset.blob, "sha256": asset.sha256} for asset in assets
    ]
    doc["readingTimeMinutes"] = rendered.reading_time_minutes
    doc["wordCount"] = rendered.word_count
    doc["toc"] = [
        {
            "id": entry.id,
            "text": plain_line(entry.text, MAX_TOC_TEXT) or entry.id,
            "level": entry.level,
        }
        for entry in rendered.toc
    ]
    doc["citations"] = _citations(rendered, sources or {})
    doc["corrections"] = _corrections(article.corrections if corrections is None else corrections)
    doc["canonicalUrl"] = f"{site_origin.rstrip('/')}/blog/{slug}"
    return doc


def redirect_stub(old_slug: str, new_slug: str, *, now: datetime) -> dict[str, object]:
    """The stub a renamed slug's old id becomes: ``status: redirect`` to ``new_slug``. Stubs
    carry no content (contract README, invariant 2) and are kept one hop deep by the caller."""
    if old_slug == new_slug:
        raise ValueError("a redirect must name another slug")
    return {
        "id": old_slug,
        "slug": old_slug,
        "schemaVersion": SCHEMA_VERSION,
        "status": "redirect",
        "redirectTo": new_slug,
        "updatedAt": contract_time(now),
    }


def tombstone(slug: str, *, now: datetime) -> dict[str, object]:
    """An unpublished slug: ``status: unpublished``, no content, the slug stays reserved."""
    return {
        "id": slug,
        "slug": slug,
        "schemaVersion": SCHEMA_VERSION,
        "status": "unpublished",
        "updatedAt": contract_time(now),
    }
