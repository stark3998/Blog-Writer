"""The stories job types: ``stories.seed`` and ``stories.classify`` (contract W-A3, section 7).

``stories.seed`` (input :class:`SeedJobInput`: ``{seedId, source, origin}``; the text itself is
an ephemeral ``SeedText`` document, so the job input never holds content):

``extract``
    Reads the ``SeedText``, checks it against the blocklist again, masks contact details and
    links, and asks ``Task.SYNTHESIS`` (``Sensitivity.PUBLIC``: the resume and portfolio are
    public) for proposed stories and POVs as structured output. Checkpointed, so a resumed step
    never asks twice.
``classify``
    Builds the candidate entries (known pillars only; a proposal with a blocking local finding
    is skipped) and runs ``services.classifier`` over them. Without the AI-use confirmation or
    a model the entries are saved unchecked (not usable until ``stories.classify`` runs).
``save``
    Creates ``proposed`` entries with deterministic ids ``story-seed-<sha256(text)[:12]>-<n>``
    and ``pov-seed-...`` (``n`` = 1-based position in the extraction; a ``Conflict`` means
    already saved), stamped with ``distinct_stamps``; emits one ``stories.seeded`` event and
    deletes the ``SeedText``.

``stories.classify`` (input :class:`ClassifyJobInput`): every story and POV whose verdict is
missing, stale or "could not judge" goes through the classifier in batches (inside
``defer_on_brake``); each verdict is written with ``update_doc`` only if the entry's text is
still the text that was judged.

No schedule entries: both jobs start from owner actions.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import regex
from pydantic import Field, JsonValue, ValidationError

from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.db import Conflict, NotFound, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import DocId, Sha256Hex, distinct_stamps, sha256_hex, to_iso
from blogwriter.domain.common import ClassifierCheck, ServedByRecord
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    check_texts,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    update_doc,
)
from blogwriter.domain.ephemeral import SeedText
from blogwriter.domain.jobflow import defer_on_brake, require_service, step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    STORIES_CLASSIFY,
    STORIES_SEED,
    JobInput,
    JsonPayload,
    entity_changed,
)
from blogwriter.domain.ports import ScheduleEntry, ServiceUnavailable
from blogwriter.domain.settings import Blocklist, CadenceSettings, PillarSettings
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext
from blogwriter.stories._detect import figure_spans
from blogwriter.stories._logging import logger
from blogwriter.stories._prompts import SEED_EXTRACT, fenced, load_prompt
from blogwriter.stories.bank import (
    entry_text,
    needs_classifier,
    pov_text_fields,
    story_text_fields,
)
from blogwriter.stories.intake import blocking_spans, local_findings, mask_text

__all__ = [
    "CLASSIFY_BATCH",
    "SEEDED_EVENT",
    "ClassifyJobInput",
    "SeedExtraction",
    "SeedJobInput",
    "SeedMetric",
    "SeedPov",
    "SeedStory",
    "classify_entries",
    "register_jobs",
    "schedule_entries",
    "seed_entry_id",
]

SEEDED_EVENT: Final = "stories.seeded"
CLASSIFIED_EVENT: Final = "stories.classified"
PROGRESS_EVENT: Final = "progress"
CLASSIFY_BATCH: Final = 8
"""Entries per classifier call in ``stories.classify``."""
_SEED_STEP: Final = "seed_extract"
_SLUG_SPLIT: Final = regex.compile(r"[^a-z0-9]+")


class SeedJobInput(JobInput):
    """``stories.seed``: a reference to the ``SeedText`` and where it came from."""

    seed_id: DocId
    source: Literal["cv", "portfolio"]


class ClassifyJobInput(JobInput):
    """``stories.classify``: no parameters beyond ``origin``."""


# --------------------------------------------------------------------------------------------
# Structured output of the extraction (limits match the stored documents)
# --------------------------------------------------------------------------------------------


class SeedMetric(ApiModel):
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=60)


class SeedStory(ApiModel):
    title: str = Field(min_length=1, max_length=120)
    problem: str = Field(default="", max_length=2000)
    actions: str = Field(default="", max_length=2000)
    decisions: str = Field(default="", max_length=2000)
    metrics: list[SeedMetric] = Field(default_factory=list[SeedMetric], max_length=10)
    lessons: str = Field(default="", max_length=2000)
    pillars: list[str] = Field(default_factory=list[str], max_length=3)
    tags: list[str] = Field(default_factory=list[str], max_length=10)


class SeedPov(ApiModel):
    pillar: str = Field(min_length=1, max_length=40)
    statement: str = Field(min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=1000)


class SeedExtraction(ApiModel):
    stories: list[SeedStory] = Field(default_factory=list[SeedStory], max_length=12)
    povs: list[SeedPov] = Field(default_factory=list[SeedPov], max_length=12)


class _ExtractOutput(JsonPayload):
    seed_sha256: Sha256Hex
    extraction: SeedExtraction
    served_by: ServedByRecord


class _Candidate(JsonPayload):
    kind: Literal["story", "pov"]
    index: int = Field(ge=0)
    sha256: Sha256Hex
    verdict: ClassifierCheck | None = None


class _ClassifyOutput(JsonPayload):
    candidates: list[_Candidate] = Field(default_factory=list[_Candidate])
    skipped: int = 0


def seed_entry_id(kind: Literal["story", "pov"], seed_sha256: str, n: int) -> str:
    """``story-seed-<sha256(text)[:12]>-<n>`` / ``pov-seed-...`` (``n`` starts at 1)."""
    if n < 1:
        raise ValueError("seed entries are numbered from 1")
    return f"{kind}-seed-{seed_sha256[:12]}-{n}"


# --------------------------------------------------------------------------------------------
# stories.seed
# --------------------------------------------------------------------------------------------


def _pillar_lines(pillars: PillarSettings, blocklist: Blocklist) -> str:
    """The pillars as ``- id: name (topics)`` lines; owner-editable text that hits the
    blocklist is left out (only the id is sent), and a blocklisted id drops the pillar."""
    lines: list[str] = []
    for pillar in pillars.pillars:
        bare = f"- {pillar.id}"
        if blocklist_findings(bare, blocklist):
            continue
        detail = f"{pillar.name} ({pillar.topics})" if pillar.topics else pillar.name
        line = f"{bare}: {detail}"
        lines.append(bare if blocklist_findings(line, blocklist) else line)
    return "\n".join(lines)


async def _extract(ctx: StepContext) -> JsonValue:
    if ctx.output is not None:
        return ctx.output
    services = step_services()
    job = SeedJobInput.from_json(ctx.input)
    runner = require_service(services.ai, "ai")
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    try:
        seed = (await read_doc(store, SeedText, job.seed_id)).doc
    except NotFound:
        raise JobFailed("seed_missing", "The uploaded text is gone. Upload it again.") from None
    if seed.expired(now):
        raise JobFailed("seed_expired", "The uploaded text expired. Upload it again.")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    try:
        check_texts({"text": seed.text}, blocklist)
    except BlocklistNotConfigured:
        raise JobFailed("blocklist_not_configured") from None
    except ConfidentialText:
        raise JobFailed("blocklist_hit", "The text matches the blocklist.") from None
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    masked = mask_text(seed.text, blocking_spans(local_findings(seed.text, blocklist)), blocklist)
    prompt = load_prompt(SEED_EXTRACT)
    user = (
        f"Source: {'resume' if seed.source == 'cv' else 'portfolio'}\n"
        f"Pillars:\n{_pillar_lines(pillars, blocklist)}\n\n" + fenced("source_text", masked)
    )
    request = Request(
        system=prompt.text,
        messages=(Message.user(user),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=SeedExtraction,
    )
    with defer_on_brake(now):
        result = await runner.run(Task.SYNTHESIS, request, origin=job.origin, job_id=ctx.job_id)
    if result.refused:
        raise JobFailed("refused", "The model declined to read this text.")
    extraction = result.value
    if not isinstance(extraction, SeedExtraction):
        raise JobFailed("invalid_output", "The model returned no proposals.")
    record = ServedByRecord.from_served_by(
        result.served_by,
        step=_SEED_STEP,
        at=stamp,
        refused=bool(result.refusals),
        cost_usd=float(result.cost_usd),
        prompt_version=prompt.version,
    )
    output = _ExtractOutput(
        seed_sha256=seed.sha256, extraction=extraction, served_by=record
    ).to_json()
    await ctx.checkpoint(output)
    return output


def _tags(raw: Sequence[str]) -> list[str]:
    tags: list[str] = []
    for tag in raw:
        slug = "-".join(part for part in _SLUG_SPLIT.split(tag.casefold()) if part)[:40].strip("-")
        if slug and slug not in tags:
            tags.append(slug)
    return tags[:10]


def _known(pillar_ids: Sequence[str], pillars: PillarSettings) -> list[str]:
    known = {pillar.id for pillar in pillars.pillars}
    return list(dict.fromkeys(pid.strip() for pid in pillar_ids if pid.strip() in known))[:3]


@dataclass(frozen=True, slots=True)
class _Built:
    kind: Literal["story", "pov"]
    index: int
    entry: StoryEntry | PovEntry


def _build(
    extract: _ExtractOutput,
    pillars: PillarSettings,
    *,
    source: Literal["cv", "portfolio"],
    stamp: str,
    kind: Literal["story", "pov"],
    index: int,
) -> StoryEntry | PovEntry | None:
    """The entry for one proposal, or ``None`` when no known pillar fits it."""
    served_by = extract.served_by.model_dump(mode="json", by_alias=True)
    common: dict[str, object] = {
        "createdAt": stamp,
        "updatedAt": stamp,
        "status": ReviewStatus.PROPOSED.value,
        "statusChangedAt": stamp,
        "origin": "seed",
        "confidentiality": "public",
        # Provenance of the extraction call (``StoryEntry.served_by`` / ``PovEntry.served_by``).
        "servedBy": served_by,
    }
    if kind == "story":
        story = extract.extraction.stories[index]
        story_pillars = _known(story.pillars, pillars)
        if not story_pillars:
            return None
        metrics = [
            StoryMetric(
                label=metric.label.strip(),
                value=metric.value.strip(),
                bucketed=not any(figure_spans(metric.value)),
            )
            for metric in story.metrics
            if metric.label.strip() and metric.value.strip()
        ]
        return StoryEntry.model_validate(
            {
                **common,
                "id": seed_entry_id("story", extract.seed_sha256, index + 1),
                "title": story.title.strip(),
                "problem": story.problem.strip(),
                "actions": story.actions.strip(),
                "decisions": story.decisions.strip(),
                "metrics": [metric.model_dump(mode="json", by_alias=True) for metric in metrics],
                "lessons": story.lessons.strip(),
                "pillars": story_pillars,
                "tags": _tags(story.tags),
                "seedSource": source,
            }
        )
    pov = extract.extraction.povs[index]
    pov_pillars = _known([pov.pillar], pillars)
    if not pov_pillars:
        return None
    context = (pov.context or "").strip() or None
    return PovEntry.model_validate(
        {
            **common,
            "id": seed_entry_id("pov", extract.seed_sha256, index + 1),
            "pillar": pov_pillars[0],
            "statement": pov.statement.strip(),
            "context": context,
        }
    )


def _text_fields(entry: StoryEntry | PovEntry) -> dict[str, str]:
    if isinstance(entry, StoryEntry):
        return story_text_fields(
            title=entry.title,
            problem=entry.problem,
            actions=entry.actions,
            decisions=entry.decisions,
            metrics=entry.metrics,
            lessons=entry.lessons,
            tags=entry.tags,
        )
    return pov_text_fields(statement=entry.statement, context=entry.context)


def _candidates(
    extract: _ExtractOutput,
    pillars: PillarSettings,
    blocklist: Blocklist,
    *,
    source: Literal["cv", "portfolio"],
    stamp: str,
) -> tuple[list[_Built], int]:
    """The proposals worth saving, and how many were skipped (no pillar, or a blocking local
    finding such as a blocklisted term, an e-mail address or a link)."""
    kept: list[_Built] = []
    skipped = 0
    kinds: list[tuple[Literal["story", "pov"], int]] = [
        *(("story", i) for i in range(len(extract.extraction.stories))),
        *(("pov", i) for i in range(len(extract.extraction.povs))),
    ]
    for kind, index in kinds:
        try:
            entry = _build(extract, pillars, source=source, stamp=stamp, kind=kind, index=index)
        except ValidationError:
            entry = None
        blocked = entry is None or any(
            finding.severity == "block"
            for text in _text_fields(entry).values()
            for finding in local_findings(text, blocklist)
        )
        if blocked or entry is None:
            skipped += 1
            continue
        kept.append(_Built(kind=kind, index=index, entry=entry))
    return kept, skipped


async def _seed_classify(ctx: StepContext) -> JsonValue:
    services = step_services()
    job = SeedJobInput.from_json(ctx.input)
    extract = _ExtractOutput.from_json(ctx.outputs["extract"])
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    kept, skipped = _candidates(extract, pillars, blocklist, source=job.source, stamp=stamp)
    texts = [entry_text(built.entry) for built in kept]
    verdicts: list[ClassifierCheck | None] = [None] * len(kept)
    classifier = services.classifier
    if classifier is not None and texts:
        try:
            with defer_on_brake(now):
                checks = await classifier.classify(texts, origin=job.origin, job_id=ctx.job_id)
        except (AiUseNotConfirmed, ServiceUnavailable):
            checks = []  # saved unchecked: not usable until stories.classify runs
        if len(checks) == len(texts):
            verdicts = list(checks)
    output = _ClassifyOutput(
        candidates=[
            _Candidate(kind=built.kind, index=built.index, sha256=sha256_hex(text), verdict=verdict)
            for built, text, verdict in zip(kept, texts, verdicts, strict=True)
        ],
        skipped=skipped,
    )
    return output.to_json()


async def _create(store: Store, entry: StoryEntry | PovEntry) -> bool:
    """Create ``entry``; ``False`` when it exists already (a resumed or repeated seed)."""
    try:
        await create_doc(store, entry)
    except Conflict:
        return False
    return True


async def _save(ctx: StepContext) -> JsonValue:
    services = step_services()
    job = SeedJobInput.from_json(ctx.input)
    extract = _ExtractOutput.from_json(ctx.outputs["extract"])
    classified = _ClassifyOutput.from_json(ctx.outputs["classify"])
    store = services.store
    now = services.clock.now()
    pillars, _ = await read_singleton(store, PillarSettings, now=to_iso(now))
    stamps = distinct_stamps(now, len(classified.candidates))
    created: dict[str, list[str]] = {"story": [], "pov": []}
    existing = 0
    skipped = classified.skipped
    for candidate, stamp in zip(classified.candidates, stamps, strict=True):
        entry = _build(
            extract,
            pillars,
            source=job.source,
            stamp=stamp,
            kind=candidate.kind,
            index=candidate.index,
        )
        if entry is None:
            skipped += 1
            continue
        verdict = candidate.verdict
        if verdict is not None and sha256_hex(entry_text(entry)) == candidate.sha256:
            entry = entry.model_copy(update={"classifier": verdict})
        if await _create(store, entry):
            created[candidate.kind].append(entry.id)
        else:
            existing += 1
    if created["story"]:
        await ctx.emit(ENTITY_CHANGED, entity_changed("story", created["story"][0]))
    if created["pov"]:
        await ctx.emit(ENTITY_CHANGED, entity_changed("pov", created["pov"][0]))
    count = len(created["story"]) + len(created["pov"])
    await ctx.emit(
        SEEDED_EVENT,
        {
            "count": count,
            "stories": len(created["story"]),
            "povs": len(created["pov"]),
            "existing": existing,
            "skipped": skipped,
        },
    )
    with contextlib.suppress(NotFound):
        await delete_doc(store, SeedText, job.seed_id)
    logger.info(
        "stories_seeded",
        extra={
            "job_id": ctx.job_id,
            "created_count": count,
            "existing": existing,
            "skipped": skipped,
        },
    )
    return {"count": count, "existing": existing, "skipped": skipped}


# --------------------------------------------------------------------------------------------
# stories.classify
# --------------------------------------------------------------------------------------------

type Emit = Callable[[str, Mapping[str, JsonValue]], Awaitable[None]]


async def _entries_to_classify(store: Store) -> list[Stored[StoryEntry] | Stored[PovEntry]]:
    stories = await find_docs(store, StoryEntry)
    povs = await find_docs(store, PovEntry)
    pending: list[Stored[StoryEntry] | Stored[PovEntry]] = [
        *(stored for stored in stories if needs_classifier(stored.doc)),
        *(stored for stored in povs if needs_classifier(stored.doc)),
    ]
    pending.sort(key=lambda stored: stored.doc.id)
    return pending


async def _write_verdict(
    store: Store, entry: StoryEntry | PovEntry, check: ClassifierCheck, stamp: str
) -> bool:
    """Store ``check`` on the entry unless its text changed since it was judged (or it was
    deleted meanwhile); ``True`` when it was written."""
    judged = check.content_sha256
    applied: list[bool] = []

    def mutate_story(doc: StoryEntry) -> StoryEntry | None:
        applied.clear()
        if sha256_hex(entry_text(doc)) != judged:
            return None
        applied.append(True)
        return doc.model_copy(update={"classifier": check, "updated_at": stamp})

    def mutate_pov(doc: PovEntry) -> PovEntry | None:
        applied.clear()
        if sha256_hex(entry_text(doc)) != judged:
            return None
        applied.append(True)
        return doc.model_copy(update={"classifier": check, "updated_at": stamp})

    try:
        if isinstance(entry, StoryEntry):
            await update_doc(store, StoryEntry, entry.id, mutate_story)
        else:
            await update_doc(store, PovEntry, entry.id, mutate_pov)
    except NotFound:
        return False
    return bool(applied)


async def classify_entries(
    *,
    origin: Literal["owner", "background"],
    job_id: str | None,
    emit: Emit | None = None,
    batch: int = CLASSIFY_BATCH,
) -> dict[str, JsonValue]:
    """The ``stories.classify`` work (also callable without a job, for tests).

    ``JobFailed`` codes: ``classifier_unavailable``, ``ai_unavailable``,
    ``ai_use_not_confirmed``. Background calls defer on the AI brake (``JobDeferred``).
    """
    services = step_services()
    classifier = require_service(services.classifier, "classifier")
    store = services.store
    pending = await _entries_to_classify(store)
    total = len(pending)
    done = 0
    written = 0
    passed = 0
    for start in range(0, total, batch):
        chunk = pending[start : start + batch]
        texts = [entry_text(stored.doc) for stored in chunk]
        try:
            with defer_on_brake(services.clock.now()):
                checks = await classifier.classify(texts, origin=origin, job_id=job_id)
        except AiUseNotConfirmed:
            raise JobFailed(
                "ai_use_not_confirmed", "Confirm the employer AI-use policy first."
            ) from None
        except ServiceUnavailable as exc:
            raise JobFailed(f"{exc.name}_unavailable", "This feature is not configured.") from None
        stamps = distinct_stamps(services.clock.now(), len(chunk))
        for stored, check, stamp in zip(chunk, checks, stamps, strict=True):
            if await _write_verdict(store, stored.doc, check, stamp):
                written += 1
                passed += 1 if check.passed else 0
                if emit is not None:
                    entity = "story" if isinstance(stored.doc, StoryEntry) else "pov"
                    await emit(ENTITY_CHANGED, entity_changed(entity, stored.doc.id))
        done += len(chunk)
        if emit is not None:
            await emit(PROGRESS_EVENT, {"done": done, "total": total})
    result: dict[str, JsonValue] = {"classified": written, "passed": passed, "total": total}
    if emit is not None:
        await emit(CLASSIFIED_EVENT, result)
    return result


async def _classify_step(ctx: StepContext) -> JsonValue:
    job = ClassifyJobInput.from_json(ctx.input if ctx.input is not None else {})
    return await classify_entries(origin=job.origin, job_id=ctx.job_id, emit=ctx.emit)


# --------------------------------------------------------------------------------------------
# Registration and schedule
# --------------------------------------------------------------------------------------------


def register_jobs(registry: JobRegistry) -> None:
    """Register ``stories.seed`` and ``stories.classify`` (called once by ``blogwriter.main``)."""
    registry.register(
        STORIES_SEED,
        [Step("extract", _extract), Step("classify", _seed_classify), Step("save", _save)],
    )
    registry.register(STORIES_CLASSIFY, [Step("classify", _classify_step)])


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """None: seeding and classification start from owner actions only."""
    return []
