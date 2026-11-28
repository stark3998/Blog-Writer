"""blogwriter.illustrations: opt-in AI illustrations for articles (``gpt-image-2``).

The owner asks for a hero and up to two inline illustrations (``POST /api/articles/{id}/images``);
the ``illustrations.generate`` job plans them with Sol from the article's **public** metadata and
the pillar's art brief (:mod:`.briefs`), generates them on the image resource, checks each one
with a vision call ("media matches text": the concept, no text, no logos or branding, no
recognisable person, nothing unsafe) on the stored bytes, keeps the survivors (re-encoded as
WebP, metadata stripped) in Blob ``media`` under their content hash and attaches them to the
article as candidates (``Article.illustrations``). The owner then makes one the hero, inserts
one after a heading, edits alt text or discards candidates (:mod:`.routes`, through the
articles API). Publishing copies the hero and every inserted image to ``published/<slug>/``
like any other article image.

Wiring (``blogwriter.main``): :func:`get_router`, :func:`register_jobs`,
:func:`schedule_entries` (none: content work starts from owner actions only). Settings: the AI
router's ``BW_AI_IMAGE_*`` (no package settings); the owner's switch and art briefs are kb
settings (``ModelPreferences.imagesEnabled``, ``BrandKit.artBriefs``).
"""

from __future__ import annotations

from blogwriter.domain.jobtypes import ILLUSTRATIONS_GENERATE
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.settings import CadenceSettings
from blogwriter.illustrations.routes import REVIEW_STATES, get_router, start_illustrations
from blogwriter.illustrations.steps import MAX_CALLS_PER_IMAGE, STEP_NAMES, steps
from blogwriter.jobs import JobRegistry

__all__ = [
    "MAX_CALLS_PER_IMAGE",
    "REVIEW_STATES",
    "STEP_NAMES",
    "get_router",
    "register_jobs",
    "schedule_entries",
    "start_illustrations",
]


def register_jobs(registry: JobRegistry) -> None:
    """Register ``illustrations.generate`` (plan, generate, check, store, attach)."""
    registry.register(ILLUSTRATIONS_GENERATE, steps())


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """No schedule items: illustrations start from an owner action only."""
    del cadence
    return []
