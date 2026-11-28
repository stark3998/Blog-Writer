"""Schedule seeding (contract I-4, section 8): every package's entries become scheduler items.

Plan: "Weekly production calendar" (times local to the cadence's IANA zone; the seeder rebuilds
the items when the cadence changes) and section 1.7 (only ``SCHEDULED_JOB_TYPES`` may run from a
schedule; nothing that generates content). The scheduler and the store are real.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue

from blogwriter.db import MemoryStore, StoreUnavailable
from blogwriter.domain.docstore import save_singleton
from blogwriter.domain.jobtypes import (
    BACKUPS_DUMP,
    GATES_STANDARDS_REFRESH,
    POSTS_GENERATE,
    PUBLISH_ARTICLE,
    SIGNALS_CRAWL,
    SIGNALS_SCOUT,
)
from blogwriter.domain.ports import ScheduleEntry, ScheduleProvider
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import (
    DailyRule,
    IntervalRule,
    JobRegistry,
    Scheduler,
    Step,
    StepContext,
    WeeklyRule,
    daily,
    every,
    weekly,
)
from blogwriter.jobs.scheduler import DEFAULT_MAX_LATENESS, ScheduleItem
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings
from blogwriter.services.schedules import ScheduleSeeder

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
NOW = "2026-11-26T12:00:00.000000Z"


def registry(*types: str) -> JobRegistry:
    async def step(ctx: StepContext) -> JsonValue:
        return None

    jobs = JobRegistry()
    for job_type in types or (SIGNALS_CRAWL, SIGNALS_SCOUT, GATES_STANDARDS_REFRESH, BACKUPS_DUMP):
        jobs.register(job_type, [Step("run", step)])
    return jobs


def signals_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    return [
        ScheduleEntry(
            id="sched-signals-crawl",
            name="Crawl the feeds",
            job_type=SIGNALS_CRAWL,
            rule=every(cadence.crawl_interval_hours * 3600),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=timedelta(hours=6),
        ),
        ScheduleEntry(
            id="sched-signals-scout",
            name="Weekly scouting",
            job_type=SIGNALS_SCOUT,
            rule=weekly([cadence.scout.day], cadence.scout.time),
            timezone=cadence.timezone,
            job_input={},
            max_lateness=timedelta(hours=12),
        ),
    ]


def backups_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    return [
        ScheduleEntry(
            id="sched-backups-dump",
            name="Nightly backup",
            job_type=BACKUPS_DUMP,
            rule=daily(cadence.backup_time),
            timezone=cadence.timezone,
            max_lateness=timedelta(hours=12),
        )
    ]


def content_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """What no package may do: schedule content generation."""
    return [
        ScheduleEntry(
            id="sched-posts-generate",
            name="Write a post",
            job_type=POSTS_GENERATE,
            rule=daily("09:00"),
            timezone=cadence.timezone,
        ),
        ScheduleEntry(
            id="sched-publish-article",
            name="Publish",
            job_type=PUBLISH_ARTICLE,
            rule=daily("09:00"),
            timezone=cadence.timezone,
        ),
    ]


def broken_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    raise RuntimeError("provider bug")


class Harness:
    def __init__(self, *providers: ScheduleProvider, jobs: JobRegistry | None = None) -> None:
        self.store = MemoryStore()
        self.clock = FakeClock(T0)
        self.scheduler = Scheduler(
            self.store,
            settings=fast_settings(),
            clock=self.clock,
            registry=jobs if jobs is not None else registry(),
        )
        self.seeder = ScheduleSeeder(self.store, self.scheduler, providers, clock=self.clock)

    async def items(self) -> dict[str, ScheduleItem]:
        return {item.id: item for item in await self.scheduler.list_items()}


async def test_default_cadence_entries_are_seeded() -> None:
    h = Harness(signals_entries, backups_entries)
    report = await h.seeder.run()
    assert sorted(report.seeded) == [
        "sched-backups-dump",
        "sched-signals-crawl",
        "sched-signals-scout",
    ]
    items = await h.items()
    crawl, scout, backup = (
        items["sched-signals-crawl"],
        items["sched-signals-scout"],
        items["sched-backups-dump"],
    )
    assert isinstance(crawl.rule, IntervalRule)
    assert crawl.rule.seconds == 6 * 3600
    assert crawl.max_lateness == timedelta(hours=6)
    assert isinstance(scout.rule, WeeklyRule)
    assert (scout.rule.days, scout.rule.at) == (["sun"], "15:00")
    assert scout.timezone == "America/New_York"
    assert backup.job_type == BACKUPS_DUMP
    assert h.seeder.last_report is report


async def test_reseeding_unchanged_entries_writes_nothing() -> None:
    h = Harness(signals_entries)
    await h.seeder.reseed()
    before = await h.items()
    h.clock.advance(3600)
    await h.seeder.reseed()
    after = await h.items()
    assert {k: v.revision for k, v in before.items()} == {k: v.revision for k, v in after.items()}
    assert before["sched-signals-crawl"].rule == after["sched-signals-crawl"].rule  # same phase


async def test_a_cadence_change_moves_the_items() -> None:
    h = Harness(signals_entries)
    await h.seeder.reseed()
    cadence = CadenceSettings.default(NOW).model_copy(
        update={"crawl_interval_hours": 12, "timezone": "Europe/London"}
    )
    await save_singleton(h.store, cadence, if_match=None)
    await h.seeder.reseed()
    items = await h.items()
    crawl = items["sched-signals-crawl"]
    assert isinstance(crawl.rule, IntervalRule)
    assert crawl.rule.seconds == 12 * 3600
    assert crawl.timezone == "Europe/London"
    assert crawl.revision == 2


async def test_entries_outside_the_scheduled_types_are_refused_and_never_seeded() -> None:
    jobs = registry(SIGNALS_CRAWL, SIGNALS_SCOUT, POSTS_GENERATE, PUBLISH_ARTICLE)
    h = Harness(signals_entries, content_entries, jobs=jobs)
    report = await h.seeder.run()
    assert sorted(report.refused) == ["sched-posts-generate", "sched-publish-article"]
    assert sorted(await h.items()) == ["sched-signals-crawl", "sched-signals-scout"]


async def test_a_refused_entry_that_an_old_revision_seeded_is_deleted() -> None:
    jobs = registry(SIGNALS_CRAWL, SIGNALS_SCOUT, POSTS_GENERATE)
    h = Harness(signals_entries, content_entries, jobs=jobs)
    await h.scheduler.put(
        "sched-posts-generate",
        name="Old mistake",
        job_type=POSTS_GENERATE,
        timezone="UTC",
        rule=daily("09:00"),
    )
    report = await h.seeder.run()
    assert "sched-posts-generate" in report.deleted
    assert "sched-posts-generate" not in await h.items()


async def test_stale_seeded_items_are_deleted_and_other_items_kept() -> None:
    h = Harness(signals_entries)
    await h.scheduler.put(
        "sched-old-thing", name="Gone", job_type=SIGNALS_CRAWL, timezone="UTC", rule=every(3600)
    )
    await h.scheduler.put(
        "owner-custom", name="Not seeded", job_type=SIGNALS_CRAWL, timezone="UTC", rule=every(3600)
    )
    report = await h.seeder.run()
    assert report.deleted == ["sched-old-thing"]
    assert sorted(await h.items()) == ["owner-custom", "sched-signals-crawl", "sched-signals-scout"]


async def test_a_failing_provider_skips_deletion() -> None:
    h = Harness(signals_entries)
    await h.seeder.reseed()
    h.seeder = ScheduleSeeder(
        h.store, h.scheduler, [broken_entries, backups_entries], clock=h.clock
    )
    report = await h.seeder.run()
    (failed,) = report.failed_providers
    assert failed.endswith("broken_entries")
    assert not report.complete
    assert report.deleted == []
    assert sorted(await h.items()) == [
        "sched-backups-dump",
        "sched-signals-crawl",
        "sched-signals-scout",
    ]


async def test_an_unregistered_job_type_is_skipped_and_its_item_removed() -> None:
    h = Harness(signals_entries, backups_entries)
    await h.seeder.reseed()  # an earlier boot, where the backups package was registered
    narrower = Scheduler(
        h.store,
        settings=fast_settings(),
        clock=h.clock,
        registry=registry(SIGNALS_CRAWL, SIGNALS_SCOUT),
    )
    seeder = ScheduleSeeder(h.store, narrower, [signals_entries, backups_entries], clock=h.clock)
    report = await seeder.run()
    assert report.unregistered == ["sched-backups-dump"]
    assert report.deleted == ["sched-backups-dump"]
    assert sorted(await h.items()) == ["sched-signals-crawl", "sched-signals-scout"]


async def test_duplicate_ids_keep_the_first_entry() -> None:
    h = Harness(signals_entries, signals_entries)
    report = await h.seeder.run()
    assert sorted(report.refused) == ["sched-signals-crawl", "sched-signals-scout"]
    assert sorted(report.seeded) == ["sched-signals-crawl", "sched-signals-scout"]


async def test_a_paused_item_stays_paused_across_reseeds() -> None:
    h = Harness(signals_entries)
    await h.seeder.reseed()
    await h.scheduler.pause("sched-signals-scout")
    cadence = CadenceSettings.default(NOW).model_copy(update={"timezone": "Europe/Paris"})
    await save_singleton(h.store, cadence, if_match=None)
    await h.seeder.reseed()
    scout = (await h.items())["sched-signals-scout"]
    assert scout.status == "paused"
    assert scout.timezone == "Europe/Paris"


async def test_concurrent_reseeds_are_serialized() -> None:
    h = Harness(signals_entries, backups_entries)
    reports = await asyncio.gather(h.seeder.run(), h.seeder.run(), h.seeder.run())
    assert all(sorted(report.seeded) == sorted(reports[0].seeded) for report in reports)
    assert all(item.revision == 1 for item in (await h.items()).values())


async def test_store_errors_propagate_to_the_caller() -> None:
    h = Harness(signals_entries)
    await h.store.create("settings", {"id": "cadence", "kind": "cadence", "timezone": 5})
    with pytest.raises(Exception, match="cadence"):
        await h.seeder.reseed()
    assert await h.items() == {}


class FlakyScheduler:
    """``put`` fails ``failures`` times (a Cosmos 429), then delegates."""

    def __init__(self, inner: Scheduler, failures: int) -> None:
        self.inner = inner
        self.failures = failures

    async def put(
        self,
        item_id: str,
        *,
        name: str,
        job_type: str,
        timezone: str,
        rule: DailyRule | WeeklyRule | IntervalRule,
        job_input: JsonValue = None,
        max_lateness: timedelta = DEFAULT_MAX_LATENESS,
    ) -> ScheduleItem:
        if self.failures > 0:
            self.failures -= 1
            raise StoreUnavailable("throttled")
        return await self.inner.put(
            item_id,
            name=name,
            job_type=job_type,
            timezone=timezone,
            rule=rule,
            job_input=job_input,
            max_lateness=max_lateness,
        )

    async def delete(self, item_id: str) -> None:
        await self.inner.delete(item_id)


async def test_a_failed_reseed_after_a_cadence_save_is_retried_until_it_lands() -> None:
    """The owner saves a new timezone while the store throttles: the reseed raises (kb logs
    it) and the seeder keeps trying in the background, so the new times win without a restart."""
    h = Harness(signals_entries)
    flaky = FlakyScheduler(h.scheduler, failures=2)
    seeder = ScheduleSeeder(
        h.store, flaky, [signals_entries], clock=h.clock, retry_seconds=0.01, retry_max_seconds=0.02
    )
    with pytest.raises(StoreUnavailable):
        await seeder.reseed()
    assert seeder.retrying
    await eventually(lambda: not seeder.retrying, within=5)
    assert sorted(await h.items()) == ["sched-signals-crawl", "sched-signals-scout"]
    await seeder.close()


async def test_closing_stops_a_pending_retry() -> None:
    h = Harness(signals_entries)
    seeder = ScheduleSeeder(
        h.store,
        FlakyScheduler(h.scheduler, failures=10_000),
        [signals_entries],
        clock=h.clock,
        retry_seconds=0.01,
        retry_max_seconds=0.02,
    )
    with pytest.raises(StoreUnavailable):
        await seeder.reseed()
    assert seeder.retrying
    await seeder.close()
    assert not seeder.retrying
    with pytest.raises(StoreUnavailable):
        await seeder.reseed()  # closed: no new retry starts
    assert not seeder.retrying


async def test_every_landed_reseed_runs_the_seeded_hook_and_records_its_time() -> None:
    """The boot starts the scheduler from this hook: only after the items match the cadence."""
    h = Harness(signals_entries)
    flaky = FlakyScheduler(h.scheduler, failures=1)
    calls: list[str] = []

    async def seeded() -> None:
        calls.append("seeded")

    seeder = ScheduleSeeder(
        h.store,
        flaky,
        [signals_entries],
        clock=h.clock,
        retry_seconds=0.01,
        retry_max_seconds=0.02,
        on_seeded=seeded,
    )
    assert seeder.seeded_at is None
    with pytest.raises(StoreUnavailable):
        await seeder.reseed()
    assert calls == []  # a failed reseed starts nothing
    assert seeder.seeded_at is None
    await eventually(lambda: not seeder.retrying, within=5)
    assert calls == ["seeded"]
    assert seeder.seeded_at == T0
    await seeder.reseed()
    assert calls == ["seeded", "seeded"]
    await seeder.close()


async def test_a_failing_seeded_hook_is_logged_and_the_reseed_still_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness(signals_entries)

    async def broken() -> None:
        raise RuntimeError("could not start")

    seeder = ScheduleSeeder(
        h.store, h.scheduler, [signals_entries], clock=h.clock, on_seeded=broken
    )
    report = await seeder.run()
    assert report.complete
    assert seeder.last_report is report
    assert "schedule_seeded_hook_failed" in caplog.text
    assert not seeder.retrying
