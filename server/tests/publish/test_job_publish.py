"""``publish.article`` on the real job runner with fake services, GitHub and site: approve ->
scheduled publish -> verified; published only after the live page carries the new version;
remote ids re-attached after a crash; late runs hold; failures alert."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from blogwriter.articles import owner_save, pipeline_write, transition
from blogwriter.core.testing import asgi_client
from blogwriter.db import Store
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, update_doc
from blogwriter.domain.jobtypes import ENTITY_CHANGED, publish_job_id
from blogwriter.domain.states import ArticleStatus, WriterConflict
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.testing import (
    PublishHarness,
    article_body,
    make_harness,
    owner_headers,
    passing_report,
    publish_app,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
"""Monday 09:00 America/New_York: the default article publish time after the fake clock's
Thursday 2026-11-26 12:00 UTC."""


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


async def etag_of(store: Store, article_id: str) -> str:
    return (await read_doc(store, Article, article_id)).etag


async def article_of(store: Store, article_id: str) -> Article:
    return (await read_doc(store, Article, article_id)).doc


async def approve(harness: PublishHarness, article_id: str, **body: object) -> httpx.Response:
    etag = await etag_of(harness.services.store, article_id)
    async with asgi_client(publish_app(harness)) as client:
        return await client.post(
            f"/api/articles/{article_id}/approve", json=body, headers=owner_headers(etag)
        )


async def published_doc(store: Store, slug: str) -> dict[str, Any]:
    return (await store.read("published-blogs", slug)).body


def alerts(caplog: pytest.LogCaptureFixture, kind: str) -> list[str]:
    return [
        str(getattr(record, "ref_id", ""))
        for record in caplog.records
        if record.getMessage() == "alert_event" and getattr(record, "alert_kind", "") == kind
    ]


async def publish_once(harness: PublishHarness, article: Article) -> str:
    response = await approve(harness, article.id)
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    harness.services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    return job_id


# --------------------------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------------------------


async def test_approve_schedules_and_the_job_publishes_after_verification(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    assert response.status_code == 200, response.text
    body = response.json()
    job_id = body["jobId"]
    assert job_id == publish_job_id(article.id, 1)
    assert body["article"]["item"]["status"] == "approved"
    assert body["article"]["item"]["publish"]["publishAt"] == "2026-11-30T14:00:00.000000Z"
    assert body["article"]["item"]["publish"]["jobId"] == job_id
    assert response.headers["ETag"] == body["article"]["etag"]
    job = await harness.job(job_id)
    assert job.run_after == "2026-11-30T14:00:00.000000Z"
    assert job.input == {
        "origin": "owner",
        "articleId": article.id,
        "version": 1,
        "publishAt": "2026-11-30T14:00:00.000000Z",
        "auto": False,
    }

    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    assert [step.status for step in job.steps] == ["succeeded"] * 7

    stored = await article_of(services.store, article.id)
    assert stored.status is S.PUBLISHED
    publish = stored.publish
    assert publish.live
    assert publish.slug == "scope-every-tool-call"
    assert publish.url == "https://site.test/blog/scope-every-tool-call"
    assert publish.published_at == to_iso(MONDAY)
    assert publish.last_published_version == 1
    assert publish.last_published_sha256 == stored.compute_sha256()
    assert publish.verified_at is not None
    assert publish.last_operation == "publish"
    assert publish.portfolio_run_id is not None
    assert len(publish.assets) == 3

    doc = await published_doc(services.store, "scope-every-tool-call")
    assert validate_published_doc(doc, now=MONDAY) == []
    assert doc["publishedAt"] == "2026-11-30T14:00:00Z"
    assert len(harness.portfolio.runs) == 1
    run = harness.portfolio.dispatches[0]
    assert (run.workflow, run.ref) == ("deploy.yml", "main")
    changed = [event for event in job.events if event.type == ENTITY_CHANGED]
    assert changed
    assert all(event.data == {"entity": "article", "id": article.id} for event in changed)
    titles = [payload.title for payload, _ in services.notifier.sent]
    assert titles[-1] == "Published: Scope every tool call, not every agent"
    assert services.notifier.sent[-1][0].url == f"/studio/{article.id}/draft"


async def test_published_only_after_the_live_page_shows_the_new_version(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    article = await seed_article(services)
    harness.portfolio.deploy_builds = False  # builds succeed, the site never changes
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "verify_timeout"
    stored = await article_of(services.store, article.id)
    assert stored.status is S.PUBLISH_FAILED
    assert stored.last_error == "verify_timeout"
    assert stored.publish.last_error == "verify_timeout"
    assert stored.publish.verified_at is None
    assert stored.publish.last_published_version is None
    assert harness.portfolio.site_requests  # it kept asking until the timeout
    assert alerts(caplog, "publish_failed") == [article.id]
    assert services.notifier.sent[-1][0].title.startswith("Publish failed: ")


async def test_a_republish_is_not_verified_by_the_old_page(harness: PublishHarness) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    await publish_once(harness, article)
    old = await published_doc(store, "scope-every-tool-call")

    # A correction: back to review, an edit, gates again, approval with a note.
    now = services.clock.now()
    await transition(store, article.id, S.IN_REVIEW, actor="owner", now=now)
    current = await read_doc(store, Article, article.id)
    saved = await owner_save(
        store,
        article.id,
        if_match=current.etag,
        base_version=current.doc.version,
        meta=current.doc.meta,
        markdown=article_body(extra=" Then the one that can send mail."),
        now=now,
    )
    edited = saved.stored.doc

    def gated(doc: Article) -> Article:
        return doc.model_copy(update={"gates": passing_report(doc, at=to_iso(now))})

    await update_doc(store, Article, article.id, gated)
    harness.portfolio.deploy_builds = False  # the site keeps the old page (old marker)
    response = await approve(
        harness,
        article.id,
        correctionNote="Added the mail tool.",
        publishAt=to_iso(now + timedelta(minutes=10)),
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    assert job_id == publish_job_id(article.id, edited.version)
    services.clock.set(now + timedelta(minutes=10))
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "verify_timeout"
    assert harness.portfolio.pages["scope-every-tool-call"][0] == 200  # the old page answered
    new = await published_doc(store, "scope-every-tool-call")
    assert new["updatedAt"] > old["updatedAt"]
    stored = await article_of(store, article.id)
    assert stored.status is S.PUBLISH_FAILED
    assert stored.publish.last_published_version == 1  # still the old version


# --------------------------------------------------------------------------------------------
# Remote ids and crashes
# --------------------------------------------------------------------------------------------


async def test_a_crash_between_dispatch_and_run_lookup_does_not_dispatch_twice(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    harness.portfolio.hidden_lookups = 1  # GitHub has not listed the run yet
    crashed: list[float] = []

    def crash_once(seconds: float) -> None:
        if not crashed:
            crashed.append(seconds)
            raise RuntimeError("the process died here")

    harness.sleep_hooks.append(crash_once)
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert crashed
    assert job.status == "succeeded", job.error
    assert len(harness.portfolio.runs) == 1  # dispatched exactly once
    dispatch = job.step("dispatch")
    assert dispatch is not None
    assert set(dispatch.remote_ids) == {"correlation", "dispatched", "run"}
    assert dispatch.remote_ids["run"] == str(harness.portfolio.dispatches[0].id)
    assert dispatch.runs == 2
    assert harness.portfolio.dispatches[0].correlation == dispatch.remote_ids["correlation"]


async def test_a_dispatch_lost_before_its_receipt_is_looked_up_then_sent_once(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    harness.portfolio.fail_next["dispatch"] = [502]  # GitHub hiccups on the first dispatch
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    assert len(harness.portfolio.runs) == 1
    posts = [call for call in harness.portfolio.calls if call[0] == "POST"]
    assert len(posts) == 2  # the failed one and the real one


async def test_a_permanent_github_error_fails_and_alerts(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    harness.portfolio.fail_next["dispatch"] = [422]
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "portfolio_rejected"
    stored = await article_of(services.store, article.id)
    assert stored.status is S.PUBLISH_FAILED
    assert alerts(caplog, "publish_failed") == [article.id]


async def test_a_failed_build_fails_and_a_system_retry_builds_again(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    harness.portfolio.conclusion = "failure"
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "build_failed"
    stored = await article_of(services.store, article.id)
    assert stored.status is S.PUBLISH_FAILED
    assert stored.publish.last_operation == "publish"
    assert alerts(caplog, "publish_failed") == [article.id]

    harness.portfolio.conclusion = "success"
    await services.jobs.retry(job_id)  # System: retry the job
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    assert len(harness.portfolio.runs) == 2  # a new build for the retry
    assert (await article_of(services.store, article.id)).status is S.PUBLISHED


async def test_unexpected_errors_retry_then_fail_the_article(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]

    def always(seconds: float) -> None:
        raise RuntimeError("boom")

    harness.portfolio.polls_to_complete = 100
    harness.sleep_hooks.append(always)
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "publish_error"
    assert job.attempt == 3
    assert (await article_of(services.store, article.id)).status is S.PUBLISH_FAILED
    assert alerts(caplog, "publish_failed") == [article.id]


# --------------------------------------------------------------------------------------------
# Holds and who writes
# --------------------------------------------------------------------------------------------


async def test_a_job_claimed_two_days_late_holds_instead_of_publishing(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    services.clock.set(MONDAY + timedelta(days=2))
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "publish_late"
    stored = await article_of(services.store, article.id)
    assert stored.status is S.IN_REVIEW
    assert stored.last_error == "publish_late"
    assert stored.publish.last_error == "publish_late"
    assert harness.portfolio.runs == {}
    assert await services.store.find("published-blogs") == []
    assert alerts(caplog, "needs_attention") == [article.id]
    assert services.notifier.sent[-1][0].title.startswith("Publish held: ")


async def test_a_run_within_the_lateness_window_still_publishes(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    services.clock.set(MONDAY + timedelta(minutes=59))
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error


async def test_a_superseded_run_changes_nothing(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await approve(harness, article.id)
    job_id = response.json()["jobId"]
    now = services.clock.now()
    await transition(services.store, article.id, S.IN_REVIEW, actor="owner", now=now)
    before = await etag_of(services.store, article.id)
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "superseded"
    assert await etag_of(services.store, article.id) == before
    assert harness.portfolio.runs == {}


async def test_pipeline_writes_are_refused_while_publishing(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services, status=S.APPROVED)
    now = services.clock.now()
    await transition(services.store, article.id, S.PUBLISHING, actor="system", now=now)
    with pytest.raises(WriterConflict):
        await pipeline_write(services.store, article.id, now=now, markdown="Changed.")
    stored = await article_of(services.store, article.id)
    assert stored.markdown == article.markdown


async def test_job_documents_hold_references_only(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    job_id = await publish_once(harness, article)
    raw = json.dumps((await services.store.read("jobs", job_id)).body)
    assert "borrowed authority" not in raw  # body text
    assert "<h2" not in raw  # rendered HTML
    assert "Scope every tool call" not in raw  # the title
    assert len(raw) < 20_000
