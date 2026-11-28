"""The blocklist at the moment an article leaves the app (security rule 1.9.5): a term the owner
adds after the gates ran is refused at approval, holds the scheduled publish and never reaches
the public document or a push. Also the employer footer disclaimer on the live page."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.articles import transition
from blogwriter.core.testing import OWNER_OID, asgi_client, principal_headers
from blogwriter.db import NotFound
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, save_singleton, update_doc
from blogwriter.domain.settings import EmployerPolicy
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.testing import (
    PublishHarness,
    make_harness,
    owner_headers,
    passing_report,
    publish_app,
    save_blocklist,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
TERM = "Gateway"
"""In the body of ``publish.testing.article_body`` (and in no title or push)."""


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


async def article_of(harness: PublishHarness, article_id: str) -> Article:
    return (await read_doc(harness.services.store, Article, article_id)).doc


async def post(
    harness: PublishHarness, article_id: str, action: str, **body: object
) -> httpx.Response:
    etag = (await read_doc(harness.services.store, Article, article_id)).etag
    async with asgi_client(publish_app(harness)) as client:
        return await client.post(
            f"/api/articles/{article_id}/{action}", json=body, headers=owner_headers(etag)
        )


async def block(harness: PublishHarness, *terms: str) -> None:
    await save_blocklist(harness.services.store, terms, now=to_iso(harness.services.clock.now()))


def alerts(caplog: pytest.LogCaptureFixture, kind: str) -> list[str]:
    return [
        str(getattr(record, "ref_id", ""))
        for record in caplog.records
        if record.getMessage() == "alert_event" and getattr(record, "alert_kind", "") == kind
    ]


async def test_a_term_blocklisted_after_the_gates_refuses_approval(harness: PublishHarness) -> None:
    article = await seed_article(harness.services)  # its passing report predates the term
    await block(harness, TERM)
    response = await post(harness, article.id, "approve")
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "confidential_text"
    assert {finding["field"] for finding in problem["findings"]} == {"document"}
    assert {finding["ref"] for finding in problem["findings"]} == {"t-1"}
    assert TERM not in response.text  # findings name the term by id only
    stored = await article_of(harness, article.id)
    assert stored.status is S.IN_REVIEW
    assert stored.publish.job_id is None
    now_response = await post(harness, article.id, "publish-now")
    assert now_response.status_code == 422


async def test_an_unsaved_blocklist_refuses_approval(harness: PublishHarness) -> None:
    article = await seed_article(harness.services, blocklist=False)
    response = await post(harness, article.id, "approve")
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")


async def test_a_blocklisted_correction_note_is_refused(harness: PublishHarness) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    services.clock.set(MONDAY)
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    now = services.clock.now()
    await transition(store, article.id, S.IN_REVIEW, actor="owner", now=now)

    def gated(doc: Article) -> Article:
        return doc.model_copy(update={"gates": passing_report(doc, at=to_iso(now))})

    await update_doc(store, Article, article.id, gated)
    await block(harness, "Contoso")
    refused = await post(
        harness, article.id, "approve", correctionNote="Removed the Contoso numbers."
    )
    assert refused.status_code == 422, refused.text
    findings = refused.json()["findings"]
    assert {finding["field"] for finding in findings} == {"correctionNote"}
    assert "Contoso" not in refused.text
    assert (await article_of(harness, article.id)).status is S.IN_REVIEW
    accepted = await post(harness, article.id, "approve", correctionNote="Fixed the numbers.")
    assert accepted.status_code == 200, accepted.text


async def test_a_term_blocklisted_after_approval_holds_the_scheduled_publish(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    await block(harness, TERM)  # the owner adds the term over the weekend
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    stored = await article_of(harness, article.id)
    assert stored.status is S.IN_REVIEW
    assert stored.last_error == "blocklist_hit"
    assert await services.store.find("published-blogs") == []
    assert harness.portfolio.runs == {}
    assert alerts(caplog, "needs_attention") == [article.id]
    assert services.notifier.sent[-1][0].title.startswith("Publish held: ")
    assert TERM not in json.dumps((await services.store.read("jobs", job_id)).body)


async def test_a_blocklisted_title_never_reaches_a_push(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    job_id = response.json()["jobId"]
    await block(harness, "Scope")  # in the title
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    titles = [payload.title for payload, _ in services.notifier.sent]
    assert titles
    assert all("Scope" not in title for title in titles)
    assert titles[-1] == "Publish held: your article"


async def test_a_blocklisted_source_title_never_reaches_the_public_document(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    job_id = response.json()["jobId"]
    # Only the brief's citation title ("OWASP Top 10 for LLM Applications") carries it.
    await block(harness, "Applications")
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    stored = await article_of(harness, article.id)
    assert stored.status is S.PUBLISH_FAILED
    assert await services.store.find("published-blogs") == []
    assert harness.portfolio.runs == {}
    assert alerts(caplog, "publish_failed") == [article.id]


async def test_the_staging_preview_is_refused_too(harness: PublishHarness) -> None:
    article = await seed_article(harness.services)
    await block(harness, TERM)
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article.id}/staging-preview", headers=owner_headers()
        )
    assert response.status_code == 422, response.text
    assert await harness.services.store.find("published-blogs-staging") == []


async def test_the_footer_disclaimer_ships_with_the_page_the_preview_shows(
    harness: PublishHarness,
) -> None:
    services = harness.services
    stamp = to_iso(services.clock.now())
    disclaimer = "Views are my own, not my employer's."
    policy = EmployerPolicy(
        id=EmployerPolicy.SINGLETON_ID,
        created_at=stamp,
        updated_at=stamp,
        disclaimer_long=disclaimer,
        configured=True,
    )
    await save_singleton(services.store, policy, if_match=None)
    article = await seed_article(services)
    async with asgi_client(publish_app(harness)) as client:
        preview = await client.get(
            f"/api/articles/{article.id}/preview", headers=principal_headers(OWNER_OID)
        )
    assert preview.status_code == 200
    shown = disclaimer.replace("'", "&#39;")
    assert shown in preview.text
    response = await post(harness, article.id, "approve")
    services.clock.set(MONDAY)
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    doc = (await services.store.read("published-blogs", "scope-every-tool-call")).body
    assert doc["htmlContent"].endswith(
        "<hr>\n<p><em>Views are my own, not my employer's.</em></p>\n"
    )


async def test_without_a_disclaimer_the_page_has_no_footer(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    response = await post(harness, article.id, "approve")
    services.clock.set(MONDAY)
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    doc = (await services.store.read("published-blogs", "scope-every-tool-call")).body
    assert "<hr>\n<p><em>" not in doc["htmlContent"]
    with pytest.raises(NotFound):
        await services.store.read("published-blogs-staging", "scope-every-tool-call")
