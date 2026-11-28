"""Carousel and image-card specs: validation against ``blogwriter.render``'s spec, the editorial
lint, and rendering through the ``RenderService`` port into content-addressed media.

A post stores its slides opaque (``carouselSpec``, a ``blogwriter.render.CarouselSpec`` as JSON).
An image card is a spec with exactly one slide, rendered with ``render_image_card``. The spec
title is the LinkedIn document title (``documentTitle``); the routes keep the two equal.

Rendering (``posts.render``, staging) goes through ``services.render`` only. Before anything is
stored the output passes the confidentiality gate on the exact bytes (plan: "LinkedIn Studio /
Lint"): PDF metadata is exactly Title/Author (no producer, creator or XMP), pages carry no link
annotations, and the PDF text layer, the alt texts, the document title and the download file
names are scanned against the blocklist (an unconfigured blocklist refuses everything).
Blobs are named ``posts/<postId>/<sha256>.<ext>`` and never overwritten (same name, same bytes).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import JsonValue, ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from blogwriter.domain.base import canonical_json, sha256_hex
from blogwriter.domain.common import Finding, MediaRef
from blogwriter.domain.confidentiality import blocklist_findings
from blogwriter.domain.ports import BlobExists, BlobStore, RenderFailed, RenderService
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, BrandKit
from blogwriter.linkedin.posts._post import located
from blogwriter.render.lint import RenderWarning, slide_text
from blogwriter.render.lint import lint_carousel as render_lint_carousel
from blogwriter.render.spec import CarouselSpec, CoverSlide, DiagramSlide, Slide

__all__ = [
    "CARD_FILENAME",
    "COVER_FILENAME",
    "MEDIA_FORMATS",
    "PDF_FILENAME",
    "InvalidSpec",
    "RenderRefused",
    "RenderedMedia",
    "card_alt_text",
    "cover_alt_text",
    "diagram_sources",
    "media_missing",
    "parse_spec",
    "pdf_problems",
    "render_post_media",
    "same_media",
    "slide_texts",
    "spec_findings",
    "spec_json",
    "spec_sha256",
    "spec_with_title",
    "with_alt_texts",
]

MEDIA_FORMATS: Final = frozenset({"carousel", "image_card"})
"""Formats whose post needs rendered media before approval."""
PDF_FILENAME: Final = "carousel.pdf"
COVER_FILENAME: Final = "carousel-cover.png"
CARD_FILENAME: Final = "image-card.png"
"""Neutral download names: file names are part of the confidentiality gate, so they never
carry the title or any other text of the post."""

_ALLOWED_PDF_METADATA: Final = frozenset({"/Title", "/Author"})
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_CARD_RULES: Final = frozenset({"slide_words"})
"""Of the carousel rules, the ones that apply to a one-slide image card."""
_COVERED_RULES: Final = frozenset({"domain_like"})
"""Rules the LinkedIn lint already reports with a span on every slide (not repeated)."""


class InvalidSpec(ValueError):
    """A spec that ``CarouselSpec`` refuses (422 ``invalid_spec``). ``errors`` hold locations,
    messages and types only, never the input values."""

    code: Final = "invalid_spec"

    def __init__(self, errors: list[JsonValue]) -> None:
        super().__init__("invalid carousel spec")
        self.errors = errors


class RenderRefused(Exception):
    """Rendering produced nothing usable. ``code``: a ``RenderFailed`` code, ``blocklist_hit``,
    ``blocklist_not_configured``, ``pdf_metadata``, ``pdf_links``, ``pdf_unreadable``,
    ``nothing_to_render``, ``storage_unavailable`` or ``render_unavailable``."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _errors(exc: ValidationError) -> list[JsonValue]:
    out: list[JsonValue] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        loc: list[JsonValue] = [
            str(part) if not isinstance(part, int) else part for part in error["loc"]
        ]
        out.append({"loc": loc, "msg": error["msg"], "type": error["type"]})
    return out[:20]


def parse_spec(spec: Mapping[str, object] | None) -> CarouselSpec:
    """The spec as ``blogwriter.render.CarouselSpec`` (:class:`InvalidSpec` when it does not
    validate or is missing)."""
    if spec is None:
        raise InvalidSpec([{"loc": ["spec"], "msg": "missing", "type": "missing"}])
    try:
        return CarouselSpec.model_validate(dict(spec))
    except ValidationError as exc:
        raise InvalidSpec(_errors(exc)) from None


def spec_json(spec: CarouselSpec) -> dict[str, Any]:
    """The JSON stored on the post (normalized plain text, so it round-trips unchanged)."""
    return spec.model_dump(mode="json")


def spec_sha256(spec: Mapping[str, object] | None) -> str:
    """Identity of a stored spec (renders and checks bind to it)."""
    return sha256_hex(canonical_json(dict(spec) if spec is not None else None))


def spec_with_title(spec: Mapping[str, object], title: str) -> dict[str, Any]:
    """The stored spec with its title (the LinkedIn document title) replaced, re-validated."""
    parsed = parse_spec({**dict(spec), "title": title})
    return spec_json(parsed)


def _slide_lines(slide: Slide) -> list[str]:
    lines = slide_text(slide)
    if isinstance(slide, DiagramSlide):
        lines = [*lines, slide.alt]
    return lines


def slide_texts(spec: CarouselSpec) -> list[str]:
    """Every slide's visible text (and a diagram's alt text), one field per line."""
    return ["\n".join(_slide_lines(slide)) for slide in spec.slides]


def diagram_sources(spec: CarouselSpec) -> list[tuple[int, str]]:
    """``(slide index, Mermaid source)`` of the diagram slides (their labels end up in the PDF:
    the blocklist scans them)."""
    return [(i, s.mermaid) for i, s in enumerate(spec.slides) if isinstance(s, DiagramSlide)]


def _rule_message(warning: RenderWarning) -> str:
    text = warning.message.strip() or warning.code
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def spec_findings(spec: CarouselSpec, *, card: bool) -> list[Finding]:
    """The plan's carousel rules (``blogwriter.render``'s editorial lint) as blocking findings.

    They block approval, not rendering. A card is one slide: only the word limit applies to
    it, plus the one-slide rule itself. Domain-like tokens are left to the LinkedIn lint, which
    runs on every slide's text with a span.
    """
    findings: list[Finding] = []
    if card and len(spec.slides) != 1:
        findings.append(
            located(
                Finding(
                    code="card-slides",
                    severity="block",
                    message="An image card has exactly one slide.",
                ),
                "carouselSpec",
            )
        )
    for warning in render_lint_carousel(spec):
        if warning.code in _COVERED_RULES or (card and warning.code not in _CARD_RULES):
            continue
        field = f"slides[{warning.slide - 1}]" if warning.slide else "carouselSpec"
        findings.append(
            located(
                Finding(
                    code="carousel-" + warning.code.replace("_", "-"),
                    severity="block",
                    message=_rule_message(warning),
                ),
                field,
            )
        )
    return findings


def media_missing(post: Post) -> bool:
    """A carousel or image card without its rendered media (approval needs them)."""
    if post.format == "carousel":
        return not any(item.role == "carousel_pdf" for item in post.media)
    if post.format == "image_card":
        return not any(item.role == "image_card" for item in post.media)
    return False


def cover_alt_text(spec: CarouselSpec) -> str | None:
    first = spec.slides[0] if spec.slides else None
    if isinstance(first, CoverSlide):
        parts = [first.hook, first.subtitle]
        return ". ".join(part for part in parts if part)[:1000]
    return spec.title[:1000]


def card_alt_text(spec: CarouselSpec) -> str:
    lines = _slide_lines(spec.slides[0]) if spec.slides else [spec.title]
    return ". ".join(line.rstrip(".") for line in lines if line)[:1000]


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedMedia:
    """What a render stored: the media references (blobs already written) and the spec they
    were rendered from."""

    media: tuple[MediaRef, ...]
    spec_sha256: str


def pdf_problems(pdf: bytes, *, title: str, pages: int) -> list[str]:
    """Why a rendered PDF may not ship (empty: fine). Synchronous and CPU-bound: call it in a
    thread. Codes: ``pdf_unreadable``, ``pdf_metadata`` (anything but Title/Author, a title
    other than the document title, XMP), ``pdf_pages``, ``pdf_links``."""
    try:
        reader = PdfReader(io.BytesIO(pdf))
        info = reader.metadata
        keys = set(info.keys()) if info is not None else set[str]()
        problems: list[str] = []
        recorded_title = info.get("/Title") if info is not None else None
        if (
            not keys <= _ALLOWED_PDF_METADATA
            or reader.xmp_metadata is not None
            or (recorded_title is not None and str(recorded_title) != title)
        ):
            problems.append("pdf_metadata")
        if len(reader.pages) != pages:
            problems.append("pdf_pages")
        if any(page.get("/Annots") for page in reader.pages):
            problems.append("pdf_links")
        return problems
    except (PyPdfError, ValueError, KeyError, TypeError):
        return ["pdf_unreadable"]


def _png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None, None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if not (1 <= width <= 16_384 and 1 <= height <= 16_384):
        return None, None
    return width, height


def _refuse_confidential(texts: Sequence[str], blocklist: Blocklist) -> None:
    if not blocklist.configured:
        raise RenderRefused("blocklist_not_configured")
    if any(blocklist_findings(text, blocklist) for text in texts if text):
        raise RenderRefused("blocklist_hit", "The rendered file matches the blocklist.")


async def _put(
    blobs: BlobStore,
    post_id: str,
    data: bytes,
    *,
    ext: Literal["pdf", "png"],
    content_type: Literal["application/pdf", "image/png"],
) -> tuple[str, str]:
    sha = sha256_hex(data)
    name = f"posts/{post_id}/{sha}.{ext}"
    with contextlib.suppress(BlobExists):  # content-addressed: the same bytes are there
        await blobs.put("media", name, data, content_type=content_type)
    return name, sha


async def render_post_media(
    post: Post,
    *,
    render: RenderService,
    blobs: BlobStore,
    brand: BrandKit,
    blocklist: Blocklist,
    disclaimer: str | None,
) -> RenderedMedia:
    """Render the post's slides, check the exact bytes and store them (:class:`RenderRefused`
    on any failure; nothing is stored then)."""
    if post.format not in MEDIA_FORMATS or post.carousel_spec is None:
        raise RenderRefused("nothing_to_render", "Only carousels and image cards are rendered.")
    try:
        spec = parse_spec(post.carousel_spec)
    except InvalidSpec:
        raise RenderRefused("invalid_spec", "The slides are not a valid carousel.") from None
    stored_spec = spec_json(spec)
    texts = [spec.title, *slide_texts(spec), *(source for _, source in diagram_sources(spec))]
    _refuse_confidential(texts, blocklist)
    try:
        if post.format == "carousel":
            media = await _render_carousel(
                post, spec, stored_spec, render, blobs, brand, blocklist, disclaimer
            )
        else:
            media = await _render_card(post, spec, render, blobs, brand, blocklist, disclaimer)
    except RenderFailed as exc:
        raise RenderRefused(exc.code, exc.detail) from None
    return RenderedMedia(media=media, spec_sha256=spec_sha256(post.carousel_spec))


async def _render_carousel(
    post: Post,
    spec: CarouselSpec,
    stored_spec: Mapping[str, object],
    render: RenderService,
    blobs: BlobStore,
    brand: BrandKit,
    blocklist: Blocklist,
    disclaimer: str | None,
) -> tuple[MediaRef, ...]:
    deck = await render.render_carousel(stored_spec, brand, disclaimer=disclaimer)
    if not deck.pages:
        raise RenderRefused("output_limit", "The render produced no pages.")
    problems = await asyncio.to_thread(
        pdf_problems, deck.pdf, title=spec.title, pages=len(spec.slides)
    )
    if problems:
        raise RenderRefused(problems[0], "The rendered PDF failed the confidentiality checks.")
    cover_alt = cover_alt_text(spec)
    _refuse_confidential(
        [*deck.text, cover_alt or "", PDF_FILENAME, COVER_FILENAME, spec.title], blocklist
    )
    pdf_name, pdf_sha = await _put(
        blobs, post.id, deck.pdf, ext="pdf", content_type="application/pdf"
    )
    cover = deck.cover_png
    cover_name, cover_sha = await _put(blobs, post.id, cover, ext="png", content_type="image/png")
    width, height = _png_size(cover)
    return (
        MediaRef(
            role="carousel_pdf",
            name=pdf_name,
            sha256=pdf_sha,
            content_type="application/pdf",
            size_bytes=len(deck.pdf),
            page_count=deck.page_count,
            filename=PDF_FILENAME,
        ),
        MediaRef(
            role="carousel_cover",
            name=cover_name,
            sha256=cover_sha,
            content_type="image/png",
            size_bytes=len(cover),
            width=width,
            height=height,
            alt_text=cover_alt,
            filename=COVER_FILENAME,
        ),
    )


async def _render_card(
    post: Post,
    spec: CarouselSpec,
    render: RenderService,
    blobs: BlobStore,
    brand: BrandKit,
    blocklist: Blocklist,
    disclaimer: str | None,
) -> tuple[MediaRef, ...]:
    if len(spec.slides) != 1:
        raise RenderRefused("invalid_spec", "An image card has exactly one slide.")
    slide: dict[str, Any] = spec.slides[0].model_dump(mode="json")
    png = await render.render_image_card(slide, brand, disclaimer=disclaimer)
    width, height = _png_size(png)
    if width is None:
        raise RenderRefused("output_limit", "The render did not produce a PNG.")
    alt = card_alt_text(spec)
    _refuse_confidential([alt, CARD_FILENAME], blocklist)
    name, sha = await _put(blobs, post.id, png, ext="png", content_type="image/png")
    return (
        MediaRef(
            role="image_card",
            name=name,
            sha256=sha,
            content_type="image/png",
            size_bytes=len(png),
            width=width,
            height=height,
            alt_text=alt,
            filename=CARD_FILENAME,
        ),
    )


def with_alt_texts(media: Sequence[MediaRef], post: Post) -> list[MediaRef]:
    """``media`` with the alt texts the post's slides give them (a cover's hook, a card's
    text): what :func:`render_post_media` set, derived again instead of carried around."""
    try:
        spec = parse_spec(post.carousel_spec)
    except InvalidSpec:
        return list(media)
    out: list[MediaRef] = []
    for item in media:
        if item.role == "carousel_cover":
            item = item.model_copy(update={"alt_text": cover_alt_text(spec)})
        elif item.role == "image_card":
            item = item.model_copy(update={"alt_text": card_alt_text(spec)})
        out.append(item)
    return out


def same_media(left: Sequence[MediaRef], right: Sequence[MediaRef]) -> bool:
    """Whether two media lists are equal for the approval hash (role, SHA-256, alt text)."""
    key = [(m.role, m.sha256, m.alt_text) for m in left]
    return key == [(m.role, m.sha256, m.alt_text) for m in right]
