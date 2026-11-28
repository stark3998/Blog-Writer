"""HTTP routes of the research package (contract W-A5).

Every route is owner-only and every non-GET passes CSRF (core). Single documents are
``Versioned[...]``, lists ``Page[...]`` (newest first, cursor on ``updatedAt``). Text bound for a
model or a search passes the blocklist first (``create_run``, ``quick_search``, brief edits).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from blogwriter.ai.providers import ProviderError
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import describe_exception
from blogwriter.db import Filter, NotFound, OrderBy
from blogwriter.domain.api import Page, Versioned, clamp_limit, decode_cursor, encode_cursor
from blogwriter.domain.base import Document, to_iso
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    Stored,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    update_doc,
)
from blogwriter.domain.jobflow import cancel_job, deliver_child_result
from blogwriter.domain.jobtypes import ChildResult, ResearchRunInput, research_job_id
from blogwriter.domain.ports import Services
from blogwriter.domain.research import Brief, BriefStat, QuickSearch, ResearchRun
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.states import RESEARCH_MACHINE, ResearchStatus
from blogwriter.jobs import Job
from blogwriter.research.models import BriefEditIn, QuickIn, RunAccepted, RunIn
from blogwriter.research.quick import QuickSearchTimeout, quick_search
from blogwriter.research.run import create_run, start_run

__all__ = ["apply_brief_edit", "get_router"]

DEFAULT_RECENCY_DAYS: Final = 365
_logger: Final = logging.getLogger("blogwriter.research.routes")
_NEWEST_FIRST: Final = OrderBy("updatedAt", descending=True)


def _filters(cursor: str | None, **equal: str | None) -> list[Filter]:
    filters = [Filter(field, "==", value) for field, value in equal.items() if value is not None]
    if cursor is not None:
        filters.append(Filter("updatedAt", "<", decode_cursor(cursor)))
    return filters


def _next_cursor[D: Document](found: Sequence[Stored[D]], limit: int) -> str | None:
    if len(found) <= limit or limit < 1:
        return None
    return encode_cursor(found[limit - 1].doc.updated_at)


def _edited_texts(body: BriefEditIn) -> dict[str, str | None]:
    texts: dict[str, str | None] = {"executiveSummary": body.executive_summary}
    for finding_index, finding in enumerate(body.findings or ()):
        texts[f"findings[{finding_index}]"] = finding.text
    for name, items in (
        ("counterpoints", body.counterpoints),
        ("openQuestions", body.open_questions),
        ("angles", body.angles),
        ("hookSeeds", body.hook_seeds),
    ):
        for index, text in enumerate(items or ()):
            texts[f"{name}[{index}]"] = text
    return texts


def apply_brief_edit(brief: Brief, body: BriefEditIn) -> Brief:
    """The owner's edit applied to ``brief``: summaries replaced, items removed, references
    kept consistent (a removed citation takes its claims and quotes with it). Never adds a
    quote, claim or citation. ``ProblemError`` 422 ``unknown_finding`` for a finding id the
    brief does not have."""
    findings = list(brief.findings)
    if body.findings is not None:
        by_id = {finding.id: finding for finding in brief.findings}
        if any(item.id not in by_id for item in body.findings):
            raise ProblemError(
                422,
                code="unknown_finding",
                detail="Findings can be edited or removed, not added.",
            )
        unique = {item.id: item for item in body.findings}
        findings = [
            by_id[key].model_copy(update={"text": item.text}) for key, item in unique.items()
        ]
    removed = set(body.remove_citation_ids) & {citation.id for citation in brief.citations}
    removed_claims = set(body.remove_claim_ids)
    quotes = [quote for quote in brief.quotes if quote.citation_id not in removed]
    quote_ids = {quote.id for quote in quotes}
    claims = [
        claim.model_copy(update={"quote_ids": [q for q in claim.quote_ids if q in quote_ids]})
        for claim in brief.claims
        if claim.id not in removed_claims and claim.citation_id not in removed
    ]
    findings = [
        finding.model_copy(
            update={"citation_ids": [c for c in finding.citation_ids if c not in removed]}
        )
        for finding in findings
    ]
    stats: list[BriefStat] = []
    for stat in brief.stats:
        ids = [c for c in stat.citation_ids if c not in removed]
        if ids:
            stats.append(
                stat.model_copy(
                    update={"citation_ids": ids, "key": stat.key and len(set(ids)) >= 2}
                )
            )
    update: dict[str, object] = {
        "findings": findings,
        "claims": claims,
        "quotes": quotes,
        "stats": stats,
        "citations": [c for c in brief.citations if c.id not in removed],
        "edited_by_owner": True,
    }
    if body.executive_summary is not None:
        update["executive_summary"] = body.executive_summary
    for name in ("counterpoints", "open_questions", "angles", "hook_seeds"):
        value: list[str] | None = getattr(body, name)
        if value is not None:
            update[name] = value
    return brief.model_copy(update=update)


async def _tell_parent(services: Services, job: Job) -> None:
    """A waiting parent (a pipeline) learns at once that its research was cancelled."""
    try:
        payload = ResearchRunInput.from_json(job.input)
    except ValueError:
        return
    if payload.parent is None:
        return
    try:
        await deliver_child_result(
            services.jobs,
            payload.parent,
            ChildResult(ok=False, error_code="cancelled"),
            window=timedelta(0),
        )
    except Exception as exc:  # best effort: the parent also reads the child when it resumes
        _logger.warning("research_cancel_delivery_failed", extra=describe_exception(exc))


def get_router() -> APIRouter:
    router = APIRouter(prefix="/api/research", tags=["research"])

    @router.post("/runs", status_code=202)
    async def post_run(body: RunIn, services: ServicesDep) -> RunAccepted:
        with problems():
            stored = await create_run(
                services.store,
                topic=body.topic,
                pillar=body.pillar,
                now=services.clock.now(),
                audience=body.audience,
                recency_days=body.recency_days or DEFAULT_RECENCY_DAYS,
                article_id=body.article_id,
                idea_id=body.idea_id,
                owner_urls=body.owner_urls,
            )
            job = await start_run(services, stored.doc, origin="owner")
        return RunAccepted(run=versioned(stored), job_id=job.id)

    @router.get("/runs")
    async def list_runs(
        store: StoreDep,
        article_id: Annotated[str | None, Query(alias="articleId")] = None,
        status: ResearchStatus | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[ResearchRun]:
        size = clamp_limit(limit)
        with problems():
            filters = _filters(
                cursor, articleId=article_id, status=status.value if status else None
            )
            found = await find_docs(
                store, ResearchRun, filters, order_by=_NEWEST_FIRST, limit=size + 1
            )
        return Page[ResearchRun](
            items=[versioned(item) for item in found[:size]],
            next_cursor=_next_cursor(found, size),
        )

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str, store: StoreDep, response: Response) -> Versioned[ResearchRun]:
        with problems():
            stored = await read_doc(store, ResearchRun, run_id)
        return versioned(stored, response)

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str, services: ServicesDep, if_match: IfMatch, response: Response
    ) -> Versioned[ResearchRun]:
        with problems():
            store = services.store
            current = await read_doc(store, ResearchRun, run_id)
            if current.etag != if_match:
                raise etag_mismatch(current)
            RESEARCH_MACHINE.transition(current.doc.status, ResearchStatus.CANCELLED)
            try:
                job: Job | None = await cancel_job(
                    services.jobs, current.doc.job_id or research_job_id(run_id)
                )
            except NotFound:
                job = None
            stamp = to_iso(services.clock.now())

            def mutate(run: ResearchRun) -> ResearchRun | None:
                if run.status == ResearchStatus.CANCELLED:
                    return None
                return run.model_copy(
                    update={
                        "status": RESEARCH_MACHINE.transition(run.status, ResearchStatus.CANCELLED),
                        "status_changed_at": stamp,
                        "updated_at": stamp,
                        "finished_at": stamp,
                        "error_code": "cancelled",
                    }
                )

            stored = await update_doc(store, ResearchRun, run_id, mutate)
            if job is not None and job.is_terminal and job.status != "cancelled":
                # A job that had already ended (quarantined): cancel_job told no one.
                await _tell_parent(services, job)
        return versioned(stored, response)

    @router.get("/briefs/{brief_id}")
    async def get_brief(brief_id: str, store: StoreDep, response: Response) -> Versioned[Brief]:
        with problems():
            stored = await read_doc(store, Brief, brief_id)
        return versioned(stored, response)

    @router.put("/briefs/{brief_id}")
    async def put_brief(
        brief_id: str,
        body: BriefEditIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Brief]:
        with problems():
            current = await read_doc(store, Brief, brief_id)
            if current.etag != if_match:
                raise etag_mismatch(current)
            stamp = to_iso(clock.now())
            texts = {name: text for name, text in _edited_texts(body).items() if text is not None}
            if texts:
                blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
                check_texts(texts, blocklist)
            changed = apply_brief_edit(current.doc, body).model_copy(update={"updated_at": stamp})
            stored = await replace_doc(store, changed, if_match=if_match)
        return versioned(stored, response)

    @router.post("/quick", status_code=201)
    async def post_quick(
        body: QuickIn, services: ServicesDep, response: Response
    ) -> Versioned[QuickSearch]:
        with problems():
            try:
                stored = await quick_search(
                    services,
                    body.query,
                    pillar=body.pillar,
                    origin="owner",
                    article_id=body.article_id,
                    post_id=body.post_id,
                    idea_id=body.idea_id,
                )
            except QuickSearchTimeout:
                raise ProblemError(
                    504, code="timeout", detail="The quick search took too long. Try again."
                ) from None
            except ProviderError:
                raise ProblemError(
                    503,
                    code="ai_unavailable",
                    detail="The model did not answer. Try again in a moment.",
                    headers={"Retry-After": "5"},
                ) from None
        return versioned(stored, response)

    @router.get("/quick")
    async def list_quick(
        store: StoreDep,
        article_id: Annotated[str | None, Query(alias="articleId")] = None,
        post_id: Annotated[str | None, Query(alias="postId")] = None,
        idea_id: Annotated[str | None, Query(alias="ideaId")] = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[QuickSearch]:
        size = clamp_limit(limit)
        with problems():
            filters = _filters(cursor, articleId=article_id, postId=post_id, ideaId=idea_id)
            found = await find_docs(
                store, QuickSearch, filters, order_by=_NEWEST_FIRST, limit=size + 1
            )
        return Page[QuickSearch](
            items=[versioned(item) for item in found[:size]],
            next_cursor=_next_cursor(found, size),
        )

    @router.get("/quick/{quick_id}")
    async def get_quick(
        quick_id: str, store: StoreDep, response: Response
    ) -> Versioned[QuickSearch]:
        with problems():
            stored = await read_doc(store, QuickSearch, quick_id)
        return versioned(stored, response)

    return router
