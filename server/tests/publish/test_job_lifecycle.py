"""After the first publish: corrections (dated notes, auto-publish off), slug renames (redirect
stubs one hop deep), unpublish (tombstone, a failed unpublish retried), staging previews, and the
waiting state when the portfolio is not configured."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from blogwriter.articles import owner_save, transition
from blogwriter.core.testing import asgi_client
from blogwriter.db import Store
from blogwriter.domain.articles import Article, ArticleMeta
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, read_singleton, update_doc, update_singleton
from blogwriter.domain.settings import AutonomySettings
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.testing import (
    PublishHarness,
    article_body,
    make_harness,
    make_publish_settings,
    owner_headers,
    passing_report,
    publish_app,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
SLUG = "scope-every-tool-call"


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


async def stored(store: Store, article_id: str) -> tuple[Article, str]:
    found = await read_doc(store, Article, article_id)
    return found.doc, found.etag


async def post(
    harness: PublishHarness, article_id: str, action: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    _, etag = await stored(harness.services.store, article_id)
    async with asgi_client(publish_app(harness)) as client:
        return await client.post(
            f"/api/articles/{article_id}/{action}", json=body, headers=owner_headers(etag)
        )


async def doc_of(store: Store, slug: str) -> dict[str, Any]:
    return (await store.read("published-blogs", slug)).body


async def published(harness: PublishHarness) -> Article:
    """A seeded article approved and published on Monday."""
    article = await seed_article(harness.services)
    response = await post(harness, article.id, "approve", {})
    assert response.status_code == 200, response.text
    harness.services.clock.set(MONDAY)
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    return (await stored(harness.services.store, article.id))[0]


async def start_correction(
    harness: PublishHarness, article: Article, *, slug: str | None = None, extra: str = " More."
) -> Article:
    """published -> in_review, an owner edit (optionally a new slug), gates passing again."""
    store = harness.services.store
    now = harness.services.clock.now()
    await transition(store, article.id, S.IN_REVIEW, actor="owner", now=now)
    current, etag = await stored(store, article.id)
    meta = current.meta
    if slug is not None:
        meta = ArticleMeta.model_validate({**meta.model_dump(), "slug": slug})
    saved = await owner_save(
        store,
        article.id,
        if_match=etag,
        base_version=current.version,
        meta=meta,
        markdown=article_body(extra=extra),
        now=now,
    )

    def gated(doc: Article) -> Article:
        return doc.model_copy(update={"gates": passing_report(doc, at=to_iso(now))})

    await update_doc(store, Article, article.id, gated)
    return saved.stored.doc


# --------------------------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------------------------


async def test_a_correction_ships_a_dated_note_and_turns_auto_publish_off(
    harness: PublishHarness,
) -> None:
    services = harness.services
    store = services.store
    article = await published(harness)
    await update_singleton(
        store,
        AutonomySettings,
        lambda autonomy: autonomy.model_copy(update={"auto_publish_enabled": True}),
        now=to_iso(MONDAY),
    )
    services.clock.set(MONDAY + timedelta(days=1))
    await start_correction(harness, article)

    missing = await post(harness, article.id, "approve", {})
    assert missing.status_code == 409
    assert missing.json()["code"] == "correction_note_required"

    publish_at = datetime(2026, 12, 3, 15, 0, tzinfo=UTC)  # Thursday
    response = await post(
        harness,
        article.id,
        "approve",
        {
            "correctionNote": "Clarified which\ntool to scope first.",
            "publishAt": to_iso(publish_at),
        },
    )
    assert response.status_code == 200, response.text
    approved = response.json()["article"]["item"]
    assert approved["corrections"][0]["note"] == "Clarified which tool to scope first."
    assert approved["calibration"]["outcome"] == "correction"
    settings, _ = await read_singleton(store, AutonomySettings, now=to_iso(MONDAY))
    assert not settings.auto_publish_enabled
    assert settings.calibration.events[-1].outcome == "correction"

    services.clock.set(publish_at + timedelta(minutes=30))
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    doc = await doc_of(store, SLUG)
    assert doc["corrections"] == [
        {"date": "2026-12-03", "note": "Clarified which tool to scope first."}
    ]
    assert doc["publishedAt"] == "2026-11-30T14:00:00Z"
    assert doc["updatedAt"] == "2026-12-03T15:30:00Z"
    assert validate_published_doc(doc, now=publish_at + timedelta(hours=1)) == []
    final, _ = await stored(store, article.id)
    assert final.status is S.PUBLISHED
    assert final.publish.last_published_version == final.version
    assert final.publish.published_at == to_iso(MONDAY)


async def test_a_pulled_back_correction_ships_one_note(harness: PublishHarness) -> None:
    services = harness.services
    store = services.store
    article = await published(harness)
    services.clock.set(MONDAY + timedelta(days=1))
    await start_correction(harness, article)
    first = await post(harness, article.id, "approve", {"correctionNote": "First try."})
    assert first.status_code == 200, first.text
    now = services.clock.now()
    await transition(store, article.id, S.IN_REVIEW, actor="owner", now=now)
    second = await post(
        harness,
        article.id,
        "approve",
        {"correctionNote": "The real note.", "publishAt": to_iso(now + timedelta(minutes=5))},
    )
    assert second.status_code == 200, second.text
    assert second.json()["jobId"].endswith("-r1")
    notes = [item["note"] for item in second.json()["article"]["item"]["corrections"]]
    assert notes == ["The real note."]
    services.clock.set(now + timedelta(minutes=5))
    job = await harness.run_job(second.json()["jobId"])
    assert job.status == "succeeded", job.error
    assert [item["note"] for item in (await doc_of(store, SLUG))["corrections"]] == [
        "The real note."
    ]


# --------------------------------------------------------------------------------------------
# Slug renames
# --------------------------------------------------------------------------------------------


async def test_renames_leave_redirect_stubs_one_hop_deep(harness: PublishHarness) -> None:
    services = harness.services
    store = services.store
    article = await published(harness)
    services.clock.set(MONDAY + timedelta(days=1))
    await start_correction(harness, article, slug="scoped-tool-calls")

    unconfirmed = await post(harness, article.id, "approve", {"correctionNote": "New address."})
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["code"] == "slug_change_unconfirmed"
    response = await post(
        harness,
        article.id,
        "publish-now",
        {"correctionNote": "New address.", "slug": "scoped-tool-calls"},
    )
    assert response.status_code == 200, response.text
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    old = await doc_of(store, SLUG)
    new = await doc_of(store, "scoped-tool-calls")
    assert old["status"] == "redirect"
    assert old["redirectTo"] == "scoped-tool-calls"
    assert set(old) == {"id", "slug", "schemaVersion", "status", "redirectTo", "updatedAt"}
    assert new["status"] == "published"
    assert new["publishedAt"] == "2026-11-30T14:00:00Z"  # the first publication
    assert harness.portfolio.pages[SLUG][0] == 200  # the old address still reaches it
    current, _ = await stored(store, article.id)
    assert current.publish.slug == "scoped-tool-calls"
    assert current.publish.previous_slugs == [SLUG]
    assert current.publish.url == "https://site.test/blog/scoped-tool-calls"

    services.clock.set(MONDAY + timedelta(days=2))
    await start_correction(harness, current, slug="tool-call-scopes", extra=" Again.")
    again = await post(
        harness,
        article.id,
        "publish-now",
        {"correctionNote": "Shorter address.", "slug": "tool-call-scopes"},
    )
    assert again.status_code == 200, again.text
    job = await harness.run_job(again.json()["jobId"])
    assert job.status == "succeeded", job.error
    for old_slug in (SLUG, "scoped-tool-calls"):
        stub = await doc_of(store, old_slug)
        assert stub["status"] == "redirect"
        assert stub["redirectTo"] == "tool-call-scopes"
        assert validate_published_doc(stub, now=services.clock.now()) == []
    final, _ = await stored(store, article.id)
    assert final.publish.previous_slugs == [SLUG, "scoped-tool-calls"]


# --------------------------------------------------------------------------------------------
# Unpublish
# --------------------------------------------------------------------------------------------


async def test_unpublish_tombstones_and_a_failed_one_can_be_retried(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    store = services.store
    article = await published(harness)
    harness.portfolio.conclusion = "failure"
    response = await post(harness, article.id, "unpublish")
    assert response.status_code == 202, response.text
    job_id = response.json()["jobId"]
    current, _ = await stored(store, article.id)
    assert current.status is S.UNPUBLISHING
    assert current.publish.job_id == job_id
    job = await harness.run_job(job_id)
    assert job.error is not None
    assert job.error.code == "build_failed"
    failed, _ = await stored(store, article.id)
    assert failed.status is S.PUBLISH_FAILED
    assert failed.publish.last_operation == "unpublish"
    assert not failed.publish.live
    assert (await doc_of(store, SLUG))["status"] == "unpublished"
    assert any(
        record.getMessage() == "alert_event"
        and getattr(record, "alert_kind", "") == "publish_failed"
        for record in caplog.records
    )

    refused = await post(harness, article.id, "publish-now")
    assert refused.status_code == 409  # the failed operation was an unpublish

    harness.portfolio.conclusion = "success"
    retry = await post(harness, article.id, "unpublish")
    assert retry.status_code == 202, retry.text
    job = await harness.run_job(retry.json()["jobId"])
    assert job.status == "succeeded", job.error
    final, _ = await stored(store, article.id)
    assert final.status is S.UNPUBLISHED
    assert not final.publish.live
    assert final.publish.url is None
    assert SLUG not in harness.portfolio.pages
    tombstone = await doc_of(store, SLUG)
    assert set(tombstone) == {"id", "slug", "schemaVersion", "status", "updatedAt"}
    assert harness.services.notifier.sent[-1][0].title.startswith("Unpublished: ")


async def test_unpublish_needs_a_published_article_and_the_portfolio(
    harness: PublishHarness,
) -> None:
    article = await seed_article(harness.services)
    response = await post(harness, article.id, "unpublish")
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"
    live = await published(harness)
    harness.set_settings(make_publish_settings(portfolio_token=None))
    response = await post(harness, live.id, "unpublish")
    assert response.status_code == 503
    assert response.json()["code"] == "portfolio_not_configured"
    assert (await stored(harness.services.store, live.id))[0].status is S.PUBLISHED


# --------------------------------------------------------------------------------------------
# Staging previews
# --------------------------------------------------------------------------------------------


async def test_a_staging_preview_writes_only_the_staging_container(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    _, etag = await stored(services.store, article.id)
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article.id}/staging-preview", headers=owner_headers()
        )
    assert response.status_code == 202, response.text
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    staged = (await services.store.read("published-blogs-staging", SLUG)).body
    assert staged["status"] == "published"
    assert validate_published_doc(staged, now=services.clock.now()) == []
    assert await services.store.find("published-blogs") == []
    assert [run.workflow for run in harness.portfolio.dispatches] == ["preview.yml"]
    dispatch = job.step("dispatch")
    assert dispatch is not None
    assert set(dispatch.remote_ids) == {"correlation", "dispatched"}
    assert (await stored(services.store, article.id))[1] == etag  # the article is untouched


async def test_a_preview_dispatch_whose_answer_was_lost_is_looked_up_not_sent_twice(
    harness: PublishHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub took the preview dispatch but its answer never came back (a gateway timeout, a
    crash): the resumed step finds the run by its correlation id instead of dispatching the
    preview workflow a second time."""
    services = harness.services
    article = await seed_article(services)
    portfolio = harness.portfolio
    original = portfolio.handle
    lost: list[str] = []

    async def answer_lost_once(request: httpx.Request) -> httpx.Response:
        response = await original(request)  # GitHub creates the run
        if request.url.path.endswith("/dispatches") and not lost:
            lost.append(request.url.path)
            return httpx.Response(504, json={"message": "gateway timeout"})
        return response

    monkeypatch.setattr(portfolio, "handle", answer_lost_once)
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article.id}/staging-preview", headers=owner_headers()
        )
    assert response.status_code == 202, response.text
    job = await harness.run_job(response.json()["jobId"])
    assert job.status == "succeeded", job.error
    assert lost
    assert [run.workflow for run in portfolio.dispatches] == ["preview.yml"]  # dispatched once
    dispatch = job.step("dispatch")
    assert dispatch is not None
    assert dispatch.runs == 2
    assert dispatch.remote_ids["run"] == str(portfolio.dispatches[0].id)
    assert portfolio.dispatches[0].correlation == dispatch.remote_ids["correlation"]


async def test_staging_needs_the_preview_workflow_and_a_free_slug(
    harness: PublishHarness,
) -> None:
    article = await seed_article(harness.services)
    harness.set_settings(make_publish_settings(preview_workflow=None))
    async with asgi_client(publish_app(harness)) as client:
        path = f"/api/articles/{article.id}/staging-preview"
        response = await client.post(path, headers=owner_headers())
        assert response.status_code == 503
        assert response.json()["code"] == "portfolio_not_configured"
        harness.set_settings(make_publish_settings())
        await harness.services.store.create(
            "published-blogs", {"id": SLUG, "slug": SLUG, "title": "Someone else's"}
        )
        response = await client.post(path, headers=owner_headers())
        assert response.status_code == 409
        assert response.json()["code"] == "slug_taken"


# --------------------------------------------------------------------------------------------
# Portfolio not configured: a clear waiting state, never a silent failure
# --------------------------------------------------------------------------------------------


async def test_without_the_token_the_job_waits_visibly_until_publish_now(
    harness: PublishHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    services = harness.services
    store = services.store
    harness.set_settings(make_publish_settings(portfolio_token=None))
    article = await seed_article(services)
    response = await post(harness, article.id, "approve", {})
    assert response.status_code == 200, response.text  # approve works
    job_id = response.json()["jobId"]
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "waiting_input"
    assert job.awaiting is not None
    assert job.awaiting.key == "portfolio"
    waiting, _ = await stored(store, article.id)
    assert waiting.status is S.APPROVED  # nothing left half done
    assert waiting.publish.last_error == "portfolio_not_configured"
    assert await store.find("published-blogs") == []
    assert any(
        record.getMessage() == "alert_event"
        and getattr(record, "alert_kind", "") == "needs_attention"
        for record in caplog.records
    )
    payload, _ = services.notifier.sent[-1]
    assert payload.url == "/settings/integrations"
    async with asgi_client(publish_app(harness)) as client:
        status = await client.get(
            f"/api/articles/{article.id}/publish-status", headers=owner_headers()
        )
    assert status.json()["job"]["awaiting"] == "portfolio"

    harness.set_settings(make_publish_settings())
    response = await post(harness, article.id, "publish-now")
    assert response.status_code == 200, response.text
    new_job = response.json()["jobId"]
    assert new_job == f"{job_id}-r1"
    assert (await harness.job(job_id)).status == "cancelled"
    job = await harness.run_job(new_job)
    assert job.status == "succeeded", job.error
    assert (await stored(store, article.id))[0].status is S.PUBLISHED
