"""Test helpers for the illustrations (no network, no Azure, no real image model).

- :func:`seed_article`: an article in review with complete metadata, three section headings, a
  body sentence and an interview quote carrying sentinels that must never reach a model, and
  the pipeline's draft Open Graph card (and its default hero copy) in ``media``.
- :func:`enable_images` / :func:`save_blocklist`: the owner's switch and a saved blocklist.
- :func:`plan_reply`, :func:`verdict`: scripted structured outputs of the plan and the check.
- :func:`generated_png`: a PNG as an image model returns it, with text, XMP and EXIF metadata
  carrying a marker (the stored copy must not keep any of it).
- :func:`illustrations_registry`, :func:`start`: the job type and an owner request through the
  real route function.
"""

from __future__ import annotations

import io
from typing import Final

from PIL import Image, PngImagePlugin

from blogwriter.articles.content import prepare_body
from blogwriter.domain.articles import Article, ArticleMeta, OwnerSignalRef
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import Stored, create_doc, read_doc, save_singleton
from blogwriter.domain.settings import Blocklist, BlocklistTerm, ModelPreferences
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, png_bytes, sample_document
from blogwriter.illustrations import register_jobs, start_illustrations
from blogwriter.illustrations.models import BriefOut, IllustrationsIn, PlanOut, VerdictOut
from blogwriter.jobs import JobRegistry

__all__ = [
    "BODY_SENTINEL",
    "HEADINGS",
    "METADATA_MARKER",
    "QUOTE_SENTINEL",
    "enable_images",
    "generated_png",
    "illustrations_registry",
    "plan_reply",
    "save_blocklist",
    "seed_article",
    "start",
    "verdict",
]

BODY_SENTINEL: Final = "BODYSENTINEL4417"
"""A word of the article body: the body never reaches a model."""
QUOTE_SENTINEL: Final = "QUOTESENTINEL0923"
"""A word of an (anonymized) interview quote: owner material never reaches a model."""
METADATA_MARKER: Final = "provenance-marker-5531"
HEADINGS: Final = ("Why scope per call", "The gateway pattern", "What to do next")

_BODY: Final = f"""Agents act with borrowed authority, and {BODY_SENTINEL} is only in the body.

## {HEADINGS[0]}

A per-call scope limits the blast radius of a confused agent.

## {HEADINGS[1]}

The gateway issues a token for each call and logs it.

## {HEADINGS[2]}

Start with the tool that can delete data.
"""


def _card(article_id: str) -> tuple[MediaRef, bytes]:
    """The pipeline's draft Open Graph card of the article (under ``articles/<id>/``)."""
    data = png_bytes(12, 6, "#0E2A24")
    sha = sha256_hex(data)
    ref = MediaRef(
        role="og_image",
        name=f"articles/{article_id}/{sha}.png",
        sha256=sha,
        content_type="image/png",
        size_bytes=len(data),
        width=1200,
        height=630,
        alt_text="Scope every tool call, not every agent",
    )
    return ref, data


async def seed_article(
    services: FakeServices,
    *,
    status: ArticleStatus = ArticleStatus.IN_REVIEW,
    title: str | None = "Scope every tool call, not every agent",
) -> Stored[Article]:
    """An article in ``status`` with metadata, headings, sentinels and the draft OG card."""
    body = prepare_body(_BODY).text
    meta = ArticleMeta(
        title=title,
        slug="scope-every-tool-call",
        excerpt="Per-call scopes keep a confused agent from spending a whole token.",
        pillar="agents",
        format="deep-explainer",
        tags=["mcp", "identity"],
    )
    article = sample_document(
        Article,
        id=Article.new_id(now=services.clock.now()),
        status=status,
        meta=meta,
        markdown=body,
        version=3,
        owner_signals=[OwnerSignalRef(kind="interview", id="intv-1", quote=QUOTE_SENTINEL)],
    )
    card, data = _card(article.id)
    article = article.model_copy(
        update={
            "content_sha256": article.compute_sha256(),
            "media": [card, card.model_copy(update={"role": "hero_image"})],
        }
    )
    if services.blobs is not None:
        await services.blobs.put("media", card.name, data, content_type="image/png")
    await create_doc(services.store, article)
    return await read_doc(services.store, Article, article.id)


async def enable_images(services: FakeServices, *, enabled: bool = True) -> None:
    """Save the owner's illustrations switch."""
    preferences = ModelPreferences.default(SAMPLE_NOW).model_copy(
        update={"images_enabled": enabled}
    )
    await save_singleton(services.store, preferences, if_match=None)


async def save_blocklist(services: FakeServices, *terms: str) -> None:
    """Save the blocklist (configured), with ``terms`` as client names."""
    blocklist = Blocklist.default(SAMPLE_NOW).model_copy(
        update={
            "configured": True,
            "terms": [
                BlocklistTerm(id=f"t-{index}", term=term, category="client", match="word")
                for index, term in enumerate(terms, 1)
            ],
        }
    )
    await save_singleton(services.store, blocklist, if_match=None)


def plan_reply(count: int = 3) -> PlanOut:
    """A plan of one hero and ``count - 1`` inline illustrations on the seeded headings."""
    briefs = [
        BriefOut(
            role="hero",
            section="",
            concept="Each tool call gets its own small key",
            prompt="A row of small glass keys on a calm teal surface, each fitted to one door.",
            alt_text="Small glass keys in a row, each fitted to its own small door.",
        ),
        BriefOut(
            role="inline",
            section=HEADINGS[0],
            concept="A narrow beam that lights only one step",
            prompt="A narrow beam of light falling on a single stone step in a dark stairwell.",
            alt_text="A narrow beam of light on one step of a dark stairwell.",
        ),
        BriefOut(
            role="inline",
            section=HEADINGS[1],
            concept="A gate that hands out one pass at a time",
            prompt="A slim archway handing a single glowing token to a passing paper boat.",
            alt_text="A slim archway handing one glowing token to a paper boat.",
        ),
    ]
    return PlanOut(illustrations=briefs[:count])


def verdict(
    *,
    depicts_concept: bool = True,
    legible_text: bool = False,
    logos_or_branding: bool = False,
    recognisable_person: bool = False,
    unsafe: bool = False,
) -> VerdictOut:
    """A scripted vision verdict (passes by default)."""
    failed = (
        not depicts_concept or legible_text or logos_or_branding or recognisable_person or unsafe
    )
    return VerdictOut(
        depicts_concept=depicts_concept,
        legible_text=legible_text,
        logos_or_branding=logos_or_branding,
        recognisable_person=recognisable_person,
        unsafe=unsafe,
        reason="It shows lettering." if failed else "",
    )


def generated_png(color: str = "#335577", size: tuple[int, int] = (48, 32)) -> bytes:
    """A PNG as an image model returns it: text, XMP and EXIF chunks carry
    :data:`METADATA_MARKER`."""
    image = Image.new("RGB", size, color)
    info = PngImagePlugin.PngInfo()
    info.add_text("Description", METADATA_MARKER)
    info.add_itxt("XML:com.adobe.xmp", f"<x:xmpmeta>{METADATA_MARKER}</x:xmpmeta>")
    exif = Image.Exif()
    exif[0x010E] = METADATA_MARKER  # ImageDescription
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info, exif=exif.tobytes())
    return buffer.getvalue()


def illustrations_registry() -> JobRegistry:
    """A registry with ``illustrations.generate``."""
    registry = JobRegistry()
    register_jobs(registry)
    return registry


async def start(
    services: FakeServices,
    article: Stored[Article],
    *,
    count: int = 3,
    direction: str | None = None,
) -> tuple[Article, str, str]:
    """An owner request through :func:`start_illustrations` (gates, link, enqueue)."""
    return await start_illustrations(
        services,
        article.doc.id,
        IllustrationsIn(count=count, direction=direction),
        if_match=article.etag,
    )
