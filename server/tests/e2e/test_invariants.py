"""Invariants of the whole server (contract I-6, the plan's Verification section):

- the e2e system is the production composition: every package router, every job type;
- no ``ScheduleEntry`` of any package starts a job type outside
  ``domain.jobtypes.SCHEDULED_JOB_TYPES`` or one that generates content, and the seeder refuses
  one that tries;
- the LinkedIn publisher is unreachable from jobs and the scheduler: no registered step names
  it, the whole job system and every schedule run without even importing it, and a week of every
  schedule item and delayed job around a staged post never sends a ``POST /rest/posts``;
- nothing on a schedule generates content (no draft, post or publish job, no new article or
  post), with autonomy on (models answer or refuse) and paused (no model call at all).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import timedelta
from typing import Final

from fastapi.routing import APIRoute

from blogwriter.ai.types import Task
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.jobtypes import (
    ALL_JOB_TYPES,
    PIPELINE_ARTICLE,
    POSTS_STAGE,
    SCHEDULED_JOB_TYPES,
    generates_content,
)
from blogwriter.domain.ports import ScheduleEntry
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.jobs import Job, daily
from blogwriter.linkedin.transport.testing import POSTS_URL
from blogwriter.main import SCHEDULE_PROVIDERS, build_registry, package_routers
from blogwriter.services.schedules import ScheduleSeeder

from .conftest import (
    SLOT_UTC,
    STAGED_AT,
    System,
    approve_for_slot,
    connect_linkedin,
    derive_posts,
    draft_flagship,
    prepare_owner,
    review_and_publish,
    seed_schedules,
    start_system,
)

PUBLISHER: Final = "blogwriter.linkedin.transport.publisher"
HTTP_METHODS: Final = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
PUBLISHER_NAMES: Final = frozenset({"publish_post", "OwnerClick", "owner_click"})
WEEK_TASKS: Final = frozenset({Task.QUICK_SEARCH, Task.TRIAGE, Task.PLAN, Task.SYNTHESIS})
"""What a week of schedules may ask a model: scouting, triage, the plan, digests, insights."""


def cadences() -> list[CadenceSettings]:
    """The default cadence and owner edits that move every schedule item."""
    default = CadenceSettings.default(SAMPLE_NOW)
    edited = default.model_copy(
        update={"timezone": "Europe/Berlin", "crawl_interval_hours": 2, "plan_lead_minutes": 0}
    )
    return [default, edited]


# --------------------------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------------------------


async def test_the_system_under_test_is_the_production_composition() -> None:
    async with start_system() as system:
        assert sorted(system.registry.names()) == sorted(ALL_JOB_TYPES)
        spec = system.app.openapi()["paths"]
        mounted = {
            (method.upper(), path)
            for path, operations in spec.items()
            for method in operations
            if method.upper() in HTTP_METHODS
        }
        wanted = {
            (method, re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path))  # {name:path} -> {name}
            for router in package_routers()
            for route in router.routes
            if isinstance(route, APIRoute) and route.include_in_schema
            for method in route.methods or ()
            if method in HTTP_METHODS
        }
        assert len(wanted) > 150
        assert wanted <= mounted


# --------------------------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------------------------


def test_no_schedule_entry_starts_a_content_or_unscheduled_job_type() -> None:
    for cadence in cadences():
        entries = [entry for provider in SCHEDULE_PROVIDERS for entry in provider(cadence)]
        assert len({entry.id for entry in entries}) == len(entries)
        assert {entry.job_type for entry in entries} == SCHEDULED_JOB_TYPES
        assert [entry.id for entry in entries if generates_content(entry.job_type)] == []
    assert not any(generates_content(job_type) for job_type in SCHEDULED_JOB_TYPES)
    assert all(generates_content(t) for t in (PIPELINE_ARTICLE, POSTS_STAGE, "publish.article"))


async def test_the_seeder_refuses_a_schedule_for_a_content_job() -> None:
    def rogue(cadence: CadenceSettings) -> list[ScheduleEntry]:
        return [
            ScheduleEntry(
                id="sched-rogue-draft",
                name="Draft every morning",
                job_type=PIPELINE_ARTICLE,
                rule=daily("06:00"),
                timezone=cadence.timezone,
            )
        ]

    async with start_system() as system:
        seeded = await seed_schedules(system)
        assert sorted(seeded) == sorted(
            entry.id for provider in SCHEDULE_PROVIDERS for entry in provider(cadences()[0])
        )
        seeder = ScheduleSeeder(
            system.store, system.jobs.scheduler, [*SCHEDULE_PROVIDERS, rogue], clock=system.clock
        )
        report = await seeder.run()
        assert report.refused == ["sched-rogue-draft"]
        items = await system.jobs.scheduler.list_items()
        assert {item.job_type for item in items} == SCHEDULED_JOB_TYPES
        assert "sched-rogue-draft" not in {item.id for item in items}


# --------------------------------------------------------------------------------------------
# The publisher
# --------------------------------------------------------------------------------------------


def test_no_registered_step_names_the_publisher() -> None:
    registry = build_registry()
    for name in registry.names():
        job_type = registry.get(name)
        assert job_type is not None
        for step in job_type.steps:
            run = step.run
            assert run.__module__ != PUBLISHER, (name, step.name)
            code = getattr(run, "__code__", None)
            names = set(code.co_names) if code is not None else set[str]()
            assert not names & PUBLISHER_NAMES, (name, step.name)


def test_the_job_system_and_the_schedules_never_load_the_publisher() -> None:
    """Building every job type and every schedule entry (what the runner and the scheduler
    execute) does not even import the publisher module: only the publish route does."""
    probe = (
        "import sys\n"
        "from blogwriter.domain.settings import CadenceSettings\n"
        "from blogwriter.main import SCHEDULE_PROVIDERS, build_registry\n"
        "registry = build_registry()\n"
        "cadence = CadenceSettings.default('2026-11-26T12:00:00.000000Z')\n"
        "entries = [e for p in SCHEDULE_PROVIDERS for e in p(cadence)]\n"
        "assert len(registry.names()) > 30 and len(entries) > 10\n"
        f"print({PUBLISHER!r} in sys.modules)\n"
    )
    result = subprocess.run(  # noqa: S603 - our own interpreter and a constant probe
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=300, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["False"]


async def staged_post(system: System) -> str:
    """A one-click post staged for its Tuesday slot (the owner never taps it)."""
    owner = await prepare_owner(system)
    await connect_linkedin(system)
    flagship = await draft_flagship(system, owner)
    published = await review_and_publish(system, flagship)
    card_id, _ = await derive_posts(system, published)
    await approve_for_slot(system, card_id)
    await system.run_until(STAGED_AT)
    assert (await read_doc(system.store, Post, card_id)).doc.status is PostStatus.STAGED
    return card_id


async def scheduled_week(system: System, card_id: str) -> list[Job]:
    """Every schedule item and every delayed job for a week; the jobs created meanwhile."""
    since = to_iso(system.clock.now())
    articles = len(await find_docs(system.store, Article))
    posts = len(await find_docs(system.store, Post))
    await seed_schedules(system)
    await system.run_until(SLOT_UTC + timedelta(days=7))
    assert system.linkedin.calls("POST", POSTS_URL) == []  # the publisher was never reached
    assert system.linkedin.creates == []
    post = (await read_doc(system.store, Post, card_id)).doc
    assert post.status is PostStatus.MISSED  # nobody tapped; the slot check offered another
    assert len(await find_docs(system.store, Article)) == articles  # nothing drafted
    assert len(await find_docs(system.store, Post)) == posts  # nothing generated
    created = [job for job in await system.all_jobs() if job.created_at >= since]
    assert [job.type for job in created if generates_content(job.type)] == []
    from_schedules = {job.type for job in created if job.schedule_id is not None}
    assert from_schedules == SCHEDULED_JOB_TYPES  # every item fired at least once
    assert all(job.status == "succeeded" for job in created), [
        (job.type, job.status) for job in created
    ]
    return created


async def test_a_week_of_schedules_never_publishes_or_generates_content() -> None:
    async with start_system(refuse_unscripted=True) as system:
        card_id = await staged_post(system)
        before = len(system.model.calls)
        await scheduled_week(system, card_id)
        asked = {call.task for call in system.model.calls[before:]}
        assert asked <= WEEK_TASKS


async def test_a_paused_week_asks_no_model_and_publishes_nothing() -> None:
    async with start_system() as system:
        card_id = await staged_post(system)
        await system.ok("POST", "/api/kb/autonomy/pause")
        before = len(system.model.calls)
        await scheduled_week(system, card_id)
        assert system.model.calls[before:] == []
