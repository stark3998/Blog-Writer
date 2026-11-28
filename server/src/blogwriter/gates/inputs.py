"""Everything the gates read besides the article: settings documents, the brief, owner material.

Reads only (``domain.docstore``). A missing document reads as "not there" (no brief, no story);
a stored document that no longer fits its model is skipped the same way, so a gate that needs it
fails closed (an owner signal that cannot be read does not count).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import InvalidStoredDocument, read_doc, read_singleton
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import (
    Blocklist,
    EmployerPolicy,
    FormatPacks,
    ModelPreferences,
    PillarSettings,
    StandardsCatalog,
    VoicePreferences,
)
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates.standards import effective_catalog

__all__ = ["GateInputs", "load_inputs", "read_optional"]


@dataclass(frozen=True, slots=True)
class GateInputs:
    """The documents one gate run reads (loaded once per step)."""

    brief: Brief | None
    catalog: StandardsCatalog
    """The stored catalog with the seed lists merged in."""
    pillars: PillarSettings
    blocklist: Blocklist
    policy: EmployerPolicy
    preferences: ModelPreferences
    format_packs: FormatPacks
    stories: tuple[StoryEntry, ...] = ()
    povs: tuple[PovEntry, ...] = ()
    interviews: tuple[Interview, ...] = ()
    guide: StyleGuide | None = field(default=None)

    @property
    def citation_ids(self) -> frozenset[str]:
        if self.brief is None:
            return frozenset()
        return frozenset(citation.id for citation in self.brief.citations)


async def read_optional[D: Article | Brief | StoryEntry | PovEntry | Interview | StyleGuide](
    store: Store, cls: type[D], doc_id: str | None
) -> D | None:
    """The document, or ``None`` when it is missing, of another kind or unreadable."""
    if doc_id is None:
        return None
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except (NotFound, InvalidStoredDocument):
        return None


async def load_inputs(store: Store, article: Article, *, now: str) -> GateInputs:
    """Read what the gates need for ``article``."""
    catalog, _ = await read_singleton(store, StandardsCatalog, now=now)
    pillars, _ = await read_singleton(store, PillarSettings, now=now)
    blocklist, _ = await read_singleton(store, Blocklist, now=now)
    policy, _ = await read_singleton(store, EmployerPolicy, now=now)
    preferences, _ = await read_singleton(store, ModelPreferences, now=now)
    packs, _ = await read_singleton(store, FormatPacks, now=now)
    voice, _ = await read_singleton(store, VoicePreferences, now=now)
    brief = await read_optional(store, Brief, article.brief_id)
    stories = await _read_all(
        store, StoryEntry, [r.id for r in article.owner_signals if r.kind == "story"]
    )
    povs = await _read_all(
        store, PovEntry, [r.id for r in article.owner_signals if r.kind == "pov"]
    )
    interview_ids = [r.id for r in article.owner_signals if r.kind == "interview"]
    if article.interview_id is not None and article.interview_id not in interview_ids:
        interview_ids.append(article.interview_id)
    interviews = await _read_all(store, Interview, interview_ids)
    guide = await read_optional(store, StyleGuide, voice.active_guide_id)
    return GateInputs(
        brief=brief,
        catalog=effective_catalog(catalog),
        pillars=pillars,
        blocklist=blocklist,
        policy=policy,
        preferences=preferences,
        format_packs=packs,
        stories=tuple(stories),
        povs=tuple(povs),
        interviews=tuple(interviews),
        guide=guide,
    )


async def _read_all[D: StoryEntry | PovEntry | Interview](
    store: Store, cls: type[D], ids: Sequence[str]
) -> list[D]:
    found: list[D] = []
    for doc_id in dict.fromkeys(ids):
        doc = await read_optional(store, cls, doc_id)
        if doc is not None:
            found.append(doc)
    return found
