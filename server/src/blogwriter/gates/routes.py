"""HTTP routes of the gates (owner-only; every non-GET passes core's CSRF check).

=======  ============================================  ==========================================
method   path                                          what
=======  ============================================  ==========================================
POST     ``/api/articles/{id}/gates/run``              202 ``JobAccepted`` (``gates.run``,
                                                       ``gates_job_id``, re-runs ``-r<n>``);
                                                       409 ``read_only_state`` in pipeline states
GET      ``/api/articles/{id}/gates``                  ``GatesView`` (the article etag in
                                                       ``ETag``)
POST     ``/api/articles/{id}/gates/{gate}/override``  ``{reason}`` + If-Match (article etag):
                                                       ``Versioned[Article]``; 409
                                                       ``not_overridable``, ``report_stale``
POST     ``/api/gates/lint``                           ``{findings[]}`` (``lint_text`` plus the
                                                       blocklist, in a thread; no AI, no
                                                       network); 413 ``markdown_too_large``
GET      ``/api/gates/standards``                      ``Versioned[StandardsCatalog]`` (the seed
                                                       lists with ``etag: null`` until saved)
POST     ``/api/gates/standards/refresh``              202 ``JobAccepted``
=======  ============================================  ==========================================
"""

from __future__ import annotations

import asyncio
from typing import Final

from fastapi import APIRouter, Response
from pydantic import Field, field_validator

from blogwriter.core.errors import ProblemError
from blogwriter.domain.api import ApiModel, JobAccepted, Versioned
from blogwriter.domain.articles import (
    MAX_ARTICLE_MARKDOWN_BYTES,
    Article,
    ArticleMeta,
    GateName,
    GateOverride,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import Finding
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    JobsDep,
    SlopDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
    versioned_singleton,
)
from blogwriter.domain.docstore import read_doc, read_singleton, replace_doc
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import (
    GATES_RUN,
    GATES_STANDARDS_REFRESH,
    GatesRunInput,
    JobInput,
    gates_job_id,
)
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import Blocklist, PillarSettings, StandardsCatalog
from blogwriter.domain.states import ARTICLE_WRITER, WriterConflict
from blogwriter.gates.confidentiality import blocklist_text_findings
from blogwriter.gates.deterministic import lint_text
from blogwriter.gates.document import prepare_text
from blogwriter.gates.run import GatesView, gates_view, report_matches
from blogwriter.gates.standards import seed_catalog

__all__ = ["LintIn", "LintOut", "OverrideIn", "get_router"]

_MAX_LINT_CHARS: Final = 4 * MAX_ARTICLE_MARKDOWN_BYTES
"""Parsing bound for the request; the byte cap itself answers 413."""


class LintIn(ApiModel):
    meta: ArticleMeta = Field(default_factory=ArticleMeta)
    markdown: str = Field(max_length=_MAX_LINT_CHARS)
    brief_id: str | None = Field(default=None, max_length=200)


class LintOut(ApiModel):
    findings: list[Finding]


class OverrideIn(ApiModel):
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def _meaningful(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 3:
            raise ValueError("the reason needs at least 3 characters")
        return text


def _read_only(article: Article) -> ProblemError:
    """409 ``read_only_state`` while the pipeline writes the article.

    Built here rather than through ``problems()``: ``domain.deps.problem_from(WriterConflict)``
    puts ``status`` in the extensions, which clashes with the problem's own ``status`` member
    (core refuses to render it: a 500). Reported as a domain change request.
    """
    status = article.status
    return ProblemError(
        409,
        code=WriterConflict.code,
        detail=f"Read-only while {status.value}: the pipeline writes now.",
        extensions={"state": status.value, "writer": ARTICLE_WRITER[status]},
    )


def _jobs_unavailable() -> ProblemError:
    return ProblemError(
        503,
        code="jobs_unavailable",
        detail="Background jobs are not available right now.",
        headers={"Retry-After": "5"},
    )


def _lint_with_blocklist(
    lint_in: LintIn,
    *,
    brief: Brief | None,
    catalog: StandardsCatalog,
    pillars: PillarSettings,
    blocklist: Blocklist,
    slop: SlopChecker,
) -> list[Finding]:
    findings = lint_text(
        lint_in.meta, lint_in.markdown, brief=brief, catalog=catalog, pillars=pillars, slop=slop
    )
    text = prepare_text(lint_in.meta, lint_in.markdown)
    findings.extend(blocklist_text_findings(text, blocklist))
    return sorted(findings, key=lambda f: (f.start if f.start is not None else -1, f.code))


def get_router() -> APIRouter:
    """The gates routes (full paths under ``/api``)."""
    router = APIRouter(prefix="/api", tags=["gates"])

    @router.post("/articles/{article_id}/gates/run", status_code=202)
    async def run_gates_route(article_id: str, store: StoreDep, jobs: JobsDep) -> JobAccepted:
        with problems():
            article = (await read_doc(store, Article, article_id)).doc
            if ARTICLE_WRITER[article.status] == "pipeline":
                raise _read_only(article)
            sha = article.compute_sha256()
            payload = GatesRunInput(
                article_id=article.id, content_sha256=sha, origin="owner"
            ).to_json()
            try:
                job = await enqueue_once(
                    jobs,
                    GATES_RUN,
                    payload,
                    job_id=gates_job_id(article.id, sha),
                    on_conflict="rerun",
                )
            except ValueError:
                raise _jobs_unavailable() from None
        return JobAccepted(job_id=job.id, status=job.status)

    @router.get("/articles/{article_id}/gates")
    async def get_gates(article_id: str, store: StoreDep, response: Response) -> GatesView:
        with problems():
            stored = await read_doc(store, Article, article_id)
        response.headers["ETag"] = stored.etag
        return gates_view(stored.doc)

    @router.post("/articles/{article_id}/gates/{gate}/override")
    async def override_gate(
        article_id: str,
        gate: str,
        body: OverrideIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        try:
            name = GateName(gate)
        except ValueError:
            raise ProblemError(404, code="not_found") from None
        with problems():
            current = await read_doc(store, Article, article_id)
            if current.etag != if_match:
                raise etag_mismatch(current)
            article = current.doc
            report = article.gates
            if report is None or not report_matches(article, report.content_sha256):
                raise ProblemError(
                    409,
                    code="report_stale",
                    detail="The gate report is not for this version. Run the gates again.",
                )
            result = report.result(name)
            if result is None or result.passed or not result.overridable:
                raise ProblemError(
                    409,
                    code="not_overridable",
                    detail="This gate cannot be overridden: fix the blocking findings.",
                )
            stamp = to_iso(clock.now())
            overridden = result.model_copy(
                update={"override": GateOverride(reason=body.reason, at=stamp)}
            )
            results = [overridden if item.gate is name else item for item in report.results]
            changed = article.model_copy(
                update={
                    "gates": report.model_copy(update={"results": results}),
                    "updated_at": stamp,
                    "updated_by": "owner",
                }
            )
            stored = await replace_doc(store, changed, if_match=if_match)
        return versioned(stored, response)

    @router.post("/gates/lint")
    async def lint_route(body: LintIn, store: StoreDep, slop: SlopDep, clock: ClockDep) -> LintOut:
        if len(body.markdown.encode("utf-8", errors="surrogatepass")) > MAX_ARTICLE_MARKDOWN_BYTES:
            raise ProblemError(
                413,
                code="markdown_too_large",
                detail=f"The article is larger than {MAX_ARTICLE_MARKDOWN_BYTES // 1024} KiB.",
            )
        stamp = to_iso(clock.now())
        with problems():
            catalog, _ = await read_singleton(store, StandardsCatalog, now=stamp)
            pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
            blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
            brief = (
                (await read_doc(store, Brief, body.brief_id)).doc
                if body.brief_id is not None
                else None
            )
        findings = await asyncio.to_thread(
            _lint_with_blocklist,
            body,
            brief=brief,
            catalog=catalog,
            pillars=pillars,
            blocklist=blocklist,
            slop=slop,
        )
        return LintOut(findings=findings)

    @router.get("/gates/standards")
    async def get_standards(
        store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[StandardsCatalog]:
        stamp = to_iso(clock.now())
        with problems():
            catalog, etag = await read_singleton(store, StandardsCatalog, now=stamp)
        if etag is None:
            catalog = seed_catalog(stamp)
        return versioned_singleton(catalog, etag, response)

    @router.post("/gates/standards/refresh", status_code=202)
    async def refresh_standards(jobs: JobsDep) -> JobAccepted:
        try:
            job = await jobs.enqueue(GATES_STANDARDS_REFRESH, JobInput(origin="owner").to_json())
        except ValueError:
            raise _jobs_unavailable() from None
        return JobAccepted(job_id=job.id, status=job.status)

    return router
