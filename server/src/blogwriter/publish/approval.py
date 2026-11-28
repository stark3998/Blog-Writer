"""What the publish routes do (contract W-B2): approve, publish now, unpublish, staging previews
and the publish status. Routes stay thin; these functions raise the store, transition and
article errors that ``articles.article_problems()`` maps, plus :class:`ProblemError` for the
publish codes.

**The article names its publish job.** Every route that starts a job first writes the job id
to ``article.publish.jobId`` (in the same If-Match write that approves the article), then
enqueues the job under exactly that id. A job whose id is not the article's (a scheduled run a
publish-now replaced, a run a pull back left behind) finds out in its first step and stops
(``superseded``), so two runs can never publish one article, and nothing is reused with a stale
``publishAt``. A failed enqueue reverts the article and answers 503 ``jobs_unavailable``.

Calibration (``review.py``) is recorded on approval from in_review: outcomes other than
``clean`` are written **before** the article is approved (a failure later still resets the
streak: fail closed) and ``clean`` only **after** the job is enqueued (an approval that did not
happen never extends the streak). One approved content counts once (re-approving the same
content after a pull back does not count it again; after a veto it does). A correction of a
live article records ``record_correction`` (the streak resets and auto-publish turns off).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from blogwriter.articles import StaleArticle, transition
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, NotFound, PreconditionFailed, Store, StoreError
from blogwriter.domain.articles import (
    GATE_ORDER,
    Article,
    CalibrationFlags,
    Correction,
    check_article_transition,
)
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.compose import MetaIncomplete, to_frontmatter
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.docstore import (
    Stored,
    read_doc,
    read_singleton,
    replace_doc,
    update_doc,
    update_singleton,
)
from blogwriter.domain.jobflow import MAX_RERUNS, rerun_job_id
from blogwriter.domain.jobtypes import (
    PUBLISH_ARTICLE,
    PUBLISH_STAGING,
    PUBLISH_UNPUBLISH,
    ArticleJobInput,
    PublishInput,
    publish_job_id,
)
from blogwriter.domain.ports import JobQueue, Services, ServiceUnavailable
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    CadenceSettings,
    CalibrationOutcome,
    EmployerPolicy,
    record_correction,
    record_review,
)
from blogwriter.domain.slots import next_article_publish
from blogwriter.domain.states import (
    ARTICLE_MACHINE,
    ArticleStatus,
    TransitionError,
    TransitionRefused,
)
from blogwriter.jobs.models import new_job_id
from blogwriter.publish.document import plain_line
from blogwriter.publish.models import ApproveIn, PublishJobView, PublishStatusOut, PublishStepView
from blogwriter.publish.published import PUBLISHED_CONTAINER, read_published
from blogwriter.publish.recovery import STRANDABLE, publish_in_progress, release_stranded
from blogwriter.publish.review import review_outcome
from blogwriter.publish.screen import article_texts
from blogwriter.publish.settings import PublishSettings
from blogwriter.publish.state import shipped_corrections

__all__ = [
    "MAX_SCHEDULE_AHEAD",
    "PUBLISH_AT_TOLERANCE",
    "Approval",
    "approve",
    "publish_status",
    "start_staging",
    "start_unpublish",
]

S = ArticleStatus
PUBLISH_AT_TOLERANCE: Final = timedelta(minutes=5)
"""A ``publishAt`` this far in the past is taken as now; earlier is refused (422)."""
MAX_SCHEDULE_AHEAD: Final = timedelta(days=366)

_logger: Final = logging.getLogger("blogwriter.publish.approval")
register_log_fields("article_id", "job_id", "outcome")

type Mode = Literal["approve", "publish_now"]


@dataclass(frozen=True, slots=True)
class Approval:
    stored: Stored[Article]
    job_id: str


# --------------------------------------------------------------------------------------------
# Problems
# --------------------------------------------------------------------------------------------


def _conflict(
    code: str, detail: str, extensions: dict[str, JsonValue] | None = None
) -> ProblemError:
    return ProblemError(409, code=code, detail=detail, extensions=extensions)


def portfolio_not_configured() -> ProblemError:
    return ProblemError(
        503,
        code="portfolio_not_configured",
        detail="Publishing to the portfolio is not configured.",
        headers={"Retry-After": "5"},
    )


def _jobs_unavailable() -> ProblemError:
    return ProblemError(
        503,
        code="jobs_unavailable",
        detail="The job could not be queued. Nothing changed; try again.",
        headers={"Retry-After": "5"},
    )


def _gates_blocking(blocking: list[str]) -> ProblemError:
    names: list[JsonValue] = []
    names.extend(blocking)
    return _conflict(
        "gates_blocking",
        "Some gates block publishing. Fix or override them first.",
        {"blocking": names},
    )


# --------------------------------------------------------------------------------------------
# Checks shared by approve and publish-now
# --------------------------------------------------------------------------------------------


async def _check_publishable(
    store: Store, article: Article, *, confirm_slug: str | None, from_review: bool
) -> None:
    """Every refusal of the approve route, in the contract's order of precedence."""
    meta = article.meta
    missing = meta.missing_for_publish()
    if missing:
        raise MetaIncomplete(missing)
    to_frontmatter(meta)  # FrontmatterError: 409 meta_invalid
    sha = article.compute_sha256()
    report = article.gates
    if report is None:
        raise _gates_blocking([gate.value for gate in GATE_ORDER])
    if report.content_sha256 != sha:
        raise _conflict("report_stale", "The gate report is for an older version. Run the gates.")
    blocking = report.blocking()
    if blocking:
        raise _gates_blocking([gate.value for gate in blocking])
    if not article.clearance.valid_for(sha):
        raise _conflict(
            "clearance_required", "Confirm the clearance attestation for this version first."
        )
    slug = meta.slug
    if slug is None:
        raise MetaIncomplete(["slug"])
    if confirm_slug is not None and confirm_slug != slug:
        raise _conflict("slug_change_unconfirmed", "The slug changed since you looked. Reload.")
    renamed = article.publish.slug is not None and article.publish.slug != slug
    if from_review and renamed and confirm_slug != slug:
        raise _conflict(
            "slug_change_unconfirmed",
            "This changes the address of a published article (the old one will redirect). "
            "Confirm the new slug.",
            {"from": article.publish.slug, "to": slug},
        )
    existing = await read_published(store, PUBLISHED_CONTAINER, slug)
    if existing is not None and not article.owns_slug(slug):
        raise _conflict("slug_taken", "Another published page already uses this slug.")


async def _screen(
    store: Store,
    article: Article,
    *,
    now: datetime,
    corrections: list[Correction] | None = None,
    note: str | None = None,
) -> None:
    """The current blocklist over everything the article will publish (the gates checked the
    blocklist of their day, and the owner may have added a term since): ``ConfidentialText``
    (422 ``confidential_text``, term ids only) or ``BlocklistNotConfigured`` (409)."""
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    texts = await asyncio.to_thread(
        article_texts,
        article,
        corrections=corrections,
        disclaimer=policy.disclaimer_long,
        extra={"correctionNote": note},
    )
    check_texts(texts, blocklist)


def _publish_time(
    requested: datetime | None, *, mode: Mode, now: datetime, cadence: CadenceSettings
) -> datetime:
    if mode == "publish_now":
        return now
    if requested is None:
        return parse_iso(next_article_publish(cadence, after=now).utc)
    moment = requested.astimezone(UTC)
    if moment < now - PUBLISH_AT_TOLERANCE or moment > now + MAX_SCHEDULE_AHEAD:
        raise ProblemError(
            422,
            code="invalid_publish_at",
            detail="Pick a publish time between now and a year from now.",
        )
    return max(moment, now)


def _correction_note(article: Article, note: str | None, *, from_review: bool) -> str | None:
    text = plain_line(note, 1000) if note is not None else ""
    if not from_review:
        return None
    if article.publish.live and not text:
        raise _conflict(
            "correction_note_required",
            "This article is live: say what the correction changes (a dated note is shown).",
        )
    if text and article.publish.published_at is None:
        raise _conflict(
            "correction_note_not_allowed", "Correction notes are for published articles."
        )
    return text or None


# --------------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------------


async def _fresh_job_id(jobs: JobQueue, base: str) -> tuple[str, list[str]]:
    """The first ``base``/``base-r<n>`` id no job has yet, and the active runs met on the way
    (cancelled once the new run is in place)."""
    active: list[str] = []
    for candidate in [base, *(rerun_job_id(base, run) for run in range(1, MAX_RERUNS + 1))]:
        try:
            existing = await jobs.get(candidate)
        except NotFound:
            return candidate, active
        if existing.type != PUBLISH_ARTICLE:
            raise ValueError(f"job id {candidate} belongs to a {existing.type} job")
        if existing.is_active:
            active.append(existing.id)
    raise _conflict("too_many_runs", "This version was scheduled too many times.")


async def _cancel_quietly(jobs: JobQueue, job_ids: list[str], *, article_id: str) -> None:
    for job_id in dict.fromkeys(job_ids):
        try:
            job = await jobs.get(job_id)
            if job.is_active:
                await jobs.cancel(job_id)
        except NotFound:
            continue
        except (StoreError, ServiceUnavailable, TimeoutError):
            # The run checks the article's job id in its first step and stops anyway.
            _logger.warning(
                "publish_job_not_cancelled", extra={"article_id": article_id, "job_id": job_id}
            )


async def _enqueue(
    jobs: JobQueue,
    job_type: str,
    payload: JsonValue,
    *,
    job_id: str,
    run_after: datetime | None,
) -> None:
    try:
        await jobs.enqueue(job_type, payload, job_id=job_id, run_after=run_after)
    except Conflict:
        existing = await jobs.get(job_id)  # created by a racing request for the same write
        if existing.type != job_type:
            raise


# --------------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------------


async def _record_calibration(
    store: Store, article_id: str, outcome: CalibrationOutcome, *, sha: str, now: datetime
) -> None:
    stamp = to_iso(now)

    def mutate(autonomy: AutonomySettings) -> AutonomySettings:
        if outcome == "correction":
            return record_correction(autonomy, article_id=article_id, at=stamp)
        return record_review(
            autonomy, article_id=article_id, outcome=outcome, at=stamp, content_sha256=sha
        )

    await update_singleton(store, AutonomySettings, mutate, now=stamp)


def _already_counted(article: Article, sha: str) -> bool:
    flags = article.calibration
    return (
        flags is not None
        and flags.counted_at is not None
        and flags.approved_sha256 == sha
        and flags.outcome not in (None, "vetoed", "correction")
    )


# --------------------------------------------------------------------------------------------
# Approve and publish now
# --------------------------------------------------------------------------------------------


def _allowed_sources(mode: Mode) -> frozenset[ArticleStatus]:
    if mode == "approve":
        return frozenset({S.IN_REVIEW})
    return frozenset({S.IN_REVIEW, S.APPROVED, S.PUBLISH_FAILED})


def _refuse_source(article: Article, mode: Mode) -> None:
    status = article.status
    if status not in _allowed_sources(mode):
        target = S.APPROVED if mode == "approve" or status is S.IN_REVIEW else S.PUBLISHING
        allowed = sorted(state.value for state in _allowed_sources(mode))
        raise TransitionError("article", status.value, target.value, allowed)
    if status is S.PUBLISH_FAILED and article.publish.last_operation == "unpublish":
        raise _conflict(
            "invalid_transition",
            "The last operation was an unpublish. Retry the unpublish instead.",
            {"from": status.value, "to": S.PUBLISHING.value, "allowed": [S.UNPUBLISHING.value]},
        )


async def approve(
    services: Services,
    article_id: str,
    *,
    if_match: str,
    request: ApproveIn,
    mode: Mode,
) -> Approval:
    """Approve (in_review -> approved) or publish now (also from approved and publish_failed, and
    from publishing/unpublishing whose job ended: :mod:`.recovery`), then schedule the publish job
    (contract W-B2, ``POST .../approve`` and ``.../publish-now``)."""
    store, jobs = services.store, services.jobs
    now = services.clock.now()
    stamp = to_iso(now)
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    if mode == "publish_now" and current.doc.status in STRANDABLE:
        current = await _released(services, current, if_match=if_match)
        if_match = current.etag
    before = current.doc
    _refuse_source(before, mode)
    from_review = before.status is S.IN_REVIEW
    check_article_transition(before, S.APPROVED if from_review else S.PUBLISHING)
    await _check_publishable(store, before, confirm_slug=request.slug, from_review=from_review)
    note = _correction_note(before, request.correction_note, from_review=from_review)
    await _screen(
        store,
        before,
        now=now,
        corrections=shipped_corrections(before) if from_review else None,
        note=note,
    )
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    publish_at = _publish_time(request.publish_at, mode=mode, now=now, cadence=cadence)
    sha = before.compute_sha256()

    outcome: CalibrationOutcome | None = None
    substantive: bool | None = None
    counted = False
    if from_review:
        if before.publish.live:
            outcome = "correction"
        else:
            outcome, substantive = await review_outcome(store, before)
        counted = _already_counted(before, sha)
        if not counted and outcome != "clean":
            await _record_calibration(store, before.id, outcome, sha=sha, now=now)

    job_id, active = await _fresh_job_id(jobs, publish_job_id(before.id, before.version))

    def update(article: Article) -> Article:
        publish = article.publish.model_copy(
            update={
                "publish_at": to_iso(publish_at),
                "auto": False,
                "job_id": job_id,
                "last_error": None,
            }
        )
        changes: dict[str, object] = {"publish": publish, "last_error": None}
        if from_review:
            corrections = shipped_corrections(article)
            if note is not None:
                corrections.append(
                    Correction(date=publish_at.date().isoformat(), note=note, created_at=stamp)
                )
            changes["corrections"] = corrections
            if outcome is not None and not counted:
                flags = article.calibration or CalibrationFlags()
                changes["calibration"] = flags.model_copy(
                    update={
                        "outcome": outcome,
                        "approved_sha256": sha,
                        "substantive_edit": substantive,
                        "counted_at": stamp,
                    }
                )
        return article.model_copy(update=changes)

    if from_review:
        stored = await transition(
            store, before.id, S.APPROVED, actor="owner", now=now, if_match=if_match, update=update
        )
    else:
        changed = update(before).model_copy(update={"updated_at": stamp, "updated_by": "owner"})
        try:
            stored = await replace_doc(store, changed, if_match=if_match)
        except PreconditionFailed:
            raise StaleArticle(await read_doc(store, Article, before.id)) from None

    payload = PublishInput(
        article_id=before.id,
        version=before.version,
        publish_at=to_iso(publish_at),
        auto=False,
        origin="owner",
    ).to_json()
    try:
        await _enqueue(jobs, PUBLISH_ARTICLE, payload, job_id=job_id, run_after=publish_at)
    except (ValueError, StoreError, ServiceUnavailable, TimeoutError):
        _logger.warning("publish_enqueue_failed", extra={"article_id": before.id, "job_id": job_id})
        await _revert(store, before, now=now)
        raise _jobs_unavailable() from None

    await _cancel_quietly(
        jobs,
        [*active, *([before.publish.job_id] if before.publish.job_id else [])],
        article_id=before.id,
    )
    if from_review and outcome == "clean" and not counted:
        try:
            await _record_calibration(store, before.id, "clean", sha=sha, now=now)
        except (StoreError, PreconditionFailed):
            _logger.warning("publish_calibration_not_recorded", extra={"article_id": before.id})
    _logger.info(
        "article_approved",
        extra={"article_id": before.id, "job_id": job_id, "outcome": outcome or "none"},
    )
    return Approval(stored=stored, job_id=job_id)


async def _revert(store: Store, before: Article, *, now: datetime) -> None:
    """Undo an approval whose job could not be queued (best effort)."""
    stamp = to_iso(now)
    try:
        if before.status is S.IN_REVIEW:

            def back(article: Article) -> Article:
                return article.model_copy(
                    update={
                        "publish": before.publish,
                        "calibration": before.calibration,
                        "corrections": before.corrections,
                        "last_error": "enqueue_failed",
                    }
                )

            await transition(store, before.id, S.IN_REVIEW, actor="system", now=now, update=back)
        else:

            def restore(article: Article) -> Article:
                return article.model_copy(
                    update={"publish": before.publish, "updated_at": stamp, "updated_by": "system"}
                )

            await update_doc(store, Article, before.id, restore)
    except (StoreError, TransitionError, TransitionRefused):
        _logger.error("publish_approval_not_reverted", extra={"article_id": before.id})


async def _released(
    services: Services, current: Stored[Article], *, if_match: str
) -> Stored[Article]:
    """A ``publishing``/``unpublishing`` article whose named job ended (cancelled, quarantined,
    never enqueued) -> ``publish_failed`` first, so the owner's retry applies (409
    ``publish_in_progress`` while the job is still active)."""
    released = await release_stranded(
        services, current.doc.id, reason="job_ended", if_match=if_match
    )
    if released is None:
        raise publish_in_progress()
    return released


# --------------------------------------------------------------------------------------------
# Unpublish, staging, status
# --------------------------------------------------------------------------------------------


async def start_unpublish(
    services: Services, article_id: str, *, if_match: str, settings: PublishSettings
) -> str:
    """published (or publish_failed after a failed unpublish, or with the document still up, or
    publishing/unpublishing whose job ended: :mod:`.recovery`) -> unpublishing, and the
    ``publish.unpublish`` job."""
    store, jobs = services.store, services.jobs
    now = services.clock.now()
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    if current.doc.status in STRANDABLE:
        current = await _released(services, current, if_match=if_match)
        if_match = current.etag
    article = current.doc
    retry = article.status is S.PUBLISH_FAILED and (
        article.publish.last_operation == "unpublish" or article.publish.live
    )
    if article.status is not S.PUBLISHED and not retry:
        ARTICLE_MACHINE.transition(article.status, S.UNPUBLISHING)
        raise _conflict("not_published", "This article has no published page to take down.")
    if article.publish.slug is None:
        raise _conflict("not_published", "This article has no published page to take down.")
    if not settings.portfolio_configured:
        raise portfolio_not_configured()
    job_id = new_job_id()

    def update(doc: Article) -> Article:
        publish = doc.publish.model_copy(
            update={"last_operation": "unpublish", "job_id": job_id, "last_error": None}
        )
        return doc.model_copy(update={"publish": publish})

    stored = await transition(
        store, article.id, S.UNPUBLISHING, actor="owner", now=now, if_match=if_match, update=update
    )
    payload = ArticleJobInput(article_id=article.id, origin="owner").to_json()
    try:
        await _enqueue(jobs, PUBLISH_UNPUBLISH, payload, job_id=job_id, run_after=None)
    except (ValueError, StoreError, ServiceUnavailable, TimeoutError):
        _logger.warning("unpublish_enqueue_failed", extra={"article_id": article.id})
        try:
            await transition(
                store,
                article.id,
                S.PUBLISH_FAILED,
                actor="system",
                now=now,
                reason="enqueue_failed",
                update=lambda doc: doc.model_copy(
                    update={
                        "publish": doc.publish.model_copy(update={"last_error": "enqueue_failed"})
                    }
                ),
            )
        except (StoreError, TransitionError):
            _logger.error("unpublish_not_reverted", extra={"article_id": article.id})
        raise _jobs_unavailable() from None
    _logger.info("article_unpublish_started", extra={"article_id": stored.doc.id, "job_id": job_id})
    return job_id


async def start_staging(services: Services, article_id: str, *, settings: PublishSettings) -> str:
    """Queue a ``publish.staging`` preview build of the article as it is now."""
    store = services.store
    article = (await read_doc(store, Article, article_id)).doc
    if article.purpose == "eval":
        raise TransitionRefused("eval_article", "Eval articles are never published.")
    if not settings.preview_configured:
        raise portfolio_not_configured()
    missing = article.meta.missing_for_publish()
    if missing:
        raise MetaIncomplete(missing)
    to_frontmatter(article.meta)
    slug = article.meta.slug
    if slug is None:
        raise MetaIncomplete(["slug"])
    existing = await read_published(store, PUBLISHED_CONTAINER, slug)
    if existing is not None and not article.owns_slug(slug):
        raise _conflict("slug_taken", "Another published page already uses this slug.")
    await _screen(store, article, now=services.clock.now())
    job_id = new_job_id()
    payload = ArticleJobInput(article_id=article.id, origin="owner").to_json()
    try:
        await _enqueue(services.jobs, PUBLISH_STAGING, payload, job_id=job_id, run_after=None)
    except (ValueError, StoreError, ServiceUnavailable, TimeoutError):
        raise _jobs_unavailable() from None
    return job_id


async def publish_status(services: Services, article_id: str) -> PublishStatusOut:
    """The article's publish state and its current publish job (no writes)."""
    article = (await read_doc(services.store, Article, article_id)).doc
    view: PublishJobView | None = None
    job_id = article.publish.job_id
    if job_id is not None:
        try:
            job = await services.jobs.get(job_id)
        except NotFound:
            job = None
        if job is not None:
            view = PublishJobView(
                id=job.id,
                type=job.type,
                status=job.status,
                steps=[PublishStepView(name=step.name, status=step.status) for step in job.steps],
                error_code=job.error.code if job.error is not None else None,
                awaiting=job.awaiting.key if job.awaiting is not None else None,
            )
    return PublishStatusOut(
        status=article.status, publish=article.publish, last_error=article.last_error, job=view
    )
