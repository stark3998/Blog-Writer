"""The voice corpus: adding, re-scoring and counting voice samples (container ``stories``).

Samples come from the owner (portfolio posts and About, 5-10 past LinkedIn posts or comments),
from the pipeline (confirmed interview answers, through :func:`add_sample`) and from the digest
(the final text of posts published through the app). Every sample is scored on write
(:func:`blogwriter.voice.scoring.score_text`).

Callers clear owner text against the blocklist before :func:`add_sample` (the route runs
``check_texts`` like every intake save; the digest checks final texts); the guide build checks
every sample again before any model call, so a sample saved before a term was added to the
blocklist never reaches a model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final

from blogwriter.db import Conflict, Filter, Store
from blogwriter.domain.base import derived_id, distinct_stamps, sha256_hex, to_iso
from blogwriter.domain.docstore import Stored, create_doc, find_docs, read_doc, update_doc
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.settings import VoicePreferences
from blogwriter.domain.states import SampleStatus
from blogwriter.domain.voice import VoiceSample, VoiceSourceType
from blogwriter.voice.scoring import TextScore, sample_weight, score_text
from blogwriter.voice.text import MAX_SAMPLE_CHARS, clip, normalize_text

__all__ = [
    "MAX_TITLE_CHARS",
    "MIN_LINKEDIN_SAMPLES",
    "active_samples",
    "add_sample",
    "apply_score",
    "count_linkedin_samples",
    "linkedin_sample_count",
    "required_linkedin_samples",
    "rescore_samples",
    "sample_id_for",
]

MIN_LINKEDIN_SAMPLES: Final = 5
"""The plan's floor: at least 5 past LinkedIn posts the owner wrote by hand before a guide can be
built (``VoicePreferences.min_linkedin_samples`` may raise it, never lower it)."""
MAX_TITLE_CHARS: Final = 200


def required_linkedin_samples(preferences: VoicePreferences) -> int:
    """How many active LinkedIn post samples a guide build needs."""
    return max(MIN_LINKEDIN_SAMPLES, preferences.min_linkedin_samples)


def sample_id_for(source_type: VoiceSourceType, source_ref: str, text: str) -> str:
    """The deterministic id of a sample made from ``source_ref`` (an interview or a post), so
    a resumed job step that adds it again gets a ``Conflict`` instead of a duplicate."""
    return derived_id("sample", source_type, source_ref, sha256_hex(text))


def apply_score(sample: VoiceSample, score: TextScore) -> VoiceSample:
    """``sample`` with the scoring fields of ``score`` (not validated: write with docstore)."""
    return sample.model_copy(
        update={
            "word_count": score.word_count,
            "ai_tell_score": score.ai_tell_score,
            "flags": list(score.flags),
            "exemplar_eligible": score.exemplar_eligible,
        }
    )


async def add_sample(
    store: Store,
    *,
    source_type: VoiceSourceType,
    text: str,
    now: datetime,
    slop: SlopChecker | None,
    title: str | None = None,
    url: str | None = None,
    posted_at: str | None = None,
    pillar: str | None = None,
    owner_unassisted: bool = False,
    source_ref: str | None = None,
) -> Stored[VoiceSample]:
    """Score and store a new voice sample.

    ``text`` is normalized (line endings, control characters, surrounding whitespace) and cut
    to 20,000 characters; ``ValueError`` when nothing is left. With ``source_ref`` (the
    pipeline's interview id, the digest's post id) the id is derived from the source and the
    text, so calling it again with the same text returns the stored sample (retry-safe).
    """
    clean = normalize_text(text)[:MAX_SAMPLE_CHARS].rstrip()
    if not clean:
        raise ValueError("a voice sample needs text")
    heading = clip(title, MAX_TITLE_CHARS) if title is not None else ""
    score = await asyncio.to_thread(score_text, clean, slop=slop)
    stamp = to_iso(now)
    sample_id = (
        sample_id_for(source_type, source_ref, clean)
        if source_ref is not None
        else VoiceSample.new_id(now=now)
    )
    sample = VoiceSample(
        id=sample_id,
        created_at=stamp,
        updated_at=stamp,
        status=SampleStatus.ACTIVE,
        status_changed_at=stamp,
        source_type=source_type,
        owner_unassisted=owner_unassisted,
        title=heading or None,
        text=clean,
        url=url,
        posted_at=posted_at,
        pillar=pillar,
        weight=sample_weight(source_type, owner_unassisted),
        content_sha256=sha256_hex(clean),
        source_ref=source_ref,
        word_count=0,
    )
    try:
        return await create_doc(store, apply_score(sample, score))
    except Conflict:
        if source_ref is None:
            raise
        return await read_doc(store, VoiceSample, sample_id)


async def active_samples(store: Store, *, strict: bool = False) -> list[Stored[VoiceSample]]:
    """Every active sample, sorted by id (a stable order for prompts and checkpoints)."""
    found = await find_docs(
        store, VoiceSample, [Filter("status", "==", SampleStatus.ACTIVE.value)], strict=strict
    )
    return sorted(found, key=lambda stored: stored.doc.id)


def count_linkedin_samples(samples: Sequence[VoiceSample]) -> int:
    """Active ``linkedin_post`` samples with distinct text (pasting one post twice counts once)."""
    return len(
        {
            sample.content_sha256 or sha256_hex(sample.text)
            for sample in samples
            if sample.status is SampleStatus.ACTIVE and sample.source_type == "linkedin_post"
        }
    )


async def linkedin_sample_count(store: Store) -> int:
    """How many past LinkedIn posts the owner added (active, distinct text)."""
    found = await find_docs(
        store,
        VoiceSample,
        [
            Filter("status", "==", SampleStatus.ACTIVE.value),
            Filter("sourceType", "==", "linkedin_post"),
        ],
    )
    return count_linkedin_samples([stored.doc for stored in found])


async def rescore_samples(
    store: Store,
    samples: Sequence[Stored[VoiceSample]],
    *,
    slop: SlopChecker | None,
    now: datetime,
) -> list[str]:
    """Re-score ``samples`` (for example once the structural detectors are available) and
    write those whose scoring changed. Returns the ids written.

    A sample edited meanwhile (its text hash changed) is left alone: the edit re-scored it.
    """
    pending: list[tuple[VoiceSample, TextScore]] = []
    for stored in samples:
        sample = stored.doc
        score = await asyncio.to_thread(score_text, sample.text, slop=slop)
        rescored = apply_score(sample, score)
        if _scoring(rescored) != _scoring(sample):
            pending.append((sample, score))
    stamps = distinct_stamps(now, len(pending))
    changed: list[str] = []
    for (sample, score), stamp in zip(pending, stamps, strict=True):
        expected = sample.content_sha256 or sha256_hex(sample.text)
        mutate = _rescored(score, stamp=stamp, expected_sha256=expected)
        written = await update_doc(store, VoiceSample, sample.id, mutate)
        if written.doc.updated_at == stamp:
            changed.append(sample.id)
    return changed


def _rescored(
    score: TextScore, *, stamp: str, expected_sha256: str
) -> Callable[[VoiceSample], VoiceSample | None]:
    def mutate(current: VoiceSample) -> VoiceSample | None:
        if (current.content_sha256 or sha256_hex(current.text)) != expected_sha256:
            return None
        updated = apply_score(current, score)
        if _scoring(updated) == _scoring(current):
            return None
        return updated.model_copy(update={"updated_at": stamp})

    return mutate


def _scoring(sample: VoiceSample) -> tuple[object, ...]:
    return (
        sample.word_count,
        sample.ai_tell_score,
        tuple(finding.model_dump_json() for finding in sample.flags),
        sample.exemplar_eligible,
    )
