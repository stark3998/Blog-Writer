"""Which pillar ids the rest of the app references (the ``pillar_in_use`` guard).

Pillar ids are stable keys: ideas, articles (eval articles included), posts, stories, POVs,
sources and lane series point at them. Every query here guards an action, so it reads with
``strict=True``: a document the check cannot read fails the check (fail closed) instead of
letting a pillar it may reference disappear.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Final

from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.base import Document
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import LaneSettings
from blogwriter.domain.signals import Idea, Source
from blogwriter.domain.stories import PovEntry, StoryEntry

__all__ = ["REFERENCE_KINDS", "pillar_ids_in_use", "pillar_references"]

REFERENCE_KINDS: Final = ("idea", "article", "post", "story", "pov", "source", "series")
"""What can reference a pillar, in the order the 409 lists them."""


async def _scan[D: Document](
    store: Store,
    cls: type[D],
    field: str,
    pillar_of: Callable[[D], str | None],
    wanted: Collection[str] | None,
) -> set[str]:
    """Pillar ids of ``cls`` documents (only ``wanted`` ones when given: one hit each)."""
    if wanted is None:
        docs = await find_docs(store, cls, [Filter(field, "!=", None)], strict=True)
        return {pillar for stored in docs if (pillar := pillar_of(stored.doc))}
    found: set[str] = set()
    for pillar in sorted(wanted):
        if await find_docs(store, cls, [Filter(field, "==", pillar)], limit=1, strict=True):
            found.add(pillar)
    return found


async def pillar_references(
    store: Store, *, among: Collection[str] | None = None
) -> dict[str, list[str]]:
    """Pillar id -> the kinds that reference it (:data:`REFERENCE_KINDS` order).

    With ``among``, only those ids are looked for (indexed equality queries, one document each);
    without it every referencing document is read (whole documents until projections exist).
    Story pillars are a list, which the filter language cannot search, so stories are always
    read in full. Raises ``InvalidStoredDocument`` when a document cannot be read.
    """
    wanted = None if among is None else frozenset(among)
    if wanted is not None and not wanted:
        return {}
    found: dict[str, set[str]] = {}

    def note(kind: str, pillars: Collection[str]) -> None:
        for pillar in pillars:
            if wanted is None or pillar in wanted:
                found.setdefault(pillar, set()).add(kind)

    note("idea", await _scan(store, Idea, "pillar", lambda doc: doc.pillar, wanted))
    note("article", await _scan(store, Article, "meta.pillar", lambda doc: doc.meta.pillar, wanted))
    note("post", await _scan(store, Post, "pillar", lambda doc: doc.pillar, wanted))
    note("pov", await _scan(store, PovEntry, "pillar", lambda doc: doc.pillar, wanted))
    note("source", await _scan(store, Source, "pillar", lambda doc: doc.pillar, wanted))
    for story in await find_docs(store, StoryEntry, strict=True):
        note("story", story.doc.pillars)
    try:
        lanes = await read_doc(store, LaneSettings, LaneSettings.SINGLETON_ID)
    except NotFound:
        pass  # the default lanes have no series
    else:
        note("series", [series.pillar for series in lanes.doc.series])
    order = {kind: index for index, kind in enumerate(REFERENCE_KINDS)}
    return {pillar: sorted(kinds, key=order.__getitem__) for pillar, kinds in sorted(found.items())}


async def pillar_ids_in_use(store: Store, *, among: Collection[str] | None = None) -> set[str]:
    """The pillar ids any idea, article, post, story, POV, source or lane series references
    (only those in ``among`` when given). Fails closed: ``InvalidStoredDocument`` when a
    document of those kinds cannot be read."""
    return set(await pillar_references(store, among=among))
