"""The v1 migration's proposals (``domain.seeds``): one model shared with the migration, decisions
that follow the review machine, and a decision stamp a re-run of the migration never mistakes for
its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from blogwriter.backups.migrate_v1 import seeds as migration_seeds
from blogwriter.domain import DOCUMENT_TYPES, KbSeed, VoiceSeed
from blogwriter.domain.base import to_iso
from blogwriter.domain.seeds import review_stamp, reviewed
from blogwriter.domain.states import ReviewStatus, TransitionError
from blogwriter.domain.testing import SAMPLE_NOW, sample_document

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
"""The time of :data:`SAMPLE_NOW`."""
P = ReviewStatus.PROPOSED
A = ReviewStatus.APPROVED
R = ReviewStatus.REJECTED
X = ReviewStatus.ARCHIVED


def test_the_migration_writes_the_domain_models() -> None:
    assert migration_seeds.KbSeed is KbSeed
    assert migration_seeds.VoiceSeed is VoiceSeed
    assert {KbSeed, VoiceSeed} <= set(DOCUMENT_TYPES)
    assert (KbSeed.CONTAINER, KbSeed.KIND, KbSeed.OWNER) == ("settings", "kb_seed", "kb")
    assert (VoiceSeed.CONTAINER, VoiceSeed.KIND, VoiceSeed.OWNER) == (
        "stories",
        "voice_seed",
        "voice",
    )
    assert KbSeed.id_for("vp-1") == KbSeed.id_for("vp-1") != VoiceSeed.id_for("vp-1")


def test_new_seeds_are_proposed_and_carry_no_outcome() -> None:
    kb = sample_document(KbSeed)
    voice = sample_document(VoiceSeed)
    assert (kb.status, voice.status) == (P, P)
    assert (kb.applied_pillar, kb.applied_keywords, voice.sample_id) == (None, [], None)
    with pytest.raises(ValidationError):
        sample_document(KbSeed, applied_keywords=["x" * 81])
    with pytest.raises(ValidationError):
        sample_document(KbSeed, applied_pillar="Not A Slug")
    with pytest.raises(ValidationError):
        sample_document(VoiceSeed, sample_id="has/slash")


@pytest.mark.parametrize(("start", "target"), [(P, A), (P, R), (R, P), (A, X), (X, A)])
def test_decisions_follow_the_review_machine(start: ReviewStatus, target: ReviewStatus) -> None:
    later = T0 + timedelta(hours=2)
    kb = sample_document(KbSeed, status=start)
    voice = sample_document(VoiceSeed, status=start)
    decided_kb = reviewed(kb, target, now=later)
    decided_voice = reviewed(voice, target, now=later)
    assert KbSeed.from_document(decided_kb.to_document()) == decided_kb
    assert VoiceSeed.from_document(decided_voice.to_document()) == decided_voice
    for decided in (decided_kb, decided_voice):
        assert decided.status is target
        assert decided.status_changed_at == decided.updated_at == to_iso(later)
    assert (kb.status, voice.status) == (start, start)  # copies; nothing changed in place


@pytest.mark.parametrize(("start", "target"), [(A, A), (A, R), (A, P), (R, A), (P, P), (P, X)])
def test_other_moves_are_refused(start: ReviewStatus, target: ReviewStatus) -> None:
    seed = sample_document(VoiceSeed, status=start)
    with pytest.raises(TransitionError) as caught:
        reviewed(seed, target, now=T0 + timedelta(hours=1))
    assert caught.value.machine == "review"
    assert (caught.value.current, caught.value.target) == (start.value, target.value)


def test_a_decision_always_moves_updated_at_forward() -> None:
    seed = sample_document(KbSeed)
    assert seed.updated_at == SAMPLE_NOW
    after = "2026-11-26T12:00:00.000001Z"
    assert review_stamp(seed, T0) == after  # same instant: one microsecond later
    assert review_stamp(seed, T0 - timedelta(days=30)) == after  # a clock behind the stamp
    assert review_stamp(seed, T0 + timedelta(seconds=1)) == "2026-11-26T12:00:01.000000Z"
    decided = reviewed(seed, R, now=T0)
    assert (decided.updated_at, decided.status_changed_at) == (after, after)


def test_the_migration_marker_survives_and_no_longer_matches() -> None:
    marker = {"source": "v1", "container": "keywords", "id": "cloud security"}
    marker["updatedAt"] = SAMPLE_NOW
    body = {**sample_document(KbSeed).to_document(), "migratedFrom": marker}
    seed = KbSeed.from_document(body)
    decided = reviewed(seed, A, now=T0).model_copy(
        update={"applied_pillar": "identity", "applied_keywords": ["zero trust"]}
    )
    written = KbSeed.from_document(decided.to_document()).to_document()
    assert written["migratedFrom"] == marker
    # The migration replaces a seed only while updatedAt equals the marker's.
    assert written["updatedAt"] != marker["updatedAt"]
    assert (written["appliedPillar"], written["appliedKeywords"]) == ("identity", ["zero trust"])
