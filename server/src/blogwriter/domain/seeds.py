"""Proposals the v1 migration leaves for the owner to review (``status: proposed``).

v1's keyword lists and voice profiles do not map onto v2 settings one to one, and nothing the
owner has not approved may steer drafting. So ``python -m blogwriter.backups.migrate_v1`` writes
them as proposals (``docs/v2/build/migration.md``) and the package that owns what they would
change reviews them:

- :class:`KbSeed` (``settings``, kind ``kb_seed``; kb, ``/api/kb/seeds``): one v1 topic's keyword
  list with a guessed pillar. Approving adds the keywords the owner keeps to a pillar's
  vocabulary.
- :class:`VoiceSeed` (``stories``, kind ``voice_seed``; voice, ``/api/voice/seeds``): one v1 voice
  profile (tone, style notes and the sample text). Approving adds the sample text to the voice
  corpus as a voice sample; the notes stay on the seed for reference.

``status`` follows the ``review`` machine: ``proposed`` (pending) -> ``approved`` (applied) or
``rejected`` (dismissed), and ``rejected`` -> ``proposed`` (restored). Ids are derived from the v1
id, so re-running the migration finds the same documents. The migration replaces a seed only
while its ``updatedAt`` is still the one it wrote (``migratedFrom.updatedAt``, kept as an extra
field); every decision moves ``updatedAt`` forward (:func:`reviewed`), so a re-run skips a
reviewed seed (``changed_in_v2``) instead of undoing the owner's decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Literal

from pydantic import Field

from blogwriter.domain.base import DocId, Document, Slug, Timestamp, derived_id, parse_iso, to_iso
from blogwriter.domain.states import REVIEW_MACHINE, ReviewStatus

__all__ = ["KbSeed", "VoiceSeed", "review_stamp", "reviewed"]


class KbSeed(Document):
    """One v1 keyword list (``keywords`` container), proposed as pillar vocabulary."""

    CONTAINER: ClassVar[str] = "settings"
    KIND: ClassVar[str] = "kb_seed"
    OWNER: ClassVar[str] = "kb"

    status: ReviewStatus = ReviewStatus.PROPOSED
    status_changed_at: Timestamp
    source: Literal["v1_keywords"] = "v1_keywords"
    topic: str = Field(min_length=1, max_length=200)
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list[str], max_length=200
    )
    suggested_pillar: Annotated[Slug, Field(max_length=40)] | None = None
    applied_pillar: Annotated[Slug, Field(max_length=40)] | None = None
    """The pillar whose vocabulary took the keywords (set when the owner approves)."""
    applied_keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list[str], max_length=200
    )
    """The keywords the owner approved (the vocabulary keeps each term once)."""

    @staticmethod
    def id_for(v1_id: str) -> str:
        return derived_id("kbseed", "v1", v1_id)


class VoiceSeed(Document):
    """One v1 voice profile (``voice-profiles`` container), proposed as a voice sample."""

    CONTAINER: ClassVar[str] = "stories"
    KIND: ClassVar[str] = "voice_seed"
    OWNER: ClassVar[str] = "voice"

    status: ReviewStatus = ReviewStatus.PROPOSED
    status_changed_at: Timestamp
    source: Literal["v1_voice_profile"] = "v1_voice_profile"
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    tone: str = Field(default="", max_length=200)
    style_notes: str = Field(default="", max_length=5000)
    text: str = Field(default="", max_length=20_000)
    """The v1 profile's sample text (owner writing)."""
    is_default: bool = False
    sample_id: DocId | None = None
    """The voice sample made from ``text`` (set when the owner approves)."""

    @staticmethod
    def id_for(v1_id: str) -> str:
        return derived_id("voiceseed", "v1", v1_id)


def review_stamp(seed: KbSeed | VoiceSeed, now: datetime) -> str:
    """The ``updatedAt`` of a review decision: ``now``, but always after the seed's current
    ``updatedAt``. A decision stamped with the migration's own ``updatedAt`` would look untouched
    to a re-run of the migration, which would then overwrite it."""
    stamp = to_iso(now)
    if stamp > seed.updated_at:  # fixed-width UTC stamps: string order is time order
        return stamp
    return to_iso(parse_iso(seed.updated_at) + timedelta(microseconds=1))


def reviewed[S: (KbSeed, VoiceSeed)](seed: S, target: ReviewStatus, *, now: datetime) -> S:
    """``seed`` moved to ``target`` by the owner. The ``review`` machine decides
    (:class:`~blogwriter.domain.states.TransitionError`, a 409, otherwise);
    ``status_changed_at`` and ``updated_at`` take :func:`review_stamp`. Unknown fields
    (``migratedFrom``) are kept. Not validated: write it with ``domain.docstore``."""
    REVIEW_MACHINE.transition(seed.status, target)
    stamp = review_stamp(seed, now)
    return seed.model_copy(
        update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
    )
