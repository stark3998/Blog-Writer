"""The veto notice (receipt or hold, the window, too late, fail closed) and the veto itself
(back to review, publish job cancelled, the calibration streak restarted)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.domain.articles import Article, VetoNotice
from blogwriter.domain.docstore import read_doc, read_singleton, upsert_doc
from blogwriter.domain.jobtypes import publish_job_id
from blogwriter.domain.settings import AutonomySettings, Calibration, CalibrationEvent
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, RecordingNotifier
from blogwriter.notify import ALERT_EVENT
from blogwriter.planner.testing import StepError, article, run_job
from blogwriter.planner.veto import HELD_TITLE, NOTICE_TITLE

FRIDAY_NOON = datetime(2026, 12, 4, 17, 0, tzinfo=UTC)  # 12:00 EST
MONDAY_PUBLISH = "2026-12-07T14:00:00.000000Z"  # 09:00 EST


async def auto_article(
    services: FakeServices, publish_at: str = MONDAY_PUBLISH, veto: VetoNotice | None = None
) -> Article:
    return await article(
        services,
        status=ArticleStatus.APPROVED,
        publish_at=publish_at,
        auto=True,
        status_changed_at="2026-12-03T20:00:00.000000Z",
        veto=veto,
    )


async def notice(services: FakeServices, job_id: str = "veto-1") -> object:
    job = await run_job(services, "planner.veto-notice", {}, job_id=job_id)
    assert job.status == "succeeded"
    return job.result


async def test_a_delivered_notice_lets_auto_publish_proceed(services: FakeServices) -> None:
    services.clock.set(FRIDAY_NOON)
    target = await auto_article(services)
    assert await notice(services) == {"notified": 1, "undelivered": 0, "held": 0}
    veto = (await read_doc(services.store, Article, target.id)).doc.veto
    assert veto == VetoNotice(
        sent_at="2026-12-04T17:00:00.000000Z",
        delivered=True,
        receipt_at="2026-12-04T17:00:00.000000Z",
    )
    payload, urgency = services.notifier.sent[0]
    assert (payload.title, payload.url, urgency) == (
        NOTICE_TITLE,
        f"/studio/{target.id}/draft",
        "high",
    )
    # Title and link only: never the article's title or text.
    assert "Agent identity" not in payload.to_json()
    assert await notice(services, "veto-2") == {"notified": 0, "undelivered": 0, "held": 0}
    assert len(services.notifier.sent) == 1


async def test_without_a_receipt_the_publish_holds_and_the_owner_is_alerted(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    services.notifier = RecordingNotifier(deliver=False)
    services.clock.set(FRIDAY_NOON)
    target = await auto_article(services)
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        assert await notice(services) == {"notified": 0, "undelivered": 1, "held": 0}
    veto = (await read_doc(services.store, Article, target.id)).doc.veto
    assert veto is not None
    assert veto.delivered is False
    assert veto.sent_at == "2026-12-04T17:00:00.000000Z"
    assert veto.alerted_at == "2026-12-04T17:00:00.000000Z"
    alerts = [r for r in caplog.records if r.getMessage() == ALERT_EVENT]
    assert [(r.__dict__["alert_kind"], r.__dict__["ref_id"]) for r in alerts] == [
        ("needs_attention", target.id)
    ]


async def test_a_notice_too_late_for_the_veto_lead_holds(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    services.clock.set(datetime(2026, 12, 5, 20, 0, tzinfo=UTC))  # Saturday: 42 h before
    target = await auto_article(services)
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        assert await notice(services) == {"notified": 0, "undelivered": 0, "held": 1}
    veto = (await read_doc(services.store, Article, target.id)).doc.veto
    assert veto is not None
    assert veto.delivered is False
    assert veto.sent_at is None
    assert veto.alerted_at == "2026-12-05T20:00:00.000000Z"
    assert services.notifier.sent[0][0].title == HELD_TITLE
    assert any(r.getMessage() == ALERT_EVENT for r in caplog.records)


async def test_only_auto_articles_in_the_window_get_a_notice(services: FakeServices) -> None:
    services.clock.set(FRIDAY_NOON)
    later = await auto_article(services, publish_at="2026-12-14T14:00:00.000000Z")
    manual = await article(services, status=ArticleStatus.APPROVED, publish_at=MONDAY_PUBLISH)
    review = await article(
        services, status=ArticleStatus.IN_REVIEW, publish_at=MONDAY_PUBLISH, auto=True
    )
    vetoed = await auto_article(services, veto=VetoNotice(vetoed_at="2026-12-04T10:00:00.000000Z"))
    assert await notice(services) == {"notified": 0, "undelivered": 0, "held": 0}
    for doc in (later, manual, review):
        assert (await read_doc(services.store, Article, doc.id)).doc.veto is None
    assert services.notifier.sent == []
    del vetoed


async def test_an_unreadable_article_in_the_window_fails_the_job(services: FakeServices) -> None:
    services.clock.set(FRIDAY_NOON)
    target = await auto_article(services)
    raw = await services.store.read("articles", target.id)
    await services.store.replace(
        "articles", {**raw.body, "wordCount": -5}, if_match=raw.etag
    )  # no longer fits the model
    with pytest.raises(StepError):
        await run_job(services, "planner.veto-notice", {}, job_id="veto-bad")
    assert services.notifier.sent == []


async def test_paused_autonomy_sends_no_notice(services: FakeServices) -> None:
    services.clock.set(FRIDAY_NOON)
    await auto_article(services)
    paused = AutonomySettings.default(SAMPLE_NOW).model_copy(update={"paused": True})
    await upsert_doc(services.store, paused)
    assert await notice(services) == {"skipped": "paused"}
    assert services.notifier.sent == []


# --------------------------------------------------------------------------------------------
# The veto
# --------------------------------------------------------------------------------------------


async def calibrated(services: FakeServices, streak: int) -> None:
    events = [
        CalibrationEvent(article_id=f"art-{n:028x}", at=SAMPLE_NOW, outcome="clean")
        for n in range(streak)
    ]
    autonomy = AutonomySettings.default(SAMPLE_NOW).model_copy(
        update={"calibration": Calibration(clean_streak=streak, events=events)}
    )
    await upsert_doc(services.store, autonomy)


async def test_a_veto_holds_the_article_and_restarts_the_streak(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    services.clock.set(FRIDAY_NOON)
    await calibrated(services, 4)
    target = await auto_article(
        services, veto=VetoNotice(sent_at="2026-12-04T17:00:00.000000Z", delivered=True)
    )
    publish = await services.jobs.enqueue(
        "publish.article", {"articleId": target.id}, job_id=publish_job_id(target.id, 1)
    )
    etag = (await read_doc(services.store, Article, target.id)).etag
    response = await client.post(
        f"/api/articles/{target.id}/veto", headers={**write_headers, "If-Match": etag}
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "in_review"
    assert item["veto"]["vetoedAt"] == "2026-12-04T17:00:00.000000Z"
    assert item["calibration"]["outcome"] == "vetoed"
    assert response.headers["ETag"] == response.json()["etag"]
    assert (await services.jobs.get(publish.id)).status == "cancelled"
    autonomy, _ = await read_singleton(services.store, AutonomySettings, now=SAMPLE_NOW)
    assert autonomy.calibration.clean_streak == 0
    assert autonomy.calibration.events[-1].outcome == "vetoed"
    assert autonomy.calibration.events[-1].article_id == target.id


async def test_veto_refusals(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    services.clock.set(FRIDAY_NOON)
    target = await auto_article(services)
    stale = await client.post(
        f"/api/articles/{target.id}/veto", headers={**write_headers, "If-Match": '"old"'}
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["status"] == "approved"
    review = await article(services, status=ArticleStatus.IN_REVIEW)
    etag = (await read_doc(services.store, Article, review.id)).etag
    refused = await client.post(
        f"/api/articles/{review.id}/veto", headers={**write_headers, "If-Match": etag}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "not_vetoable"
    missing = await client.post(
        "/api/articles/art-0000000000000000000000000000/veto",
        headers={**write_headers, "If-Match": etag},
    )
    assert missing.status_code == 404
    no_match = await client.post(f"/api/articles/{target.id}/veto", headers=write_headers)
    assert no_match.status_code == 428
    view = await client.get(f"/api/articles/{target.id}/veto", headers=read_headers)
    assert view.json() == {"publishAt": MONDAY_PUBLISH, "auto": True, "notice": None}


async def test_a_vetoed_article_approved_again_gets_a_fresh_notice(
    services: FakeServices,
) -> None:
    """Vetoed last week, re-run and auto-approved again (2026-12-03): the old veto and notice
    belong to the earlier approval, so this approval is notified like any other."""
    services.clock.set(FRIDAY_NOON)
    earlier = "2026-11-27T17:00:00.000000Z"
    target = await auto_article(
        services, veto=VetoNotice(sent_at=earlier, delivered=True, vetoed_at=earlier)
    )
    assert await notice(services) == {"notified": 1, "undelivered": 0, "held": 0}
    veto = (await read_doc(services.store, Article, target.id)).doc.veto
    assert veto is not None
    assert veto.sent_at == "2026-12-04T17:00:00.000000Z"
    assert veto.delivered is True
    assert veto.vetoed_at is None
