"""Job types of the gates: ``gates.run`` and ``gates.standards-refresh``.

``gates.run`` (input :class:`~blogwriter.domain.jobtypes.GatesRunInput`, id
``gates_job_id(articleId, sha)``, re-runs ``-r<n>``) checkpoints each gate's draft:

==================  ===========================================================================
step                what it does
==================  ===========================================================================
``deterministic``   every gate's deterministic checks (``lint_text`` and more). Any blocklist
                    finding (``blocklist-not-configured`` included) ends the model work here
``network``         links through egress, diagrams through ``render_mermaid_svg``
``confidentiality`` ``services.classifier`` (mosaic check)
``fact_check``      FACT_CHECK mapping, re-fetch and verification (passages in memory only)
``originality``     embedding novelty (plus the passage verbatim counts of ``fact_check``)
``rubric``          CRITIQUE
``report``          ``save_report``: stale -> event ``gates.stale`` and no write; else
                    ``entity.changed`` article. ``finish_child`` when ``parent`` is set
==================  ===========================================================================

Every step re-reads the article; if its content no longer has the job's SHA-256 the rest of the
run is a no-op and ``report`` emits ``gates.stale``. Model steps run inside ``defer_on_brake``.

``gates.standards-refresh`` (input ``{}``): ``refresh`` merges the shipped seed lists, the stored
catalog and (with ``BW_GATES_ATLAS_URL``) the MITRE ATLAS data file fetched through egress. A
failed fetch keeps the old ATLAS ids, saves the rest and fails the job ``atlas_unavailable``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Final, cast

from pydantic import JsonValue, ValidationError

from blogwriter.ai.ledger import BrakeEngaged
from blogwriter.core.egress import EgressError
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import NotFound
from blogwriter.domain.articles import GATE_ORDER, Article, GateName, GateReport
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import InvalidStoredDocument, read_doc, update_singleton
from blogwriter.domain.jobflow import (
    JobDeferred,
    defer_on_brake,
    finish_child,
    step_services,
)
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    GATES_RUN,
    GATES_STANDARDS_REFRESH,
    ChildResult,
    GatesRunInput,
    JobInput,
    entity_changed,
)
from blogwriter.domain.ports import Services
from blogwriter.domain.settings import StandardsCatalog
from blogwriter.gates.confidentiality import classifier_draft
from blogwriter.gates.deterministic import deterministic_checks
from blogwriter.gates.document import prepare_text
from blogwriter.gates.factcheck import fact_check_draft
from blogwriter.gates.inputs import GateInputs, load_inputs
from blogwriter.gates.originality import novelty, passage_verbatim_findings
from blogwriter.gates.results import GateDraft, finalize
from blogwriter.gates.rubric import rubric_draft
from blogwriter.gates.run import (
    after_blocklist_hit,
    default_slop,
    guarded,
    network_draft,
    save_report,
)
from blogwriter.gates.settings import gates_settings
from blogwriter.gates.standards import AtlasData, merge_catalog, parse_atlas
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext

__all__ = ["GATES_RUN_STEPS", "STALE_EVENT", "register_jobs"]

STALE_EVENT: Final = "gates.stale"
GATES_RUN_STEPS: Final = (
    "deterministic",
    "network",
    "confidentiality",
    "fact_check",
    "originality",
    "rubric",
    "report",
)
_STEP_GATES: Final[Mapping[str, GateName]] = {
    "network": GateName.LINT,
    "confidentiality": GateName.CONFIDENTIALITY,
    "fact_check": GateName.FACT_CHECK,
    "originality": GateName.ORIGINALITY,
    "rubric": GateName.RUBRIC,
}

_logger: Final = logging.getLogger("blogwriter.gates.jobs")
register_log_fields("article_id", "job_id", "atlas")


def register_jobs(registry: JobRegistry) -> None:
    """Register ``gates.run`` and ``gates.standards-refresh``."""
    registry.register(
        GATES_RUN,
        [
            Step("deterministic", _deterministic),
            Step("network", _network),
            Step("confidentiality", _confidentiality),
            Step("fact_check", _fact_check),
            Step("originality", _originality),
            Step("rubric", _rubric),
            Step("report", _report),
        ],
    )
    registry.register(GATES_STANDARDS_REFRESH, [Step("refresh", _refresh)])


# --------------------------------------------------------------------------------------------
# gates.run
# --------------------------------------------------------------------------------------------


def _input(ctx: StepContext) -> GatesRunInput:
    try:
        return GatesRunInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input", "The gates job input is invalid.") from None


async def _article(ctx: StepContext, services: Services, run: GatesRunInput) -> Article:
    try:
        return (await read_doc(services.store, Article, run.article_id)).doc
    except (NotFound, InvalidStoredDocument):
        await finish_child(
            ctx, services.jobs, run.parent, ChildResult(ok=False, error_code="article_not_found")
        )
        raise JobFailed("article_not_found", "The article no longer exists.") from None


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value) if isinstance(value, dict) else {}


def _deterministic_output(ctx: StepContext) -> dict[str, JsonValue]:
    return _mapping(ctx.outputs.get("deterministic"))


def _skip_model_work(ctx: StepContext) -> bool:
    output = _deterministic_output(ctx)
    return (
        bool(output.get("stale"))
        or bool(output.get("blocklistHit"))
        or any(bool(_mapping(ctx.outputs.get(step)).get("stale")) for step in _STEP_GATES)
    )


def _draft_json(draft: GateDraft) -> JsonValue:
    return cast(JsonValue, draft.model_dump(mode="json", by_alias=True))


def _stored_draft(ctx: StepContext, gate: GateName) -> GateDraft:
    drafts = _mapping(_deterministic_output(ctx).get("drafts"))
    value = drafts.get(gate.value)
    if value is None:
        return GateDraft.of(gate)
    return GateDraft.model_validate(value)


async def _prepare(ctx: StepContext) -> tuple[Services, GatesRunInput, Article] | None:
    """The services, input and article for a model step, or ``None`` when the run stopped
    (stale content, a blocklist hit) or the article changed now (the step records it)."""
    services = step_services()
    run = _input(ctx)
    if _skip_model_work(ctx):
        return None
    article = await _article(ctx, services, run)
    if article.compute_sha256() != run.content_sha256:
        return None
    return services, run, article


async def _deterministic(ctx: StepContext) -> JsonValue:
    services = step_services()
    run = _input(ctx)
    article = await _article(ctx, services, run)
    if article.compute_sha256() != run.content_sha256:
        return {"stale": True}
    now = services.clock.now()
    inputs = await load_inputs(services.store, article, now=to_iso(now))
    checks = await asyncio.to_thread(
        deterministic_checks,
        article,
        inputs,
        slop=default_slop(services),
        site_host=gates_settings().site_host,
        content_sha256=run.content_sha256,
        now=now,
    )
    drafts: dict[str, JsonValue] = {
        gate.value: _draft_json(draft) for gate, draft in checks.drafts.items()
    }
    return {
        "stale": False,
        "blocklistHit": checks.blocklist_hit,
        "words": checks.measure.words,
        "drafts": drafts,
    }


def _step_output(draft: GateDraft, **extra: JsonValue) -> JsonValue:
    return {"stale": False, "draft": _draft_json(draft), **extra}


async def _network(ctx: StepContext) -> JsonValue:
    prepared = await _prepare(ctx)
    if prepared is None:
        return _stale_or_skipped(ctx)
    services, _, article = prepared
    text = await asyncio.to_thread(prepare_text, article.meta, article.markdown)
    found = await guarded(
        GateName.LINT, network_draft(text, services=services, settings=gates_settings())
    )
    return _step_output(_stored_draft(ctx, GateName.LINT).merge(found))


async def _confidentiality(ctx: StepContext) -> JsonValue:
    prepared = await _prepare(ctx)
    if prepared is None:
        return _stale_or_skipped(ctx)
    services, run, article = prepared
    text = await asyncio.to_thread(prepare_text, article.meta, article.markdown)
    with defer_on_brake(services.clock.now()):
        classified = await guarded(
            GateName.CONFIDENTIALITY,
            classifier_draft(
                text, classifier=services.classifier, origin=run.origin, job_id=ctx.job_id
            ),
        )
    return _step_output(_stored_draft(ctx, GateName.CONFIDENTIALITY).merge(classified))


async def _inputs(services: Services, article: Article) -> GateInputs:
    return await load_inputs(services.store, article, now=to_iso(services.clock.now()))


async def _fact_check(ctx: StepContext) -> JsonValue:
    prepared = await _prepare(ctx)
    if prepared is None:
        return _stale_or_skipped(ctx)
    services, run, article = prepared
    inputs = await _inputs(services, article)
    base = _stored_draft(ctx, GateName.FACT_CHECK)
    try:
        outcome = await fact_check_draft(
            article,
            brief=inputs.brief,
            stories=inputs.stories,
            povs=inputs.povs,
            interviews=inputs.interviews,
            policy=inputs.policy,
            blocklist=inputs.blocklist,
            runner=services.ai,
            fetcher=services.fetcher,
            origin=run.origin,
            job_id=ctx.job_id,
            now=services.clock.now(),
        )
    except (JobDeferred, BrakeEngaged):
        raise
    except Exception as exc:
        _logger.warning(
            "gates_gate_failed", extra={"job_id": ctx.job_id, **describe_exception(exc)}
        )
        return _step_output(base.with_error("unexpected"), passageCounts={})
    counts: dict[str, JsonValue] = dict(outcome.passage_counts.items())
    return _step_output(base.merge(outcome.draft), passageCounts=counts)


def _passage_counts(ctx: StepContext) -> dict[str, int]:
    counts = _mapping(_mapping(ctx.outputs.get("fact_check")).get("passageCounts"))
    return {key: value for key, value in counts.items() if isinstance(value, int)}


async def _originality(ctx: StepContext) -> JsonValue:
    prepared = await _prepare(ctx)
    if prepared is None:
        return _stale_or_skipped(ctx)
    services, run, article = prepared
    inputs = await _inputs(services, article)
    base = _stored_draft(ctx, GateName.ORIGINALITY)
    extra = await asyncio.to_thread(
        passage_verbatim_findings,
        _passage_counts(ctx),
        body=article.markdown,
        brief=inputs.brief,
    )
    try:
        with defer_on_brake(services.clock.now()):
            result = await novelty(
                article,
                store=services.store,
                embedder=services.embedder,
                policy=inputs.policy,
                preferences=inputs.preferences,
                corpus_size=gates_settings().novelty_corpus,
                origin=run.origin,
                job_id=ctx.job_id,
            )
    except (JobDeferred, BrakeEngaged):
        raise
    except Exception as exc:
        _logger.warning(
            "gates_gate_failed", extra={"job_id": ctx.job_id, **describe_exception(exc)}
        )
        return _step_output(base.with_findings(extra).with_error("unexpected"))
    draft = base.with_findings([*extra, *result.findings])
    if result.max_similarity is not None:
        draft = draft.model_copy(update={"score": round(result.max_similarity, 4)})
    embedding: JsonValue = (
        cast(JsonValue, [float(value) for value in result.embedding])
        if result.embedding is not None
        else None
    )
    return _step_output(draft, embedding=embedding)


async def _rubric(ctx: StepContext) -> JsonValue:
    prepared = await _prepare(ctx)
    if prepared is None:
        return _stale_or_skipped(ctx)
    services, run, article = prepared
    inputs = await _inputs(services, article)
    words = _deterministic_output(ctx).get("words")
    draft = await guarded(
        GateName.RUBRIC,
        rubric_draft(
            article,
            runner=services.ai,
            policy=inputs.policy,
            blocklist=inputs.blocklist,
            pillars=inputs.pillars,
            packs=inputs.format_packs,
            guide=inputs.guide,
            words=words if isinstance(words, int) else 0,
            origin=run.origin,
            job_id=ctx.job_id,
            now=services.clock.now(),
        ),
    )
    return _step_output(draft)


def _stale_or_skipped(ctx: StepContext) -> JsonValue:
    """The output of a model step that did not run: stale content or the blocklist."""
    output = _deterministic_output(ctx)
    if output.get("blocklistHit") and not output.get("stale"):
        return {"stale": False, "skipped": "blocklist_hit"}
    return {"stale": True}


def _final_drafts(ctx: StepContext) -> dict[GateName, GateDraft]:
    output = _deterministic_output(ctx)
    base = {gate: _stored_draft(ctx, gate) for gate in GATE_ORDER}
    if output.get("blocklistHit"):
        return after_blocklist_hit(base)
    drafts = dict(base)
    for step, gate in _STEP_GATES.items():
        value = _mapping(ctx.outputs.get(step)).get("draft")
        if value is not None:
            drafts[gate] = GateDraft.model_validate(value)
    return drafts


def _embedding(ctx: StepContext) -> list[float] | None:
    value = _mapping(ctx.outputs.get("originality")).get("embedding")
    if not isinstance(value, list):
        return None
    numbers = [item for item in value if isinstance(item, int | float)]
    return [float(item) for item in numbers] if len(numbers) == len(value) else None


async def _report(ctx: StepContext) -> JsonValue:
    services = step_services()
    run = _input(ctx)
    jobs = services.jobs
    stale = bool(_deterministic_output(ctx).get("stale")) or any(
        bool(_mapping(ctx.outputs.get(step)).get("stale")) for step in _STEP_GATES
    )
    saved = False
    report: GateReport | None = None
    if not stale:
        now = services.clock.now()
        stamp = to_iso(now)
        article = await _article(ctx, services, run)
        drafts = _final_drafts(ctx)
        report = GateReport(
            content_sha256=run.content_sha256,
            version=max(1, article.version),
            ran_at=stamp,
            job_id=ctx.job_id,
            results=[finalize(drafts[gate], ran_at=stamp) for gate in GATE_ORDER],
        )
        stored = await save_report(
            services.store, run.article_id, report, now=now, embedding=_embedding(ctx)
        )
        saved = stored is not None
        stale = not saved
    if stale:
        await ctx.emit(STALE_EVENT, {"articleId": run.article_id})
    else:
        await ctx.emit(ENTITY_CHANGED, entity_changed("article", run.article_id))
    result = ChildResult(ok=saved, ref=run.article_id, error_code=None if saved else "report_stale")
    await finish_child(ctx, jobs, run.parent, result)
    summary: dict[str, JsonValue] = dict(result.to_json())
    summary["saved"] = saved
    if report is not None and saved:
        summary["autoPublishReady"] = report.auto_publish_ready
        summary["manualPublishReady"] = report.manual_publish_ready
        summary["blocking"] = [gate.value for gate in report.blocking()]
    return summary


# --------------------------------------------------------------------------------------------
# gates.standards-refresh
# --------------------------------------------------------------------------------------------


async def _refresh(ctx: StepContext) -> JsonValue:
    services = step_services()
    try:
        JobInput.from_json(ctx.input if isinstance(ctx.input, dict) else {})
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input", "The refresh job input is invalid.") from None
    settings = gates_settings()
    now = services.clock.now()
    atlas: AtlasData | None = None
    status = "skipped"
    if settings.atlas_url is not None:
        try:
            response = await services.egress.get(settings.atlas_url)
            if response.status != 200:
                raise ValueError("unexpected status")
            atlas = await asyncio.to_thread(parse_atlas, response.content)
            status = "ok"
        except (EgressError, ValueError) as exc:
            status = "failed"
            _logger.warning(
                "gates_atlas_refresh_failed",
                extra={"job_id": ctx.job_id, **describe_exception(exc)},
            )

    def mutate(current: StandardsCatalog) -> StandardsCatalog:
        return merge_catalog(current, atlas=atlas, now=now)

    catalog, _ = await update_singleton(services.store, StandardsCatalog, mutate, now=to_iso(now))
    await ctx.emit(ENTITY_CHANGED, entity_changed("settings", StandardsCatalog.SINGLETON_ID))
    _logger.info("gates_standards_refreshed", extra={"job_id": ctx.job_id, "atlas": status})
    if status == "failed":
        raise JobFailed(
            "atlas_unavailable",
            "The MITRE ATLAS data could not be refreshed; the previous list is kept.",
        )
    counts: dict[str, JsonValue] = {family: len(ids) for family, ids in catalog.families.items()}
    return {"atlas": status, "families": counts}
