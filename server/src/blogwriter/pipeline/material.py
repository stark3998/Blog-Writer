"""What a pipeline step reads and sends: the settings, the article's subject, the drafting
material, the blocklist screen in front of every model call, and the model calls themselves.

Every model call here is ``Sensitivity.ANONYMIZED`` and tool-free, so :func:`load_material`
requires the employer's AI-use confirmation first (``domain.confidentiality.require_ai_use``)
and :func:`screen_request` runs the blocklist over every text part of a request before it is
sent (an unconfigured blocklist clears nothing). Model calls run inside
``jobflow.defer_on_brake``: background work pauses when the spend brake is engaged; owner work
is never braked. Calls are recorded as :class:`~blogwriter.domain.common.ServedByRecord`.

No background Responses are used (every call is ``store=false``), so there is no remote id to
record before awaiting and nothing to delete afterwards; a step that crashes mid-call runs the
call again on resume, from its checkpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel

from blogwriter.ai import DISCARD_EVENTS, Finished, NoRouteError, PolicyViolation, RouterResult
from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Request, Task, TextDelta, TextPart
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import ServedByRecord, review_only_reasons
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.docstore import InvalidStoredDocument, read_doc, read_singleton
from blogwriter.domain.jobflow import defer_on_brake, require_service
from blogwriter.domain.jobtypes import DRAFT_RESET
from blogwriter.domain.ports import ModelRunner, Services
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import (
    Blocklist,
    EmployerPolicy,
    FormatPack,
    FormatPacks,
    Pillar,
    PillarSettings,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import InterviewStatus
from blogwriter.domain.stories import Interview, PovEntry, StoryEntry
from blogwriter.domain.voice import StyleGuide
from blogwriter.jobs import JobFailed, StepContext
from blogwriter.notify.payload import NotificationPayload
from blogwriter.pipeline.text import chunks, clip
from blogwriter.stories import StoryBank, load_bank
from blogwriter.voice import load_active_guide

__all__ = [
    "Material",
    "Streamed",
    "Structured",
    "Subject",
    "load_material",
    "log_failure",
    "push",
    "read_optional",
    "request_texts",
    "resolve_subject",
    "run_structured",
    "screen_request",
    "select_bank",
    "stream_text",
]

_logger: Final = logging.getLogger("blogwriter.pipeline")
register_log_fields("article_id", "pipeline_step", "model_task", "fallback")

_MAX_STORIES: Final = 25
_MAX_POVS: Final = 25
_NEWS_FORMAT: Final = "news-analysis"


async def read_optional[D: Article | Brief | Idea | Interview | StyleGuide | StoryEntry | PovEntry](
    store: Store, cls: type[D], doc_id: str | None
) -> D | None:
    """The document, or ``None`` when the id is unset, missing or unreadable."""
    if doc_id is None:
        return None
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except (NotFound, InvalidStoredDocument):
        return None


# --------------------------------------------------------------------------------------------
# The article's subject: pillar, format pack, research topic
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Subject:
    """What the article is about, from its metadata (or its idea, while the metadata is unset).

    ``missing`` names the frontmatter fields that could not be resolved (camelCase), so a route
    can answer 409 ``meta_incomplete`` and a job ``JobFailed("meta_incomplete")``.
    """

    pillar: Pillar | None
    pack: FormatPack | None
    topic: str | None
    idea: Idea | None
    missing: tuple[str, ...]
    recency_days: int = 365

    @property
    def owner_urls(self) -> list[str]:
        """The idea's link, which research may read like an owner-typed URL."""
        return [self.idea.url] if self.idea is not None and self.idea.url else []


async def resolve_subject(store: Store, article: Article, *, now: str) -> Subject:
    """The pillar, format pack and research topic of ``article``."""
    pillars, _ = await read_singleton(store, PillarSettings, now=now)
    packs, _ = await read_singleton(store, FormatPacks, now=now)
    idea = await read_optional(store, Idea, article.idea_id)
    pillar_id = article.meta.pillar or (idea.pillar if idea is not None else None)
    format_id = article.meta.format or (
        idea.suggested_format if idea is not None and idea.suggested_format else None
    )
    pillar = pillars.get(pillar_id) if pillar_id else None
    pack = packs.get(format_id) if format_id else None
    title = article.meta.title or (idea.title if idea is not None else None)
    topic = clip(title, 300) if title else None
    if topic is not None and idea is not None and idea.angle:
        with_angle = f"{topic} ({clip(idea.angle, 200)})"
        topic = with_angle if len(with_angle) <= 300 else topic
    missing: list[str] = []
    if topic is None or len(topic) < 3:
        missing.append("title")
        topic = None
    if pillar is None:
        missing.append("pillar")
    if pack is None:
        missing.append("format")
    recency = 30 if pack is not None and pack.id == _NEWS_FORMAT else 365
    return Subject(
        pillar=pillar,
        pack=pack,
        topic=topic,
        idea=idea,
        missing=tuple(missing),
        recency_days=recency,
    )


# --------------------------------------------------------------------------------------------
# Drafting material
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Material:
    """Everything a drafting call reads. ``bank`` is the usable stories and POVs (pillar first)
    or the subset a plan selected; ``interview`` is set only when it has confirmed answers."""

    guide: StyleGuide
    brief: Brief | None
    pillar: Pillar
    pack: FormatPack
    bank: StoryBank
    interview: Interview | None
    blocklist: Blocklist
    policy: EmployerPolicy

    def require_brief(self) -> Brief:
        if self.brief is None:
            raise JobFailed("brief_missing", "The article has no research brief.")
        return self.brief


async def _usable_bank(store: Store, pillar: Pillar) -> StoryBank:
    """Usable stories (the pillar's first) and the pillar's usable POVs."""
    everything = await load_bank(store, usable_only=True)
    on_pillar = [story for story in everything.stories if pillar.id in story.pillars]
    others = [story for story in everything.stories if pillar.id not in story.pillars]
    povs = [pov for pov in everything.povs if pov.pillar == pillar.id]
    return StoryBank(stories=[*on_pillar, *others][:_MAX_STORIES], povs=povs[:_MAX_POVS])


def select_bank(bank: StoryBank, story_ids: Sequence[str], pov_ids: Sequence[str]) -> StoryBank:
    """The entries of ``bank`` a plan (or an article's owner signals) names, in that order.
    An entry that is no longer usable is not in ``bank`` and is dropped."""
    stories = {story.id: story for story in bank.stories}
    povs = {pov.id: pov for pov in bank.povs}
    return StoryBank(
        stories=[stories[sid] for sid in dict.fromkeys(story_ids) if sid in stories],
        povs=[povs[pid] for pid in dict.fromkeys(pov_ids) if pid in povs],
    )


async def load_material(services: Services, article: Article, *, now: datetime) -> Material:
    """The drafting material of ``article``, or ``JobFailed`` with the reason it cannot be
    drafted: ``voice_guide_missing``, ``ai_use_not_confirmed``, ``meta_incomplete``."""
    store = services.store
    stamp = to_iso(now)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    try:
        require_ai_use(policy)
    except AiUseNotConfirmed:
        raise JobFailed(
            "ai_use_not_confirmed", "Confirm the employer AI-use policy in Settings."
        ) from None
    guide = await load_active_guide(store)
    if guide is None:
        raise JobFailed("voice_guide_missing", "Activate a Voice & Style Guide first.")
    subject = await resolve_subject(store, article, now=stamp)
    if subject.pillar is None or subject.pack is None:
        raise JobFailed("meta_incomplete", "Set the article's pillar and format first.")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    brief = await read_optional(store, Brief, article.brief_id)
    bank = await _usable_bank(store, subject.pillar)
    interview = await read_optional(store, Interview, article.interview_id)
    if interview is not None and (
        interview.status is not InterviewStatus.ANSWERED or not interview.answers
    ):
        interview = None
    return Material(
        guide=guide,
        brief=brief,
        pillar=subject.pillar,
        pack=subject.pack,
        bank=bank,
        interview=interview,
        blocklist=blocklist,
        policy=policy,
    )


# --------------------------------------------------------------------------------------------
# The blocklist in front of every model call
# --------------------------------------------------------------------------------------------


def request_texts(request: Request) -> dict[str, str]:
    """Every user text part of ``request`` by position (what the blocklist screens)."""
    texts: dict[str, str] = {}
    for message_index, message in enumerate(request.messages):
        for part_index, part in enumerate(message.parts):
            if isinstance(part, TextPart):
                texts[f"messages[{message_index}].parts[{part_index}]"] = part.text
    return texts


def screen_request(request: Request, blocklist: Blocklist) -> None:
    """Refuse a request whose text matches the blocklist (``JobFailed("blocklist_hit")``) or when
    the blocklist was never saved (``JobFailed("blocklist_not_configured")``). The system prompts
    are the package's own files; every user part is checked."""
    try:
        check_texts(request_texts(request), blocklist)
    except ConfidentialText:
        raise JobFailed(
            "blocklist_hit", "Some material matches the blocklist. Fix it and run again."
        ) from None
    except BlocklistNotConfigured:
        raise JobFailed(
            "blocklist_not_configured", "Save the blocklist once in Settings > Policy."
        ) from None


# --------------------------------------------------------------------------------------------
# Model calls
# --------------------------------------------------------------------------------------------


def _runner(services: Services) -> ModelRunner:
    return require_service(services.ai, "ai")


def _record(
    result: RouterResult, *, step: str, now: datetime, prompt_version: str
) -> ServedByRecord:
    return ServedByRecord.from_served_by(
        result.served_by,
        step=step,
        at=to_iso(now),
        refused=result.refused or bool(result.refusals),
        prompt_version=prompt_version,
        cost_usd=float(result.cost_usd),
    )


def _log_call(article_id: str, step: str, result: RouterResult) -> None:
    _logger.info(
        "pipeline_model_call",
        extra={
            "article_id": article_id,
            "pipeline_step": step,
            "model_task": result.served_by.task.value,
            "fallback": result.served_by.fallback_reason or "",
        },
    )


@dataclass(frozen=True, slots=True)
class Structured[T: BaseModel]:
    """A structured call's outcome: the value (``None`` when refused or without output)."""

    value: T | None
    record: ServedByRecord
    refused: bool
    review_only: tuple[str, ...]


async def run_structured[T: BaseModel](
    services: Services,
    task: Task,
    request: Request,
    *,
    output: type[T],
    origin: Origin,
    job_id: str | None,
    article_id: str,
    step: str,
    prompt_version: str,
) -> Structured[T]:
    """One tool-free structured call (inside ``defer_on_brake``)."""
    runner = _runner(services)
    now = services.clock.now()
    try:
        with defer_on_brake(now):
            result = await runner.run(task, request, origin=origin, job_id=job_id)
    except PolicyViolation:
        raise JobFailed("policy_violation") from None
    except NoRouteError:
        raise JobFailed("ai_unavailable", "No model is configured for this step.") from None
    _log_call(article_id, step, result)
    record = _record(result, step=step, now=now, prompt_version=prompt_version)
    if result.refused:
        return Structured(value=None, record=record, refused=True, review_only=("refusal",))
    try:
        value = result.output(output)
    except TypeError:
        value = None
    return Structured(
        value=value, record=record, refused=False, review_only=review_only_reasons(result)
    )


@dataclass(frozen=True, slots=True)
class Streamed:
    """A streamed call's outcome (``text`` is empty when refused)."""

    result: RouterResult
    record: ServedByRecord
    discarded: int
    """How many times the router discarded streamed text (refusal, retry, repair, fallback)."""

    @property
    def refused(self) -> bool:
        return self.result.refused

    @property
    def text(self) -> str:
        return self.result.text

    @property
    def review_only(self) -> tuple[str, ...]:
        return review_only_reasons(self.result)


async def stream_text(
    ctx: StepContext,
    services: Services,
    task: Task,
    request: Request,
    *,
    origin: Origin,
    article_id: str,
    step: str,
    prompt_version: str,
) -> Streamed:
    """A streamed call: token deltas go to the broker (``ctx.delta``, never stored); every
    discard event (refusal, retry, repair, fallback) emits ``DRAFT_RESET`` so the web clears
    what it showed. Only the final result counts."""
    runner = _runner(services)
    now = services.clock.now()
    result: RouterResult | None = None
    discarded = 0
    try:
        with defer_on_brake(now):
            stream = runner.stream(task, request, origin=origin, job_id=ctx.job_id)
            async with aclosing(stream) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        for piece in chunks(event.text):
                            ctx.delta(piece)
                    elif isinstance(event, DISCARD_EVENTS):
                        discarded += 1
                        await ctx.emit(DRAFT_RESET, {})
                    elif isinstance(event, Finished):
                        result = event.result
    except PolicyViolation:
        raise JobFailed("policy_violation") from None
    except NoRouteError:
        raise JobFailed("ai_unavailable", "No model is configured for this step.") from None
    if result is None:  # pragma: no cover - the router always ends with Finished
        raise RuntimeError("the model stream ended without a result")
    _log_call(article_id, step, result)
    return Streamed(
        result=result,
        record=_record(result, step=step, now=now, prompt_version=prompt_version),
        discarded=discarded,
    )


def log_failure(event: str, exc: BaseException, **fields: str | int) -> None:
    """A warning with the exception type only (never its message: it may quote content)."""
    _logger.warning(event, extra={**fields, **describe_exception(exc)})


async def push(services: Services, *, title: str, url: str, tag: str) -> bool:
    """One web push (a title and a same-origin path, nothing else). A push that cannot be sent
    never fails the step: it is logged and the Today inbox still shows the item."""
    try:
        payload = NotificationPayload(title=clip(title, 80), url=url, tag=tag)
        result = await services.notifier.notify(payload)
    except Exception as exc:
        log_failure("pipeline_push_failed", exc)
        return False
    return result.any_delivered
