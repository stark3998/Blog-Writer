"""A publish job that ends without running its own failure handling (a System cancel of a
running step, a quarantine, a crash between the route's write and its enqueue) never strands
its article in publishing/unpublishing: the System cancel and the quarantine hook release it to
publish_failed, and publish-now and unpublish retry from there."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.articles import transition
from blogwriter.core.testing import asgi_client
from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.jobtypes import PUBLISH_UNPUBLISH, ArticleJobInput
from blogwriter.domain.ports import services_installed
from blogwriter.domain.states import ArticleStatus
from blogwriter.jobs import Job, JobRunner
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.publish.recovery import after_cancel, after_quarantine
from blogwriter.publish.testing import (
    PublishHarness,
    make_harness,
    owner_headers,
    publish_app,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


async def article_of(harness: PublishHarness, article_id: str) -> Article:
    return (await read_doc(harness.services.store, Article, article_id)).doc


async def post(harness: PublishHarness, article_id: str, action: str) -> httpx.Response:
    etag = (await read_doc(harness.services.store, Article, article_id)).etag
    async with asgi_client(publish_app(harness)) as client:
        return await client.post(
            f"/api/articles/{article_id}/{action}", json={}, headers=owner_headers(etag)
        )


def alerts(caplog: pytest.LogCaptureFixture, kind: str) -> list[str]:
    return [
        str(getattr(record, "ref_id", ""))
        for record in caplog.records
        if record.getMessage() == "alert_event" and getattr(record, "alert_kind", "") == kind
    ]


async def cancel_during_build(harness: PublishHarness, job_id: str) -> Job:
    """Run the job until its build wait hangs, cancel it the way System does
    (``JobsService.cancel``: the durable request, then the local task), and return it once the
    runner has stopped the step."""
    harness.portfolio.status_gate = asyncio.Event()  # the build never answers
    services = harness.services
    runner = JobRunner(
        services.store,
        harness.registry,
        settings=fast_settings(lease_seconds=3600.0, retry_base_seconds=0.0),
        clock=services.clock,
    )

    async def waiting_for_the_build() -> bool:
        job = await harness.job(job_id)
        return {step.name: step.status for step in job.steps}.get("wait_build") == "running"

    async def cancelled() -> bool:
        return (await harness.job(job_id)).status == "cancelled"

    with services_installed(services):
        await runner.start()
        try:
            await eventually(waiting_for_the_build, within=15.0, interval=0.005)
            await services.jobs.cancel(job_id)
            assert runner.cancel_local(job_id)
            await eventually(cancelled, within=15.0, interval=0.005)
        finally:
            await runner.stop()
    return await harness.job(job_id)


async def test_a_system_cancel_during_the_build_releases_the_article(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    job_id = response.json()["jobId"]
    services.clock.set(MONDAY)
    job = await cancel_during_build(harness, job_id)
    assert job.status == "cancelled"
    stranded = await article_of(harness, article.id)
    assert stranded.status is S.PUBLISHING  # what the runner leaves behind
    released = await after_cancel(services, job_id, wait_seconds=0)
    assert released is not None
    assert released.doc.status is S.PUBLISH_FAILED
    assert released.doc.publish.last_error == "cancelled"
    assert alerts(caplog, "publish_failed") == [article.id]
    # The owner retries with publish-now, and it publishes.
    harness.portfolio.status_gate = None
    again = await post(harness, article.id, "publish-now")
    assert again.status_code == 200, again.text
    rerun = await harness.run_job(again.json()["jobId"])
    assert rerun.status == "succeeded", rerun.error
    assert (await article_of(harness, article.id)).status is S.PUBLISHED


async def test_publish_now_releases_an_article_whose_job_ended(harness: PublishHarness) -> None:
    """No hook ran (a crash, an older process): publish-now itself is the exit."""
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    job_id = response.json()["jobId"]
    services.clock.set(MONDAY)
    await cancel_during_build(harness, job_id)
    assert (await article_of(harness, article.id)).status is S.PUBLISHING
    harness.portfolio.status_gate = None
    again = await post(harness, article.id, "publish-now")
    assert again.status_code == 200, again.text
    assert again.json()["article"]["item"]["status"] == "publish_failed"  # the job moves it on
    rerun = await harness.run_job(again.json()["jobId"])
    assert rerun.status == "succeeded", rerun.error
    assert (await article_of(harness, article.id)).status is S.PUBLISHED


async def test_an_active_run_is_never_released(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services, status=S.APPROVED)
    job = await services.jobs.enqueue(
        PUBLISH_UNPUBLISH, ArticleJobInput(article_id=article.id).to_json()
    )

    def publishing(doc: Article) -> Article:
        publish = doc.publish.model_copy(update={"job_id": job.id, "last_operation": "publish"})
        return doc.model_copy(update={"publish": publish})

    now = services.clock.now()
    await transition(
        services.store, article.id, S.PUBLISHING, actor="system", now=now, update=publishing
    )
    response = await post(harness, article.id, "publish-now")
    assert (response.status_code, response.json()["code"]) == (409, "publish_in_progress")
    assert (await article_of(harness, article.id)).status is S.PUBLISHING
    assert await after_quarantine(services, job.id) is None  # still queued: not quarantined


async def test_a_quarantined_unpublish_is_released_and_retried(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    services.clock.set(MONDAY)
    published = await harness.run_job(response.json()["jobId"])
    assert published.status == "succeeded", published.error
    accepted = await post(harness, article.id, "unpublish")
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["jobId"]

    async def quarantine(job: Job) -> None:
        def change(body: dict[str, object]) -> dict[str, object]:
            return {**body, "status": "quarantined"}

        raw = await services.store.read("jobs", job.id)
        await services.store.replace("jobs", change(dict(raw.body)), if_match=raw.etag)

    await quarantine(await harness.job(job_id))
    assert (await article_of(harness, article.id)).status is S.UNPUBLISHING
    released = await after_quarantine(services, job_id)
    assert released is not None
    assert released.doc.status is S.PUBLISH_FAILED
    assert released.doc.publish.last_error == "quarantined"
    assert alerts(caplog, "publish_failed") == [article.id]
    again = await post(harness, article.id, "unpublish")
    assert again.status_code == 202, again.text
    done = await harness.run_job(again.json()["jobId"])
    assert done.status == "succeeded", done.error
    assert (await article_of(harness, article.id)).status is S.UNPUBLISHED


async def test_unpublish_exits_an_unpublishing_article_whose_job_never_existed(
    harness: PublishHarness,
) -> None:
    """A crash between the route's write and its enqueue: the named job does not exist."""
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    services.clock.set(MONDAY)
    published = await harness.run_job(response.json()["jobId"])
    assert published.status == "succeeded", published.error
    now = services.clock.now()

    def orphaned(doc: Article) -> Article:
        publish = doc.publish.model_copy(
            update={"job_id": "job-0193f0000000000000000000000f", "last_operation": "unpublish"}
        )
        return doc.model_copy(update={"publish": publish})

    await transition(
        services.store, article.id, S.UNPUBLISHING, actor="system", now=now, update=orphaned
    )
    again = await post(harness, article.id, "unpublish")
    assert again.status_code == 202, again.text
    done = await harness.run_job(again.json()["jobId"])
    assert done.status == "succeeded", done.error
    assert (await article_of(harness, article.id)).status is S.UNPUBLISHED
