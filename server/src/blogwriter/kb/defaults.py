"""The plan's defaults for the knowledge-base settings, and how kb reads its singletons.

A settings document the owner never saved reads as its defaults and the API reports it with
``etag: null`` (contract 1.4). The domain models carry the plan's values
(:meth:`~blogwriter.domain.base.SingletonDocument.default`), the three complete pillars included
(:func:`blogwriter.domain.settings.default_pillars`), so every package reading the never-saved
pillars with ``docstore.read_singleton`` sees what Library shows.

Only reads go through here; nothing is written until the owner saves (no GET changes state).
"""

from __future__ import annotations

from typing import Final, cast

from blogwriter.db import NotFound, Store
from blogwriter.domain.base import SingletonDocument
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.settings import Pillar, PillarSettings, default_pillars

__all__ = ["PLAN_PILLARS", "kb_default", "read_settings"]

PLAN_PILLARS: Final[tuple[Pillar, ...]] = tuple(default_pillars())
"""The plan's three pillars (:func:`blogwriter.domain.settings.default_pillars`, the domain
default of :class:`~blogwriter.domain.settings.PillarSettings`)."""


def kb_default[S: SingletonDocument](cls: type[S], now: str) -> S:
    """The plan's default for a never-saved kb singleton (``now`` stamps it)."""
    doc = cls.default(now)
    if isinstance(doc, PillarSettings):
        pillars = [pillar.model_copy(deep=True) for pillar in PLAN_PILLARS]
        return cast(S, doc.model_copy(update={"pillars": pillars}))
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
