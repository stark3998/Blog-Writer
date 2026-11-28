"""Running the seven gates over one exact article version, and saving the report.

:func:`run_gates` is the inline form (the pipeline's ``gates`` step, the eval judge with
``gates={GateName.RUBRIC}``); the ``gates.run`` job runs the same pieces step by step with a
checkpoint after each (:mod:`blogwriter.gates.jobs`). Order: the deterministic checks of every
gate first; **any blocklist finding ends the model work there** (the model gates are
``skipped``, gate 3 fails, nothing goes to CRITIQUE, FACT_CHECK, the classifier or the
embedder); then links and diagrams, the classifier, the fact check, originality (novelty) and
the rubric. A gate that cannot run is ``skipped`` or ``error``, never a pass.

:func:`save_report` writes ``article.gates`` (and the embedding) only while the article still
has the gated content, so a newer version's report is never overwritten; it never touches
``version``, ``meta`` or ``markdown``, so an owner editing in review is not interrupted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from blogwriter.ai.ledger import BrakeEngaged, Origin
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.articles import (
    GATE_ORDER,
    MAX_EMBEDDING_DIMENSIONS,
    Article,
    GateName,
    GateReport,
)
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.docstore import Stored, read_doc, update_doc
from blogwriter.domain.jobflow import JobDeferred, defer_on_brake
from blogwriter.domain.ports import Services, SlopChecker
from blogwriter.gates.confidentiality import classifier_draft
from blogwriter.gates.deterministic import DeterministicChecks, deterministic_checks
from blogwriter.gates.document import ArticleText, prepare_text
from blogwriter.gates.factcheck import fact_check_draft
from blogwriter.gates.inputs import GateInputs, load_inputs
from blogwriter.gates.lint import network_findings
from blogwriter.gates.originality import embedding_input, novelty, passage_verbatim_findings
from blogwriter.gates.results import GateDraft, finalize
from blogwriter.gates.rubric import rubric_draft
from blogwriter.gates.settings import GatesSettings, gates_settings
from blogwriter.gates.slop import SlopDetectors

__all__ = [
    "MODEL_GATES",
    "GatesOutcome",
    "GatesView",
    "after_blocklist_hit",
    "default_slop",
    "gates_view",
    "guarded",
    "network_draft",
    "report_matches",
    "run_gates",
    "run_gates_detailed",
    "save_report",
]

MODEL_GATES: Final = frozenset(
    {GateName.RUBRIC, GateName.FACT_CHECK, GateName.ORIGINALITY, GateName.LINT}
)
"""Gates with work after the deterministic step that a blocklist hit skips (gate 3 fails)."""

_logger: Final = logging.getLogger("blogwriter.gates")
register_log_fields("article_id", "gate", "verdict", "blocking_count")
_DEFAULT_SLOP: Final = SlopDetectors()


def default_slop(services: Services) -> SlopChecker:
    """``services.slop``, or the package's own detectors when the service is not set."""
    return services.slop if services.slop is not None else _DEFAULT_SLOP


@dataclass(frozen=True, slots=True)
class GatesOutcome:
    """A report and the article embedding the novelty check computed (for ``save_report``)."""

    report: GateReport
    embedding: list[float] | None = None


def after_blocklist_hit(drafts: Mapping[GateName, GateDraft]) -> dict[GateName, GateDraft]:
    """The drafts of a run that stops after the deterministic step: the model gates (1, 2, 4,
    5) are marked as not run; gates 3, 6 and 7 keep their complete deterministic result."""
    out = {gate: drafts.get(gate, GateDraft.of(gate)) for gate in GATE_ORDER}
    for gate in MODEL_GATES:
        out[gate] = out[gate].with_unavailable("blocklist_hit")
    return out


async def guarded(gate: GateName, work: Awaitable[GateDraft]) -> GateDraft:
    """Await ``work``; an unexpected failure is an ``error`` draft (logged by type only)."""
    try:
        return await work
    except (JobDeferred, BrakeEngaged):
        raise
    except Exception as exc:
        _logger.warning("gates_gate_failed", extra={"gate": gate.value, **describe_exception(exc)})
        return GateDraft.of(gate).with_error("unexpected")


async def run_gates(
    article: Article,
    *,
    services: Services,
    origin: Origin,
    job_id: str | None = None,
    gates: Collection[GateName] = GATE_ORDER,
) -> GateReport:
    """Run ``gates`` (all seven by default) on ``article`` as given; the report covers only
    the gates asked for."""
    outcome = await run_gates_detailed(
        article, services=services, origin=origin, job_id=job_id, gates=gates
    )
    return outcome.report


async def run_gates_detailed(
    article: Article,
    *,
    services: Services,
    origin: Origin,
    job_id: str | None = None,
    gates: Collection[GateName] = GATE_ORDER,
    settings: GatesSettings | None = None,
) -> GatesOutcome:
    """:func:`run_gates` plus the embedding to store with :func:`save_report`."""
    requested = frozenset(GateName(gate) for gate in gates)
    config = settings if settings is not None else gates_settings()
    now = services.clock.now()
    stamp = to_iso(now)
    content_sha256 = article.compute_sha256()
    inputs = await load_inputs(services.store, article, now=stamp)
    checks = await asyncio.to_thread(
        deterministic_checks,
        article,
        inputs,
        slop=default_slop(services),
        site_host=config.site_host,
        content_sha256=content_sha256,
        now=now,
    )
    embedding: list[float] | None = None
    if checks.blocklist_hit:
        drafts = after_blocklist_hit(checks.drafts)
    else:
        drafts, embedding = await _model_gates(
            article,
            checks,
            inputs,
            services=services,
            requested=requested,
            config=config,
            origin=origin,
            job_id=job_id,
            now=now,
        )
    results = [
        finalize(drafts.get(gate, GateDraft.of(gate)), ran_at=stamp)
        for gate in GATE_ORDER
        if gate in requested
    ]
    report = GateReport(
        content_sha256=content_sha256,
        version=max(1, article.version),
        ran_at=stamp,
        job_id=job_id,
        results=results,
    )
    _log_report(article.id, report)
    return GatesOutcome(report=report, embedding=embedding)


async def _model_gates(
    article: Article,
    checks: DeterministicChecks,
    inputs: GateInputs,
    *,
    services: Services,
    requested: frozenset[GateName],
    config: GatesSettings,
    origin: Origin,
    job_id: str | None,
    now: datetime,
) -> tuple[dict[GateName, GateDraft], list[float] | None]:
    drafts = dict(checks.drafts)
    text = await asyncio.to_thread(prepare_text, article.meta, article.markdown)
    embedding: list[float] | None = None
    if GateName.LINT in requested:
        network = await guarded(
            GateName.LINT, network_draft(text, services=services, settings=config)
        )
        drafts[GateName.LINT] = drafts[GateName.LINT].merge(network)
    if GateName.CONFIDENTIALITY in requested:
        with defer_on_brake(now):
            classified = await guarded(
                GateName.CONFIDENTIALITY,
                classifier_draft(
                    text, classifier=services.classifier, origin=origin, job_id=job_id
                ),
            )
        drafts[GateName.CONFIDENTIALITY] = drafts[GateName.CONFIDENTIALITY].merge(classified)
    passage_counts: dict[str, int] = {}
    if GateName.FACT_CHECK in requested:
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
                origin=origin,
                job_id=job_id,
                now=now,
            )
            drafts[GateName.FACT_CHECK] = drafts[GateName.FACT_CHECK].merge(outcome.draft)
            passage_counts = outcome.passage_counts
        except (JobDeferred, BrakeEngaged):
            raise
        except Exception as exc:
            _logger.warning(
                "gates_gate_failed", extra={"gate": "fact_check", **describe_exception(exc)}
            )
            drafts[GateName.FACT_CHECK] = drafts[GateName.FACT_CHECK].with_error("unexpected")
    if GateName.ORIGINALITY in requested:
        extra = await asyncio.to_thread(
            passage_verbatim_findings, passage_counts, body=article.markdown, brief=inputs.brief
        )
        try:
            with defer_on_brake(now):
                result = await novelty(
                    article,
                    store=services.store,
                    embedder=services.embedder,
                    policy=inputs.policy,
                    preferences=inputs.preferences,
                    corpus_size=config.novelty_corpus,
                    origin=origin,
                    job_id=job_id,
                )
            embedding = result.embedding
            original = drafts[GateName.ORIGINALITY].with_findings([*extra, *result.findings])
            if result.max_similarity is not None:
                original = original.model_copy(update={"score": round(result.max_similarity, 4)})
            drafts[GateName.ORIGINALITY] = original
        except (JobDeferred, BrakeEngaged):
            raise
        except Exception as exc:
            _logger.warning(
                "gates_gate_failed", extra={"gate": "originality", **describe_exception(exc)}
            )
            drafts[GateName.ORIGINALITY] = drafts[GateName.ORIGINALITY].with_error("unexpected")
    if GateName.RUBRIC in requested:
        drafts[GateName.RUBRIC] = await guarded(
            GateName.RUBRIC,
            rubric_draft(
                article,
                runner=services.ai,
                policy=inputs.policy,
                blocklist=inputs.blocklist,
                pillars=inputs.pillars,
                packs=inputs.format_packs,
                guide=inputs.guide,
                words=checks.measure.words,
                origin=origin,
                job_id=job_id,
                now=now,
            ),
        )
    return drafts, embedding


async def network_draft(
    text: ArticleText, *, services: Services, settings: GatesSettings
) -> GateDraft:
    """Gate 5's network part as a draft (links through egress, diagrams through render)."""
    found = await network_findings(
        text, egress=services.egress, render=services.render, settings=settings
    )
    return GateDraft.of(GateName.LINT, found)


def _log_report(article_id: str, report: GateReport) -> None:
    _logger.info(
        "gates_report",
        extra={
            "article_id": article_id,
            "verdict": "ready" if report.auto_publish_ready else "review",
            "blocking_count": len(report.blocking()),
        },
    )


# --------------------------------------------------------------------------------------------
# Saving and reading the report
# --------------------------------------------------------------------------------------------


def _current_sha(article: Article) -> str:
    return article.compute_sha256()


def report_matches(article: Article, content_sha256: str) -> bool:
    """Whether ``article`` still has the content a report with ``content_sha256`` gated."""
    if article.content_sha256 is not None and article.content_sha256 != content_sha256:
        return False
    return _current_sha(article) == content_sha256


async def save_report(
    store: Store,
    article_id: str,
    report: GateReport,
    *,
    now: datetime,
    embedding: list[float] | None = None,
) -> Stored[Article] | None:
    """Write ``report`` (and the embedding) on the article if it still has the gated content.

    Returns the stored article, or ``None`` when the article changed (the report is stale and
    nothing is written). Every result already holds at most 50 findings (``truncated`` counts
    the rest). Only ``gates``, ``embedding``, ``embeddingSha256``, ``updatedAt`` and
    ``updatedBy`` change.
    """
    stamp = to_iso(now)
    stale = False
    vector = (
        [float(value) for value in embedding]
        if embedding is not None and 1 <= len(embedding) <= MAX_EMBEDDING_DIMENSIONS
        else None
    )
    embedding_sha256: str | None = None
    if vector is not None:
        # The embedding input depends on the content only, and the write below happens only
        # while the content is the gated content: hash it once, off the event loop.
        first = await read_doc(store, Article, article_id)
        if report_matches(first.doc, report.content_sha256):
            embedded = await asyncio.to_thread(embedding_input, first.doc)
            embedding_sha256 = sha256_hex(embedded)

    def mutate(current: Article) -> Article | None:
        nonlocal stale
        stale = not report_matches(current, report.content_sha256)
        if stale:
            return None
        update: dict[str, object] = {"gates": report, "updated_at": stamp, "updated_by": "system"}
        if vector is not None and embedding_sha256 is not None:
            update["embedding"] = vector
            update["embedding_sha256"] = embedding_sha256
        return current.model_copy(update=update)

    stored = await update_doc(store, Article, article_id, mutate)
    return None if stale else stored


class GatesView(ApiModel):
    """``GET /api/articles/{id}/gates``: the report and what it allows for this version."""

    report: GateReport | None
    stale: bool
    auto_publish_ready: bool
    manual_publish_ready: bool
    blocking: list[GateName]


def gates_view(article: Article) -> GatesView:
    report = article.gates
    if report is None:
        return GatesView(
            report=None,
            stale=False,
            auto_publish_ready=False,
            manual_publish_ready=False,
            blocking=list(GATE_ORDER),
        )
    stale = not report_matches(article, report.content_sha256)
    return GatesView(
        report=report,
        stale=stale,
        auto_publish_ready=not stale and report.auto_publish_ready,
        manual_publish_ready=not stale and report.manual_publish_ready,
        blocking=list(GATE_ORDER) if stale else report.blocking(),
    )
