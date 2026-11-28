"""The ``triage`` step of ``signals.crawl`` and ``signals.scout``: TRIAGE in batches -> ideas.

**Feed items never generate content without an owner action.** Triage only files ideas in the
inbox (and marks signals); it never starts a draft, a post or any other job. The owner decides
what happens to an idea.

Per batch of ``TRIAGE_BATCH`` distinct URLs (candidates are grouped by ``url_sha256``, so one
URL seen in two sources is triaged once):

1. **Blocklist screen.** A candidate whose title, URL, publisher or pillar hint matches the
   blocklist never reaches the model: its signals are marked triaged without an idea. The
   pillar and format texts the prompt carries are checked as a whole first (owner-editable text
   bound for a model): a hit fails the job ``blocklist_hit``, an unsaved blocklist
   ``blocklist_not_configured``.
2. **Known URLs.** A URL that already has an idea (any origin, quick capture included) is not
   triaged again.
3. **TRIAGE** (``gpt-6-luna``; public, tool-free, structured output) inside ``defer_on_brake``:
   pillar, relevance, novelty, lane, format, angle, time sensitivity, reasons.
4. Ideas for relevance >= ``MIN_RELEVANCE`` (origin ``feed`` or ``scout``, id
   ``idea-<urlSha256[:24]>``, a ``Conflict`` means it exists; ``distinct_stamps`` per batch).
   Angles that break the slop rules are dropped (the idea stays).
5. Signals marked ``triaged`` (with the idea id), then one ``entity.changed`` per batch.

A batch whose output is refused or invalid leaves its signals untriaged: the next crawl that
sees those items queues them again. The checkpoint holds the next batch index and counters only.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from pydantic import Field, JsonValue, ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import RouterResult
from blogwriter.ai.schema import StructuredOutputError
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import distinct_stamps, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_findings,
    blocklist_hit,
    check_texts,
)
from blogwriter.domain.docstore import create_doc, find_docs, read_singleton, update_doc
from blogwriter.domain.jobflow import defer_on_brake
from blogwriter.domain.jobtypes import ENTITY_CHANGED, entity_changed
from blogwriter.domain.ports import ModelRunner, Services
from blogwriter.domain.settings import Blocklist, FormatPacks, PillarSettings
from blogwriter.domain.signals import Idea, Signal, TriageInfo
from blogwriter.domain.states import IdeaStatus
from blogwriter.jobs.runner import JobFailed
from blogwriter.signals._text import clean_text, prompt_json, slop_codes
from blogwriter.signals.candidates import Candidate
from blogwriter.signals.ideas import (
    POST_FORMAT_SUGGESTIONS,
    expires_for,
    idea_id_for_url,
    quick_take_hours,
)
from blogwriter.signals.prompts import TRIAGE_PROMPT, load_prompt
from blogwriter.signals.settings import SignalsSettings

__all__ = [
    "INBOX_CONTEXT",
    "PROMPT_DESCRIPTION_CHARS",
    "TriageContext",
    "TriageOutput",
    "TriageProgress",
    "TriageVerdict",
    "build_idea",
    "check_prompt_settings",
    "group_candidates",
    "load_triage_context",
    "mark_signals",
    "triage_candidates",
    "triage_request",
]

INBOX_CONTEXT: Final = 25
"""Recent inbox titles shown to the model, so it can score duplicates as not novel."""
PROMPT_DESCRIPTION_CHARS: Final = 400
"""Pillar descriptions and audiences are cut to this length in the prompt."""
_MAX_ANGLE: Final = 500
_MAX_REASONS: Final = 500

type IdeaFeedOrigin = Literal["feed", "scout"]
type EmitFn = Callable[[str, Mapping[str, JsonValue]], Awaitable[None]]

_logger: Final = logging.getLogger("blogwriter.signals.triage")
register_log_fields("batch", "ideas_created", "candidates", "cost_usd")


class TriageVerdict(ApiModel):
    """The model's verdict on one item (structured output; plain fields only)."""

    item: int = Field(ge=1, description="The item's number in <items>.")
    pillar: str | None = Field(default=None, description="A pillar id from <pillars>, or null.")
    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    lane: Literal["flagship", "quick_take", "native"] | None = None
    format: str | None = Field(default=None, description="A format id from <formats>, or null.")
    angle: str | None = Field(default=None, description="One plain sentence, at most 30 words.")
    time_sensitive: bool = False
    reasons: str | None = Field(default=None, description="At most 25 words.")


class TriageOutput(ApiModel):
    verdicts: list[TriageVerdict]


@dataclass(frozen=True, slots=True)
class TriageContext:
    """What every batch of one step shares: the blocklist and the checked prompt sections."""

    blocklist: Blocklist
    pillars: tuple[dict[str, str], ...]
    pillar_ids: frozenset[str]
    flagship_formats: tuple[dict[str, str], ...]
    flagship_ids: frozenset[str]
    inbox: tuple[str, ...]
    quick_take_hours: int


MAX_REPORTED_IDS: Final = 50
_PROGRESS_COUNTS: Final = (
    ("next_batch", "nextBatch"),
    ("candidates", "candidates"),
    ("triaged", "triaged"),
    ("ideas", "ideas"),
    ("existing", "existing"),
    ("below_threshold", "belowThreshold"),
    ("blocked", "blocked"),
    ("failed_batches", "failedBatches"),
    ("refused_batches", "refusedBatches"),
)


@dataclass
class TriageProgress:
    """The triage step's checkpoint and output (counts and ids only)."""

    next_batch: int = 0
    candidates: int = 0
    triaged: int = 0
    ideas: int = 0
    existing: int = 0
    below_threshold: int = 0
    blocked: int = 0
    failed_batches: int = 0
    refused_batches: int = 0
    idea_ids: list[str] = field(default_factory=list[str])

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {key: getattr(self, name) for name, key in _PROGRESS_COUNTS}
        out["ideaIds"] = list(self.idea_ids[-MAX_REPORTED_IDS:])
        return out

    @classmethod
    def from_json(cls, value: JsonValue) -> TriageProgress:
        progress = cls()
        if not isinstance(value, dict):
            return progress
        for name, key in _PROGRESS_COUNTS:
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                setattr(progress, name, item)
        ids = value.get("ideaIds")
        if isinstance(ids, list):
            progress.idea_ids = [item for item in ids if isinstance(item, str)]
        return progress


# --------------------------------------------------------------------------------------------
# Context and request
# --------------------------------------------------------------------------------------------


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _pillar_entries(pillars: PillarSettings) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for pillar in pillars.pillars:
        entry = {"id": pillar.id, "name": pillar.name}
        if pillar.topics.strip():
            entry["topics"] = _cut(pillar.topics, PROMPT_DESCRIPTION_CHARS)
        if pillar.audience.strip():
            entry["audience"] = _cut(pillar.audience, PROMPT_DESCRIPTION_CHARS)
        if pillar.description.strip():
            entry["description"] = _cut(pillar.description, PROMPT_DESCRIPTION_CHARS)
        entries.append(entry)
    return entries


def check_prompt_settings(
    texts: Mapping[str, str], blocklist: Blocklist, *, what: str = "A pillar or format setting"
) -> None:
    """``check_texts`` for owner-editable settings bound for a model, as job failures."""
    try:
        check_texts(texts, blocklist)
    except BlocklistNotConfigured:
        raise JobFailed(
            "blocklist_not_configured", "Save the blocklist once in Settings > Policy."
        ) from None
    except ConfidentialText:
        raise JobFailed(
            "blocklist_hit", f"{what} matches the blocklist. Rephrase it in the Library."
        ) from None


async def _inbox_titles(store: Store, blocklist: Blocklist) -> tuple[str, ...]:
    recent = await find_docs(
        store,
        Idea,
        [Filter("status", "in", [s.value for s in (IdeaStatus.NEW, IdeaStatus.ACCEPTED)])],
        order_by=OrderBy("createdAt", descending=True),
        limit=INBOX_CONTEXT,
    )
    titles: list[str] = []
    for stored in recent:
        title = stored.doc.title
        if not blocklist_findings(title, blocklist):
            titles.append(title)
    return tuple(titles)


async def load_triage_context(store: Store, *, now: datetime) -> TriageContext:
    """Read the settings a triage step needs and check the owner-editable parts that go to the
    model (``JobFailed`` ``blocklist_not_configured`` / ``blocklist_hit``)."""
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    packs, _ = await read_singleton(store, FormatPacks, now=stamp)
    entries = _pillar_entries(pillars)
    formats = [{"id": pack.id, "name": pack.name} for pack in packs.packs]
    texts: dict[str, str] = {}
    for index, entry in enumerate(entries):
        for key, value in entry.items():
            texts[f"pillars[{index}].{key}"] = value
    for index, entry in enumerate(formats):
        for key, value in entry.items():
            texts[f"formats[{index}].{key}"] = value
    check_prompt_settings(texts, blocklist)
    return TriageContext(
        blocklist=blocklist,
        pillars=tuple(entries),
        pillar_ids=frozenset(pillar.id for pillar in pillars.pillars),
        flagship_formats=tuple(formats),
        flagship_ids=frozenset(pack.id for pack in packs.packs),
        inbox=await _inbox_titles(store, blocklist),
        quick_take_hours=await quick_take_hours(store, now=now),
    )


def triage_request(context: TriageContext, items: Sequence[Candidate], *, now: datetime) -> Request:
    """The TRIAGE request for one batch: public data only, no tools, structured output."""
    numbered: list[dict[str, JsonValue]] = [
        {
            "item": number,
            "title": candidate.title,
            "publisher": candidate.publisher,
            "url": candidate.url,
            "published": candidate.published_at[:10] if candidate.published_at else None,
            "hint": candidate.pillar,
        }
        for number, candidate in enumerate(items, start=1)
    ]
    formats: dict[str, JsonValue] = {
        "flagship": [dict(entry) for entry in context.flagship_formats],
        "posts": list(POST_FORMAT_SUGGESTIONS),
    }
    sections = [
        f"Today is {now:%Y-%m-%d} (UTC).",
        "<pillars>\n" + prompt_json([dict(entry) for entry in context.pillars]) + "\n</pillars>",
        "<formats>\n" + prompt_json(formats) + "\n</formats>",
        "<inbox>\n" + prompt_json(list(context.inbox)) + "\n</inbox>",
        "<items>\n" + prompt_json(numbered) + "\n</items>",
        f"Return exactly one verdict for each of the {len(numbered)} items.",
    ]
    return Request(
        system=load_prompt(TRIAGE_PROMPT),
        messages=(Message.user("\n\n".join(sections)),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=TriageOutput,
    )


# --------------------------------------------------------------------------------------------
# Verdicts -> ideas
# --------------------------------------------------------------------------------------------


def group_candidates(candidates: Sequence[Candidate]) -> list[list[Candidate]]:
    """Candidates grouped by URL (first seen first): one triage per URL, every signal marked."""
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        try:
            key = candidate.url_sha256
        except ValueError:
            continue
        groups.setdefault(key, []).append(candidate)
    return list(groups.values())


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, round(value, 3)))


def _format_for(verdict: TriageVerdict, lane: str | None, context: TriageContext) -> str | None:
    value = (verdict.format or "").strip().lower() or None
    if value is None:
        return None
    if lane == "flagship":
        return value if value in context.flagship_ids else None
    if lane in ("quick_take", "native"):
        return value if value in POST_FORMAT_SUGGESTIONS else None
    known = context.flagship_ids | frozenset(POST_FORMAT_SUGGESTIONS)
    return value if value in known else None


def build_idea(
    candidate: Candidate,
    verdict: TriageVerdict | None,
    *,
    origin: IdeaFeedOrigin,
    context: TriageContext,
    settings: SignalsSettings,
    stamp: str,
    now: datetime,
    served_by: ServedByRecord | None,
) -> Idea | None:
    """The idea a verdict files, or ``None`` below ``MIN_RELEVANCE`` (or without a verdict)."""
    if verdict is None:
        return None
    relevance = _clamp(verdict.relevance)
    if relevance < settings.min_relevance:
        return None
    pillar = verdict.pillar if verdict.pillar in context.pillar_ids else None
    lane: str | None = verdict.lane
    angle = clean_text(verdict.angle, max_chars=_MAX_ANGLE)
    if angle is not None and slop_codes(angle):
        angle = None
    time_sensitive = verdict.time_sensitive
    expires_at = (
        expires_for(
            published_at=candidate.published_at, now=now, max_age_hours=context.quick_take_hours
        )
        if time_sensitive
        else None
    )
    if time_sensitive and expires_at is None:
        # The quick-take window is already over: keep the idea, without the deadline.
        time_sensitive = False
        if lane == "quick_take":
            lane = None
    digest = candidate.url_sha256
    fields: dict[str, object] = {
        "id": idea_id_for_url(digest),
        "status": IdeaStatus.NEW,
        "status_changed_at": stamp,
        "origin": origin,
        "title": candidate.title,
        "url": candidate.url,
        "url_sha256": digest,
        "publisher": candidate.publisher,
        "published_at": candidate.published_at,
        "source_id": candidate.source_id,
        "signal_id": candidate.signal_id,
        "pillar": pillar,
        "relevance": relevance,
        "novelty": _clamp(verdict.novelty),
        "suggested_lane": lane,
        "suggested_format": _format_for(verdict, lane, context),
        "angle": angle,
        "time_sensitive": time_sensitive,
        "expires_at": expires_at,
        "triage": TriageInfo(
            ran_at=stamp,
            reasons=clean_text(verdict.reasons, max_chars=_MAX_REASONS),
            served_by=served_by,
        ),
        "created_at": stamp,
        "updated_at": stamp,
    }
    try:
        return Idea.model_validate(fields)
    except ValidationError:
        # A bug, not a model mistake (every field was normalized above): log ids only.
        _logger.error("signals_triage_invalid_idea", extra={"doc_id": idea_id_for_url(digest)})
        return None


async def mark_signals(
    store: Store, group: Sequence[Candidate], *, idea_id: str | None, now: datetime
) -> None:
    """Mark the signals of one URL triaged (with the idea filed for it, if any)."""
    stamp = to_iso(now)

    def mutate(signal: Signal) -> Signal | None:
        if signal.triaged and (idea_id is None or signal.idea_id == idea_id):
            return None
        return signal.model_copy(
            update={
                "triaged": True,
                "idea_id": idea_id or signal.idea_id,
                "updated_at": stamp,
            }
        )

    for candidate in group:
        try:
            await update_doc(
                store, Signal, candidate.signal_id, mutate, partition_key=candidate.partition
            )
        except NotFound:
            continue  # aged out (90-day TTL): nothing to mark


def _served_by(result: RouterResult, *, stamp: str, items: int) -> ServedByRecord:
    # The batch's cost, shared by the items it triaged.
    return ServedByRecord.from_served_by(
        result.served_by,
        step="triage",
        at=stamp,
        refused=bool(result.refusals) or result.refused,
        prompt_version=TRIAGE_PROMPT,
        cost_usd=float(result.cost_usd) / max(1, items),
    )


# --------------------------------------------------------------------------------------------
# The step
# --------------------------------------------------------------------------------------------


async def _triage_batch(
    services: Services,
    ai: ModelRunner,
    batch: Sequence[list[Candidate]],
    *,
    index: int,
    origin: IdeaFeedOrigin,
    job_origin: Origin,
    job_id: str | None,
    context: TriageContext,
    settings: SignalsSettings,
    progress: TriageProgress,
    emit: EmitFn,
) -> None:
    store = services.store
    now = services.clock.now()
    clean: list[list[Candidate]] = []
    for group in batch:
        first = group[0]
        texts = [first.title, first.url, first.publisher or "", first.pillar or ""]
        if blocklist_hit([text for text in texts if text], context.blocklist):
            await mark_signals(store, group, idea_id=None, now=now)
            progress.blocked += 1
        else:
            clean.append(group)
    known: dict[str, str] = {}
    if clean:
        found = await find_docs(
            store, Idea, [Filter("urlSha256", "in", [group[0].url_sha256 for group in clean])]
        )
        known = {
            stored.doc.url_sha256: stored.doc.id
            for stored in found
            if stored.doc.url_sha256 is not None
        }
    todo: list[list[Candidate]] = []
    for group in clean:
        existing = known.get(group[0].url_sha256)
        if existing is not None:
            await mark_signals(store, group, idea_id=existing, now=now)
            progress.existing += 1
        else:
            todo.append(group)
    if not todo:
        return
    request = triage_request(context, [group[0] for group in todo], now=now)
    try:
        with defer_on_brake(services.clock.now()):
            result = await ai.run(Task.TRIAGE, request, origin=job_origin, job_id=job_id)
    except StructuredOutputError as exc:
        progress.failed_batches += 1
        _logger.warning(
            "signals_triage_batch_failed",
            extra={"batch": index, "reason": exc.reason, "candidates": len(todo)},
        )
        return
    if result.refused:
        progress.refused_batches += 1
        _logger.warning(
            "signals_triage_batch_refused", extra={"batch": index, "candidates": len(todo)}
        )
        return
    try:
        output = result.output(TriageOutput)
    except TypeError:
        progress.failed_batches += 1
        return
    verdicts: dict[int, TriageVerdict] = {}
    for verdict in output.verdicts:
        verdicts.setdefault(verdict.item, verdict)
    stamps = distinct_stamps(now, len(todo))
    served_by = _served_by(result, stamp=stamps[0], items=len(todo))
    created: str | None = None
    for number, (group, stamp) in enumerate(zip(todo, stamps, strict=True), start=1):
        idea = build_idea(
            group[0],
            verdicts.get(number),
            origin=origin,
            context=context,
            settings=settings,
            stamp=stamp,
            now=now,
            served_by=served_by,
        )
        progress.triaged += 1
        if idea is None:
            progress.below_threshold += 1
            await mark_signals(store, group, idea_id=None, now=now)
            continue
        try:
            await create_doc(store, idea)
        except Conflict:
            progress.existing += 1
        else:
            progress.ideas += 1
            progress.idea_ids.append(idea.id)
            created = idea.id
        await mark_signals(store, group, idea_id=idea.id, now=now)
    if created is not None:
        await emit(ENTITY_CHANGED, entity_changed("idea", created))
    _logger.info(
        "signals_triage_batch",
        extra={
            "batch": index,
            "candidates": len(todo),
            "ideas_created": progress.ideas,
            "cost_usd": float(result.cost_usd),
        },
    )


async def triage_candidates(
    services: Services,
    ai: ModelRunner,
    candidates: Sequence[Candidate],
    *,
    origin: IdeaFeedOrigin,
    job_origin: Origin,
    job_id: str | None,
    settings: SignalsSettings,
    progress: TriageProgress,
    save: Callable[[TriageProgress], Awaitable[None]],
    emit: EmitFn,
) -> TriageProgress:
    """Triage ``candidates`` into ideas (see the module docs). Resumes at
    ``progress.next_batch``: batches are formed from the candidates alone, so a resumed step
    sees the same batches."""
    groups = group_candidates(candidates)
    size = settings.triage_batch
    batches = [groups[start : start + size] for start in range(0, len(groups), size)]
    progress.candidates = len(groups)
    if progress.next_batch >= len(batches):
        return progress
    context = await load_triage_context(services.store, now=services.clock.now())
    for index in range(progress.next_batch, len(batches)):
        await _triage_batch(
            services,
            ai,
            batches[index],
            index=index,
            origin=origin,
            job_origin=job_origin,
            job_id=job_id,
            context=context,
            settings=settings,
            progress=progress,
            emit=emit,
        )
        progress.next_batch = index + 1
        await save(progress)
    return progress
