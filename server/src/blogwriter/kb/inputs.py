"""The owner-input checklist (contract W-A2 and section 12): what only the owner can provide,
and how far along it is. Today shows the open items as banners; Settings > Policy lists them.

Counts are informational (a document that cannot be read is skipped, never an error) and
capped at :data:`COUNT_CAP` reads per kind. Nothing here quotes stored text.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Final

from blogwriter.db import Filter, Store
from blogwriter.domain.analytics import EvalResult
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import SingletonDocument, to_iso
from blogwriter.domain.docstore import InvalidStoredDocument, find_docs
from blogwriter.domain.engagement import WatchEntry
from blogwriter.domain.settings import (
    Blocklist,
    BrandKit,
    EmployerPolicy,
    LinkedInConnection,
    PillarSettings,
    VoicePreferences,
)
from blogwriter.domain.states import ConnectionStatus, ReviewStatus, SampleStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.voice import VoiceSample
from blogwriter.kb.defaults import kb_default, read_settings

__all__ = [
    "COUNT_CAP",
    "EVAL_TOPICS_NEEDED",
    "POVS_PER_PILLAR",
    "STORIES_NEEDED",
    "WATCHLIST_NEEDED",
    "OwnerInput",
    "OwnerInputs",
    "owner_inputs",
]

STORIES_NEEDED: Final = 10
POVS_PER_PILLAR: Final = 3
WATCHLIST_NEEDED: Final = 5
EVAL_TOPICS_NEEDED: Final = 12
COUNT_CAP: Final = 1000
_EVAL_LABELS: Final = ["as_is", "needs_edits", "reject"]
_logger: Final = logging.getLogger("blogwriter.kb")


class OwnerInput(ApiModel):
    """One item: ``have``/``need`` are counts where the item is counted, else ``null``."""

    code: str
    label: str
    done: bool
    have: int | None = None
    need: int | None = None
    href: str


class OwnerInputs(ApiModel):
    """Codes, in this order: ``linkedin_samples``, ``stories_approved``, ``povs_per_pillar``,
    ``blocklist``, ``employer_policy``, ``ai_use_confirmed``, ``brand_kit``,
    ``linkedin_connected``, ``watchlist``, ``eval_labels``."""

    items: list[OwnerInput]


def _counted(code: str, label: str, have: int, need: int, href: str) -> OwnerInput:
    return OwnerInput(code=code, label=label, done=have >= need, have=have, need=need, href=href)


async def _settings[S: SingletonDocument](store: Store, cls: type[S], *, now: str) -> S:
    """A settings document for the checklist; one that cannot be read counts as never saved
    (its item shows as open, the direction every other reader fails in too)."""
    try:
        doc, _ = await read_settings(store, cls, now=now)
    except InvalidStoredDocument:
        _logger.warning("kb_unreadable_settings", extra={"kind": cls.KIND})
        return kb_default(cls, now)
    return doc


async def owner_inputs(store: Store, *, now: datetime) -> OwnerInputs:
    """The owner-input checklist against the stored documents.

    The blocklist counts as done once it is ``configured`` (saved at least once, even empty).
    POVs count per pillar: ``have`` is the number of pillars with at least
    :data:`POVS_PER_PILLAR` approved POVs, ``need`` the number of pillars. Eval labels count the
    eval topics with at least one labelled result.
    """
    stamp = to_iso(now)
    preferences = await _settings(store, VoicePreferences, now=stamp)
    pillars = await _settings(store, PillarSettings, now=stamp)
    blocklist = await _settings(store, Blocklist, now=stamp)
    policy = await _settings(store, EmployerPolicy, now=stamp)
    brand = await _settings(store, BrandKit, now=stamp)
    connection = await _settings(store, LinkedInConnection, now=stamp)

    samples = await find_docs(
        store,
        VoiceSample,
        [
            Filter("sourceType", "==", "linkedin_post"),
            Filter("status", "==", SampleStatus.ACTIVE.value),
        ],
        limit=COUNT_CAP,
    )
    stories = await find_docs(
        store, StoryEntry, [Filter("status", "==", ReviewStatus.APPROVED.value)], limit=COUNT_CAP
    )
    povs = await find_docs(
        store, PovEntry, [Filter("status", "==", ReviewStatus.APPROVED.value)], limit=COUNT_CAP
    )
    watchlist = await find_docs(store, WatchEntry, [Filter("active", "==", True)], limit=COUNT_CAP)
    labelled = await find_docs(
        store, EvalResult, [Filter("ownerLabel", "in", _EVAL_LABELS)], limit=COUNT_CAP
    )

    per_pillar = Counter(pov.doc.pillar for pov in povs)
    covered = sum(1 for pillar in pillars.pillars if per_pillar[pillar.id] >= POVS_PER_PILLAR)
    labelled_topics = len({result.doc.topic_id for result in labelled})

    items = [
        _counted(
            "linkedin_samples",
            "Past LinkedIn posts you wrote by hand",
            len(samples),
            preferences.min_linkedin_samples,
            "/library/voice",
        ),
        _counted(
            "stories_approved", "Approved stories", len(stories), STORIES_NEEDED, "/library/stories"
        ),
        _counted(
            "povs_per_pillar",
            f"Pillars with {POVS_PER_PILLAR} or more approved POVs",
            covered,
            len(pillars.pillars),
            "/library/stories",
        ),
        OwnerInput(
            code="blocklist",
            label="Blocklist of client and internal project names saved",
            done=blocklist.configured,
            href="/settings/policy",
        ),
        OwnerInput(
            code="employer_policy",
            label="Employer social media policy and disclaimer saved",
            done=policy.configured,
            href="/settings/policy",
        ),
        OwnerInput(
            code="ai_use_confirmed",
            label="Employer AI-use policy confirmed",
            done=policy.ai_use_confirmed,
            href="/settings/policy",
        ),
        OwnerInput(
            code="brand_kit",
            label="Brand kit with your name for carousels and images",
            done=brand.complete,
            href="/library/brand",
        ),
        OwnerInput(
            code="linkedin_connected",
            label="LinkedIn connected",
            done=connection.status is ConnectionStatus.CONNECTED,
            href="/settings/integrations",
        ),
        _counted(
            "watchlist",
            "Niche voices on your watchlist",
            len(watchlist),
            WATCHLIST_NEEDED,
            "/today",
        ),
        _counted(
            "eval_labels",
            "Eval topics you labelled",
            labelled_topics,
            EVAL_TOPICS_NEEDED,
            "/settings/system",
        ),
    ]
    return OwnerInputs(items=items)
