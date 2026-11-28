"""The veto notice and the veto (plan: "Veto notice", "Auto-publish calibration").

An article approved on the auto-publish path (``publish.auto``) goes out only after the owner
had the chance to stop it. ``planner.veto-notice`` (weekly at ``cadence.veto_notice``, Friday
12:00 by default) pushes a title and a link for every such article publishing before the next
notice could cover it, and records ``veto.sentAt`` and ``veto.delivered`` (the push receipt).
The publish job proceeds only with ``delivered`` true (fail closed):

- **no receipt** (no device, push failed): ``delivered: false``, ``emit_alert("needs_attention")``
  (Azure Monitor e-mails the owner); the publish holds;
- **too late** (it publishes within ``cadence.min_veto_lead_hours``, so no timely notice is
  possible any more, for example after downtime): no notice, ``delivered: false``, an alert
  and a push saying it is on hold; the owner approves it by hand.

A veto (``POST /api/articles/{id}/veto``) moves the article ``approved -> in_review``, records
``veto.vetoedAt`` and ``calibration.outcome = vetoed``, restarts the calibration streak
(``record_review(outcome="vetoed")`` on the autonomy settings) and cancels the publish job.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from blogwriter.articles import transition
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.articles import Article, CalibrationFlags, VetoNotice
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.deps import etag_mismatch
from blogwriter.domain.docstore import Stored, find_docs, read_doc, update_doc, update_singleton
from blogwriter.domain.jobflow import MAX_RERUNS, rerun_job_id
from blogwriter.domain.jobtypes import PUBLISH_ARTICLE, publish_job_id
from blogwriter.domain.ports import JobQueue, Notifier
from blogwriter.domain.settings import AutonomySettings, CadenceSettings, record_review
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.weeks import add_days
from blogwriter.jobs.queue import JobStateError
from blogwriter.notify import emit_alert
from blogwriter.notify.payload import NotificationPayload
from blogwriter.planner.links import article_href
from blogwriter.planner.models import VetoView
from blogwriter.planner.slots import current_week_start

__all__ = [
    "HELD_TITLE",
    "NOTICE_TITLE",
    "VetoReport",
    "cancel_publish_jobs",
    "next_notice",
    "notice_window",
    "send_veto_notices",
    "veto_article",
    "veto_view",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("article_id", "delivered", "late", "notified", "held")

NOTICE_TITLE: Final = "Auto-publish scheduled. Tap to review or veto"
HELD_TITLE: Final = "Auto-publish on hold: approve the article yourself"


def next_notice(cadence: CadenceSettings, *, after: datetime) -> datetime:
    """The first ``cadence.veto_notice`` time strictly after ``after``."""
    monday = current_week_start(after, cadence.timezone)
    for weeks in range(3):
        slot = weekly_slot(add_days(monday, 7 * weeks), cadence.veto_notice, cadence.timezone)
        moment = parse_iso(slot.utc)
        if moment > after:
            return moment
    raise AssertionError("unreachable: a weekly time recurs within two weeks")  # pragma: no cover


def notice_window(cadence: CadenceSettings, *, now: datetime) -> tuple[datetime, datetime]:
    """``(now, end]``: articles publishing in it get this notice. ``end`` is the next notice
    time plus the minimum veto lead: anything later is covered (in time) by a later notice."""
    lead = timedelta(hours=cadence.min_veto_lead_hours)
    return now, next_notice(cadence, after=now) + lead


@dataclass
class VetoReport:
    notified: list[str] = field(default_factory=list[str])
    undelivered: list[str] = field(default_factory=list[str])
    held: list[str] = field(default_factory=list[str])


def _covered(article: Article) -> bool:
    """A notice (or a hold) was already recorded for the current approval, or this approval was
    vetoed. Stamps from an earlier approval (before ``statusChangedAt``) do not count: a vetoed
    article that is approved again gets a fresh notice."""
    veto = article.veto
    if veto is None:
        return False
    stamps = [
        stamp for stamp in (veto.vetoed_at, veto.sent_at, veto.alerted_at) if stamp is not None
    ]
    return any(parse_iso(stamp) >= parse_iso(article.status_changed_at) for stamp in stamps)


async def _record(
    store: Store, article: Article, notice: VetoNotice, *, stamp: str
) -> Stored[Article]:
    publish_at = article.publish.publish_at

    def mutate(doc: Article) -> Article | None:
        if (
            doc.status is not ArticleStatus.APPROVED
            or not doc.publish.auto
            or doc.publish.publish_at != publish_at
        ):
            return None
        return doc.model_copy(update={"veto": notice, "updated_at": stamp})

    return await update_doc(store, Article, article.id, mutate)


async def send_veto_notices(
    store: Store,
    notifier: Notifier,
    *,
    cadence: CadenceSettings,
    now: datetime,
    done: Collection[str] = (),
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> VetoReport:
    """The ``notify`` step of ``planner.veto-notice`` (module docs). ``done`` are article ids
    an earlier attempt of this step already handled; ``progress`` checkpoints each one."""
    start, end = notice_window(cadence, now=now)
    found = await find_docs(
        store,
        Article,
        [
            Filter("status", "==", ArticleStatus.APPROVED.value),
            Filter("publish.auto", "==", True),
            Filter("publish.publishAt", ">", to_iso(start)),
            Filter("publish.publishAt", "<=", to_iso(end)),
        ],
        strict=True,
    )
    report = VetoReport()
    lead = timedelta(hours=cadence.min_veto_lead_hours)
    stamp = to_iso(now)
    articles = sorted((stored.doc for stored in found), key=lambda a: a.publish.publish_at or "")
    for article in articles:
        if article.id in done or _covered(article) or article.publish.publish_at is None:
            continue
        previous = VetoNotice()  # not covered: whatever is recorded belongs to an earlier approval
        late = parse_iso(article.publish.publish_at) - now < lead
        if late:
            notice = previous.model_copy(update={"delivered": False, "alerted_at": stamp})
            title = HELD_TITLE
        else:
            title = NOTICE_TITLE
            notice = previous
        payload = NotificationPayload(
            title=title, url=article_href(article.id), tag=f"veto:{article.id}"
        )
        delivered = False
        try:
            result = await notifier.notify(payload, urgency="high")
            delivered = result.any_delivered
        except Exception:  # no receipt: the publish holds and the owner is alerted
            _logger.warning(
                "planner_veto_push_failed",
                extra={"article_id": article.id, "delivered": False, "late": late},
            )
        if not late:
            notice = notice.model_copy(
                update={
                    "sent_at": stamp,
                    "delivered": delivered,
                    "receipt_at": stamp if delivered else None,
                    "alerted_at": None if delivered else stamp,
                }
            )
        if late or not delivered:
            emit_alert("needs_attention", article.id)
        await _record(store, article, notice, stamp=stamp)
        if late:
            report.held.append(article.id)
        elif delivered:
            report.notified.append(article.id)
        else:
            report.undelivered.append(article.id)
        _logger.info(
            "planner_veto_notice",
            extra={"article_id": article.id, "delivered": delivered, "late": late},
        )
        if progress is not None:
            await progress(article.id)
    return report


# --------------------------------------------------------------------------------------------
# The veto
# --------------------------------------------------------------------------------------------


def veto_view(article: Article) -> VetoView:
    return VetoView(
        publish_at=article.publish.publish_at, auto=article.publish.auto, notice=article.veto
    )


async def _cancel_active(jobs: JobQueue, job_id: str) -> bool | None:
    """Cancel ``job_id`` when it is an active publish job; ``None`` when it does not exist."""
    try:
        job = await jobs.get(job_id)
    except NotFound:
        return None
    if job.type != PUBLISH_ARTICLE or not job.is_active:
        return False
    with contextlib.suppress(JobStateError, NotFound):
        await jobs.cancel(job_id)
        return True
    return False


async def cancel_publish_jobs(jobs: JobQueue, article: Article) -> list[str]:
    """Cancel the article's active ``publish.article`` jobs: its recorded job, and the
    deterministic id of its version with every re-run. Returns the ids cancelled."""
    cancelled: list[str] = []
    if article.publish.job_id is not None and await _cancel_active(jobs, article.publish.job_id):
        cancelled.append(article.publish.job_id)
    if article.version >= 1:
        base = publish_job_id(article.id, article.version)
        for run in range(MAX_RERUNS + 1):
            candidate = base if run == 0 else rerun_job_id(base, run)
            if candidate in cancelled:
                continue
            outcome = await _cancel_active(jobs, candidate)
            if outcome is None:
                break  # re-runs are consecutive: the first missing id ends them
            if outcome:
                cancelled.append(candidate)
    return cancelled


async def veto_article(
    store: Store, jobs: JobQueue, article_id: str, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Stop an approved article from publishing (module docs). 412 on a stale etag, 409
    ``not_vetoable`` unless the article is approved."""
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    article = current.doc
    if article.status is not ArticleStatus.APPROVED:
        raise ProblemError(
            409,
            code="not_vetoable",
            detail="Only an approved article waiting to publish can be vetoed.",
        )
    stamp = to_iso(now)

    def calibrate(autonomy: AutonomySettings) -> AutonomySettings:
        return record_review(
            autonomy,
            article_id=article_id,
            outcome="vetoed",
            at=stamp,
            content_sha256=article.content_sha256,
        )

    await update_singleton(store, AutonomySettings, calibrate, now=stamp)

    def mark(doc: Article) -> Article:
        veto = (doc.veto or VetoNotice()).model_copy(update={"vetoed_at": stamp})
        flags = (doc.calibration or CalibrationFlags()).model_copy(
            update={"outcome": "vetoed", "counted_at": stamp}
        )
        return doc.model_copy(update={"veto": veto, "calibration": flags})

    stored = await transition(
        store,
        article_id,
        ArticleStatus.IN_REVIEW,
        actor="owner",
        now=now,
        if_match=if_match,
        reason="Vetoed before publishing.",
        update=mark,
    )
    cancelled = await cancel_publish_jobs(jobs, stored.doc)
    _logger.info("planner_article_vetoed", extra={"article_id": article_id, "held": len(cancelled)})
    return stored
