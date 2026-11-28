"""The banks: canonical texts, classifier freshness, ``load_bank`` and ``story_gaps``."""

from __future__ import annotations

from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck, Finding
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.settings import PillarSettings
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.stories import (
    StoryBank,
    entry_text,
    load_bank,
    needs_classifier,
    pov_text,
    story_gaps,
    story_text,
)


def verdict(entry: StoryEntry | PovEntry, *, passed: bool = True) -> ClassifierCheck:
    return ClassifierCheck(
        passed=passed, checked_at=SAMPLE_NOW, content_sha256=sha256_hex(entry_text(entry))
    )


def usable_story(**fields: object) -> StoryEntry:
    story = sample_document(StoryEntry, status=ReviewStatus.APPROVED, **fields)
    return story.model_copy(update={"classifier": verdict(story)})


def usable_pov(**fields: object) -> PovEntry:
    pov = sample_document(PovEntry, status=ReviewStatus.APPROVED, **fields)
    return pov.model_copy(update={"classifier": verdict(pov)})


def test_canonical_texts_cover_every_owner_field() -> None:
    story = sample_document(
        StoryEntry,
        problem="Two IdPs.",
        actions="I moved apps in waves.",
        decisions="SAML last.",
        metrics=[StoryMetric(label="Apps", value="2,000+")],
        lessons="Owners first.",
        tags=["okta"],
    )
    assert story_text(story) == (
        "Title: A large identity migration\n"
        "Problem: Two IdPs.\n"
        "Actions: I moved apps in waves.\n"
        "Decisions and trade-offs: SAML last.\n"
        "Metrics: Apps: 2,000+\n"
        "Lessons: Owners first.\n"
        "Tags: okta"
    )
    pov = sample_document(PovEntry, context="Pilots share an admin account.")
    assert pov_text(pov) == (
        "Statement: Agents need their own identities, not borrowed ones.\n"
        "Context: Pilots share an admin account."
    )
    assert entry_text(story) == story_text(story)
    assert entry_text(pov) == pov_text(pov)


def test_needs_classifier() -> None:
    story = sample_document(StoryEntry)
    assert needs_classifier(story)  # never checked
    checked = story.model_copy(update={"classifier": verdict(story)})
    assert not needs_classifier(checked)
    edited = checked.model_copy(update={"lessons": "A new lesson."})
    assert needs_classifier(edited)  # stale
    refused = story.model_copy(
        update={
            "classifier": verdict(story, passed=False).model_copy(
                update={
                    "findings": [
                        Finding(code="classifier-refused", severity="block", message="Declined.")
                    ]
                }
            )
        }
    )
    assert needs_classifier(refused)  # "could not judge" is tried again
    failed = story.model_copy(update={"classifier": verdict(story, passed=False)})
    assert not needs_classifier(failed)  # a real verdict stays until the text changes


async def test_load_bank_returns_usable_entries_only(services: FakeServices) -> None:
    identity = usable_story(pillars=["identity"])
    agents = usable_story(pillars=["agents", "identity"], title="Agent identity")
    proposed = sample_document(StoryEntry, pillars=["identity"])
    stale = usable_story(pillars=["identity"]).model_copy(update={"lessons": "Edited."})
    flagged = sample_document(StoryEntry, status=ReviewStatus.APPROVED)
    flagged = flagged.model_copy(update={"classifier": verdict(flagged, passed=False)})
    pov = usable_pov(pillar="agents")
    pov_unchecked = sample_document(PovEntry, status=ReviewStatus.APPROVED, pillar="agents")
    for doc in (identity, agents, proposed, stale, flagged, pov, pov_unchecked):
        await create_doc(services.store, doc)

    bank = await load_bank(services.store)
    assert {s.id for s in bank.stories} == {identity.id, agents.id}
    assert [p.id for p in bank.povs] == [pov.id]
    by_pillar = await load_bank(services.store, pillar="agents")
    assert [s.id for s in by_pillar.stories] == [agents.id]
    assert [p.id for p in by_pillar.povs] == [pov.id]
    everything = await load_bank(services.store, usable_only=False)
    assert len(everything.stories) == 5
    assert len(everything.povs) == 2


def test_story_gaps_are_deterministic_codes() -> None:
    pillars = sample_document(PillarSettings)
    complete = usable_story(
        pillars=["identity"],
        metrics=[StoryMetric(label="Apps", value="2,000+")],
        decisions="SAML last.",
        lessons="Owners first.",
    )
    thin = usable_story(pillars=["agents"])
    povs = [usable_pov(pillar="agents") for _ in range(3)] + [usable_pov(pillar="identity")]
    gaps = story_gaps(StoryBank(stories=[complete, thin], povs=povs), pillars)
    expected_story_gaps = [
        f"metrics-missing:{thin.id}",
        f"decisions-missing:{thin.id}",
        f"lessons-missing:{thin.id}",
    ]
    assert gaps[:3] == ["few-povs:identity", "no-stories:governance", "few-povs:governance"]
    assert gaps[3:] == expected_story_gaps
    assert story_gaps(StoryBank(stories=[], povs=[]), pillars) == [
        "no-stories:agents",
        "few-povs:agents",
        "no-stories:identity",
        "few-povs:identity",
        "no-stories:governance",
        "few-povs:governance",
    ]
