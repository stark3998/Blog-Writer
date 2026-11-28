"""The eval set: topics, eval runs (``analytics.eval``) and the judge's calibration.

**Topics.** 12 by default (4 formats x 3 pillars; at most :data:`MAX_ACTIVE_TOPICS` active).
A topic names a configured pillar and format pack and clears the blocklist (it becomes a research
topic). Editing the topic or pillar, or ``refreshBrief``, clears its brief so the next run
researches it again.

**Runs** measure the generator that ships, not a copy of it. ``analytics.eval`` (owner origin;
children inherit it), per active topic, checkpointed:

``brief``
    A topic without a usable brief gets one research child: ``create_run(run_id=
    ResearchRun.id_for_eval(topicId, jobId))`` + ``start_run(parent=(jobId, "brief-<n>"))``,
    then ``wait_for_child``; a good result becomes the topic's ``briefId``. The children run in
    parallel; the step waits for each in turn (a child that finished first is read, not lost).
``draft``
    The eval article ``Article.id_for_eval(topicId, jobId)`` (``purpose=eval``; ``Conflict`` =
    it exists) linked to the brief, moved to ``drafting`` like the pipeline route does, and the
    real ``pipeline.article`` as a child (``pipeline_job_id(articleId, 1)``,
    ``PipelineInput(start_from="draft", interview="skip", parent=(jobId, "eval-<n>"))``), then
    ``wait_for_child``. Without a registered pipeline the article goes to ``needs_attention``
    and the job fails ``pipeline_unavailable`` instead of waiting forever.
``judge``
    ``gates.run_gates(article, gates={GateName.RUBRIC})``: the judge is gate 1 with its
    ``RUBRIC_*`` thresholds. ``EvalResult`` (``EvalResult.id_for(topicId, jobId)``):
    ``judgePassed`` = the rubric passed on its own, ``judgeScore`` = its overall score. A topic
    without a draft, or a judge that could not run, gets a result with ``judgePassed: null`` and
    ``errorCode``.
``notify``
    Push "Eval drafts ready to label" (once).

Eval articles are never approved or published (``check_article_transition``: ``eval_article``)
and lists hide them.

**Calibration** (``autonomy.judge``, recomputed on every label): over the results of the
labelled result's run, ``asIsRate`` = judge-passed results labelled ``as_is`` / all judge-passed
results. A judge-passed result that is not labelled yet counts against the rate (fail closed:
the rate can only reach the 90 % threshold once the owner agrees with the judge).
``sampleSize`` = judge-passed results; ``checkedAt`` = when the run judged (so the monthly
re-check is a new run, not a new label). No judge-passed result: ``asIsRate`` stays unknown.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from pydantic import Field, JsonValue

from blogwriter.analytics._support import MAX_ENTITY_EVENTS, as_object, as_strings, job_origin
from blogwriter.articles import ArticleLinks, create_article, set_links, transition
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.analytics import EvalLabel, EvalResult, EvalTopic
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import Article, ArticleMeta, GateName, GateResult, GateVerdict
from blogwriter.domain.base import Slug, distinct_stamps, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    Stored,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    update_doc,
    update_singleton,
)
from blogwriter.domain.jobflow import enqueue_once, step_services, wait_for_child
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    PIPELINE_ARTICLE,
    ParentRef,
    PipelineInput,
    entity_changed,
    pipeline_job_id,
    research_job_id,
)
from blogwriter.domain.ports import JobQueue, Services
from blogwriter.domain.research import Brief, ResearchRun
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    EmployerPolicy,
    FormatPacks,
    PillarSettings,
)
from blogwriter.domain.states import ArticleStatus, GuideStatus
from blogwriter.domain.voice import StyleGuide
from blogwriter.gates import run_gates
from blogwriter.jobs import Job, JobFailed, Step, StepContext
from blogwriter.notify.payload import NotificationPayload
from blogwriter.research import create_run, start_run

__all__ = [
    "EVAL_STEPS",
    "MAX_ACTIVE_TOPICS",
    "EvalLabelIn",
    "EvalRefused",
    "EvalTopicIn",
    "EvalTopicUpdate",
    "JudgeStats",
    "active_topics",
    "check_run",
    "check_topic",
    "check_topic_room",
    "error_code_of",
    "eval_brief",
    "eval_draft",
    "eval_job_id",
    "eval_judge",
    "eval_notify",
    "judge_stats",
    "judge_verdict",
    "labelled",
    "recompute_judge",
    "with_judge",
]

_logger: Final = logging.getLogger("blogwriter.analytics.evals")
register_log_fields("eval_run", "topic_id", "result_id", "article_id")

MAX_ACTIVE_TOPICS: Final = 20
"""The plan's set is 12 (4 formats x 3 pillars); a hard cap keeps a run's cost bounded."""
PUSH_TITLE: Final = "Eval drafts ready to label"
EVALS_URL: Final = "/settings/system"
ERROR_CODE: Final = "errorCode"
"""The ``EvalResult.error_code`` key in a stored (camelCase) result."""
_RATE_DIGITS: Final = 4
_BLOCKLIST_DETAIL: Final = "Save the blocklist once in Settings > Policy first."
_RESEARCH_PROMPT: Final = "Eval research is running."
_DRAFT_PROMPT: Final = "Eval drafts are being written."

type EvalSlug = Annotated[Slug, Field(max_length=40)]


class EvalRefused(ValueError):
    """A problem the eval rules give (``status``, ``code``, owner-facing ``detail``)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


class EvalTopicIn(ApiModel):
    """``POST /api/evals/topics``."""

    pillar: EvalSlug
    format: EvalSlug
    topic: str = Field(min_length=3, max_length=300)
    active: bool = True


class EvalTopicUpdate(EvalTopicIn):
    """``PUT /api/evals/topics/{id}``; ``refreshBrief`` makes the next run research it again."""

    refresh_brief: bool = False


class EvalLabelIn(ApiModel):
    label: EvalLabel


def eval_job_id(now: datetime) -> str:
    """The owner's eval run of the day (``enqueue_once`` with ``rerun``: a double tap returns
    the active run, a later run that day gets ``-r<n>``)."""
    return f"analytics-eval-{now:%Y%m%d}"


def _brief_key(index: int) -> str:
    return f"brief-{index}"


def _eval_key(index: int) -> str:
    return f"eval-{index}"


# --------------------------------------------------------------------------------------------
# Topics and run checks (routes)
# --------------------------------------------------------------------------------------------


async def check_topic(store: Store, body: EvalTopicIn, *, stamp: str) -> str:
    """The topic text to store, after the pillar, format and blocklist checks (raises
    :class:`EvalRefused`, ``ConfidentialText`` or ``BlocklistNotConfigured``)."""
    topic = " ".join(body.topic.split())
    if len(topic) < 3:
        raise EvalRefused(422, "topic_too_short", "Describe the topic in a few words.")
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    if pillars.get(body.pillar) is None:
        raise EvalRefused(422, "unknown_pillar", "Pick one of your pillars.")
    packs, _ = await read_singleton(store, FormatPacks, now=stamp)
    if packs.get(body.format) is None:
        raise EvalRefused(422, "unknown_format", "Pick one of your format packs.")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    check_texts({"topic": topic}, blocklist)
    return topic


async def active_topics(store: Store, *, strict: bool = False) -> list[Stored[EvalTopic]]:
    """Active topics, oldest first."""
    return await find_docs(
        store,
        EvalTopic,
        [Filter("active", "==", True)],
        order_by=OrderBy("createdAt"),
        strict=strict,
    )


async def check_topic_room(store: Store, *, excluding: str | None = None) -> None:
    """409 ``too_many_topics`` when :data:`MAX_ACTIVE_TOPICS` are active (strict)."""
    active = await active_topics(store, strict=True)
    if sum(1 for stored in active if stored.doc.id != excluding) >= MAX_ACTIVE_TOPICS:
        raise EvalRefused(
            409,
            "too_many_topics",
            f"At most {MAX_ACTIVE_TOPICS} topics can be active. Deactivate one first.",
        )


async def check_run(store: Store, *, stamp: str) -> None:
    """Before a run: active topics, an active voice guide, the blocklist and the AI-use
    confirmation (raises :class:`EvalRefused`, ``BlocklistNotConfigured``,
    ``AiUseNotConfirmed``)."""
    if not await active_topics(store, strict=True):
        raise EvalRefused(409, "no_eval_topics", "Add the eval topics first.")
    guides = await find_docs(
        store, StyleGuide, [Filter("status", "==", GuideStatus.ACTIVE.value)], limit=1, strict=True
    )
    if not guides:
        raise EvalRefused(
            409, "voice_guide_missing", "Activate a Voice & Style Guide first: evals draft with it."
        )
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    if not blocklist.configured:
        raise BlocklistNotConfigured()
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    require_ai_use(policy)


# --------------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgeStats:
    """One run's judge against the owner's labels."""

    judge_passed: int
    labelled: int
    """Judge-passed results the owner labelled."""
    as_is: int
    """Judge-passed results labelled ``as_is``."""
    checked_at: str | None
    """When the run judged (its newest judged result)."""

    @property
    def as_is_rate(self) -> float | None:
        """``as_is / judge_passed`` (unlabelled ones count against it); ``None`` without any."""
        if self.judge_passed == 0:
            return None
        return round(self.as_is / self.judge_passed, _RATE_DIGITS)


def judge_stats(results: Iterable[EvalResult]) -> JudgeStats:
    """The calibration numbers of one run's results."""
    items = list(results)
    passed = [result for result in items if result.judge_passed is True]
    labelled_ones = [result for result in passed if result.owner_label is not None]
    as_is = [result for result in labelled_ones if result.owner_label == "as_is"]
    judged = [result.created_at for result in items if result.judge_passed is not None]
    return JudgeStats(
        judge_passed=len(passed),
        labelled=len(labelled_ones),
        as_is=len(as_is),
        checked_at=max(judged) if judged else None,
    )


def with_judge(
    autonomy: AutonomySettings, stats: JudgeStats, *, now: str
) -> AutonomySettings | None:
    """``autonomy`` with ``judge`` from ``stats`` (threshold and max age stay the owner's);
    ``None`` when nothing changes."""
    judge = autonomy.judge.model_copy(
        update={
            "as_is_rate": stats.as_is_rate,
            "sample_size": stats.judge_passed,
            "checked_at": stats.checked_at,
        }
    )
    if judge == autonomy.judge:
        return None
    return autonomy.model_copy(update={"judge": judge, "updated_at": now})


async def recompute_judge(store: Store, run_id: str, *, now: datetime) -> AutonomySettings:
    """Recompute ``autonomy.judge`` from one run's results (strict: an unreadable result fails
    the recomputation instead of being left out)."""
    stamp = to_iso(now)
    found = await find_docs(store, EvalResult, [Filter("runId", "==", run_id)], strict=True)
    stats = judge_stats(stored.doc for stored in found)
    doc, _ = await update_singleton(
        store, AutonomySettings, lambda current: with_judge(current, stats, now=stamp), now=stamp
    )
    return doc


def labelled(current: EvalResult, label: EvalLabel, *, now: datetime) -> EvalResult:
    """The result with the owner's label (409 ``eval_not_ready`` without a draft)."""
    if current.article_id is None:
        raise EvalRefused(409, "eval_not_ready", "This topic has no draft to label.")
    stamp = to_iso(now)
    return current.model_copy(
        update={"owner_label": label, "labelled_at": stamp, "updated_at": stamp}
    )


def error_code_of(result: EvalResult) -> str | None:
    """The result's ``errorCode`` (why it has no judged draft), or ``None``."""
    return result.error_code


# --------------------------------------------------------------------------------------------
# Step helpers
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Topic:
    index: int
    topic_id: str
    brief_id: str | None = None
    article_id: str | None = None
    drafted: bool = False
    error_code: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"index": self.index, "topicId": self.topic_id}
        if self.brief_id is not None:
            out["briefId"] = self.brief_id
        if self.article_id is not None:
            out["articleId"] = self.article_id
            out["drafted"] = self.drafted
        if self.error_code is not None:
            out["errorCode"] = self.error_code
        return out

    @classmethod
    def from_json(cls, value: JsonValue) -> _Topic | None:
        data = as_object(value)
        index, topic_id = data.get("index"), data.get("topicId")
        if not isinstance(index, int) or not isinstance(topic_id, str):
            return None
        brief, article, error = data.get("briefId"), data.get("articleId"), data.get("errorCode")
        return cls(
            index=index,
            topic_id=topic_id,
            brief_id=brief if isinstance(brief, str) else None,
            article_id=article if isinstance(article, str) else None,
            drafted=data.get("drafted") is True,
            error_code=error if isinstance(error, str) else None,
        )


def _topics_of(output: JsonValue) -> list[_Topic]:
    raw = as_object(output).get("topics")
    items = raw if isinstance(raw, list) else []
    return [topic for item in items if (topic := _Topic.from_json(item)) is not None]


def _output(topics: Sequence[_Topic]) -> dict[str, JsonValue]:
    ordered = sorted(topics, key=lambda topic: topic.index)
    return {"topics": [topic.to_json() for topic in ordered]}


async def _read_optional[D: EvalTopic | Brief | Article | EvalResult](
    store: Store, cls: type[D], doc_id: str
) -> D | None:
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except (NotFound, InvalidStoredDocument):
        return None


async def _job_or_none(jobs: JobQueue, job_id: str) -> Job | None:
    try:
        return await jobs.get(job_id)
    except NotFound:
        return None


async def _set_topic_brief(store: Store, topic_id: str, brief_id: str, *, now: str) -> None:
    def mutate(topic: EvalTopic) -> EvalTopic | None:
        if topic.brief_id == brief_id:
            return None
        return topic.model_copy(update={"brief_id": brief_id, "updated_at": now})

    with contextlib.suppress(NotFound):  # deleted during the run: the brief still serves it
        await update_doc(store, EvalTopic, topic_id, mutate)


# --------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------


async def _run_topics(ctx: StepContext, store: Store) -> list[str]:
    """The run's topics: fixed at the first attempt of ``brief`` (checkpointed)."""
    state = as_object(ctx.output)
    if "topicIds" in state:
        return as_strings(state.get("topicIds"))
    topic_ids = [stored.doc.id for stored in await active_topics(store)][:MAX_ACTIVE_TOPICS]
    await ctx.checkpoint({"topicIds": list[JsonValue](topic_ids)})
    return topic_ids


async def eval_brief(ctx: StepContext) -> JsonValue:
    """A brief for every topic of the run (research children where needed)."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    origin = job_origin(ctx)
    topic_ids = await _run_topics(ctx, store)
    done: list[_Topic] = []
    pending: list[tuple[int, str, str]] = []
    for index, topic_id in enumerate(topic_ids):
        topic = await _read_optional(store, EvalTopic, topic_id)
        if topic is None:
            done.append(_Topic(index, topic_id, error_code="topic_missing"))
            continue
        if topic.brief_id is not None and await _read_optional(store, Brief, topic.brief_id):
            done.append(_Topic(index, topic_id, brief_id=topic.brief_id))
            continue
        run_id = ResearchRun.id_for_eval(topic_id, ctx.job_id)
        try:
            run = await create_run(
                store, topic=topic.topic, pillar=topic.pillar, now=now, run_id=run_id
            )
        except BlocklistNotConfigured:
            raise JobFailed("blocklist_not_configured", _BLOCKLIST_DETAIL) from None
        except ConfidentialText:
            done.append(_Topic(index, topic_id, error_code="blocklist_hit"))
            continue
        parent = ParentRef(job_id=ctx.job_id, key=_brief_key(index))
        try:
            await start_run(services, run.doc, origin=origin, parent=parent)
        except ValueError:
            raise JobFailed("research_unavailable", "Research is not available.") from None
        pending.append((index, topic_id, run_id))
    for index, topic_id, run_id in pending:
        result = await wait_for_child(
            ctx,
            services.jobs,
            research_job_id(run_id),
            key=_brief_key(index),
            prompt=_RESEARCH_PROMPT,
        )
        if result.ok and result.ref:
            await _set_topic_brief(store, topic_id, result.ref, now=stamp)
            done.append(_Topic(index, topic_id, brief_id=result.ref))
        else:
            code = result.error_code or "research_failed"
            done.append(_Topic(index, topic_id, error_code=code))
    return _output(done)


async def _eval_article(
    store: Store, topic: EvalTopic, article_id: str, *, brief_id: str, job_id: str, now: datetime
) -> Article:
    """The run's eval article for ``topic``, created once and linked to the brief."""
    meta = ArticleMeta(title=topic.topic[:150], pillar=topic.pillar, format=topic.format)
    with contextlib.suppress(Conflict):  # a resumed step created it
        await create_article(store, now=now, meta=meta, purpose="eval", article_id=article_id)
    links = ArticleLinks(brief_id=brief_id, pipeline_job_id=job_id, pipeline_runs=1)
    return (await set_links(store, article_id, links, now=now)).doc


async def _hold(store: Store, article_id: str, *, now: datetime) -> None:
    """drafting -> needs_attention when no pipeline can run (the stuck state's exit)."""
    try:
        await transition(
            store,
            article_id,
            ArticleStatus.NEEDS_ATTENTION,
            actor="system",
            now=now,
            reason="pipeline_unavailable",
        )
    except Exception as exc:  # best effort: the job fails with its own code anyway
        _logger.warning(
            "analytics_eval_hold_failed",
            extra={"article_id": article_id, **describe_exception(exc)},
        )


async def _start_pipeline(
    services: Services,
    ctx: StepContext,
    topic: EvalTopic,
    entry: _Topic,
    brief_id: str,
    *,
    now: datetime,
) -> tuple[str, str]:
    """Create the eval article and enqueue its ``pipeline.article`` child; the ids."""
    store = services.store
    article_id = Article.id_for_eval(topic.id, ctx.job_id)
    child_id = pipeline_job_id(article_id, 1)
    article = await _eval_article(
        store, topic, article_id, brief_id=brief_id, job_id=child_id, now=now
    )
    if await _job_or_none(services.jobs, child_id) is None:
        if article.status in (ArticleStatus.PLANNED, ArticleStatus.NEEDS_ATTENTION):
            await transition(store, article_id, ArticleStatus.DRAFTING, actor="system", now=now)
        payload = PipelineInput(
            article_id=article_id,
            start_from="draft",
            interview="skip",
            parent=ParentRef(job_id=ctx.job_id, key=_eval_key(entry.index)),
            origin=job_origin(ctx),
        )
        try:
            await enqueue_once(services.jobs, PIPELINE_ARTICLE, payload.to_json(), job_id=child_id)
        except ValueError:
            await _hold(store, article_id, now=now)
            raise JobFailed(
                "pipeline_unavailable", "The article pipeline is not available."
            ) from None
    return article_id, child_id


async def eval_draft(ctx: StepContext) -> JsonValue:
    """The real pipeline drafts every topic that has a brief."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    done: list[_Topic] = []
    pending: list[tuple[_Topic, str, str]] = []
    for entry in _topics_of(ctx.outputs.get("brief")):
        if entry.brief_id is None:
            done.append(
                _Topic(entry.index, entry.topic_id, error_code=entry.error_code or "brief_missing")
            )
            continue
        topic = await _read_optional(store, EvalTopic, entry.topic_id)
        if topic is None:
            done.append(
                _Topic(entry.index, entry.topic_id, entry.brief_id, error_code="topic_missing")
            )
            continue
        article_id, child_id = await _start_pipeline(
            services, ctx, topic, entry, entry.brief_id, now=now
        )
        pending.append((entry, article_id, child_id))
    for entry, article_id, child_id in pending:
        result = await wait_for_child(
            ctx, services.jobs, child_id, key=_eval_key(entry.index), prompt=_DRAFT_PROMPT
        )
        done.append(
            _Topic(
                entry.index,
                entry.topic_id,
                entry.brief_id,
                article_id=article_id,
                drafted=result.ok,
                error_code=None if result.ok else result.error_code or "pipeline_failed",
            )
        )
    return _output(done)


def judge_verdict(
    rubric: GateResult | None,
) -> tuple[bool | None, float | None, str | None, list[ServedByRecord]]:
    """``(judgePassed, judgeScore, errorCode, servedBy)`` from gate 1's result.

    ``pass``/``warn`` pass, ``fail`` fails; a skipped or errored rubric (or none) did not judge.
    """
    if rubric is None:
        return None, None, "judge_missing", []
    records = list(rubric.served_by)[-10:]
    score = None if rubric.score is None else min(10.0, max(0.0, float(rubric.score)))
    match rubric.verdict:
        case GateVerdict.PASS | GateVerdict.WARN:
            return True, score, None, records
        case GateVerdict.FAIL:
            return False, score, None, records
        case _:
            return None, score, f"judge_{rubric.verdict.value}", records


async def _judge_one(
    services: Services, ctx: StepContext, entry: _Topic, *, stamp: str
) -> EvalResult:
    result_id = EvalResult.id_for(entry.topic_id, ctx.job_id)
    fields: dict[str, object] = {
        "id": result_id,
        "createdAt": stamp,
        "updatedAt": stamp,
        "topicId": entry.topic_id,
        "runId": ctx.job_id,
        "articleId": entry.article_id,
    }
    article = (
        await _read_optional(services.store, Article, entry.article_id)
        if entry.article_id is not None and entry.drafted
        else None
    )
    if article is None:
        code = entry.error_code or ("article_missing" if entry.drafted else "not_drafted")
        return EvalResult.model_validate({**fields, ERROR_CODE: code})
    report = await run_gates(
        article,
        services=services,
        origin=job_origin(ctx),
        job_id=ctx.job_id,
        gates={GateName.RUBRIC},
    )
    passed, score, code, records = judge_verdict(report.result(GateName.RUBRIC))
    fields.update({"judgePassed": passed, "judgeScore": score, "servedBy": records})
    if code is not None:
        fields[ERROR_CODE] = code
    return EvalResult.model_validate(fields)


async def eval_judge(ctx: StepContext) -> JsonValue:
    """Gate 1 alone on every eval article; one ``EvalResult`` per topic."""
    services = step_services()
    store = services.store
    topics = _topics_of(ctx.outputs.get("draft"))
    stamps = distinct_stamps(services.clock.now(), len(topics))
    ids: list[str] = []
    passed = judged = 0
    for entry, stamp in zip(topics, stamps, strict=True):
        existing = await _read_optional(
            store, EvalResult, EvalResult.id_for(entry.topic_id, ctx.job_id)
        )
        if existing is None:
            result = await _judge_one(services, ctx, entry, stamp=stamp)
            try:
                existing = (await create_doc(store, result)).doc
            except Conflict:
                existing = (await read_doc(store, EvalResult, result.id)).doc
            if len(ids) < MAX_ENTITY_EVENTS:
                await ctx.emit(ENTITY_CHANGED, entity_changed("eval_result", existing.id))
        ids.append(existing.id)
        judged += int(existing.judge_passed is not None)
        passed += int(existing.judge_passed is True)
    return {"resultIds": list[JsonValue](ids), "judged": judged, "passed": passed}


async def eval_notify(ctx: StepContext) -> JsonValue:
    """Tell the owner the drafts are ready to label (push once, best effort)."""
    judged = as_object(ctx.outputs.get("judge"))
    results = as_strings(judged.get("resultIds"))
    pushed = as_object(ctx.output).get("pushed")
    if not isinstance(pushed, bool):
        pushed = bool(results) and await _push(step_services(), ctx.job_id)
        await ctx.checkpoint({"pushed": pushed})
    return {
        "runId": ctx.job_id,
        "results": len(results),
        "judged": judged.get("judged"),
        "passed": judged.get("passed"),
        "pushed": pushed,
    }


async def _push(services: Services, run_id: str) -> bool:
    payload = NotificationPayload(
        title=PUSH_TITLE, url=f"{EVALS_URL}?evalRun={run_id}", tag=f"evals:{run_id}"[:64]
    )
    try:
        result = await services.notifier.notify(payload, urgency="low")
    except Exception as exc:  # push is best effort: the results are listed anyway
        _logger.warning(
            "analytics_eval_push_failed", extra={"eval_run": run_id, **describe_exception(exc)}
        )
        return False
    return result.any_delivered


EVAL_STEPS: Final = (
    Step("brief", eval_brief),
    Step("draft", eval_draft),
    Step("judge", eval_judge),
    Step("notify", eval_notify),
)
