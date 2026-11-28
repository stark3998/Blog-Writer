"""The plan's defaults for the knowledge-base settings, and how kb reads its singletons.

A settings document the owner never saved reads as its defaults and the API reports it with
``etag: null`` (contract 1.4). The domain models carry the plan's values
(:meth:`~blogwriter.domain.base.SingletonDocument.default`), the three complete pillars included
(:func:`blogwriter.domain.settings.default_pillars`), so every package reading the never-saved
pillars with ``docstore.read_singleton`` sees what Library shows.

Only reads go through here; nothing is written until the owner saves (no GET changes state).

**Art briefs** (``BrandKit.artBriefs``, per pillar id, at most 500 characters each) steer the
opt-in AI illustrations. A pillar without a brief (never saved, or saved empty) uses
:func:`default_art_brief`: editorial and abstract, in the brand palette, no text, logos or
recognisable people. A brief names no colour value: every illustration request carries the
brand kit's **current** palette by hex value next to the brief
(``illustrations.briefs.palette``), so a default the owner saved verbatim with the kit (the
first save of the kit stores what it shows) never goes stale when the colours change. The
never-saved brand kit shows these defaults for the plan's pillars, so the owner sees what is
used and edits it in Library > Brand kit (``PUT /api/kb/brand``); ``imagesEnabled`` is
switched on in Settings > Models (``PUT /api/kb/models``, 409 ``images_unavailable`` without an
image route).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, cast

from blogwriter.db import NotFound, Store
from blogwriter.domain.base import SingletonDocument
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.settings import BrandKit, Pillar, PillarSettings, default_pillars

__all__ = [
    "MAX_ART_BRIEF",
    "PLAN_PILLARS",
    "art_brief_for",
    "default_art_brief",
    "default_art_briefs",
    "kb_default",
    "read_settings",
]

MAX_ART_BRIEF: Final = 500
"""``BrandKit.artBriefs`` values are at most 500 characters."""

PLAN_PILLARS: Final[tuple[Pillar, ...]] = tuple(default_pillars())
"""The plan's three pillars (:func:`blogwriter.domain.settings.default_pillars`, the domain
default of :class:`~blogwriter.domain.settings.PillarSettings`)."""


def default_art_brief(pillar: Pillar) -> str:
    """The default art brief of ``pillar``: editorial, abstract, in the brand palette, no text
    in the image.

    It names the palette's roles (deep, slate and soft tones, the ground, one accent), never a
    colour value: the image model gets the brand kit's current colours by hex value next to it
    in every request, so the brief stays right after the owner saves it and changes colours."""
    subject = " ".join(pillar.name.split())[:40].rstrip()
    brief = (
        "Editorial illustration, abstract and conceptual: clean geometric forms, layered planes, "
        f"paths and nodes as a visual metaphor for {subject}. Calm, precise and modern, with "
        "generous negative space and soft light. Colours from the brand palette only: mostly its "
        "deep, slate and soft tones on its light ground, with at most one small touch of its "
        "accent. No text, letters, numbers, logos, brand marks or recognisable people."
    )
    return brief[:MAX_ART_BRIEF]


def default_art_briefs(pillars: Iterable[Pillar]) -> dict[str, str]:
    """:func:`default_art_brief` for every pillar, by pillar id."""
    return {pillar.id: default_art_brief(pillar) for pillar in pillars}


def art_brief_for(brand: BrandKit, pillar: Pillar) -> str:
    """The owner's art brief for ``pillar``, or :func:`default_art_brief` when it is empty."""
    owned = brand.art_briefs.get(pillar.id, "").strip()
    return owned if owned else default_art_brief(pillar)


def kb_default[S: SingletonDocument](cls: type[S], now: str) -> S:
    """The plan's default for a never-saved kb singleton (``now`` stamps it). The brand kit
    carries the default art briefs of the plan's pillars."""
    doc = cls.default(now)
    if isinstance(doc, PillarSettings):
        pillars = [pillar.model_copy(deep=True) for pillar in PLAN_PILLARS]
        return cast(S, doc.model_copy(update={"pillars": pillars}))
    if isinstance(doc, BrandKit):
        briefs = default_art_briefs(PLAN_PILLARS)
        return cast(S, doc.model_copy(update={"art_briefs": briefs}))
    return doc


async def read_settings[S: SingletonDocument](
    store: Store, cls: type[S], *, now: str
) -> tuple[S, str | None]:
    """The stored singleton and its etag, or ``(kb_default(cls, now), None)`` if never saved.

    Like :func:`blogwriter.domain.docstore.read_singleton`, with kb's plan defaults.
    """
    try:
        stored = await read_doc(store, cls, cls.SINGLETON_ID)
    except NotFound:
        return kb_default(cls, now), None
    return stored.doc, stored.etag
