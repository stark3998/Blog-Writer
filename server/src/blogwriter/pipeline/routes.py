"""HTTP routes of the pipeline (contract W-B1; every route is owner-only and every non-GET
passes CSRF, both enforced by core).

| Method | Path | Notes |
|---|---|---|
| POST | ``/api/articles/{id}/pipeline`` | ``{startFrom}``, If-Match; 202 ``{article, jobId}`` |
| POST | ``/api/articles/{id}/pipeline/cancel`` | If-Match; the article -> needs_attention |
| POST | ``/api/articles/{id}/revise`` | ``{instruction}``, If-Match; 202 ``{article, jobId}`` |
| POST | ``/api/articles/{id}/selection`` | ``{text, instruction}``; 200 ``{proposal}`` |
| GET | ``/api/interviews?articleId=&postId=&status=&limit=&cursor=`` | ``Page[Interview]`` |
| GET | ``/api/interviews/{id}`` | ``Versioned[Interview]`` |
| POST | ``/api/interviews/{id}/answers`` | confirmed answers, If-Match |
| POST | ``/api/interviews/{id}/skip`` | If-Match |

Text bound for a model (the revise instruction, the selection and its instruction) goes through
the blocklist first (``check_texts``: 422 ``confidential_text``, 409
``blocklist_not_configured``), and anonymized owner material needs the AI-use confirmation (409
``ai_use_not_confirmed``). Interview routes answer with core's ``Cache-Control: no-store``.
"""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from blogwriter.ai import NoRouteError, PolicyViolation
from blogwriter.ai.types import Task
from blogwriter.articles import (
    ArticleLinks,
    StaleArticle,
    article_problems,
    set_links,
    transition,
)
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Filter, NotFound, OrderBy, Store
from blogwriter.domain.api import (
    InvalidCursor,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.articles import Article, check_article_transition
from blogwriter.domain.base import is_timestamp, to_iso
from blogwriter.domain.common import review_only_reasons
from blogwriter.domain.compose import MetaIncomplete
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    blocklist_hit,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.deps import (
    AIDep,
    IfMatch,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import find_docs, read_doc, read_singleton
from blogwriter.domain.jobflow import cancel_job, enqueue_once
from blogwriter.domain.jobtypes import (
    PIPELINE_ARTICLE,
    PIPELINE_REVISE,
    PipelineInput,
    ReviseInput,
    pipeline_job_id,
)
from blogwriter.domain.ports import JobQueue, Services
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import Blocklist, EmployerPolicy, PillarSettings
from blogwriter.domain.states import ArticleStatus, InterviewStatus, TransitionError
from blogwriter.domain.stories import Interview
from blogwriter.jobs import Job
from blogwriter.pipeline.interviews import (
    InvalidAnswers,
    StaleInterview,
    answer_interview,
    skip_interview,
)
from blogwriter.pipeline.material import read_optional, request_texts, resolve_subject
from blogwriter.pipeline.models import (
    AnswersIn,
    ArticleJobAccepted,
    PipelineStartIn,
    ReviseIn,
    SelectionIn,
    SelectionOut,
    StartFrom,
)
from blogwriter.pipeline.requests import selection_request
from blogwriter.pipeline.text import clean_draft
from blogwriter.voice import guide_prompt, load_active_guide

__all__ = ["get_router"]

_logger: Final = logging.getLogger("blogwriter.pipeline.routes")
register_log_fields("article_id", "job_id")

_S = ArticleStatus
_START_STATES: Final = {
    "research": _S.RESEARCHING,
    "interview": _S.INTERVIEWING,
    "draft": _S.DRAFTING,
}
_OWNER_STARTS: Final = frozenset({_S.PLANNED, _S.IN_REVIEW, _S.NEEDS_ATTENTION})
"""The pipeline starts (or restarts) only from these states (contract W-B1)."""
_REVISABLE: Final = frozenset({_S.IN_REVIEW, _S.NEEDS_ATTENTION})
_CANCELLABLE: Final = frozenset({_S.RESEARCHING, _S.INTERVIEWING, _S.DRAFTING, _S.GATING})
_BUSY: Final = frozenset({"running", "waiting_input"})
_MAX_PROPOSAL_CHARS: Final = 16_000
_QUOTED_RE: Final = re.compile(r'^(["“])(?P<body>.*)(["”])$', re.DOTALL)
_INTERVIEW_LIST_LIMIT: Final = 50


# --------------------------------------------------------------------------------------------
# Problems
# --------------------------------------------------------------------------------------------


def _conflict(code: str, detail: str, **extensions: JsonValue) -> ProblemError:
    return ProblemError(409, code=code, detail=detail, extensions=extensions or None)


def _not_from_here(article: Article, target: ArticleStatus) -> TransitionError:
    """409 ``invalid_transition``: this route moves the article only from its own states."""
    return TransitionError("article", article.status.value, target.value, ())


@contextmanager
def _interview_problems() -> Generator[None]:
    with problems():
        try:
            yield
        except StaleInterview as exc:
            raise etag_mismatch(exc.current) from None
        except InvalidAnswers:
            raise ProblemError(
                422,
                code=InvalidAnswers.code,
                detail="Answer each question of this interview at most once.",
            ) from None


# --------------------------------------------------------------------------------------------
# Shared checks
# --------------------------------------------------------------------------------------------


async def _current_job(jobs: JobQueue, job_id: str | None) -> Job | None:
    if job_id is None:
        return None
    try:
        return await jobs.get(job_id)
    except NotFound:
        return None


def _refuse_busy(job: Job | None) -> None:
    if job is not None and job.status in _BUSY:
        raise _conflict(
            "pipeline_active",
            "A pipeline run is still working on this article. Wait for it or cancel it.",
            jobId=job.id,
        )


async def _require_drafting(store: Store, *, now: str) -> Blocklist:
    """What any drafting work needs: the AI-use confirmation, a saved blocklist and an active
    Voice & Style Guide. Returns the blocklist."""
    policy, _ = await read_singleton(store, EmployerPolicy, now=now)
    require_ai_use(policy)
    blocklist, _ = await read_singleton(store, Blocklist, now=now)
    if not blocklist.configured:
        raise BlocklistNotConfigured()
    if await load_active_guide(store) is None:
        raise _conflict(
            "voice_guide_missing",
            "Activate a Voice & Style Guide in Library > Voice first.",
        )
    return blocklist


async def _cancel_quietly(jobs: JobQueue, job_id: str) -> None:
    try:
        await cancel_job(jobs, job_id)
    except Exception as exc:  # the job checks it owns the article before it writes anything
        _logger.warning(
            "pipeline_job_not_cancelled", extra={"job_id": job_id, **describe_exception(exc)}
        )


# --------------------------------------------------------------------------------------------
# Starting, cancelling and revising
# --------------------------------------------------------------------------------------------


async def start_pipeline(
    services: Services, article_id: str, start_from: StartFrom, *, if_match: str
) -> tuple[Article, str, str]:
    """Move the article into the pipeline and enqueue ``pipeline.article`` (owner origin).
    Returns the article, its etag and the job id."""
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    article = current.doc
    target = _START_STATES[start_from]
    if article.status not in _OWNER_STARTS:
        raise _not_from_here(article, target)
    check_article_transition(article, target)
    job = await _current_job(services.jobs, article.pipeline_job_id)
    _refuse_busy(job)
    await _require_drafting(store, now=stamp)
    if start_from != "research" and await read_optional(store, Brief, article.brief_id) is None:
        raise _conflict("brief_missing", "There is no research brief yet: start from research.")
    subject = await resolve_subject(store, article, now=stamp)
    if subject.missing:
        raise MetaIncomplete(subject.missing)
    if job is not None and job.status == "queued":
        await services.jobs.cancel(job.id)  # the start planned by plan approval: this replaces it
    number = article.pipeline_runs + 1
    payload = PipelineInput(article_id=article.id, start_from=start_from, origin="owner")
    try:
        enqueued = await enqueue_once(
            services.jobs,
            PIPELINE_ARTICLE,
            payload.to_json(),
            job_id=pipeline_job_id(article.id, number),
            on_conflict="replace",
        )
    except ValueError:
        raise ProblemError(503, code="pipeline_unavailable", headers={"Retry-After": "5"}) from None

    def update(doc: Article) -> Article:
        changes: dict[str, object] = {"pipeline_job_id": enqueued.id, "pipeline_runs": number}
        if start_from == "research":
            changes.update({"brief_id": None, "research_run_id": None})
        if target is _S.DRAFTING:
            changes.update({"review_only": False, "review_only_reasons": []})
        return doc.model_copy(update=changes)

    try:
        stored = await transition(
            store,
            article.id,
            target,
            actor="owner",
            now=now,
            if_match=if_match,
            update=update,
            reason=f"Pipeline started from {start_from}.",
        )
    except Exception:
        await _cancel_quietly(services.jobs, enqueued.id)
        raise
    return stored.doc, stored.etag, enqueued.id


async def cancel_pipeline(
    services: Services, article_id: str, *, if_match: str
) -> tuple[Article, str]:
    """Cancel the article's run (a finished, failed or quarantined job is fine) and move the
    article to needs_attention (``lastError: cancelled``). A planned start is only cancelled."""
    store = services.store
    now = services.clock.now()
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    article = current.doc
    if article.status is _S.PLANNED:
        if article.pipeline_job_id is None:
            raise _not_from_here(article, _S.NEEDS_ATTENTION)
        await _cancel_quietly(services.jobs, article.pipeline_job_id)
        stored = await set_links(store, article.id, ArticleLinks(pipeline_job_id=None), now=now)
        return stored.doc, stored.etag
    if article.status not in _CANCELLABLE:
        raise _not_from_here(article, _S.NEEDS_ATTENTION)
    if article.pipeline_job_id is not None:
        with contextlib.suppress(NotFound):
            # An eval article's pipeline is a child of analytics.eval: tell it (cancel_job).
            await cancel_job(services.jobs, article.pipeline_job_id)
    # The job may still write until it notices the cancel: a CAS loop, not the owner's etag.
    stored = await transition(
        store, article.id, _S.NEEDS_ATTENTION, actor="owner", now=now, reason="cancelled"
    )
    return stored.doc, stored.etag


async def request_revision(
    services: Services, article_id: str, instruction: str, *, if_match: str
) -> tuple[Article, str, str]:
    """in_review (or needs_attention with a draft) -> drafting and ``pipeline.revise``."""
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    article = current.doc
    if article.status not in _REVISABLE:
        raise _not_from_here(article, _S.DRAFTING)
    check_article_transition(article, _S.DRAFTING)
    if not article.markdown.strip():
        raise _conflict("draft_missing", "There is no draft to revise yet.")
    _refuse_busy(await _current_job(services.jobs, article.pipeline_job_id))
    blocklist = await _require_drafting(store, now=stamp)
    check_texts({"instruction": instruction}, blocklist)
    subject = await resolve_subject(store, article, now=stamp)
    missing = [name for name in subject.missing if name != "title"]
    if missing:
        raise MetaIncomplete(missing)
    job_id = f"revise-{article.id}-{secrets.token_hex(6)}"
    payload = ReviseInput(article_id=article.id, instruction=instruction.strip(), origin="owner")
    try:
        enqueued = await enqueue_once(
            services.jobs, PIPELINE_REVISE, payload.to_json(), job_id=job_id
        )
    except ValueError:
        raise ProblemError(503, code="pipeline_unavailable", headers={"Retry-After": "5"}) from None
    try:
        stored = await transition(
            store,
            article.id,
            _S.DRAFTING,
            actor="owner",
            now=now,
            if_match=if_match,
            update=lambda doc: doc.model_copy(update={"pipeline_job_id": enqueued.id}),
            reason="Revision requested.",
        )
    except Exception:
        await _cancel_quietly(services.jobs, enqueued.id)
        raise
    return stored.doc, stored.etag, enqueued.id


def _clean_proposal(text: str) -> str:
    cleaned = clean_draft(text).strip()
    quoted = _QUOTED_RE.fullmatch(cleaned)
    if quoted is not None and "\n" not in quoted.group("body"):
        cleaned = quoted.group("body").strip()
    return cleaned[:_MAX_PROPOSAL_CHARS]


# --------------------------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------------------------


def _updated_cursor(cursor: str) -> str:
    value = decode_cursor(cursor)
    if not is_timestamp(value):
        raise InvalidCursor()
    return value


def get_router() -> APIRouter:
    """The pipeline routes (``/api/articles/{id}/...`` and ``/api/interviews``)."""
    router = APIRouter(prefix="/api", tags=["pipeline"])

    @router.post("/articles/{article_id}/pipeline", status_code=202)
    async def post_pipeline(
        article_id: str,
        body: PipelineStartIn,
        services: ServicesDep,
        ai: AIDep,
        if_match: IfMatch,
        response: Response,
    ) -> ArticleJobAccepted:
        del ai  # only its presence matters: no model, no pipeline (503 ai_unavailable)
        with article_problems():
            article, etag, job_id = await start_pipeline(
                services, article_id, body.start_from, if_match=if_match
            )
        response.headers["ETag"] = etag
        return ArticleJobAccepted(
            article=Versioned[Article](item=article, etag=etag), job_id=job_id
        )

    @router.post("/articles/{article_id}/pipeline/cancel")
    async def post_cancel(
        article_id: str, services: ServicesDep, if_match: IfMatch, response: Response
    ) -> Versioned[Article]:
        with article_problems():
            article, etag = await cancel_pipeline(services, article_id, if_match=if_match)
        response.headers["ETag"] = etag
        return Versioned[Article](item=article, etag=etag)

    @router.post("/articles/{article_id}/revise", status_code=202)
    async def post_revise(
        article_id: str,
        body: ReviseIn,
        services: ServicesDep,
        ai: AIDep,
        if_match: IfMatch,
        response: Response,
    ) -> ArticleJobAccepted:
        del ai
        with article_problems():
            article, etag, job_id = await request_revision(
                services, article_id, body.instruction, if_match=if_match
            )
        response.headers["ETag"] = etag
        return ArticleJobAccepted(
            article=Versioned[Article](item=article, etag=etag), job_id=job_id
        )

    @router.post("/articles/{article_id}/selection")
    async def post_selection(
        article_id: str, body: SelectionIn, services: ServicesDep, ai: AIDep
    ) -> SelectionOut:
        store = services.store
        with article_problems():
            stamp = to_iso(services.clock.now())
            article = (await read_doc(store, Article, article_id)).doc
            blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
            check_texts({"text": body.text, "instruction": body.instruction}, blocklist)
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
            require_ai_use(policy)
            guide = await load_active_guide(store)
            pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
            pillar = pillars.get(article.meta.pillar) if article.meta.pillar else None
            if guide is not None:
                check_texts({"voiceGuide": guide_prompt(guide)}, blocklist)
            request = selection_request(
                guide=guide, pillar=pillar, text=body.text, instruction=body.instruction
            )
            check_texts(request_texts(request), blocklist)  # every part, as sent
        try:
            result = await ai.run(Task.REVISE, request, origin="owner")
        except NoRouteError:
            raise ProblemError(503, code="ai_unavailable", headers={"Retry-After": "5"}) from None
        except PolicyViolation:
            raise ProblemError(500, code="internal_error") from None
        except Exception as exc:
            _logger.warning(
                "pipeline_selection_failed",
                extra={"article_id": article_id, **describe_exception(exc)},
            )
            raise ProblemError(
                502, code="ai_failed", detail="The model did not answer. Try again."
            ) from None
        review_only = bool(review_only_reasons(result))
        if result.refused:
            return SelectionOut(proposal=None, reason="refused", review_only=True)
        proposal = _clean_proposal(result.text)
        if not proposal:
            return SelectionOut(proposal=None, reason="empty", review_only=review_only)
        if blocklist_hit([proposal], blocklist):
            return SelectionOut(proposal=None, reason="blocklist_hit", review_only=review_only)
        return SelectionOut(proposal=proposal, review_only=review_only)

    @router.get("/interviews")
    async def list_interviews(
        store: StoreDep,
        article_id: Annotated[str | None, Query(alias="articleId", max_length=255)] = None,
        post_id: Annotated[str | None, Query(alias="postId", max_length=255)] = None,
        status: Annotated[list[InterviewStatus] | None, Query()] = None,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> Page[Interview]:
        size = clamp_limit(limit) if limit is not None else _INTERVIEW_LIST_LIMIT
        filters: list[Filter] = []
        if article_id:
            filters.append(Filter("articleId", "==", article_id))
        if post_id:
            filters.append(Filter("postId", "==", post_id))
        if status:
            filters.append(Filter("status", "in", [item.value for item in status]))
        with problems():
            if cursor is not None:
                filters.append(Filter("updatedAt", "<", _updated_cursor(cursor)))
            found = await find_docs(
                store,
                Interview,
                filters,
                order_by=OrderBy("updatedAt", descending=True),
                limit=size + 1,
            )
        page = found[:size]
        items = [Versioned[Interview](item=stored.doc, etag=stored.etag) for stored in page]
        more = len(found) > size and bool(page)
        next_cursor = encode_cursor(page[-1].doc.updated_at) if more else None
        return Page[Interview](items=items, next_cursor=next_cursor)

    @router.get("/interviews/{interview_id}")
    async def get_interview(
        interview_id: str, store: StoreDep, response: Response
    ) -> Versioned[Interview]:
        with problems():
            stored = await read_doc(store, Interview, interview_id)
        return versioned(stored, response)

    @router.post("/interviews/{interview_id}/answers")
    async def post_answers(
        interview_id: str,
        body: AnswersIn,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Interview]:
        with _interview_problems():
            stored = await answer_interview(
                services,
                interview_id,
                body.answers,
                if_match=if_match,
                now=services.clock.now(),
            )
        return versioned(stored, response)

    @router.post("/interviews/{interview_id}/skip")
    async def post_skip(
        interview_id: str, services: ServicesDep, if_match: IfMatch, response: Response
    ) -> Versioned[Interview]:
        with _interview_problems():
            stored = await skip_interview(
                services, interview_id, if_match=if_match, now=services.clock.now()
            )
        return versioned(stored, response)

    return router
