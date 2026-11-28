"""The story bank and the POV bank: canonical texts, classifier freshness, loading and gaps.

An entry is **usable** (drafts, posts and gate 6 may use it) only when the owner approved it and
the confidentiality classifier passed the text it has now. The classifier's verdict records the
SHA-256 of the text it judged (:func:`entry_text`); an edit makes it stale, and the stories
routes reset a stale verdict to "not checked yet" in the same write, so ``usable`` never
outlives an edit.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from blogwriter.db import Filter, OrderBy, Store
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck
from blogwriter.domain.docstore import find_docs
from blogwriter.domain.settings import PillarSettings
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric

__all__ = [
    "FEW_POVS",
    "RETRYABLE_CODES",
    "StoryBank",
    "entry_text",
    "load_bank",
    "needs_classifier",
    "pov_text",
    "pov_text_fields",
    "story_gaps",
    "story_text",
    "story_text_fields",
    "verdict_current",
]

FEW_POVS: Final = 3
"""The plan's minimum: at least three POV entries per pillar."""

RETRYABLE_CODES: Final = frozenset({"classifier-refused", "classifier-incomplete"})
"""Classifier findings that say "could not judge": ``stories.classify`` tries those again."""


def _metrics_text(metrics: Sequence[StoryMetric]) -> str:
    return "; ".join(f"{metric.label}: {metric.value}" for metric in metrics)


def story_text_fields(
    *,
    title: str,
    problem: str,
    actions: str,
    decisions: str,
    metrics: Sequence[StoryMetric],
    lessons: str,
    tags: Sequence[str],
) -> dict[str, str]:
    """A story's owner-written text by field (what the blocklist and entity checks read)."""
    fields: dict[str, str] = {
        "title": title,
        "problem": problem,
        "actions": actions,
        "decisions": decisions,
        "lessons": lessons,
    }
    for index, metric in enumerate(metrics):
        fields[f"metrics[{index}].label"] = metric.label
        fields[f"metrics[{index}].value"] = metric.value
    for index, tag in enumerate(tags):
        fields[f"tags[{index}]"] = tag
    return fields


def pov_text_fields(*, statement: str, context: str | None) -> dict[str, str]:
    """A POV's owner-written text by field."""
    fields = {"statement": statement}
    if context:
        fields["context"] = context
    return fields


def story_text(story: StoryEntry) -> str:
    """The canonical text of a story: what the classifier judges and what its hash covers."""
    sections = [
        ("Title", story.title),
        ("Problem", story.problem),
        ("Actions", story.actions),
        ("Decisions and trade-offs", story.decisions),
        ("Metrics", _metrics_text(story.metrics)),
        ("Lessons", story.lessons),
        ("Tags", ", ".join(story.tags)),
    ]
    return "\n".join(f"{label}: {value}" for label, value in sections if value)


def pov_text(pov: PovEntry) -> str:
    """The canonical text of a POV entry."""
    sections = [("Statement", pov.statement), ("Context", pov.context or "")]
    return "\n".join(f"{label}: {value}" for label, value in sections if value)


def entry_text(entry: StoryEntry | PovEntry) -> str:
    """:func:`story_text` or :func:`pov_text`."""
    return story_text(entry) if isinstance(entry, StoryEntry) else pov_text(entry)


def verdict_current(check: ClassifierCheck, text: str) -> bool:
    """Whether ``check`` is a finished verdict about exactly ``text``."""
    return check.passed is not None and check.content_sha256 == sha256_hex(text)


def needs_classifier(entry: StoryEntry | PovEntry) -> bool:
    """Not checked yet, stale (the text changed) or a "could not judge" verdict."""
    check = entry.classifier
    if not verdict_current(check, entry_text(entry)):
        return True
    return any(finding.code in RETRYABLE_CODES for finding in check.findings)


@dataclass(frozen=True, slots=True)
class StoryBank:
    """Stories and POV entries, newest first."""

    stories: list[StoryEntry]
    povs: list[PovEntry]


def _usable(entry: StoryEntry | PovEntry) -> bool:
    return entry.usable and verdict_current(entry.classifier, entry_text(entry))


async def load_bank(
    store: Store, *, pillar: str | None = None, usable_only: bool = True
) -> StoryBank:
    """The story and POV banks, optionally for one pillar and only usable entries.

    Usable means approved, classifier passed, and the verdict is about the current text.
    Unreadable documents are skipped (lists, not guards).
    """
    newest = OrderBy("updatedAt", descending=True)
    story_filters: list[Filter] = []
    pov_filters: list[Filter] = []
    if usable_only:
        approved = [
            Filter("status", "==", ReviewStatus.APPROVED.value),
            Filter("classifier.passed", "==", True),
        ]
        story_filters.extend(approved)
        pov_filters.extend(approved)
    if pillar is not None:
        pov_filters.append(Filter("pillar", "==", pillar))
    stories = [s.doc for s in await find_docs(store, StoryEntry, story_filters, order_by=newest)]
    povs = [p.doc for p in await find_docs(store, PovEntry, pov_filters, order_by=newest)]
    if pillar is not None:
        stories = [story for story in stories if pillar in story.pillars]
    if usable_only:
        stories = [story for story in stories if _usable(story)]
        povs = [pov for pov in povs if _usable(pov)]
    return StoryBank(stories=stories, povs=povs)


def _count_by_pillar(pillar_lists: Iterable[Sequence[str]]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for pillars in pillar_lists:
        for pillar in set(pillars):
            counts[pillar] = counts.get(pillar, 0) + 1
    return counts


def story_gaps(bank: StoryBank, pillars: PillarSettings) -> list[str]:
    """What the bank is missing, as codes (for interview questions and owner inputs).

    Per pillar, in settings order: ``no-stories:<pillar>`` (no story), ``few-povs:<pillar>``
    (fewer than :data:`FEW_POVS` POVs). Per story, by id: ``metrics-missing:<storyId>``,
    ``decisions-missing:<storyId>``, ``lessons-missing:<storyId>``. Deterministic.
    """
    stories = _count_by_pillar(story.pillars for story in bank.stories)
    povs = _count_by_pillar([pov.pillar] for pov in bank.povs)
    gaps: list[str] = []
    for pillar in pillars.pillars:
        if stories.get(pillar.id, 0) == 0:
            gaps.append(f"no-stories:{pillar.id}")
        if povs.get(pillar.id, 0) < FEW_POVS:
            gaps.append(f"few-povs:{pillar.id}")
    for story in sorted(bank.stories, key=lambda entry: entry.id):
        if not story.metrics:
            gaps.append(f"metrics-missing:{story.id}")
        if not story.decisions.strip():
            gaps.append(f"decisions-missing:{story.id}")
        if not story.lessons.strip():
            gaps.append(f"lessons-missing:{story.id}")
    return gaps
