"""The auto-publish path of ``publish.article``: it needs ``publish.auto``, the autonomy decision,
autonomy not paused, a draft that is not review-only and a veto notice sent at least
``minVetoLeadHours`` ahead with a push delivery receipt and not vetoed; anything else holds
(approved -> in_review, alert, push). A slug owned by another document is refused on both paths.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from blogwriter.domain.articles import Article, GateVerdict, PublishState, VetoNotice
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, replace_doc, save_singleton
from blogwriter.domain.jobtypes import PUBLISH_ARTICLE, PublishInput, publish_job_id
from blogwriter.domain.settings import AutonomySettings, Calibration, JudgeCalibration
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.testing import PublishHarness, make_harness, seed_article

S = ArticleStatus
PUBLISH_AT = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
NOTICE_AT = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)  # Fri 12:00 local: 69 h ahead


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


async def allow_auto(harness: PublishHarness, **changes: object) -> None:
    stamp = to_iso(harness.services.clock.now())
    autonomy = AutonomySettings.default(stamp).model_copy(
        update={
            "auto_publish_enabled": True,
            "calibration": Calibration(clean_streak=6),
            "judge": JudgeCalibration(as_is_rate=0.95, sample_size=12, checked_at=stamp),
            **changes,
        }
    )
    await save_singleton(harness.services.store, autonomy, if_match=None)


def delivered(**changes: object) -> VetoNotice:
    base = VetoNotice(
        sent_at=to_iso(NOTICE_AT),
        delivered=True,
        receipt_at=to_iso(NOTICE_AT + timedelta(seconds=2)),
    )
    return base.model_copy(update=changes)


async def auto_approved(
    harness: PublishHarness, *, veto: VetoNotice | None, review_only: bool = False
) -> tuple[Article, str]:
    """An article the pipeline approved on the auto path (it does not set ``publish.jobId``)."""
    article = await seed_article(
        harness.services,
        status=S.APPROVED,
        publish=PublishState(publish_at=to_iso(PUBLISH_AT), auto=True),
        veto=veto,
        review_only=review_only,
        review_only_reasons=["fallback_model"] if review_only else [],
    )
    job_id = publish_job_id(article.id, 1)
    payload = PublishInput(
        article_id=article.id, version=1, publish_at=to_iso(PUBLISH_AT), auto=True
    )
    await harness.services.jobs.enqueue(
        PUBLISH_ARTICLE, payload.to_json(), job_id=job_id, run_after=PUBLISH_AT
    )
    harness.services.clock.set(PUBLISH_AT)
    return article, job_id


async def held(harness: PublishHarness, article: Article, job_id: str, code: str) -> None:
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == code
    stored = (await read_doc(harness.services.store, Article, article.id)).doc
    assert stored.status is S.IN_REVIEW
    assert stored.last_error == code
    assert harness.portfolio.runs == {}
    assert await harness.services.store.find("published-blogs") == []


async def test_the_auto_path_publishes_with_everything_in_place(harness: PublishHarness) -> None:
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=delivered())
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    stored = (await read_doc(harness.services.store, Article, article.id)).doc
    assert stored.status is S.PUBLISHED
    assert stored.publish.auto
    assert stored.publish.job_id == job_id


async def test_without_a_veto_notice_it_holds(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=None)
    await held(harness, article, job_id, "veto_notice_missing")
    alerts = [
        record
        for record in caplog.records
        if record.getMessage() == "alert_event" and getattr(record, "ref_id", None) == article.id
    ]
    assert [getattr(record, "alert_kind", None) for record in alerts] == ["needs_attention"]
    payload, _ = harness.services.notifier.sent[-1]
    assert payload.title == "Publish held: Scope every tool call, not every agent"
    assert payload.url == f"/studio/{article.id}/draft"


async def test_without_a_delivery_receipt_it_holds(harness: PublishHarness) -> None:
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=delivered(delivered=False, receipt_at=None))
    await held(harness, article, job_id, "veto_not_delivered")


async def test_a_notice_sent_less_than_the_lead_time_ahead_holds(harness: PublishHarness) -> None:
    await allow_auto(harness)
    late = PUBLISH_AT - timedelta(hours=47)
    article, job_id = await auto_approved(harness, veto=delivered(sent_at=to_iso(late)))
    await held(harness, article, job_id, "veto_notice_late")


async def test_a_vetoed_article_holds(harness: PublishHarness) -> None:
    await allow_auto(harness)
    article, job_id = await auto_approved(
        harness, veto=delivered(vetoed_at=to_iso(NOTICE_AT + timedelta(hours=1)))
    )
    await held(harness, article, job_id, "vetoed")


async def test_paused_autonomy_holds(harness: PublishHarness) -> None:
    await allow_auto(harness, paused=True, paused_at=to_iso(NOTICE_AT))
    article, job_id = await auto_approved(harness, veto=delivered())
    await held(harness, article, job_id, "autonomy_paused")


async def test_the_autonomy_decision_is_required(harness: PublishHarness) -> None:
    await allow_auto(harness, calibration=Calibration(clean_streak=2))
    article, job_id = await auto_approved(harness, veto=delivered())
    await held(harness, article, job_id, "auto_publish_not_allowed")


async def test_a_review_only_draft_never_auto_publishes(harness: PublishHarness) -> None:
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=delivered(), review_only=True)
    await held(harness, article, job_id, "review_only")


async def test_auto_path_needs_gates_that_passed_on_their_own(harness: PublishHarness) -> None:
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=delivered())
    assert article.gates is not None
    results = [
        result.model_copy(update={"verdict": GateVerdict.WARN if index else GateVerdict.FAIL})
        for index, result in enumerate(article.gates.results)
    ]
    report = article.gates.model_copy(update={"results": results})
    stored = await read_doc(harness.services.store, Article, article.id)
    await replace_doc(
        harness.services.store,
        stored.doc.model_copy(update={"gates": report}),
        if_match=stored.etag,
    )
    await held(harness, article, job_id, "gates_blocking")


@pytest.mark.parametrize("auto", [True, False])
async def test_a_slug_owned_by_another_document_is_refused(
    harness: PublishHarness, auto: bool
) -> None:
    await allow_auto(harness)
    legacy = {
        "id": "scope-every-tool-call",
        "slug": "scope-every-tool-call",
        "title": "A v1 post that already uses this slug",
        "publishedAt": "2025-01-01T00:00:00+00:00",
    }
    await harness.services.store.create("published-blogs", legacy)
    if auto:
        article, job_id = await auto_approved(harness, veto=delivered())
    else:
        article = await seed_article(
            harness.services,
            status=S.APPROVED,
            publish=PublishState(publish_at=to_iso(PUBLISH_AT)),
        )
        job_id = publish_job_id(article.id, 1)
        payload = PublishInput(article_id=article.id, version=1, publish_at=to_iso(PUBLISH_AT))
        await harness.services.jobs.enqueue(PUBLISH_ARTICLE, payload.to_json(), job_id=job_id)
        harness.services.clock.set(PUBLISH_AT)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "slug_taken"
    stored = (await read_doc(harness.services.store, Article, article.id)).doc
    assert stored.status is S.IN_REVIEW
    assert (await harness.services.store.read("published-blogs", legacy["id"])).body == legacy


async def test_without_the_author_name_it_holds_before_rendering(harness: PublishHarness) -> None:
    article = await seed_article(
        harness.services,
        status=S.APPROVED,
        publish=PublishState(publish_at=to_iso(PUBLISH_AT)),
        brand=False,
    )
    job_id = publish_job_id(article.id, 1)
    payload = PublishInput(article_id=article.id, version=1, publish_at=to_iso(PUBLISH_AT))
    await harness.services.jobs.enqueue(PUBLISH_ARTICLE, payload.to_json(), job_id=job_id)
    harness.services.clock.set(PUBLISH_AT)
    await held(harness, article, job_id, "brand_incomplete")
    assert harness.services.render is not None
    assert harness.services.render.calls == []


async def test_a_finished_job_left_on_the_article_does_not_block_the_auto_path(
    harness: PublishHarness,
) -> None:
    """The pipeline's auto path does not set ``publish.jobId``: a reference an earlier publish
    left there (a finished job) is not a rival run."""
    await allow_auto(harness)
    old = await harness.services.jobs.enqueue(PUBLISH_ARTICLE, None, job_id="publish-old-v00001")
    await harness.services.jobs.cancel(old.id)
    article = await seed_article(
        harness.services,
        status=S.APPROVED,
        publish=PublishState(publish_at=to_iso(PUBLISH_AT), auto=True, job_id=old.id),
        veto=delivered(),
    )
    job_id = publish_job_id(article.id, 1)
    payload = PublishInput(
        article_id=article.id, version=1, publish_at=to_iso(PUBLISH_AT), auto=True
    )
    await harness.services.jobs.enqueue(PUBLISH_ARTICLE, payload.to_json(), job_id=job_id)
    harness.services.clock.set(PUBLISH_AT)
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    stored = (await read_doc(harness.services.store, Article, article.id)).doc
    assert stored.publish.job_id == job_id


async def test_an_active_rival_run_supersedes(harness: PublishHarness) -> None:
    rival = await harness.services.jobs.enqueue(
        PUBLISH_ARTICLE, None, job_id="publish-rival", run_after=PUBLISH_AT + timedelta(days=9)
    )
    article = await seed_article(
        harness.services,
        status=S.APPROVED,
        publish=PublishState(publish_at=to_iso(PUBLISH_AT), job_id=rival.id),
    )
    job_id = publish_job_id(article.id, 1)
    payload = PublishInput(article_id=article.id, version=1, publish_at=to_iso(PUBLISH_AT))
    await harness.services.jobs.enqueue(PUBLISH_ARTICLE, payload.to_json(), job_id=job_id)
    harness.services.clock.set(PUBLISH_AT)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "superseded"
    stored = (await read_doc(harness.services.store, Article, article.id)).doc
    assert stored.status is S.APPROVED


async def test_a_notice_from_an_earlier_approval_does_not_cover_a_later_one(
    harness: PublishHarness,
) -> None:
    """Approved, notified Friday, pulled back, re-run and auto-approved again after the notice
    for the same Monday: the receipt belongs to the earlier approval, so the publish holds."""
    await allow_auto(harness)
    article, job_id = await auto_approved(harness, veto=delivered())
    reapproved = article.model_copy(
        update={"status_changed_at": to_iso(NOTICE_AT + timedelta(hours=3))}
    )
    current = await read_doc(harness.services.store, Article, article.id)
    await replace_doc(harness.services.store, reapproved, if_match=current.etag)
    await held(harness, article, job_id, "veto_notice_missing")


async def test_a_veto_of_an_earlier_approval_is_not_a_veto_of_this_one(
    harness: PublishHarness,
) -> None:
    await allow_auto(harness)
    earlier = to_iso(datetime(2026, 11, 20, 17, 0, tzinfo=UTC))
    article, job_id = await auto_approved(
        harness, veto=VetoNotice(sent_at=earlier, delivered=True, vetoed_at=earlier)
    )
    # No notice was sent for this approval: it holds for that, not as "vetoed".
    await held(harness, article, job_id, "veto_notice_missing")
