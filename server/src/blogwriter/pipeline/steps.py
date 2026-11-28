"""The steps of ``pipeline.article`` and ``pipeline.revise`` (contract W-B1).

``pipeline.article``: ``research`` (a chained ``research.run``), ``interview_questions``,
``interview`` (the owner's wait), ``outline``, ``draft`` (streamed), ``normalize``,
``critique_revise`` (at most ``BW_PIPELINE_MAX_REVISE_LOOPS`` rounds), ``gates``, ``assets``,
``finalize``. ``pipeline.revise``: ``revise``, ``normalize``, ``gates``, ``finalize``.

**Crash-only.** Every step is re-entrant. Documents a step creates have derived ids
(``ResearchRun.id_for_pipeline``, ``Interview.id_for_pipeline``), expensive results are
checkpointed before the step moves on (the plan, the critique), and a step that writes the
draft first checkpoints the article version it starts from, so a resumed step can tell that its
write landed instead of calling the model twice. No background Responses are used (every call is
``store=false``), so there is no remote id to re-attach to: a call cut off by a crash is made
again on resume, and ``draft.reset`` clears what the web showed of it.

**Who writes.** A step only touches an article this job owns (``pipelineJobId`` is this job)
in a pipeline state. Anything else (the owner cancelled, a newer run took over, the article was
archived) stops the job with :class:`Stopped` and leaves the article alone; any other permanent
failure moves the article to ``needs_attention`` with ``lastError`` set to the code. Child
runs (evals) report every outcome to their parent with ``jobflow.finish_child``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import Field, JsonValue, ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Task
from blogwriter.articles import (
    ArticleLinks,
    MarkdownTooLarge,
    StaleArticle,
    pipeline_write,
    set_links,
    transition,
)
from blogwriter.core.logging import register_log_fields
from blogwriter.db import NotFound, PreconditionFailed, Store
from blogwriter.domain.articles import (
    Article,
    ArticleMeta,
    CalibrationFlags,
    GateReport,
    OwnerSignalRef,
)
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import Finding, MediaRef, ServedByRecord
from blogwriter.domain.compose import word_stats
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
    blocklist_hit,
)
from blogwriter.domain.docstore import Stored, read_doc, read_singleton
from blogwriter.domain.jobflow import (
    defer_on_brake,
    enqueue_once,
    finish_child,
    step_services,
    wait_for_child,
)
from blogwriter.domain.jobtypes import (
    DRAFT_RESET,
    ENTITY_CHANGED,
    PIPELINE_INTERVIEW_EXPIRE,
    PUBLISH_ARTICLE,
    RESEARCH_RUN,
    ChildResult,
    EntityName,
    InterviewInput,
    JsonPayload,
    ParentRef,
    PipelineInput,
    PublishInput,
    ReviseInput,
    entity_changed,
    interview_expire_job_id,
    publish_job_id,
    research_job_id,
)
from blogwriter.domain.ports import RenderFailed, Services, ServiceUnavailable, SlopChecker
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.settings import (
    AutonomySettings,
    BrandKit,
    CadenceSettings,
    auto_publish_decision,
)
from blogwriter.domain.slots import next_article_publish
from blogwriter.domain.states import ArticleStatus, InterviewStatus, TransitionError, WriterConflict
from blogwriter.domain.stories import Interview
from blogwriter.gates import (
    RUBRIC_DIMENSION_MIN,
    RUBRIC_OVERALL_MIN,
    RUBRIC_SPECIFICITY_MIN,
    SlopDetectors,
    run_gates_detailed,
    save_report,
)
from blogwriter.jobs import ClaimLost, JobFailed, StepContext
from blogwriter.pipeline.interviews import INTERVIEW_KEY, SubjectIncomplete, generate_interview
from blogwriter.pipeline.material import (
    Material,
    load_material,
    log_failure,
    push,
    read_optional,
    resolve_subject,
    run_structured,
    screen_request,
    select_bank,
    stream_text,
)
from blogwriter.pipeline.models import CritiqueOut, OutlineOut
from blogwriter.pipeline.prompting import CRITIQUE, DRAFT, OUTLINE, REVISE
from blogwriter.pipeline.requests import (
    critique_request,
    drafting_request,
    outline_request,
    owner_revise_request,
    revise_request,
    slop_notes,
)
from blogwriter.pipeline.settings import pipeline_settings
from blogwriter.pipeline.text import (
    LintIssue,
    bind_citations,
    clean_draft,
    clip,
    dialect_errors,
    existing_definitions,
    known_ids,
    mermaid_sources,
    normalize_body,
    plain_text,
    slug_of,
    tag_slugs,
)
from blogwriter.research import create_run, start_run
from blogwriter.storage import store_media
from blogwriter.stories import StoryBank

__all__ = [
    "PIPELINE_STATES",
    "SLOP_REVISE_PER_1000_WORDS",
    "Stopped",
    "article_steps",
    "revise_steps",
    "rubric_passes",
    "run_number",
]

_logger: Final = logging.getLogger("blogwriter.pipeline")
register_log_fields("article_id", "job_type", "code", "loops")

_S = ArticleStatus
PIPELINE_STATES: Final = frozenset({_S.RESEARCHING, _S.INTERVIEWING, _S.DRAFTING, _S.GATING})
"""States in which the pipeline writes the article (``ARTICLE_WRITER``)."""
_MOVE_ATTEMPTS: Final = 5
SLOP_REVISE_PER_1000_WORDS: Final = 3.0
"""A revise round also runs when structural slop exceeds gate 4's budget (3 per 1,000 words,
AI tells at half weight, at least 3)."""
_SLOP_BUDGET_MIN: Final = 3.0
DIAGRAM_PNG_WIDTH: Final = 1200
_RUN_SUFFIX_RE: Final = re.compile(r"([1-9][0-9]{0,5})(?:-r[1-9][0-9]{0,3})?")


class Stopped(JobFailed):
    """The article is not this run's to write any more (cancelled, restarted, archived,
    deleted): the job fails and the article is left exactly as it is."""


# --------------------------------------------------------------------------------------------
# The run a step belongs to
# --------------------------------------------------------------------------------------------


def run_number(job_id: str, article_id: str) -> int | None:
    """The pipeline run number in a ``pipeline_job_id(articleId, n)`` id (re-runs ``-r<k>``
    included), or ``None`` for an id of another shape."""
    prefix = f"pipeline-{article_id}-"
    if not job_id.startswith(prefix):
        return None
    match = _RUN_SUFFIX_RE.fullmatch(job_id[len(prefix) :])
    return int(match.group(1)) if match is not None else None


@dataclass(frozen=True, slots=True)
class _Run:
    ctx: StepContext
    services: Services
    article_id: str
    origin: Origin
    number_hint: int | None
    start_from: Literal["research", "interview", "draft"]
    interview: Literal["ask", "skip"]
    parent: ParentRef | None
    instruction: str | None = None
    """Set for ``pipeline.revise``."""

    @property
    def store(self) -> Store:
        return self.services.store

    @property
    def job_id(self) -> str:
        return self.ctx.job_id

    @property
    def revising(self) -> bool:
        return self.instruction is not None

    @property
    def asks_interview(self) -> bool:
        return not self.revising and self.interview == "ask" and self.start_from != "draft"

    def now(self) -> datetime:
        return self.services.clock.now()

    def number_for(self, article: Article) -> int:
        return self.number_hint if self.number_hint is not None else max(1, article.pipeline_runs)


def _article_run(ctx: StepContext) -> _Run:
    try:
        payload = PipelineInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None
    return _Run(
        ctx=ctx,
        services=step_services(),
        article_id=payload.article_id,
        origin=payload.origin,
        number_hint=run_number(ctx.job_id, payload.article_id),
        start_from=payload.start_from,
        interview=payload.interview,
        parent=payload.parent,
    )


def _revise_run(ctx: StepContext) -> _Run:
    try:
        payload = ReviseInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None
    return _Run(
        ctx=ctx,
        services=step_services(),
        article_id=payload.article_id,
        origin=payload.origin,
        number_hint=None,
        start_from="draft",
        interview="skip",
        parent=None,
        instruction=payload.instruction,
    )


type _Body = Callable[[_Run], Awaitable[JsonValue]]
type _StepFunction = Callable[[StepContext], Awaitable[JsonValue]]


def _step(build: Callable[[StepContext], _Run], body: _Body) -> _StepFunction:
    async def step(ctx: StepContext) -> JsonValue:
        run = build(ctx)
        try:
            return await body(run)
        except JobFailed as exc:
            await _on_failure(run, exc)
            raise

    return step


async def _on_failure(run: _Run, exc: JobFailed) -> None:
    """A permanent failure: the article goes to ``needs_attention`` (unless the job was
    stopped because the article is not its own any more) and a parent hears about it."""
    _logger.info("pipeline_failed", extra={"article_id": run.article_id, "code": exc.code})
    if not isinstance(exc, Stopped):
        try:
            await _needs_attention(run, exc.code)
        except ClaimLost:
            raise
        except Exception as error:
            log_failure("pipeline_attention_failed", error, article_id=run.article_id)
    if run.parent is not None:
        result = ChildResult(ok=False, ref=run.article_id, error_code=exc.code)
        try:
            await finish_child(run.ctx, run.services.jobs, run.parent, result)
        except ClaimLost:
            raise
        except Exception as error:
            log_failure("pipeline_child_result_failed", error, article_id=run.article_id)


async def _needs_attention(run: _Run, code: str) -> None:
    for _ in range(_MOVE_ATTEMPTS):
        try:
            current = await read_doc(run.store, Article, run.article_id)
        except NotFound:
            return
        doc = current.doc
        if doc.pipeline_job_id not in (None, run.job_id) or doc.status not in PIPELINE_STATES:
            return
        try:
            await transition(
                run.store,
                run.article_id,
                _S.NEEDS_ATTENTION,
                actor="pipeline",
                now=run.now(),
                if_match=current.etag,
                reason=code,
            )
            return
        except StaleArticle:
            continue


# --------------------------------------------------------------------------------------------
# Reading and writing the article (only while this job owns it)
# --------------------------------------------------------------------------------------------


async def _own(run: _Run, allowed: Collection[ArticleStatus]) -> Stored[Article]:
    """The article, if this job owns it and it is in one of ``allowed``; claims an unclaimed
    article (an eval or a planned start) for this job."""
    try:
        stored = await read_doc(run.store, Article, run.article_id)
    except NotFound:
        raise Stopped("article_missing", "The article no longer exists.") from None
    doc = stored.doc
    if doc.pipeline_job_id is not None and doc.pipeline_job_id != run.job_id:
        raise Stopped("superseded", "A newer pipeline run works on this article.")
    if doc.status not in allowed:
        raise Stopped("article_moved", "The article left the pipeline.")
    links: dict[str, object] = {}
    if doc.pipeline_job_id is None:
        links["pipeline_job_id"] = run.job_id
    if not run.revising and run.number_hint is not None and doc.pipeline_runs < run.number_hint:
        links["pipeline_runs"] = run.number_hint
    if links:
        stored = await set_links(
            run.store, run.article_id, ArticleLinks.model_validate(links), now=run.now()
        )
    return stored


async def _move(
    run: _Run,
    target: ArticleStatus,
    *,
    sources: Collection[ArticleStatus],
    update: Callable[[Article], Article] | None = None,
) -> Stored[Article]:
    """``sources`` -> ``target`` with If-Match on the state this job just read (a concurrent
    cancel or restart is never overwritten); already in ``target`` is fine."""
    for _ in range(_MOVE_ATTEMPTS):
        current = await _own(run, {*sources, target})
        if current.doc.status is target:
            return current
        try:
            return await transition(
                run.store,
                run.article_id,
                target,
                actor="pipeline",
                now=run.now(),
                if_match=current.etag,
                update=update,
            )
        except StaleArticle:
            continue
        except TransitionError:
            raise Stopped("article_moved", "The article left the pipeline.") from None
    raise PreconditionFailed(f"articles/{run.article_id}: lost {_MOVE_ATTEMPTS} rounds")


async def _write(
    run: _Run,
    *,
    markdown: str | None = None,
    meta: ArticleMeta | None = None,
    served_by: Sequence[ServedByRecord] = (),
    review_only: Sequence[str] = (),
    media: Sequence[MediaRef] | None = None,
    note: str | None = None,
) -> Stored[Article]:
    await _own(run, PIPELINE_STATES)
    try:
        return await pipeline_write(
            run.store,
            run.article_id,
            now=run.now(),
            markdown=markdown,
            meta=meta,
            served_by=served_by,
            review_only_reasons=review_only,
            media=media,
            note=note,
        )
    except WriterConflict:
        raise Stopped("article_moved", "The article left the pipeline.") from None
    except MarkdownTooLarge:
        raise JobFailed("markdown_too_large", "The draft is larger than 512 KiB.") from None


async def _changed(run: _Run, entity: EntityName = "article", doc_id: str | None = None) -> None:
    await run.ctx.emit(ENTITY_CHANGED, entity_changed(entity, doc_id or run.article_id))


def _entering_drafting(article: Article) -> Article:
    """A new draft replaces the old one: the old draft's review-only reasons go with it."""
    return article.model_copy(update={"review_only": False, "review_only_reasons": []})


def _first_state(run: _Run, *, has_brief: bool) -> ArticleStatus:
    if not has_brief and run.start_from == "research":
        return _S.RESEARCHING
    if run.asks_interview:
        return _S.INTERVIEWING
    return _S.DRAFTING


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _state[P: JsonPayload](cls: type[P], value: JsonValue) -> P:
    try:
        return cls.from_json(value)
    except (ValueError, ValidationError):
        return cls()


# --------------------------------------------------------------------------------------------
# research
# --------------------------------------------------------------------------------------------


async def _research(run: _Run) -> JsonValue:
    stored = await _own(run, {_S.PLANNED, _S.RESEARCHING, _S.INTERVIEWING, _S.DRAFTING})
    article = stored.doc
    if article.status is _S.PLANNED:
        first = _first_state(run, has_brief=article.brief_id is not None)
        stored = await _move(
            run,
            first,
            sources={_S.PLANNED},
            update=_entering_drafting if first is _S.DRAFTING else None,
        )
        article = stored.doc
    if article.brief_id is not None:
        return {"skipped": "brief", "briefId": article.brief_id}
    if run.start_from != "research":
        raise JobFailed("brief_missing", "There is no research brief: start from research.")
    now = run.now()
    subject = await resolve_subject(run.store, article, now=to_iso(now))
    if subject.topic is None or subject.pillar is None:
        raise JobFailed("meta_incomplete", "Set the article's title and pillar first.")
    run_id = ResearchRun.id_for_pipeline(article.id, run.number_for(article))
    try:
        created = await create_run(
            run.store,
            topic=subject.topic,
            pillar=subject.pillar.id,
            now=now,
            audience=clip(subject.pillar.audience, 300) or None,
            recency_days=subject.recency_days,
            article_id=article.id,
            idea_id=article.idea_id,
            owner_urls=subject.owner_urls,
            run_id=run_id,
        )
    except ConfidentialText:
        raise JobFailed("blocklist_hit", "The topic matches the blocklist.") from None
    except BlocklistNotConfigured:
        raise JobFailed("blocklist_not_configured", "Save the blocklist once first.") from None
    parent = ParentRef(job_id=run.job_id, key="research")
    try:
        await start_run(run.services, created.doc, origin=run.origin, parent=parent)
    except ValueError:
        _logger.warning("job_hook_unregistered", extra={"job_type": RESEARCH_RUN})
        raise JobFailed("research_unavailable", "Research is not available.") from None
    result = await wait_for_child(
        run.ctx,
        run.services.jobs,
        research_job_id(run_id),
        key="research",
        prompt="Research is running.",
    )
    if not result.ok:
        raise JobFailed("research_failed", "Research did not produce a brief.")
    brief_id = result.ref or Brief.id_for(run_id)
    if await read_optional(run.store, Brief, brief_id) is None:
        raise JobFailed("research_failed", "Research did not produce a brief.")
    await set_links(
        run.store,
        article.id,
        ArticleLinks(brief_id=brief_id, research_run_id=run_id),
        now=run.now(),
    )
    await _changed(run)
    return {"briefId": brief_id, "researchRunId": run_id}


# --------------------------------------------------------------------------------------------
# interview_questions, interview
# --------------------------------------------------------------------------------------------


async def _interview_questions(run: _Run) -> JsonValue:
    if not run.asks_interview:
        return {"skipped": "not_asked"}
    stored = await _own(run, {_S.RESEARCHING, _S.INTERVIEWING})
    article = stored.doc
    number = run.number_for(article)
    own = await read_optional(run.store, Interview, Interview.id_for_pipeline(article.id, number))
    if own is None and run.start_from == "research":
        earlier = await read_optional(run.store, Interview, article.interview_id)
        if earlier is not None and earlier.status is InterviewStatus.ANSWERED and earlier.answers:
            return {"skipped": "answered", "interviewId": earlier.id}
    stored = await _move(run, _S.INTERVIEWING, sources={_S.RESEARCHING})
    try:
        interview = (
            await generate_interview(
                run.services,
                stored.doc,
                pipeline_run=number,
                origin=run.origin,
                job_id=run.job_id,
            )
        ).doc
    except AiUseNotConfirmed:
        raise JobFailed("ai_use_not_confirmed", "Confirm the AI-use policy first.") from None
    except BlocklistNotConfigured:
        raise JobFailed("blocklist_not_configured", "Save the blocklist once first.") from None
    except ConfidentialText:
        raise JobFailed("blocklist_hit", "Some material matches the blocklist.") from None
    except ServiceUnavailable:
        raise JobFailed("ai_unavailable", "No model is configured.") from None
    except SubjectIncomplete:
        raise JobFailed("meta_incomplete", "Set the article's title, pillar and format.") from None
    await set_links(run.store, article.id, ArticleLinks(interview_id=interview.id), now=run.now())
    if interview.status is InterviewStatus.PENDING and interview.due_at is not None:
        payload = InterviewInput(interview_id=interview.id, origin=run.origin)
        try:
            await enqueue_once(
                run.services.jobs,
                PIPELINE_INTERVIEW_EXPIRE,
                payload.to_json(),
                job_id=interview_expire_job_id(interview.id),
                run_after=parse_iso(interview.due_at),
            )
        except ValueError:
            _logger.warning("job_hook_unregistered", extra={"job_type": PIPELINE_INTERVIEW_EXPIRE})
    await _changed(run, "interview", interview.id)
    return {"interviewId": interview.id}


async def _interview(run: _Run) -> JsonValue:
    if not run.asks_interview:
        return {"skipped": "not_asked"}
    interview_id = _dict(run.ctx.outputs.get("interview_questions")).get("interviewId")
    if not isinstance(interview_id, str):
        return {"skipped": "no_interview"}
    stored = await _own(run, {_S.RESEARCHING, _S.INTERVIEWING})
    interview = await read_optional(run.store, Interview, interview_id)
    if interview is None:
        raise JobFailed("interview_missing", "The interview no longer exists.")
    if interview.status is InterviewStatus.PENDING:
        # The answer route stores the answers first and then wakes this step, so the document
        # is read on every (re)start: an answer given before the job suspended is not lost.
        await run.ctx.wait_for_input("Answer the interview.", key=INTERVIEW_KEY)
        interview = await read_optional(run.store, Interview, interview_id)
        if interview is None:
            raise JobFailed("interview_missing", "The interview no longer exists.")
    if interview.status is InterviewStatus.ANSWERED and interview.answers:
        signals = [ref for ref in stored.doc.owner_signals if ref.id != interview.id]
        signals.append(OwnerSignalRef(kind="interview", id=interview.id))
        await set_links(
            run.store,
            run.article_id,
            ArticleLinks(interview_id=interview.id, owner_signals=signals[-50:]),
            now=run.now(),
        )
        await _changed(run)
    return {"interviewId": interview.id, "status": interview.status.value}


# --------------------------------------------------------------------------------------------
# outline
# --------------------------------------------------------------------------------------------


class _PlanState(JsonPayload):
    outline: OutlineOut | None = None
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord])
    review_only: list[str] = Field(default_factory=list[str])


def _tidy_outline(outline: OutlineOut, material: Material, brief: Brief) -> OutlineOut:
    """The plan with every id checked against the material and every text trimmed."""
    citations = {citation.id for citation in brief.citations}
    stories = {story.id for story in material.bank.stories}
    povs = {pov.id for pov in material.bank.povs}
    questions = (
        {question.id for question in material.interview.questions}
        if material.interview is not None
        else set[str]()
    )
    sections = [
        section.model_copy(
            update={
                "heading": plain_text(section.heading, 120),
                "purpose": clip(section.purpose, 300),
                "points": [clip(point, 300) for point in section.points if point.strip()][:6],
                "citation_ids": known_ids(section.citation_ids, citations),
                "story_ids": known_ids(section.story_ids, stories),
                "pov_ids": known_ids(section.pov_ids, povs),
                "interview_question_ids": known_ids(section.interview_question_ids, questions),
            }
        )
        for section in outline.sections
        if section.heading.strip()
    ][:8]
    story_ids = known_ids(
        [*outline.story_ids, *(sid for section in sections for sid in section.story_ids)], stories
    )
    pov_ids = known_ids(
        [*outline.pov_ids, *(pid for section in sections for pid in section.pov_ids)], povs
    )
    return outline.model_copy(
        update={
            "title": plain_text(outline.title, 150),
            "excerpt": plain_text(outline.excerpt, 400),
            "slug": slug_of(outline.slug) or slug_of(outline.title) or "",
            "tags": tag_slugs(outline.tags),
            "thesis": clip(outline.thesis, 400),
            "opening": clip(outline.opening, 600),
            "closing": clip(outline.closing, 400),
            "diagram": clip(outline.diagram, 300) or None,
            "sections": sections,
            "story_ids": story_ids,
            "pov_ids": pov_ids,
        }
    )


def _signals(outline: OutlineOut, interview: Interview | None) -> list[OwnerSignalRef]:
    refs = [OwnerSignalRef(kind="story", id=sid) for sid in outline.story_ids]
    refs += [OwnerSignalRef(kind="pov", id=pid) for pid in outline.pov_ids]
    if interview is not None:
        refs.append(OwnerSignalRef(kind="interview", id=interview.id))
    return refs[:50]


async def _outline(run: _Run) -> JsonValue:
    stored = await _move(
        run, _S.DRAFTING, sources={_S.RESEARCHING, _S.INTERVIEWING}, update=_entering_drafting
    )
    material = await load_material(run.services, stored.doc, now=run.now())
    brief = material.require_brief()
    state = _state(_PlanState, run.ctx.output)
    outline = state.outline
    if outline is None:
        request = outline_request(
            brief=brief,
            bank=material.bank,
            interview=material.interview,
            pack=material.pack,
            pillar=material.pillar,
            title=stored.doc.meta.title,
        )
        screen_request(request, material.blocklist)
        outcome = await run_structured(
            run.services,
            Task.OUTLINE,
            request,
            output=OutlineOut,
            origin=run.origin,
            job_id=run.job_id,
            article_id=run.article_id,
            step="pipeline.outline",
            prompt_version=OUTLINE,
        )
        if outcome.value is None:
            await _write(
                run, served_by=[outcome.record], review_only=("refusal",) if outcome.refused else ()
            )
            if outcome.refused:
                raise JobFailed("refused", "The model declined to plan this article.")
            raise JobFailed("outline_failed", "The model did not return a plan.")
        outline = _tidy_outline(outcome.value, material, brief)
        if not outline.sections:
            raise JobFailed("outline_failed", "The model returned an empty plan.")
        refusals = ["refusal"] if "refusal" in outcome.review_only else []
        state = _PlanState(outline=outline, served_by=[outcome.record], review_only=refusals)
        await run.ctx.checkpoint(state.to_json())
    await set_links(
        run.store,
        run.article_id,
        ArticleLinks(owner_signals=_signals(outline, material.interview)),
        now=run.now(),
    )
    return state.to_json()


def _plan(run: _Run) -> tuple[_PlanState, OutlineOut]:
    state = _state(_PlanState, run.ctx.outputs.get("outline"))
    if state.outline is None:
        raise JobFailed("outline_missing", "The plan is missing: run the pipeline again.")
    return state, state.outline


# --------------------------------------------------------------------------------------------
# draft
# --------------------------------------------------------------------------------------------


class _WriteState(JsonPayload):
    base_version: int | None = None
    """The article version before the model call (its write landed when the version moved)."""
    written: dict[str, JsonValue] | None = None


def _meta_for(current: ArticleMeta, outline: OutlineOut, material: Material) -> ArticleMeta:
    """Fill the metadata the owner did not set from the plan (never overwrite theirs)."""
    title = current.title or plain_text(outline.title, 150) or None
    values: dict[str, object] = {
        "title": title,
        "excerpt": current.excerpt or plain_text(outline.excerpt, 400) or None,
        "slug": current.slug or slug_of(outline.slug) or slug_of(title),
        "tags": list(current.tags) or tag_slugs(outline.tags),
        "pillar": current.pillar or material.pillar.id,
        "format": current.format or material.pack.id,
    }
    try:
        return ArticleMeta.model_validate({**current.model_dump(), **values})
    except ValidationError:
        return current


async def _landed(run: _Run, state: _WriteState, article: Article) -> JsonValue | None:
    """The output of a write that already happened (resume), else ``None``."""
    if state.written is not None:
        return state.written
    if state.base_version is not None and article.version > state.base_version:
        written: dict[str, JsonValue] = {"version": article.version, "resumed": True}
        await run.ctx.checkpoint(_WriteState(written=written).to_json())
        return written
    return None


async def _start_write(run: _Run, article: Article) -> None:
    await run.ctx.checkpoint(_WriteState(base_version=article.version).to_json())
    if run.ctx.resumed:
        await run.ctx.emit(DRAFT_RESET, {})  # what the web showed of a cut-off call is void


async def _draft(run: _Run) -> JsonValue:
    stored = await _own(run, {_S.DRAFTING})
    state = _state(_WriteState, run.ctx.output)
    done = await _landed(run, state, stored.doc)
    if done is not None:
        return done
    plan, outline = _plan(run)
    material = await load_material(run.services, stored.doc, now=run.now())
    brief = material.require_brief()
    request = drafting_request(
        guide=material.guide,
        brief=brief,
        bank=select_bank(material.bank, outline.story_ids, outline.pov_ids),
        interview=material.interview,
        outline=outline,
        pack=material.pack,
        pillar=material.pillar,
    )
    screen_request(request, material.blocklist)
    await _start_write(run, stored.doc)
    streamed = await stream_text(
        run.ctx,
        run.services,
        Task.DRAFT,
        request,
        origin=run.origin,
        article_id=run.article_id,
        step="pipeline.draft",
        prompt_version=DRAFT,
    )
    records = [*plan.served_by, streamed.record]
    if streamed.refused:
        await _write(run, served_by=records, review_only=("refusal",))
        raise JobFailed("refused", "The model declined to write this draft.")
    body = bind_citations(clean_draft(streamed.text), brief.citations)
    if not body.text.strip():
        raise JobFailed("empty_draft", "The model returned an empty draft.")
    reasons = tuple(dict.fromkeys([*plan.review_only, *streamed.review_only]))
    written = await _write(
        run,
        markdown=body.text,
        meta=_meta_for(stored.doc.meta, outline, material),
        served_by=records,
        review_only=reasons,
        note=f"Draft (pipeline run {run.number_for(stored.doc)})",
    )
    out: dict[str, JsonValue] = {
        "version": written.doc.version,
        "citations": len(body.cited),
        "droppedCitations": body.removed,
        "reviewOnly": list(reasons),
    }
    await run.ctx.checkpoint(_WriteState(written=out).to_json())
    await _changed(run)
    return out


# --------------------------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------------------------


async def _normalize(run: _Run) -> JsonValue:
    stored = await _own(run, {_S.DRAFTING})
    body = stored.doc.markdown
    result = await asyncio.to_thread(normalize_body, body)
    changed = bool(result.text.strip()) and result.text != body
    if changed:
        await _write(run, markdown=result.text, note="Normalized")
        await _changed(run)
    errors: list[JsonValue] = [
        {"code": error.code, "line": error.line} for error in result.errors[:50]
    ]
    return {"changed": changed, "errors": errors}


# --------------------------------------------------------------------------------------------
# critique_revise
# --------------------------------------------------------------------------------------------


def rubric_passes(critique: CritiqueOut) -> bool:
    """Gate 1's thresholds: overall >= 8, every dimension >= 6, first-hand specificity >= 7."""
    scores = critique.scores()
    return (
        critique.overall >= RUBRIC_OVERALL_MIN
        and all(value >= RUBRIC_DIMENSION_MIN for value in scores.values())
        and scores["first_hand_specificity"] >= RUBRIC_SPECIFICITY_MIN
    )


class _Round(JsonPayload):
    overall: int
    passed: bool
    errors: int = 0
    slop: int = 0


class _LoopState(JsonPayload):
    loops: int = 0
    rounds: list[_Round] = Field(default_factory=list[_Round])
    critique: CritiqueOut | None = None
    """The critique of the current version (``None``: not critiqued yet)."""
    pending: int | None = None
    """The article version a revision in flight started from."""
    stopped: str | None = None


@dataclass(frozen=True, slots=True)
class _Checks:
    words: int
    errors: tuple[LintIssue, ...]
    slop: tuple[Finding, ...]
    slop_over: bool

    @property
    def blocking(self) -> bool:
        return bool(self.errors) or self.slop_over


def _slop_findings(checker: SlopChecker | None, body: str) -> list[Finding]:
    detectors = checker if checker is not None else SlopDetectors()
    if isinstance(detectors, SlopDetectors):
        return detectors.check_markdown(body)
    return detectors.check(body)


def _checks(body: str, source_ids: Collection[str], checker: SlopChecker | None) -> _Checks:
    """Deterministic checks of a draft (CPU-bound: call in a thread)."""
    words, _ = word_stats(body)
    slop = _slop_findings(checker, body)
    structural = sum(1 for finding in slop if finding.code != "ai-tell")
    tells = sum(1 for finding in slop if finding.code == "ai-tell")
    budget = max(_SLOP_BUDGET_MIN, SLOP_REVISE_PER_1000_WORDS * words / 1000.0)
    return _Checks(
        words=words,
        errors=dialect_errors(body, source_ids),
        slop=tuple(slop),
        slop_over=structural + 0.5 * tells > budget,
    )


async def _critique(
    run: _Run, material: Material, brief: Brief, bank: StoryBank, article: Article, words: int
) -> tuple[CritiqueOut | None, bool]:
    request = critique_request(
        guide=material.guide,
        brief=brief,
        bank=bank,
        interview=material.interview,
        pack=material.pack,
        pillar=material.pillar,
        title=article.meta.title,
        excerpt=article.meta.excerpt,
        body=article.markdown,
        words=words,
    )
    screen_request(request, material.blocklist)
    outcome = await run_structured(
        run.services,
        Task.CRITIQUE,
        request,
        output=CritiqueOut,
        origin=run.origin,
        job_id=run.job_id,
        article_id=run.article_id,
        step="pipeline.critique",
        prompt_version=CRITIQUE,
    )
    await _write(run, served_by=[outcome.record], review_only=outcome.review_only)
    return outcome.value, outcome.refused


def _bank_for(run: _Run, material: Material, article: Article) -> StoryBank:
    plan = _state(_PlanState, run.ctx.outputs.get("outline"))
    if plan.outline is not None:
        return select_bank(material.bank, plan.outline.story_ids, plan.outline.pov_ids)
    return select_bank(
        material.bank,
        [ref.id for ref in article.owner_signals if ref.kind == "story"],
        [ref.id for ref in article.owner_signals if ref.kind == "pov"],
    )


def _summary(state: _LoopState) -> JsonValue:
    rounds: list[JsonValue] = [item.to_json() for item in state.rounds]
    return {"loops": state.loops, "stopped": state.stopped, "rounds": rounds}


async def _critique_revise(run: _Run) -> JsonValue:
    stored = await _own(run, {_S.DRAFTING})
    state = _state(_LoopState, run.ctx.output)
    if state.stopped is not None:
        return _summary(state)
    limit = pipeline_settings().max_revise_loops
    if state.pending is not None:
        landed = stored.doc.version > state.pending  # the revision was written before a crash
        loops = state.loops + 1 if landed else state.loops
        state = state.model_copy(
            update={
                "pending": None,
                "loops": loops,
                "critique": None if landed else state.critique,
                "stopped": "max_loops" if landed and loops >= limit else None,
            }
        )
        if state.stopped is not None:
            await run.ctx.checkpoint(state.to_json())
            return _summary(state)
    material = await load_material(run.services, stored.doc, now=run.now())
    brief = material.require_brief()
    if not material.blocklist.configured:
        raise JobFailed("blocklist_not_configured", "Save the blocklist once first.")
    bank = _bank_for(run, material, stored.doc)
    plan = _state(_PlanState, run.ctx.outputs.get("outline"))
    source_ids = [citation.id for citation in brief.citations]
    while state.stopped is None:
        article = (await _own(run, {_S.DRAFTING})).doc
        texts = [article.markdown, article.meta.title or "", article.meta.excerpt or ""]
        if blocklist_hit(texts, material.blocklist):
            # Nothing that matches the blocklist goes to a model: gate 3 reports it.
            state = state.model_copy(update={"stopped": "blocklist_hit"})
            break
        checks = await asyncio.to_thread(_checks, article.markdown, source_ids, run.services.slop)
        critique = state.critique
        if critique is None:
            critique, refused = await _critique(run, material, brief, bank, article, checks.words)
            if critique is None:
                state = state.model_copy(
                    update={"stopped": "refused" if refused else "no_critique"}
                )
                break
            passed = rubric_passes(critique)
            round_ = _Round(
                overall=critique.overall,
                passed=passed,
                errors=len(checks.errors),
                slop=len(checks.slop),
            )
            state = state.model_copy(
                update={"critique": critique, "rounds": [*state.rounds, round_]}
            )
            await run.ctx.checkpoint(state.to_json())
        if rubric_passes(critique) and not checks.blocking:
            state = state.model_copy(update={"stopped": "passed"})
            break
        if state.loops >= limit:
            state = state.model_copy(update={"stopped": "max_loops"})
            break
        state = state.model_copy(update={"pending": article.version})
        await run.ctx.checkpoint(state.to_json())
        request = revise_request(
            guide=material.guide,
            brief=brief,
            bank=bank,
            interview=material.interview,
            outline=plan.outline,
            pack=material.pack,
            pillar=material.pillar,
            title=article.meta.title,
            draft=article.markdown,
            critique=critique,
            words=checks.words,
            errors=checks.errors,
            slop=slop_notes(article.markdown, checks.slop),
        )
        screen_request(request, material.blocklist)
        streamed = await stream_text(
            run.ctx,
            run.services,
            Task.REVISE,
            request,
            origin=run.origin,
            article_id=run.article_id,
            step="pipeline.revise",
            prompt_version=REVISE,
        )
        if streamed.refused:
            await _write(run, served_by=[streamed.record], review_only=("refusal",))
            state = state.model_copy(update={"pending": None, "stopped": "refused"})
            break
        bound = bind_citations(
            clean_draft(streamed.text),
            brief.citations,
            keep=existing_definitions(article.markdown),
        )
        normalized = await asyncio.to_thread(normalize_body, bound.text)
        if not normalized.text.strip():
            await _write(run, served_by=[streamed.record], review_only=streamed.review_only)
            state = state.model_copy(update={"pending": None, "stopped": "empty_revision"})
            break
        await _write(
            run,
            markdown=normalized.text,
            served_by=[streamed.record],
            review_only=streamed.review_only,
            note=f"Revision {state.loops + 1} after critique",
        )
        state = state.model_copy(
            update={"pending": None, "loops": state.loops + 1, "critique": None}
        )
        if state.loops >= limit:
            state = state.model_copy(update={"stopped": "max_loops"})
        await run.ctx.checkpoint(state.to_json())
        await _changed(run)
    await run.ctx.checkpoint(state.to_json())
    _logger.info(
        "pipeline_critique_done", extra={"article_id": run.article_id, "loops": state.loops}
    )
    return _summary(state)


# --------------------------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------------------------


def _report_summary(report: GateReport) -> JsonValue:
    blocking: list[JsonValue] = [gate.value for gate in report.blocking()]
    return {
        "contentSha256": report.content_sha256,
        "version": report.version,
        "autoPublishReady": report.auto_publish_ready,
        "blocking": blocking,
    }


async def _gates(run: _Run) -> JsonValue:
    stored = await _move(run, _S.GATING, sources={_S.DRAFTING})
    article = stored.doc
    current = article.gates
    if (
        current is not None
        and current.job_id == run.job_id
        and current.content_sha256 == article.compute_sha256()
    ):
        return _report_summary(current)  # a resumed step: the report is saved
    with defer_on_brake(run.now()):
        outcome = await run_gates_detailed(
            article, services=run.services, origin=run.origin, job_id=run.job_id
        )
    saved = await save_report(
        run.store, article.id, outcome.report, now=run.now(), embedding=outcome.embedding
    )
    if saved is None:
        raise JobFailed("report_stale", "The article changed while the gates ran.")
    await _changed(run)
    return _report_summary(outcome.report)


# --------------------------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------------------------


def _made_by_pipeline(media: MediaRef, article_id: str) -> bool:
    return media.container == "media" and media.name.startswith(f"articles/{article_id}/")


async def _assets(run: _Run) -> JsonValue:
    stored = await _own(run, {_S.GATING})
    article = stored.doc
    render, blobs = run.services.render, run.services.blobs
    if render is None or blobs is None:
        return {"skipped": "not_configured"}
    now = run.now()
    stamp = to_iso(now)
    brand, _ = await read_singleton(run.store, BrandKit, now=stamp)
    subject = await resolve_subject(run.store, article, now=stamp)
    title = article.meta.title or "Untitled draft"
    kept = [item for item in article.media if not _made_by_pipeline(item, article.id)]
    owner_hero = next((item for item in kept if item.role == "hero_image"), None)
    made: list[MediaRef] = []
    failures: list[str] = []
    sources = await asyncio.to_thread(mermaid_sources, article.markdown)
    for index, source in enumerate(sources, 1):
        alt = clip(f"Diagram {index}: {title}", 1000)
        try:
            svg = await render.render_mermaid_svg(source, brand)
            made.append(
                await store_media(
                    blobs,
                    svg,
                    role="diagram_svg",
                    area="articles",
                    owner_id=article.id,
                    content_type="image/svg+xml",
                    now=now,
                    alt_text=alt,
                )
            )
            png = await render.render_mermaid_png(source, DIAGRAM_PNG_WIDTH, brand)
            made.append(
                await store_media(
                    blobs,
                    png,
                    role="diagram_png",
                    area="articles",
                    owner_id=article.id,
                    content_type="image/png",
                    now=now,
                    alt_text=alt,
                    width=DIAGRAM_PNG_WIDTH,
                )
            )
        except RenderFailed as exc:
            failures.append(exc.code)
    og: MediaRef | None = None
    try:
        image = await render.render_og_image(
            title=title,
            subtitle=article.meta.excerpt,
            pillar_label=subject.pillar.name if subject.pillar is not None else None,
            brand=brand,
        )
        og = await store_media(
            blobs,
            image,
            role="og_image",
            area="articles",
            owner_id=article.id,
            content_type="image/png",
            now=now,
            alt_text=title,
            width=1200,
            height=630,
        )
        made.append(og)
    except RenderFailed as exc:
        failures.append(exc.code)
    hero = "owner" if owner_hero is not None else None
    if owner_hero is None and og is not None:
        made.append(og.model_copy(update={"role": "hero_image", "alt_text": title}))
        hero = "og"
    media = [*kept, *made][:50]
    if [item.model_dump() for item in media] != [item.model_dump() for item in article.media]:
        await _write(run, media=media)
        await _changed(run)
    codes: list[JsonValue] = list(dict.fromkeys(failures))
    return {"diagrams": len(sources), "og": og is not None, "hero": hero, "failures": codes}


# --------------------------------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------------------------------


class _FinalState(JsonPayload):
    decision: Literal["auto", "review"] | None = None
    reasons: list[str] = Field(default_factory=list[str])
    publish_at: str | None = None
    publish_job_id: str | None = None
    pushed: bool = False


def _calibration(article: Article, *, keep_gated: bool) -> CalibrationFlags:
    """How this article counts toward auto-publish calibration (the publish package classifies
    the review against ``gatedVersion``, which version pruning keeps)."""
    sha = article.compute_sha256()
    report = article.gates
    passed = report is not None and report.content_sha256 == sha and report.auto_publish_ready
    earlier = article.calibration
    if keep_gated and earlier is not None and earlier.gated_version is not None:
        return earlier.model_copy(
            update={"gates_passed": passed, "review_only": article.review_only, "outcome": None}
        )
    return CalibrationFlags(
        gated_sha256=sha,
        gated_version=max(1, article.version),
        gates_passed=passed,
        review_only=article.review_only,
    )


async def _decide(run: _Run, article: Article) -> _FinalState:
    """The auto path needs everything at once: a flagship that is not live (a correction needs the
    owner), all seven gates passed on their own for this content, a draft that is not
    review-only, a calibrated and enabled autonomy that is not paused, and a valid clearance.
    Anything else goes to review."""
    now = run.now()
    stamp = to_iso(now)
    sha = article.compute_sha256()
    reasons: list[str] = []
    if article.purpose == "eval":
        reasons.append("eval")
    if article.publish.live:
        reasons.append("live_article")  # a correction needs the owner's dated note
    report = article.gates
    if report is None or report.content_sha256 != sha or not report.auto_publish_ready:
        reasons.append("gates")
    if article.review_only:
        reasons.append("review_only")
    autonomy, _ = await read_singleton(run.store, AutonomySettings, now=stamp)
    reasons.extend(auto_publish_decision(autonomy, now=now).reasons)
    if not article.clearance.valid_for(sha):
        reasons.append("clearance")
    if reasons:
        return _FinalState(decision="review", reasons=reasons)
    cadence, _ = await read_singleton(run.store, CadenceSettings, now=stamp)
    slot = next_article_publish(
        cadence, after=now, min_lead=timedelta(hours=cadence.min_veto_lead_hours)
    )
    return _FinalState(decision="auto", publish_at=slot.utc)


async def _enqueue_publish(
    run: _Run, article: Article, state: _FinalState, publish_at: str
) -> _FinalState:
    payload = PublishInput(
        article_id=article.id,
        version=max(1, article.version),
        publish_at=publish_at,
        auto=True,
        origin=run.origin,
    )
    try:
        job = await enqueue_once(
            run.services.jobs,
            PUBLISH_ARTICLE,
            payload.to_json(),
            job_id=publish_job_id(article.id, max(1, article.version)),
            run_after=parse_iso(publish_at),
            on_conflict="rerun",
        )
    except ValueError:
        # Nothing could publish it: an approved article without its job would be stranded.
        _logger.warning("job_hook_unregistered", extra={"job_type": PUBLISH_ARTICLE})
        return state.model_copy(
            update={"decision": "review", "reasons": ["publish_unavailable"], "publish_at": None}
        )
    return state.model_copy(update={"publish_job_id": job.id})


async def _cancel_quietly(run: _Run, job_id: str) -> None:
    try:
        await run.services.jobs.cancel(job_id)
    except ClaimLost:
        raise
    except Exception as exc:
        log_failure("pipeline_job_not_cancelled", exc, article_id=run.article_id)


async def _finish(run: _Run, article: Article, state: _FinalState) -> JsonValue:
    if run.parent is not None:
        await finish_child(
            run.ctx, run.services.jobs, run.parent, ChildResult(ok=True, ref=article.id)
        )
    await _changed(run)
    result = ChildResult(ok=True, ref=article.id).to_json()
    reasons: list[JsonValue] = list(state.reasons)
    result.update(
        {
            "status": article.status.value,
            "decision": state.decision,
            "reasons": reasons,
            "publishAt": state.publish_at,
            "publishJobId": state.publish_job_id,
        }
    )
    return result


async def _finalize(run: _Run) -> JsonValue:
    state = _state(_FinalState, run.ctx.output)
    stored = await _own(run, {_S.GATING, _S.IN_REVIEW, _S.APPROVED})
    article = stored.doc
    if article.status is not _S.GATING and state.decision is None:
        raise Stopped("article_moved", "The article left the pipeline.")
    if state.decision is None:
        state = await _decide(run, article)
        await run.ctx.checkpoint(state.to_json())
    if state.decision == "auto" and state.publish_job_id is None and state.publish_at is not None:
        state = await _enqueue_publish(run, article, state, state.publish_at)
        await run.ctx.checkpoint(state.to_json())
    if article.status is _S.GATING:
        flags = _calibration(article, keep_gated=False)
        if state.decision == "auto":
            publish_at, job_id = state.publish_at, state.publish_job_id

            def approve(doc: Article) -> Article:
                publish = doc.publish.model_copy(
                    update={"publish_at": publish_at, "auto": True, "job_id": job_id}
                )
                # A notice or veto of an earlier approval never covers this one.
                return doc.model_copy(
                    update={"publish": publish, "calibration": flags, "veto": None}
                )

            try:
                stored = await _move(run, _S.APPROVED, sources={_S.GATING}, update=approve)
            except Stopped:
                if job_id is not None:  # nothing may publish an article that left the pipeline
                    await _cancel_quietly(run, job_id)
                raise
        else:
            stored = await _move(
                run,
                _S.IN_REVIEW,
                sources={_S.GATING},
                update=lambda doc: doc.model_copy(update={"calibration": flags}),
            )
        article = stored.doc
    if state.decision == "review" and not state.pushed and article.purpose != "eval":
        await push(
            run.services,
            title="Your article is ready for review",
            url=f"/studio/{article.id}/draft",
            tag=f"review:{article.id}",
        )
        state = state.model_copy(update={"pushed": True})
        await run.ctx.checkpoint(state.to_json())
    _logger.info(
        "pipeline_finalized",
        extra={"article_id": article.id, "code": state.decision or "review"},
    )
    return await _finish(run, article, state)


# --------------------------------------------------------------------------------------------
# pipeline.revise: revise, then normalize, gates and its own finalize
# --------------------------------------------------------------------------------------------


async def _revise(run: _Run) -> JsonValue:
    instruction = run.instruction
    if instruction is None:
        raise JobFailed("invalid_input")
    stored = await _own(run, {_S.DRAFTING})
    article = stored.doc
    state = _state(_WriteState, run.ctx.output)
    done = await _landed(run, state, article)
    if done is not None:
        return done
    if not article.markdown.strip():
        raise JobFailed("draft_missing", "There is no draft to revise.")
    material = await load_material(run.services, article, now=run.now())
    bank = select_bank(
        material.bank,
        [ref.id for ref in article.owner_signals if ref.kind == "story"],
        [ref.id for ref in article.owner_signals if ref.kind == "pov"],
    )
    request = owner_revise_request(
        guide=material.guide,
        brief=material.brief,
        bank=bank,
        interview=material.interview,
        pack=material.pack,
        pillar=material.pillar,
        title=article.meta.title,
        draft=article.markdown,
        instruction=instruction,
    )
    screen_request(request, material.blocklist)
    await _start_write(run, article)
    streamed = await stream_text(
        run.ctx,
        run.services,
        Task.REVISE,
        request,
        origin=run.origin,
        article_id=run.article_id,
        step="pipeline.revise",
        prompt_version=REVISE,
    )
    if streamed.refused:
        await _write(run, served_by=[streamed.record], review_only=("refusal",))
        raise JobFailed("refused", "The model declined this revision.")
    citations = material.brief.citations if material.brief is not None else []
    bound = bind_citations(
        clean_draft(streamed.text), citations, keep=existing_definitions(article.markdown)
    )
    normalized = await asyncio.to_thread(normalize_body, bound.text)
    if not normalized.text.strip():
        raise JobFailed("empty_draft", "The model returned an empty revision.")
    written = await _write(
        run,
        markdown=normalized.text,
        served_by=[streamed.record],
        review_only=streamed.review_only,
        note="Revision the owner asked for",
    )
    out: dict[str, JsonValue] = {
        "version": written.doc.version,
        "reviewOnly": list(streamed.review_only),
    }
    await run.ctx.checkpoint(_WriteState(written=out).to_json())
    await _changed(run)
    return out


async def _finalize_revision(run: _Run) -> JsonValue:
    state = _state(_FinalState, run.ctx.output)
    stored = await _own(run, {_S.GATING, _S.IN_REVIEW})
    article = stored.doc
    if article.status is not _S.GATING and state.decision is None:
        raise Stopped("article_moved", "The article left the pipeline.")
    if state.decision is None:
        state = _FinalState(decision="review", reasons=["owner_revision"])
        await run.ctx.checkpoint(state.to_json())
    if article.status is _S.GATING:
        flags = _calibration(article, keep_gated=True)
        stored = await _move(
            run,
            _S.IN_REVIEW,
            sources={_S.GATING},
            update=lambda doc: doc.model_copy(update={"calibration": flags}),
        )
        article = stored.doc
    if not state.pushed and article.purpose != "eval":
        await push(
            run.services,
            title="Your revised article is ready",
            url=f"/studio/{article.id}/draft",
            tag=f"review:{article.id}",
        )
        state = state.model_copy(update={"pushed": True})
        await run.ctx.checkpoint(state.to_json())
    return await _finish(run, article, state)


# --------------------------------------------------------------------------------------------
# Registration helpers
# --------------------------------------------------------------------------------------------


def article_steps() -> list[tuple[str, _StepFunction]]:
    """The ``pipeline.article`` steps in order."""
    bodies: Sequence[tuple[str, _Body]] = (
        ("research", _research),
        ("interview_questions", _interview_questions),
        ("interview", _interview),
        ("outline", _outline),
        ("draft", _draft),
        ("normalize", _normalize),
        ("critique_revise", _critique_revise),
        ("gates", _gates),
        ("assets", _assets),
        ("finalize", _finalize),
    )
    return [(name, _step(_article_run, body)) for name, body in bodies]


def revise_steps() -> list[tuple[str, _StepFunction]]:
    """The ``pipeline.revise`` steps in order."""
    bodies: Sequence[tuple[str, _Body]] = (
        ("revise", _revise),
        ("normalize", _normalize),
        ("gates", _gates),
        ("finalize", _finalize_revision),
    )
    return [(name, _step(_revise_run, body)) for name, body in bodies]
