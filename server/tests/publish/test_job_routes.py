"""The publish routes through the real app (auth, CSRF, problems): approve and publish-now with
every refusal, job ids and re-runs, and the read-only publish status."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from blogwriter.articles import transition
from blogwriter.core.testing import OWNER_OID, asgi_client, principal_headers
from blogwriter.db import Store
from blogwriter.domain.articles import Article, Clearance, GateVerdict, PublishState
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, read_singleton, update_doc
from blogwriter.domain.jobtypes import publish_job_id
from blogwriter.domain.settings import AutonomySettings
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.testing import (
    PublishHarness,
    make_harness,
    owner_headers,
    publish_app,
    seed_article,
)

S = ArticleStatus


@pytest.fixture
def harness() -> PublishHarness:
    return make_harness()


@pytest.fixture
async def client(harness: PublishHarness) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(publish_app(harness)) as http:
        yield http


async def etag_of(store: Store, article_id: str) -> str:
    return (await read_doc(store, Article, article_id)).etag


async def approve(
    client: httpx.AsyncClient, store: Store, article_id: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    etag = await etag_of(store, article_id)
    return await client.post(
        f"/api/articles/{article_id}/approve", json=body or {}, headers=owner_headers(etag)
    )


async def change(store: Store, article_id: str, **fields: object) -> None:
    def mutate(doc: Article) -> Article:
        return doc.model_copy(update=fields)

    await update_doc(store, Article, article_id, mutate)


async def test_approve_needs_if_match_and_a_current_etag(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    article = await seed_article(harness.services)
    path = f"/api/articles/{article.id}/approve"
    missing = await client.post(path, json={}, headers=owner_headers())
    assert missing.status_code == 428
    stale = await client.post(path, json={}, headers=owner_headers('"stale"'))
    assert stale.status_code == 412
    assert stale.json()["current"]["status"] == "in_review"
    read_only = await client.post(path, json={}, headers=principal_headers(OWNER_OID))
    assert read_only.status_code == 403  # CSRF: no same-origin write headers


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("no_report", "gates_blocking"),
        ("failing_gate", "gates_blocking"),
        ("stale_report", "report_stale"),
        ("meta", "meta_incomplete"),
        ("clearance", "clearance_required"),
        ("eval", "eval_article"),
        ("taken", "slug_taken"),
        ("not_in_review", "invalid_transition"),
        ("slug_confirmation", "slug_change_unconfirmed"),
        ("note_for_new_article", "correction_note_not_allowed"),
    ],
)
async def test_approve_refusals(
    harness: PublishHarness, client: httpx.AsyncClient, setup: str, code: str
) -> None:
    store = harness.services.store
    article = await seed_article(harness.services)
    body: dict[str, Any] = {}
    assert article.gates is not None
    match setup:
        case "no_report":
            await change(store, article.id, gates=None)
        case "failing_gate":
            results = [
                result.model_copy(update={"verdict": GateVerdict.FAIL}) if index == 3 else result
                for index, result in enumerate(article.gates.results)
            ]
            await change(
                store, article.id, gates=article.gates.model_copy(update={"results": results})
            )
        case "stale_report":
            await change(
                store,
                article.id,
                gates=article.gates.model_copy(update={"content_sha256": "0" * 64}),
            )
        case "meta":
            await change(store, article.id, meta=article.meta.model_copy(update={"excerpt": None}))
        case "clearance":
            await change(store, article.id, clearance=Clearance(required=True))
        case "eval":
            await change(store, article.id, purpose="eval")
        case "taken":
            await store.create(
                "published-blogs", {"id": "scope-every-tool-call", "slug": "scope-every-tool-call"}
            )
        case "not_in_review":
            await change(store, article.id, status=S.DRAFTING)
        case "slug_confirmation":
            body = {"slug": "another-slug"}
        case "note_for_new_article":
            body = {"correctionNote": "Nothing to correct yet."}
        case _:
            raise AssertionError(setup)
    before = await etag_of(store, article.id)
    response = await approve(client, store, article.id, body)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code
    if code == "gates_blocking":
        blocking = response.json()["blocking"]
        assert blocking == ["originality"] if setup == "failing_gate" else len(blocking) == 7
    if code == "meta_incomplete":
        assert response.json()["missing"] == ["excerpt"]
    assert await etag_of(store, article.id) == before  # nothing written
    assert harness.services.jobs.enqueued == []


async def test_publish_at_must_be_between_now_and_a_year(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    store = harness.services.store
    article = await seed_article(harness.services)
    now = harness.services.clock.now()
    for moment in (now - timedelta(hours=1), now + timedelta(days=400)):
        response = await approve(client, store, article.id, {"publishAt": moment.isoformat()})
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_publish_at"
    soon = now + timedelta(hours=3)
    response = await approve(client, store, article.id, {"publishAt": soon.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["article"]["item"]["publish"]["publishAt"] == to_iso(soon)
    job = await harness.job(response.json()["jobId"])
    assert job.run_after == to_iso(soon)


async def test_approve_records_the_job_and_reruns_after_a_pull_back(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    first = await approve(client, store, article.id)
    assert first.status_code == 200, first.text
    base = publish_job_id(article.id, 1)
    assert first.json()["jobId"] == base
    item = first.json()["article"]["item"]
    assert item["publish"]["auto"] is False
    assert item["publish"]["lastError"] is None

    # Pull back through the articles route: it cancels the scheduled job.
    etag = await etag_of(store, article.id)
    back = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "in_review"},
        headers=owner_headers(etag),
    )
    assert back.status_code == 200, back.text
    assert (await harness.job(base)).status == "cancelled"
    again = await approve(client, store, article.id)
    assert again.status_code == 200, again.text
    assert again.json()["jobId"] == f"{base}-r1"


async def test_publish_now_replaces_the_scheduled_run(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    scheduled = await approve(client, store, article.id)
    base = scheduled.json()["jobId"]
    etag = await etag_of(store, article.id)
    now_response = await client.post(
        f"/api/articles/{article.id}/publish-now", headers=owner_headers(etag)
    )
    assert now_response.status_code == 200, now_response.text
    assert now_response.json()["jobId"] == f"{base}-r1"
    assert (await harness.job(base)).status == "cancelled"
    replaced = await harness.job(f"{base}-r1")
    assert replaced.run_after == to_iso(services.clock.now())
    item = now_response.json()["article"]["item"]
    assert item["status"] == "approved"
    assert item["publish"]["publishAt"] == to_iso(services.clock.now())
    assert item["publish"]["jobId"] == f"{base}-r1"


async def test_publish_now_from_review_approves_and_counts_once(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    etag = await etag_of(store, article.id)
    response = await client.post(
        f"/api/articles/{article.id}/publish-now", headers=owner_headers(etag)
    )
    assert response.status_code == 200, response.text
    item = response.json()["article"]["item"]
    assert item["status"] == "approved"
    assert item["calibration"]["outcome"] == "clean"
    assert item["calibration"]["approvedSha256"] == article.compute_sha256()
    autonomy, _ = await read_singleton(store, AutonomySettings, now=to_iso(services.clock.now()))
    assert autonomy.calibration.clean_streak == 1

    # Pulled back and approved again with the same content: it does not count twice.
    etag = await etag_of(store, article.id)
    back = await client.post(
        f"/api/articles/{article.id}/transition",
        json={"to": "in_review"},
        headers=owner_headers(etag),
    )
    assert back.status_code == 200, back.text
    again = await approve(client, store, article.id)
    assert again.status_code == 200, again.text
    autonomy, _ = await read_singleton(store, AutonomySettings, now=to_iso(services.clock.now()))
    assert autonomy.calibration.clean_streak == 1
    assert len(autonomy.calibration.events) == 1


async def test_enqueue_failure_reverts_the_approval(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    services.jobs.registered = frozenset({"something.else"})  # publish.article not registered
    article = await seed_article(services)
    response = await approve(client, store, article.id)
    assert response.status_code == 503
    assert response.json()["code"] == "jobs_unavailable"
    after = (await read_doc(store, Article, article.id)).doc
    assert after.status is S.IN_REVIEW
    assert after.last_error == "enqueue_failed"
    assert after.publish.job_id is None


async def test_publish_status_reads_without_writing(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(services)
    response = await approve(client, store, article.id)
    job_id = response.json()["jobId"]
    before = {doc.id: doc.etag for doc in await store.find("articles")}
    jobs_before = {doc.id: doc.etag for doc in await store.find("jobs")}
    status = await client.get(
        f"/api/articles/{article.id}/publish-status", headers=principal_headers(OWNER_OID)
    )
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "approved"
    assert body["publish"]["jobId"] == job_id
    assert body["job"]["id"] == job_id
    assert body["job"]["status"] == "queued"
    assert [step["name"] for step in body["job"]["steps"]] == []
    assert {doc.id: doc.etag for doc in await store.find("articles")} == before
    assert {doc.id: doc.etag for doc in await store.find("jobs")} == jobs_before
    missing = await client.get(
        "/api/articles/art-missing/publish-status", headers=principal_headers(OWNER_OID)
    )
    assert missing.status_code == 404


async def test_publish_now_on_a_live_article_needs_the_correction_route(
    harness: PublishHarness, client: httpx.AsyncClient
) -> None:
    services = harness.services
    store = services.store
    article = await seed_article(
        services,
        status=S.PUBLISHED,
        publish=PublishState(
            slug="scope-every-tool-call",
            live=True,
            published_at=to_iso(datetime(2026, 11, 20, tzinfo=UTC)),
        ),
    )
    etag = await etag_of(store, article.id)
    response = await client.post(
        f"/api/articles/{article.id}/publish-now", headers=owner_headers(etag)
    )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"
    await transition(store, article.id, S.IN_REVIEW, actor="owner", now=services.clock.now())
    etag = await etag_of(store, article.id)
    response = await client.post(
        f"/api/articles/{article.id}/publish-now", headers=owner_headers(etag)
    )
    assert response.status_code == 409
    assert response.json()["code"] == "correction_note_required"
