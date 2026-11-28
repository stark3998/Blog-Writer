"""Job routes (contract I-2): ``GET /api/jobs``, ``GET /api/jobs/{id}``, cancel and retry.

Plan: "Reliability model" (the owner retries a quarantined job from System; a cancelled or
finished job never strands its document) and the web map's System page. The views are the SSE
snapshot shape: never a job's input, step outputs or result.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from pydantic import JsonValue

from blogwriter.api.jobs import get_router
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs import Job, enqueue, request_cancel
from blogwriter.jobs.models import JobError

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
SECRET_INPUT: dict[str, JsonValue] = {
    "articleId": "art-1",
    "instruction": "mention the Contoso migration",
}


@asynccontextmanager
async def client(services: FakeServices) -> AsyncGenerator[httpx.AsyncClient]:
    async with asgi_client(make_app(services, [get_router()], settings=SETTINGS)) as http:
        yield http


async def put_job(services: FakeServices, job: Job) -> None:
    current = await services.store.read("jobs", job.id)
    await services.store.replace("jobs", job.to_body(), if_match=current.etag)


async def failed_job(services: FakeServices, job_type: str = "pipeline.revise") -> Job:
    job = await enqueue(services.store, job_type, SECRET_INPUT, clock=services.clock)
    stamp = "2026-11-26T12:05:00.000000Z"
    job.status = "failed"
    job.result = {"draft": "secret result text"}
    job.error = JobError(code="ai_unavailable", at=stamp, step="revise", detail="Not configured.")
    job.finished_at = stamp
    await put_job(services, job)
    return job


async def test_the_list_is_newest_first_and_carries_no_input_or_output() -> None:
    services = make_services()
    first = await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
    services.clock.advance(10)
    second = await failed_job(services)
    async with client(services) as http:
        response = await http.get("/api/jobs", headers=READ)
    assert response.status_code == 200
    page = response.json()
    assert [entry["item"]["jobId"] for entry in page["items"]] == [second.id, first.id]
    assert page["nextCursor"] is None
    entry = page["items"][0]
    assert entry["etag"].startswith('"')
    item = entry["item"]
    assert item["status"] == "failed"
    assert item["error"]["code"] == "ai_unavailable"
    assert item["events"] is None
    text = json.dumps(page)
    assert "Contoso" not in text
    assert "secret result" not in text
    assert "input" not in item
    assert "result" not in item


async def test_filters_and_pages() -> None:
    services = make_services()
    for _ in range(3):
        await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
        services.clock.advance(1)
    await failed_job(services)
    async with client(services) as http:
        failed = await http.get("/api/jobs", params={"status": "failed"}, headers=READ)
        crawls = await http.get("/api/jobs", params={"type": "signals.crawl"}, headers=READ)
        first = await http.get("/api/jobs", params={"limit": 2}, headers=READ)
        cursor = first.json()["nextCursor"]
        rest = await http.get("/api/jobs", params={"limit": 2, "cursor": cursor}, headers=READ)
        bad_cursor = await http.get("/api/jobs", params={"cursor": "!!"}, headers=READ)
        bad_status = await http.get("/api/jobs", params={"status": "exploded"}, headers=READ)
        bad_type = await http.get("/api/jobs", params={"type": "Bad Type"}, headers=READ)
    assert [e["item"]["status"] for e in failed.json()["items"]] == ["failed"]
    assert {e["item"]["type"] for e in crawls.json()["items"]} == {"signals.crawl"}
    assert len(crawls.json()["items"]) == 3
    assert len(first.json()["items"]) == 2
    assert cursor is not None
    assert len(rest.json()["items"]) == 2
    seen = [e["item"]["jobId"] for e in [*first.json()["items"], *rest.json()["items"]]]
    assert len(set(seen)) == 4
    assert rest.json()["nextCursor"] is None
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["code"] == "invalid_cursor"
    assert bad_status.status_code == 422
    assert bad_type.status_code == 422


async def test_schedule_items_are_not_jobs() -> None:
    services = make_services()
    await services.store.create(
        "jobs", {"id": "sched-signals-crawl", "kind": "schedule", "updatedAt": "x"}
    )
    async with client(services) as http:
        listed = await http.get("/api/jobs", headers=READ)
        read = await http.get("/api/jobs/sched-signals-crawl", headers=READ)
    assert listed.json()["items"] == []
    assert read.status_code == 404
    assert read.json()["code"] == "not_found"


async def test_one_job_has_its_events_and_unknown_ids_are_404() -> None:
    services = make_services()
    job = await failed_job(services)
    async with client(services) as http:
        found = await http.get(f"/api/jobs/{job.id}", headers=READ)
        missing = await http.get("/api/jobs/job-missing", headers=READ)
        impossible = await http.get("/api/jobs/a%23b", headers=READ)
    assert found.status_code == 200
    body = found.json()
    assert body["jobId"] == job.id
    assert [event["type"] for event in body["events"]] == ["status"]
    assert body["steps"] == []
    assert missing.status_code == 404
    assert impossible.status_code == 404


async def test_cancel_settles_a_queued_job_and_leaves_a_finished_one_alone() -> None:
    services = make_services()
    queued = await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
    finished = await failed_job(services)
    async with client(services) as http:
        cancelled = await http.post(f"/api/jobs/{queued.id}/cancel", headers=WRITE)
        again = await http.post(f"/api/jobs/{queued.id}/cancel", headers=WRITE)
        untouched = await http.post(f"/api/jobs/{finished.id}/cancel", headers=WRITE)
        missing = await http.post("/api/jobs/job-missing/cancel", headers=WRITE)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert again.json()["status"] == "cancelled"
    assert untouched.json()["status"] == "failed"
    assert missing.status_code == 404


async def test_retry_requeues_a_failed_job_and_refuses_an_active_one() -> None:
    services = make_services()
    failed = await failed_job(services)
    queued = await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
    cancelled = await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
    await request_cancel(services.store, cancelled.id, clock=services.clock)
    async with client(services) as http:
        retried = await http.post(f"/api/jobs/{failed.id}/retry", headers=WRITE)
        refused = await http.post(f"/api/jobs/{queued.id}/retry", headers=WRITE)
        revived = await http.post(f"/api/jobs/{cancelled.id}/retry", headers=WRITE)
    assert retried.status_code == 200
    body = retried.json()
    assert (body["status"], body["attempt"], body["error"]) == ("queued", 0, None)
    assert body["events"][-1]["data"] == {"status": "queued", "reason": "retry"}
    assert refused.status_code == 409
    assert refused.json()["code"] == "job_state"
    assert refused.json()["reason"] == "not_retryable"
    assert revived.json()["status"] == "queued"


async def test_actions_need_csrf_and_every_route_needs_the_owner() -> None:
    services = make_services()
    job = await enqueue(services.store, "signals.crawl", {}, clock=services.clock)
    async with client(services) as http:
        no_csrf = await http.post(f"/api/jobs/{job.id}/cancel", headers=READ)
    async with client(services) as http:  # a fresh client: no __Host-bw cookie
        anonymous = await http.get("/api/jobs")
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_failed"
    assert anonymous.status_code == 401
    assert (await services.jobs.get(job.id)).status == "queued"
