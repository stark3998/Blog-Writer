"""HTTP routes of the analytics package (contract W-B6).

Every route is owner-only and every non-GET passes CSRF (core). Mutations of existing documents
take ``If-Match``; a stale etag answers 412 with the current document's etag and time. Paths:

- ``POST /api/posts/{id}/snapshots``: the snapshot form (analytics writes ``Post.snapshots``).
- ``/api/analytics/imports`` (beta), ``/overview``, ``/spend``, ``/experiments``,
  ``/suggestions``.
- ``/api/evals/topics``, ``/runs``, ``/results``.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request, Response

from blogwriter.analytics.evals import (
    EvalLabelIn,
    EvalRefused,
    EvalTopicIn,
    EvalTopicUpdate,
    check_run,
    check_topic,
    check_topic_room,
    eval_job_id,
    labelled,
    recompute_judge,
)
from blogwriter.analytics.experiments import (
    ExperimentIn,
    ExperimentRefused,
    ExperimentTransitionIn,
    edited_experiment,
    new_experiment,
    transitioned_experiment,
)
from blogwriter.analytics.imports import (
    CSV_CONTENT_TYPES,
    GENERIC_CONTENT_TYPES,
    XLSX_CONTENT_TYPES,
    ExportFormatError,
    import_export,
)
from blogwriter.analytics.rates import (
    DEFAULT_WEEKS,
    MAX_WEEKS,
    InsightsOverview,
    build_overview,
    parse_dimension,
)
from blogwriter.analytics.settings import AnalyticsSettings, analytics_settings
from blogwriter.analytics.snapshots import MetricsSnapshotIn, SnapshotRefused, add_form_snapshot
from blogwriter.analytics.spend import (
    DEFAULT_DAYS,
    MAX_DAYS,
    BrakeParams,
    SpendView,
    brake_params,
    build_spend,
)
from blogwriter.analytics.suggestions import InsightDecisionIn, decided, suggest_job_id
from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, OrderBy
from blogwriter.domain.analytics import (
    AnalyticsImport,
    EvalResult,
    EvalTopic,
    Experiment,
    InsightSuggestion,
)
from blogwriter.domain.api import (
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    JobsDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    replace_doc,
)
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import ANALYTICS_EVAL, ANALYTICS_SUGGEST, JobInput
from blogwriter.domain.posts import Post
from blogwriter.domain.states import ExperimentStatus, SuggestionStatus

__all__ = [
    "IMPORT_REQUEST_BODY",
    "BrakeParamsDep",
    "ImportSettingsDep",
    "get_router",
    "import_settings",
    "spend_params",
]

type LimitQuery = Annotated[int | None, Query()]
type CursorQuery = Annotated[str | None, Query()]

_UPLOAD_TYPES: Final = XLSX_CONTENT_TYPES | CSV_CONTENT_TYPES | GENERIC_CONTENT_TYPES

IMPORT_REQUEST_BODY: Final[dict[str, object]] = {
    "required": True,
    "description": (
        "The export file itself as the raw body (no multipart): .xlsx or .csv, with its own "
        "Content-Type (application/octet-stream when the browser has none), at most "
        "BW_ANALYTICS_IMPORT_MAX_BYTES (413 too_large)."
    ),
    "content": {content_type: {} for content_type in sorted(_UPLOAD_TYPES) if content_type},
}
"""``POST /api/analytics/imports``'s request body in the OpenAPI document (raw file bytes)."""


def import_settings() -> AnalyticsSettings:
    """FastAPI dependency: the import settings (``BW_ANALYTICS_*``; tests override it)."""
    return analytics_settings()


def spend_params() -> BrakeParams:
    """FastAPI dependency: the brake parameters and primary models (``BW_AI_*``)."""
    return brake_params()


ImportSettingsDep = Annotated[AnalyticsSettings, Depends(import_settings)]
BrakeParamsDep = Annotated[BrakeParams, Depends(spend_params)]


def _refused(exc: EvalRefused | SnapshotRefused | ExportFormatError) -> ProblemError:
    return ProblemError(exc.status, code=exc.code, detail=exc.detail)


def _jobs_unavailable() -> ProblemError:
    return ProblemError(
        503,
        code="jobs_unavailable",
        detail="This feature is not configured or not running.",
        headers={"Retry-After": "5"},
    )


# --------------------------------------------------------------------------------------------
# Snapshots and imports
# --------------------------------------------------------------------------------------------


async def add_snapshot(
    post_id: str,
    body: MetricsSnapshotIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[Post]:
    """The +48 h / +7 d snapshot form (409 ``not_posted`` before the post is live)."""
    with problems():
        current = await read_doc(store, Post, post_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        try:
            changed = add_form_snapshot(current.doc, body, now=clock.now())
        except SnapshotRefused as exc:
            raise _refused(exc) from None
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


def _declared_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _too_large(limit: int) -> ProblemError:
    return ProblemError(413, code="too_large", detail=f"Exports are limited to {limit} bytes.")


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 as soon as it passes ``limit`` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise _too_large(limit)
    return bytes(received)


async def upload_import(
    request: Request,
    store: StoreDep,
    clock: ClockDep,
    settings: ImportSettingsDep,
    response: Response,
    filename: Annotated[str | None, Query(max_length=255)] = None,
) -> Versioned[AnalyticsImport]:
    """Import a LinkedIn analytics export (raw ``.xlsx`` or ``.csv`` body; **beta**).

    201 with the new import; 200 with the earlier one when the same bytes were imported before.
    """
    if not settings.import_beta:
        raise ProblemError(
            503,
            code="import_unavailable",
            detail="Export import is a beta feature and is turned off. Use the snapshot form.",
            headers={"Retry-After": "5"},
        )
    declared = _declared_type(request)
    if declared not in _UPLOAD_TYPES:
        raise ProblemError(
            415,
            code="unsupported_media_type",
            detail="Upload the export as an .xlsx or .csv file.",
        )
    body = await _read_capped(request, settings.import_max_bytes)
    with problems():
        try:
            outcome = await import_export(
                store, body, content_type=declared, filename=filename, now=clock.now()
            )
        except ExportFormatError as exc:
            raise _refused(exc) from None
    if not outcome.created:
        response.status_code = 200
    return versioned(outcome.stored, response)


async def list_imports(
    store: StoreDep, limit: LimitQuery = None, cursor: CursorQuery = None
) -> Page[AnalyticsImport]:
    """Imports, newest first."""
    size = clamp_limit(limit)
    with problems():
        filters = [Filter("createdAt", "<", decode_cursor(cursor))] if cursor else []
        found = await find_docs(
            store,
            AnalyticsImport,
            filters,
            order_by=OrderBy("createdAt", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.created_at) if len(found) > size else None
    return Page[AnalyticsImport](
        items=[versioned(stored) for stored in items], next_cursor=next_cursor
    )


# --------------------------------------------------------------------------------------------
# InsightsOverview and spend
# --------------------------------------------------------------------------------------------


async def get_overview(
    store: StoreDep,
    clock: ClockDep,
    weeks: Annotated[int, Query(ge=1, le=MAX_WEEKS)] = DEFAULT_WEEKS,
    dimension: Annotated[str | None, Query(max_length=40)] = None,
) -> InsightsOverview:
    """Rates per 1K impressions vs the owner's trailing median, by dimension."""
    chosen = None
    if dimension:
        try:
            chosen = parse_dimension(dimension)
        except ValueError:
            raise ProblemError(
                422,
                code="unknown_dimension",
                detail="Use pillar, format, hookType, lane, goLiveMode or ownerSignal.",
            ) from None
    with problems():
        return await build_overview(store, now=clock.now(), weeks=weeks, dimension=chosen)


async def get_spend(
    store: StoreDep,
    clock: ClockDep,
    params: BrakeParamsDep,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
) -> SpendView:
    """Daily AI spend, the brake's numbers, the fallback and review-only rates."""
    with problems():
        return await build_spend(store, now=clock.now(), days=days, params=params)


# --------------------------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------------------------


async def list_experiments(
    store: StoreDep,
    status: Annotated[ExperimentStatus | None, Query()] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[Experiment]:
    """Experiments, most recently changed first."""
    size = clamp_limit(limit)
    with problems():
        filters: list[Filter] = []
        if status is not None:
            filters.append(Filter("status", "==", status.value))
        if cursor:
            filters.append(Filter("updatedAt", "<", decode_cursor(cursor)))
        found = await find_docs(
            store,
            Experiment,
            filters,
            order_by=OrderBy("updatedAt", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.updated_at) if len(found) > size else None
    return Page[Experiment](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def create_experiment(
    body: ExperimentIn, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[Experiment]:
    """Pre-register an experiment (``draft``)."""
    with problems():
        stored = await create_doc(store, new_experiment(body, now=clock.now()))
    return versioned(stored, response)


async def get_experiment(
    experiment_id: str, store: StoreDep, response: Response
) -> Versioned[Experiment]:
    with problems():
        stored = await read_doc(store, Experiment, experiment_id)
    return versioned(stored, response)


def _experiment_problem(exc: ExperimentRefused) -> ProblemError:
    return ProblemError(409, code=exc.code, detail=exc.detail, extensions=exc.extensions)


async def update_experiment(
    experiment_id: str,
    body: ExperimentIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[Experiment]:
    """Edit a draft; assign posts to the arms of a running experiment (409
    ``experiment_locked`` for anything else)."""
    with problems():
        current = await read_doc(store, Experiment, experiment_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        try:
            changed = edited_experiment(current.doc, body, now=clock.now())
        except ExperimentRefused as exc:
            raise _experiment_problem(exc) from None
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def transition_experiment(
    experiment_id: str,
    body: ExperimentTransitionIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[Experiment]:
    """Start (409 ``experiment_running``), conclude (409 ``too_few_posts``) or abandon."""
    with problems():
        current = await read_doc(store, Experiment, experiment_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        try:
            changed = await transitioned_experiment(store, current.doc, body, now=clock.now())
        except ExperimentRefused as exc:
            raise _experiment_problem(exc) from None
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------------------------


async def list_suggestions(
    store: StoreDep,
    status: Annotated[SuggestionStatus | None, Query()] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[InsightSuggestion]:
    """Insight suggestions, newest first."""
    size = clamp_limit(limit)
    with problems():
        filters: list[Filter] = []
        if status is not None:
            filters.append(Filter("status", "==", status.value))
        if cursor:
            filters.append(Filter("createdAt", "<", decode_cursor(cursor)))
        found = await find_docs(
            store,
            InsightSuggestion,
            filters,
            order_by=OrderBy("createdAt", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.created_at) if len(found) > size else None
    return Page[InsightSuggestion](
        items=[versioned(stored) for stored in items], next_cursor=next_cursor
    )


async def decide_suggestion(
    suggestion_id: str,
    body: InsightDecisionIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[InsightSuggestion]:
    """Accept or dismiss: records the decision only (nothing self-adjusts)."""
    with problems():
        current = await read_doc(store, InsightSuggestion, suggestion_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        changed = decided(current.doc, body.decision, now=clock.now())
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def run_suggestions(jobs: JobsDep, clock: ClockDep) -> JobAccepted:
    """Start ``analytics.suggest`` now (owner origin: runs even while autonomy is paused)."""
    with problems():
        try:
            job = await enqueue_once(
                jobs,
                ANALYTICS_SUGGEST,
                JobInput(origin="owner").to_json(),
                job_id=suggest_job_id(clock.now()),
                on_conflict="rerun",
            )
        except ValueError:
            raise _jobs_unavailable() from None
    return JobAccepted(job_id=job.id, status=job.status)


# --------------------------------------------------------------------------------------------
# Evals
# --------------------------------------------------------------------------------------------


async def list_topics(
    store: StoreDep,
    active: Annotated[bool | None, Query()] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[EvalTopic]:
    """Eval topics, oldest first."""
    size = clamp_limit(limit)
    with problems():
        filters: list[Filter] = []
        if active is not None:
            filters.append(Filter("active", "==", active))
        if cursor:
            filters.append(Filter("createdAt", ">", decode_cursor(cursor)))
        found = await find_docs(
            store, EvalTopic, filters, order_by=OrderBy("createdAt"), limit=size + 1
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.created_at) if len(found) > size else None
    return Page[EvalTopic](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def create_topic(
    body: EvalTopicIn, store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[EvalTopic]:
    """Add an eval topic (a configured pillar and format; the topic clears the blocklist)."""
    now = clock.now()
    stamp = to_iso(now)
    with problems():
        try:
            topic = await check_topic(store, body, stamp=stamp)
            if body.active:
                await check_topic_room(store)
        except EvalRefused as exc:
            raise _refused(exc) from None
        created = EvalTopic(
            id=EvalTopic.new_id(now=now),
            created_at=stamp,
            updated_at=stamp,
            pillar=body.pillar,
            format=body.format,
            topic=topic,
            active=body.active,
        )
        stored = await create_doc(store, created)
    return versioned(stored, response)


async def update_topic(
    topic_id: str,
    body: EvalTopicUpdate,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[EvalTopic]:
    """Edit a topic. A new topic text or pillar, or ``refreshBrief``, clears its brief."""
    stamp = to_iso(clock.now())
    with problems():
        current = await read_doc(store, EvalTopic, topic_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        try:
            topic = await check_topic(store, body, stamp=stamp)
            if body.active and not current.doc.active:
                await check_topic_room(store, excluding=topic_id)
        except EvalRefused as exc:
            raise _refused(exc) from None
        before = current.doc
        stale = body.refresh_brief or topic != before.topic or body.pillar != before.pillar
        changed = before.model_copy(
            update={
                "pillar": body.pillar,
                "format": body.format,
                "topic": topic,
                "active": body.active,
                "brief_id": None if stale else before.brief_id,
                "updated_at": stamp,
            }
        )
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)


async def delete_topic(topic_id: str, store: StoreDep, if_match: IfMatch) -> Response:
    """Delete a topic (its earlier results stay)."""
    with problems():
        current = await read_doc(store, EvalTopic, topic_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        await delete_doc(store, EvalTopic, topic_id, if_match=if_match)
    return Response(status_code=204)


async def start_eval_run(store: StoreDep, jobs: JobsDep, clock: ClockDep) -> JobAccepted:
    """Start ``analytics.eval`` (owner origin). 409 ``no_eval_topics``, ``voice_guide_missing``,
    ``blocklist_not_configured``, ``ai_use_not_confirmed``."""
    now = clock.now()
    with problems():
        try:
            await check_run(store, stamp=to_iso(now))
        except EvalRefused as exc:
            raise _refused(exc) from None
        try:
            job = await enqueue_once(
                jobs,
                ANALYTICS_EVAL,
                JobInput(origin="owner").to_json(),
                job_id=eval_job_id(now),
                on_conflict="rerun",
            )
        except ValueError:
            raise _jobs_unavailable() from None
    return JobAccepted(job_id=job.id, status=job.status)


async def list_results(
    store: StoreDep,
    run_id: Annotated[str | None, Query(alias="runId", max_length=100)] = None,
    limit: LimitQuery = None,
    cursor: CursorQuery = None,
) -> Page[EvalResult]:
    """Eval results, newest first (``runId``: one run)."""
    size = clamp_limit(limit)
    with problems():
        filters: list[Filter] = []
        if run_id is not None:
            filters.append(Filter("runId", "==", run_id))
        if cursor:
            filters.append(Filter("createdAt", "<", decode_cursor(cursor)))
        found = await find_docs(
            store,
            EvalResult,
            filters,
            order_by=OrderBy("createdAt", descending=True),
            limit=size + 1,
        )
    items = found[:size]
    next_cursor = encode_cursor(items[-1].doc.created_at) if len(found) > size else None
    return Page[EvalResult](items=[versioned(stored) for stored in items], next_cursor=next_cursor)


async def label_result(
    result_id: str,
    body: EvalLabelIn,
    store: StoreDep,
    clock: ClockDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[EvalResult]:
    """Label a result and recompute ``autonomy.judge`` for its run."""
    now = clock.now()
    with problems():
        current = await read_doc(store, EvalResult, result_id)
        if current.etag != if_match:
            raise etag_mismatch(current)
        try:
            changed = labelled(current.doc, body.label, now=now)
        except EvalRefused as exc:
            raise _refused(exc) from None
        stored = await replace_doc(store, changed, if_match=if_match)
        await recompute_judge(store, stored.doc.run_id, now=now)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


def get_router() -> APIRouter:
    """Every analytics route (full paths: ``/api/posts/{id}/snapshots``, ``/api/analytics``,
    ``/api/evals``)."""
    router = APIRouter(tags=["analytics"])
    add = router.add_api_route
    add(
        "/api/posts/{post_id}/snapshots",
        add_snapshot,
        methods=["POST"],
        summary="Add a metrics snapshot",
    )
    add(
        "/api/analytics/imports",
        upload_import,
        methods=["POST"],
        status_code=201,
        summary="Import a LinkedIn analytics export (beta)",
        openapi_extra={"requestBody": IMPORT_REQUEST_BODY},
    )
    add("/api/analytics/imports", list_imports, methods=["GET"], summary="Export imports")
    add("/api/analytics/overview", get_overview, methods=["GET"], summary="MetricRates vs median")
    add("/api/analytics/spend", get_spend, methods=["GET"], summary="AI spend")
    add("/api/analytics/experiments", list_experiments, methods=["GET"], summary="Experiments")
    add(
        "/api/analytics/experiments",
        create_experiment,
        methods=["POST"],
        status_code=201,
        summary="Pre-register an experiment",
    )
    add(
        "/api/analytics/experiments/{experiment_id}",
        get_experiment,
        methods=["GET"],
        summary="One experiment",
    )
    add(
        "/api/analytics/experiments/{experiment_id}",
        update_experiment,
        methods=["PUT"],
        summary="Edit an experiment",
    )
    add(
        "/api/analytics/experiments/{experiment_id}/transition",
        transition_experiment,
        methods=["POST"],
        summary="Start, conclude or abandon",
    )
    add("/api/analytics/suggestions", list_suggestions, methods=["GET"], summary="Suggestions")
    add(
        "/api/analytics/suggestions/run",
        run_suggestions,
        methods=["POST"],
        status_code=202,
        summary="Run the suggestions job",
    )
    add(
        "/api/analytics/suggestions/{suggestion_id}/decide",
        decide_suggestion,
        methods=["POST"],
        summary="Accept or dismiss a suggestion",
    )
    add("/api/evals/topics", list_topics, methods=["GET"], summary="Eval topics")
    add(
        "/api/evals/topics",
        create_topic,
        methods=["POST"],
        status_code=201,
        summary="Add an eval topic",
    )
    add("/api/evals/topics/{topic_id}", update_topic, methods=["PUT"], summary="Edit a topic")
    add(
        "/api/evals/topics/{topic_id}",
        delete_topic,
        methods=["DELETE"],
        status_code=204,
        response_class=Response,
        summary="Delete a topic",
    )
    add(
        "/api/evals/runs",
        start_eval_run,
        methods=["POST"],
        status_code=202,
        summary="Run the evals",
    )
    add("/api/evals/results", list_results, methods=["GET"], summary="Eval results")
    add(
        "/api/evals/results/{result_id}/label",
        label_result,
        methods=["POST"],
        summary="Label an eval result",
    )
    return router
