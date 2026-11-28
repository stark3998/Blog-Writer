"""Schedule routes (contract I-2): list, confirm a missed slot, pause and resume.

Plan: "Reliability model" > Scheduler tick (DST-safe local times with a maximum lateness; past it
an item waits for the owner) and section 8 (Settings > Slots shows the schedule items). The
scheduler and the store are real; time is a fake clock.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from pydantic import JsonValue

from blogwriter.api.schedules import get_router
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs import (
    Broker,
    JobRegistry,
    JobRunner,
    JobsService,
    Scheduler,
    Step,
    StepContext,
    daily,
    every,
    get_job,
    weekly,
)
from blogwriter.jobs.bootguard import BootGuard
from blogwriter.jobs.clock import parse_iso
from blogwriter.jobs.scheduler import scheduled_job_id
from blogwriter.jobs.testing import fast_settings

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}


def jobs_service(services: FakeServices) -> JobsService:
    async def crawl(ctx: StepContext) -> JsonValue:
        return None

    registry = JobRegistry()
    registry.register("signals.crawl", [Step("crawl", crawl)])
    registry.register("backups.dump", [Step("dump", crawl)])
    settings = fast_settings()
    owner = "test-replica:boot"
    broker = Broker(queue_size=64, max_subscribers=4)
    runner = JobRunner(
        services.store, registry, settings=settings, owner=owner, clock=services.clock
    )
    scheduler = Scheduler(
        services.store,
        settings=settings,
        owner=owner,
        clock=services.clock,
        registry=registry,
        broker=broker,
    )
    guard = BootGuard(services.store, max_boots=5, window=timedelta(hours=1), clock=services.clock)
    return JobsService(
        store=services.store,
        registry=registry,
        settings=settings,
        clock=services.clock,
        owner=owner,
        broker=broker,
        runner=runner,
        scheduler=scheduler,
        boot_guard=guard,
    )


@asynccontextmanager
async def client(services: FakeServices) -> AsyncGenerator[tuple[httpx.AsyncClient, JobsService]]:
    app = make_app(services, [get_router()], settings=SETTINGS)
    jobs = jobs_service(services)
    app.state.jobs = jobs
    async with asgi_client(app) as http:
        yield http, jobs


async def test_items_are_listed_soonest_first_with_their_rules() -> None:
    services = make_services()
    async with client(services) as (http, jobs):
        await jobs.scheduler.put(
            "sched-backups-dump",
            name="Nightly backup",
            job_type="backups.dump",
            timezone="America/New_York",
            rule=daily("03:30"),
        )
        await jobs.scheduler.put(
            "sched-signals-crawl",
            name="Crawl the feeds",
            job_type="signals.crawl",
            timezone="America/New_York",
            rule=every(6 * 3600),
            max_lateness=timedelta(hours=6),
        )
        await jobs.scheduler.put(
            "sched-signals-scout",
            name="Scouting",
            job_type="signals.crawl",
            timezone="America/New_York",
            rule=weekly(["sun"], "15:00"),
        )
        response = await http.get("/api/schedules", headers=READ)
    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == [
        "sched-signals-crawl",  # every 6 h from 12:00 UTC: 18:00 UTC today
        "sched-backups-dump",  # 03:30 in New York: 08:30 UTC tomorrow
        "sched-signals-scout",  # Sunday 15:00 in New York
    ]
    by_id = {item["id"]: item for item in items}
    assert by_id["sched-backups-dump"]["rule"] == {
        "kind": "daily",
        "at": "03:30",
        "days": None,
        "seconds": None,
        "anchor": None,
    }
    assert by_id["sched-signals-crawl"]["rule"]["kind"] == "interval"
    assert by_id["sched-signals-crawl"]["rule"]["seconds"] == 21_600
    assert by_id["sched-signals-scout"]["rule"]["days"] == ["sun"]
    first = by_id["sched-signals-crawl"]
    assert set(first) == {
        "id",
        "name",
        "jobType",
        "rule",
        "timezone",
        "status",
        "nextRunAt",
        "pendingRunAt",
        "lastRunAt",
        "skippedRuns",
    }
    assert (first["status"], first["skippedRuns"]) == ("active", 0)
    nexts = [item["nextRunAt"] for item in items]
    assert nexts == sorted(nexts)


async def test_pause_is_idempotent_and_resume_restarts_from_the_next_slot() -> None:
    services = make_services()
    async with client(services) as (http, jobs):
        await jobs.scheduler.put(
            "sched-signals-crawl",
            name="Crawl",
            job_type="signals.crawl",
            timezone="UTC",
            rule=every(3600),
        )
        paused = await http.post("/api/schedules/sched-signals-crawl/pause", headers=WRITE)
        again = await http.post("/api/schedules/sched-signals-crawl/pause", headers=WRITE)
        services.clock.advance(5 * 3600)
        resumed = await http.post("/api/schedules/sched-signals-crawl/resume", headers=WRITE)
        not_paused = await http.post("/api/schedules/sched-signals-crawl/resume", headers=WRITE)
    assert paused.json()["status"] == "paused"
    assert again.json()["status"] == "paused"
    assert resumed.json()["status"] == "active"
    assert resumed.json()["nextRunAt"] > "2026-11-26T17:00:00.000000Z"  # no catch-up
    assert not_paused.status_code == 409
    assert not_paused.json()["code"] == "schedule_state"
    assert not_paused.json()["reason"] == "not_paused"


async def test_a_missed_slot_is_confirmed_by_running_it_or_skipping_it() -> None:
    services = make_services()
    async with client(services) as (http, jobs):
        for item_id in ("sched-a-crawl", "sched-b-crawl"):
            await jobs.scheduler.put(
                item_id,
                name="Crawl",
                job_type="signals.crawl",
                timezone="UTC",
                rule=daily("12:30"),
                max_lateness=timedelta(minutes=30),
            )
        services.clock.advance(3 * 3600)  # downtime: the 12:30 slot is 2.5 h late at 15:00
        report = await jobs.scheduler.tick()
        assert sorted(report.needs_confirmation) == ["sched-a-crawl", "sched-b-crawl"]
        listed = await http.get("/api/schedules", headers=READ)
        run = await http.post(
            "/api/schedules/sched-a-crawl/confirm", json={"run": True}, headers=WRITE
        )
        skip = await http.post(
            "/api/schedules/sched-b-crawl/confirm", json={"run": False}, headers=WRITE
        )
        twice = await http.post(
            "/api/schedules/sched-a-crawl/confirm", json={"run": True}, headers=WRITE
        )
    statuses = {item["id"]: item["status"] for item in listed.json()}
    assert statuses == {
        "sched-a-crawl": "needs_confirmation",
        "sched-b-crawl": "needs_confirmation",
    }
    assert listed.json()[0]["pendingRunAt"] is not None
    ran = run.json()
    assert (ran["status"], ran["pendingRunAt"]) == ("active", None)
    assert ran["lastRunAt"] is not None
    job = await get_job(
        services.store, scheduled_job_id("sched-a-crawl", parse_iso(ran["lastRunAt"]))
    )
    assert job.type == "signals.crawl"
    skipped = skip.json()
    assert (skipped["status"], skipped["lastRunAt"]) == ("active", None)
    assert skipped["skippedRuns"] >= 1
    assert twice.status_code == 409
    assert twice.json()["reason"] == "not_pending"


async def test_unknown_items_and_bad_bodies() -> None:
    services = make_services()
    async with client(services) as (http, _jobs):
        missing = await http.post("/api/schedules/sched-none/pause", headers=WRITE)
        impossible = await http.post("/api/schedules/a%23b/pause", headers=WRITE)
        no_body = await http.post("/api/schedules/sched-none/confirm", headers=WRITE)
        extra = await http.post(
            "/api/schedules/sched-none/confirm", json={"run": True, "force": 1}, headers=WRITE
        )
        csrf = await http.post("/api/schedules/sched-none/pause", headers=READ)
    assert missing.status_code == 404
    assert impossible.status_code == 404
    assert no_body.status_code == 422
    assert extra.status_code == 422
    assert csrf.status_code == 403


async def test_without_the_jobs_service_the_routes_answer_503() -> None:
    services = make_services()
    app = make_app(services, [get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        response = await http.get("/api/schedules", headers=READ)
    assert response.status_code == 503
    assert response.json()["code"] == "jobs_unavailable"
