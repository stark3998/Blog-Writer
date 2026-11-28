"""The ``engagement.purge`` job, its schedule entry and the package wiring."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute

from blogwriter.db import Filter
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.jobtypes import ENGAGEMENT_PURGE, SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.planner import Reminder
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import FakeServices, sample_document
from blogwriter.engagement import (
    PURGE_SCHEDULE_ID,
    get_router,
    purge_expired,
    register_jobs,
    schedule_entries,
)
from blogwriter.engagement.helpers import PURGE_BATCH
from blogwriter.jobs import Job, JobRegistry
from blogwriter.jobs.scheduler import every

type RunPurge = Callable[[], Awaitable[Job]]


def item_at(services: FakeServices, *, age: timedelta, lifetime: timedelta) -> EngagementItem:
    now = services.clock.now()
    created = to_iso(now - age)
    return sample_document(
        EngagementItem,
        created_at=created,
        updated_at=created,
        status_changed_at=created,
        expires_at=to_iso(now - age + lifetime),
    )


async def kinds_left(services: FakeServices) -> list[str]:
    return sorted(str(raw.body["kind"]) for raw in await services.store.find("engagement", []))


async def test_the_purge_job_deletes_expired_items_and_nothing_else(
    services: FakeServices, run_purge: RunPurge
) -> None:
    hours = timedelta(hours=1)
    live = item_at(services, age=hours, lifetime=48 * hours)
    expired = item_at(services, age=48 * hours, lifetime=48 * hours)
    overdue = item_at(services, age=49 * hours, lifetime=480 * hours)
    for document in (live, expired, overdue):
        await create_doc(services.store, document)
    await services.store.create(
        "engagement",
        {
            "id": "eng-0000000000000000000000000001",
            "kind": "engagement_item",
            "pastedText": "A malformed item without an expiry.",
        },
    )
    reminder = sample_document(Reminder, expires_at=to_iso(services.clock.now() - hours))
    for document in (
        sample_document(EngagementRecord),
        sample_document(WatchEntry),
        reminder,
    ):
        await create_doc(services.store, document)
    job = await run_purge()
    assert job.status == "succeeded", job.error
    assert job.result == {"deleted": 3}
    assert await kinds_left(services) == [
        "engagement_item",
        "engagement_record",
        "reminder",
        "watch_entry",
    ]
    [left] = await services.store.find("engagement", [Filter("kind", "==", "engagement_item")])
    assert left.id == live.id
    again = await run_purge()
    assert again.result == {"deleted": 0}


async def test_the_purge_works_through_more_than_one_batch(services: FakeServices) -> None:
    hours = timedelta(hours=1)
    for _ in range(PURGE_BATCH + 20):
        await create_doc(services.store, item_at(services, age=50 * hours, lifetime=48 * hours))
    deleted = await purge_expired(services.store, now=services.clock.now())
    assert deleted == PURGE_BATCH + 20
    assert await services.store.find("engagement", []) == []


def test_the_purge_schedule_entry() -> None:
    [entry] = schedule_entries(sample_document(CadenceSettings))
    assert entry.id == PURGE_SCHEDULE_ID == "sched-engagement-purge"
    assert entry.job_type == ENGAGEMENT_PURGE
    assert entry.job_type in SCHEDULED_JOB_TYPES
    assert not generates_content(entry.job_type)
    assert entry.rule == every(6 * 3600)
    assert entry.timezone == "America/New_York"
    assert entry.max_lateness == timedelta(hours=6)
    assert entry.job_input == {}


def test_the_entry_follows_the_cadence_and_is_deterministic() -> None:
    cadence = sample_document(CadenceSettings, timezone="Europe/London")
    entries = schedule_entries(cadence)
    assert entries == schedule_entries(cadence)
    assert entries[0].timezone == "Europe/London"


def test_the_job_registers_once_with_its_step() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    job_type = registry.get(ENGAGEMENT_PURGE)
    assert job_type is not None
    assert job_type.step_names == ("purge",)
    with pytest.raises(ValueError, match="already registered"):
        register_jobs(registry)


def test_the_router_serves_the_contract_routes() -> None:
    routes = {
        (method, route.path)
        for route in get_router().routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    assert routes == {
        ("GET", "/api/engagement/items"),
        ("POST", "/api/engagement/items"),
        ("POST", "/api/engagement/items/{item_id}/angles"),
        ("POST", "/api/engagement/items/{item_id}/lint"),
        ("POST", "/api/engagement/items/{item_id}/finalize"),
        ("POST", "/api/engagement/items/{item_id}/dismiss"),
        ("POST", "/api/engagement/extract"),
        ("GET", "/api/engagement/records"),
        ("GET", "/api/engagement/watchlist"),
        ("POST", "/api/engagement/watchlist"),
        ("PUT", "/api/engagement/watchlist/{entry_id}"),
        ("DELETE", "/api/engagement/watchlist/{entry_id}"),
        ("POST", "/api/engagement/watchlist/{entry_id}/engaged"),
        ("GET", "/api/engagement/profile-checklist"),
        ("PUT", "/api/engagement/profile-checklist"),
    }
