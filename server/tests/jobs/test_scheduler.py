"""The scheduler: DST-safe next runs, ticks that fire once, missed slots, max lateness."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast, override
from zoneinfo import ZoneInfo

import pytest
from pydantic import JsonValue, ValidationError

from blogwriter.db import Doc, Filter, MemoryStore, NotFound, Store, StoreUnavailable
from blogwriter.jobs import JobRegistry, Step, StepContext, enqueue, get_job
from blogwriter.jobs.models import Job
from blogwriter.jobs.scheduler import (
    IntervalRule,
    Scheduler,
    ScheduleStateError,
    daily,
    every,
    latest_due,
    next_run_after,
    scheduled_job_id,
    weekly,
)
from blogwriter.jobs.testing import DelegatingStore, FakeClock, eventually, fast_settings

NEW_YORK = ZoneInfo("America/New_York")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def local(text: str) -> datetime:
    """A New York wall time (``fold=0``) as an aware datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=NEW_YORK)


async def jobs_in(store: Store) -> list[Job]:
    docs = await store.find("jobs", [Filter("kind", "==", "job")])
    return sorted((Job.from_body(doc.body) for doc in docs), key=lambda job: job.id)


def registry() -> JobRegistry:
    async def noop(ctx: StepContext) -> JsonValue:
        return None

    result = JobRegistry()
    result.register("crawl", [Step("fetch", noop), Step("triage", noop)])
    result.register("plan.week", [Step("plan", noop)])
    return result


def make_scheduler(store: Store, clock: FakeClock, **kwargs: Any) -> Scheduler:
    return Scheduler(
        store,
        settings=fast_settings(),
        owner=kwargs.pop("owner", "sched:1"),
        clock=clock,
        registry=registry(),
        **kwargs,
    )


# ----------------------------------------------------------------------------------------------
# next_run_after: DST in America/New_York (2026: spring forward 03-08, fall back 11-01)
# ----------------------------------------------------------------------------------------------


def chain(rule: Any, start: datetime, count: int) -> list[datetime]:
    slots: list[datetime] = []
    moment = start
    for _ in range(count):
        moment = next_run_after(rule, NEW_YORK, moment)
        slots.append(moment)
    return slots


def test_daily_across_spring_forward_keeps_wall_time_and_runs_once_in_the_gap() -> None:
    assert chain(daily("09:00"), utc("2026-03-06T15:00:00"), 3) == [
        utc("2026-03-07T14:00:00"),  # 09:00 EST
        utc("2026-03-08T13:00:00"),  # 09:00 EDT
        utc("2026-03-09T13:00:00"),
    ]
    # 02:30 does not exist on 2026-03-08: it runs once, at 03:30 EDT (the same offset after
    # the gap), and the day after at 02:30 EDT again.
    assert chain(daily("02:30"), utc("2026-03-06T12:00:00"), 3) == [
        utc("2026-03-07T07:30:00"),  # 02:30 EST
        utc("2026-03-08T07:30:00"),  # 03:30 EDT
        utc("2026-03-09T06:30:00"),  # 02:30 EDT
    ]


def test_daily_across_fall_back_runs_the_repeated_hour_once() -> None:
    assert chain(daily("01:30"), utc("2026-10-30T12:00:00"), 3) == [
        utc("2026-10-31T05:30:00"),  # 01:30 EDT
        utc("2026-11-01T05:30:00"),  # first 01:30 (EDT), not the second (EST)
        utc("2026-11-02T06:30:00"),  # 01:30 EST
    ]
    # From inside the repeated hour (01:10 EST, after the first 01:30 already ran) the next
    # run is the next day, not the second 01:30.
    assert next_run_after(daily("01:30"), NEW_YORK, utc("2026-11-01T06:10:00")) == utc(
        "2026-11-02T06:30:00"
    )
    assert chain(daily("09:00"), utc("2026-10-31T12:00:00"), 2) == [
        utc("2026-10-31T13:00:00"),  # 09:00 EDT
        utc("2026-11-01T14:00:00"),  # 09:00 EST
    ]


def test_weekly_slots_follow_the_production_calendar_through_dst() -> None:
    plan = weekly(["sun"], "18:00")  # plan next week, Sunday 18:00
    assert chain(plan, utc("2026-03-01T23:30:00"), 2) == [
        utc("2026-03-08T22:00:00"),  # Sun 18:00 EDT (DST began that morning)
        utc("2026-03-15T22:00:00"),
    ]
    assert chain(plan, utc("2026-10-25T21:00:00"), 2) == [
        utc("2026-10-25T22:00:00"),  # Sun 18:00 EDT
        utc("2026-11-01T23:00:00"),  # Sun 18:00 EST
    ]
    posts = weekly(["tue", "wed", "thu"], "10:00")
    assert chain(posts, local("2026-11-02T12:00:00"), 4) == [
        local("2026-11-03T10:00:00").astimezone(UTC),
        local("2026-11-04T10:00:00").astimezone(UTC),
        local("2026-11-05T10:00:00").astimezone(UTC),
        local("2026-11-10T10:00:00").astimezone(UTC),
    ]


def test_interval_slots_are_absolute_time() -> None:
    rule = every(3600, anchor=utc("2026-11-01T04:00:00"))
    assert chain(rule, utc("2026-11-01T03:00:00"), 4) == [
        utc("2026-11-01T04:00:00"),  # the anchor itself
        utc("2026-11-01T05:00:00"),  # 01:00 EDT
        utc("2026-11-01T06:00:00"),  # 01:00 EST: an hour later, same wall clock
        utc("2026-11-01T07:00:00"),
    ]
    assert next_run_after(rule, NEW_YORK, utc("2026-11-01T05:00:00")) == utc(
        "2026-11-01T06:00:00"
    )  # strictly after
    with pytest.raises(ValueError, match="anchor"):
        next_run_after(IntervalRule(seconds=60), NEW_YORK, utc("2026-11-01T00:00:00"))


def test_latest_due_collapses_missed_slots() -> None:
    assert latest_due(
        daily("09:00"), NEW_YORK, utc("2026-03-06T14:00:00"), utc("2026-03-09T13:30:00")
    ) == (utc("2026-03-09T13:00:00"), 3)
    rule = every(3600, anchor=utc("2026-11-01T00:00:00"))
    assert latest_due(rule, NEW_YORK, utc("2026-11-01T01:00:00"), utc("2026-11-01T05:30:00")) == (
        utc("2026-11-01T05:00:00"),
        4,
    )


def test_rule_and_item_validation() -> None:
    with pytest.raises(ValidationError):
        daily("24:00")
    with pytest.raises(ValidationError):
        daily("9:00")
    with pytest.raises(ValidationError):
        weekly([], "09:00")
    with pytest.raises(ValidationError):
        weekly(["mon", "mon"], "09:00")
    with pytest.raises(ValidationError):
        every(59)
    assert scheduled_job_id("sched-crawl", utc("2026-03-08T07:30:00")) == (
        "sched-crawl-20260308T073000Z"
    )


# ----------------------------------------------------------------------------------------------
# Ticks
# ----------------------------------------------------------------------------------------------


async def run_ticks(
    scheduler: Scheduler, clock: FakeClock, until: datetime, step: float = 60
) -> list[str]:
    fired: list[str] = []
    while clock.now() < until:
        report = await scheduler.tick()
        assert report.errors == 0
        fired.extend(report.fired)
        clock.advance(step)
    return fired


async def test_a_daily_item_fires_once_per_day_across_spring_forward(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-03-07T00:00:17"))
    scheduler = make_scheduler(store, clock)
    item = await scheduler.put(
        "sched-crawl",
        name="Nightly crawl",
        job_type="crawl",
        timezone="America/New_York",
        rule=daily("02:30"),
    )
    assert item.next_run_at == "2026-03-07T07:30:00.000000Z"
    fired = await run_ticks(scheduler, clock, local("2026-03-10T00:00:00"))
    assert fired == [
        "sched-crawl-20260307T073000Z",
        "sched-crawl-20260308T073000Z",  # 03:30 EDT on the day 02:30 does not exist
        "sched-crawl-20260309T063000Z",
    ]
    jobs = await jobs_in(store)
    assert [job.id for job in jobs] == fired
    assert [job.scheduled_for for job in jobs] == [
        "2026-03-07T07:30:00.000000Z",
        "2026-03-08T07:30:00.000000Z",
        "2026-03-09T06:30:00.000000Z",
    ]
    assert all(job.schedule_id == "sched-crawl" for job in jobs)
    assert [step.name for step in jobs[0].steps] == ["fetch", "triage"]
    final = await scheduler.get("sched-crawl")
    assert final.next_run_at == "2026-03-10T06:30:00.000000Z"
    assert final.last_job_id == "sched-crawl-20260309T063000Z"
    assert final.skipped_runs == 0
    assert final.owner is None


async def test_a_daily_item_fires_once_in_the_repeated_hour_of_fall_back(
    store: MemoryStore,
) -> None:
    clock = FakeClock(local("2026-10-31T00:00:41"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-crawl",
        name="Nightly crawl",
        job_type="crawl",
        timezone="America/New_York",
        rule=daily("01:30"),
    )
    fired = await run_ticks(scheduler, clock, local("2026-11-03T00:00:00"))
    assert fired == [
        "sched-crawl-20261031T053000Z",
        "sched-crawl-20261101T053000Z",  # the first 01:30 only
        "sched-crawl-20261102T063000Z",
    ]


async def test_missed_slots_collapse_into_the_latest_one(store: MemoryStore) -> None:
    clock = FakeClock(utc("2026-11-02T00:00:00"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-hourly",
        name="Hourly",
        job_type="crawl",
        timezone="America/New_York",
        rule=every(3600),
    )
    clock.advance(timedelta(hours=5, minutes=30))  # downtime: 5 slots missed
    report = await scheduler.tick()
    assert report.fired == ["sched-hourly-20261102T050000Z"]
    item = await scheduler.get("sched-hourly")
    assert item.skipped_runs == 4
    assert item.next_run_at == "2026-11-02T06:00:00.000000Z"
    assert len(await jobs_in(store)) == 1


async def test_a_slot_past_its_max_lateness_waits_for_confirmation(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:00:00"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-plan",
        name="Plan the week",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
        max_lateness=timedelta(hours=2),
    )
    clock.set(local("2026-11-02T11:00:00"))  # back after downtime: 09:00 is exactly 2 h late
    assert (await scheduler.tick()).fired == ["sched-plan-20261102T140000Z"]

    clock.set(local("2026-11-03T11:00:01"))  # the next day: 2 h and 1 s late
    report = await scheduler.tick()
    assert report.fired == []
    assert report.needs_confirmation == ["sched-plan"]
    waiting = await scheduler.get("sched-plan")
    assert waiting.status == "needs_confirmation"
    assert waiting.pending_run_at == "2026-11-03T14:00:00.000000Z"
    clock.advance(timedelta(days=2))
    assert (await scheduler.tick()).fired == []  # nothing fires until the owner decides
    assert len(await jobs_in(store)) == 1

    confirmed = await scheduler.confirm("sched-plan", run=True)
    assert confirmed.status == "active"
    assert confirmed.pending_run_at is None
    assert confirmed.last_job_id == "sched-plan-20261103T140000Z"
    assert confirmed.next_run_at == "2026-11-06T14:00:00.000000Z"
    assert [job.id for job in await jobs_in(store)] == [
        "sched-plan-20261102T140000Z",
        "sched-plan-20261103T140000Z",
    ]
    with pytest.raises(ScheduleStateError, match="nothing to confirm"):
        await scheduler.confirm("sched-plan", run=True)


async def test_skipping_a_missed_slot(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:00:00"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    clock.set(local("2026-11-02T13:00:00"))
    assert (await scheduler.tick()).needs_confirmation == ["sched-plan"]
    skipped = await scheduler.confirm("sched-plan", run=False)
    assert skipped.status == "active"
    assert skipped.skipped_runs == 1
    assert skipped.next_run_at == "2026-11-03T14:00:00.000000Z"
    assert await jobs_in(store) == []


async def test_two_schedulers_ticking_together_enqueue_one_job_per_slot(
    store: MemoryStore,
) -> None:
    clock = FakeClock(local("2026-11-02T08:59:00"))
    first = make_scheduler(store, clock, owner="sched:1")
    second = make_scheduler(store, clock, owner="sched:2")
    await first.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    for _ in range(3):
        clock.advance(60)
        await asyncio.gather(first.tick(), second.tick())
    assert [job.id for job in await jobs_in(store)] == ["sched-plan-20261102T140000Z"]
    item = await first.get("sched-plan")
    assert item.next_run_at == "2026-11-03T14:00:00.000000Z"


class BrokenWrites(DelegatingStore):
    """Every replace fails: the scheduler 'crashes' after enqueueing, before advancing."""

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        if body.get("kind") == "schedule" and body.get("owner") is None:
            raise StoreUnavailable("connection reset")
        return await self.inner.replace(container, body, if_match=if_match)


async def test_a_crash_between_enqueue_and_advance_never_enqueues_twice(
    store: MemoryStore,
) -> None:
    clock = FakeClock(local("2026-11-02T08:59:30"))
    dying = Scheduler(BrokenWrites(store), settings=fast_settings(), owner="sched:1", clock=clock)
    await dying.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    clock.advance(60)
    report = await dying.tick()
    assert report.errors == 1
    assert [job.id for job in await jobs_in(store)] == ["sched-plan-20261102T140000Z"]
    stuck = await dying.get("sched-plan")
    assert stuck.owner == "sched:1"  # still claimed: the release never landed

    survivor = make_scheduler(store, clock, owner="sched:2")
    assert (await survivor.tick()).fired == []  # the claim is still valid
    clock.advance(66)
    assert (await survivor.tick()).fired == ["sched-plan-20261102T140000Z"]
    assert len(await jobs_in(store)) == 1
    item = await survivor.get("sched-plan")
    assert item.next_run_at == "2026-11-03T14:00:00.000000Z"
    assert item.owner is None
    assert item.fence == 2


async def test_a_definition_changed_during_a_tick_is_not_overwritten(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:59:30"))
    changes: list[Callable[[], Any]] = []

    class EditDuringEnqueue(DelegatingStore):
        @override
        async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
            doc = await self.inner.create(container, body)
            while changes:
                await changes.pop()()
            return doc

    scheduler = make_scheduler(EditDuringEnqueue(store), clock)
    await scheduler.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    editor = make_scheduler(store, clock, owner="api:1")
    changes.append(
        lambda: editor.put(
            "sched-plan",
            name="Plan",
            job_type="plan.week",
            timezone="America/New_York",
            rule=daily("17:00"),
        )
    )
    clock.advance(60)
    assert (await scheduler.tick()).fired == ["sched-plan-20261102T140000Z"]
    item = await scheduler.get("sched-plan")
    assert item.rule == daily("17:00")
    assert item.revision == 2
    assert item.next_run_at == "2026-11-02T22:00:00.000000Z"  # from the new rule, not 09:00
    assert item.owner is None


async def test_put_keeps_state_for_an_unchanged_definition(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:59:30"))
    scheduler = make_scheduler(store, clock)
    arguments: dict[str, Any] = {
        "name": "Plan",
        "job_type": "plan.week",
        "timezone": "America/New_York",
        "rule": daily("09:00"),
    }
    created = await scheduler.put("sched-plan", **arguments)
    clock.advance(60)
    await scheduler.tick()
    again = await scheduler.put("sched-plan", **arguments)
    assert again.revision == created.revision == 1
    assert again.last_job_id == "sched-plan-20261102T140000Z"
    renamed: dict[str, Any] = {**arguments, "name": "Plan next week"}
    changed = await scheduler.put("sched-plan", **renamed)
    assert changed.revision == 2
    assert changed.last_job_id == "sched-plan-20261102T140000Z"
    assert changed.created_at == created.created_at


async def test_put_validation(store: MemoryStore, clock: FakeClock) -> None:
    scheduler = make_scheduler(store, clock)
    with pytest.raises(ValueError, match="not registered"):
        await scheduler.put(
            "sched-x", name="X", job_type="unknown", timezone="UTC", rule=daily("09:00")
        )
    for zone in ("Mars/Olympus", "../../etc/passwd", ""):
        with pytest.raises(ValueError, match="IANA"):
            await scheduler.put(
                "sched-x", name="X", job_type="crawl", timezone=zone, rule=daily("09:00")
            )
    with pytest.raises(ValueError, match="at most"):
        await scheduler.put(
            "s" * 201, name="X", job_type="crawl", timezone="UTC", rule=daily("09:00")
        )
    with pytest.raises(ValueError, match="JSON"):
        await scheduler.put(
            "sched-x",
            name="X",
            job_type="crawl",
            timezone="UTC",
            rule=daily("09:00"),
            job_input=cast(JsonValue, {"when": object()}),
        )
    assert await scheduler.list_items() == []


async def test_pause_resume_delete_and_list(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:00:00"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-a", name="A", job_type="crawl", timezone="America/New_York", rule=daily("09:00")
    )
    await scheduler.put(
        "sched-b", name="B", job_type="crawl", timezone="America/New_York", rule=daily("08:30")
    )
    assert [item.id for item in await scheduler.list_items()] == ["sched-b", "sched-a"]
    paused = await scheduler.pause("sched-a")
    assert paused.status == "paused"
    assert (await scheduler.pause("sched-a")).status == "paused"
    clock.set(local("2026-11-04T11:00:00"))
    report = await scheduler.tick()
    assert report.fired == []  # sched-a is paused; sched-b's latest slot is 2.5 h late
    assert report.needs_confirmation == ["sched-b"]
    resumed = await scheduler.resume("sched-a")
    assert resumed.status == "active"
    assert resumed.next_run_at == "2026-11-05T14:00:00.000000Z"  # no catch-up
    with pytest.raises(ScheduleStateError):
        await scheduler.resume("sched-a")
    await scheduler.delete("sched-b")
    with pytest.raises(NotFound):
        await scheduler.get("sched-b")
    job = await enqueue(store, "crawl")
    with pytest.raises(NotFound):
        await scheduler.get(job.id)


async def test_no_tick_after_shutdown_was_requested(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:59:30"))
    exiting: list[bool] = []
    scheduler = make_scheduler(store, clock, stop_requested=lambda: bool(exiting))
    await scheduler.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    clock.advance(60)
    exiting.append(True)
    assert (await scheduler.tick()).fired == []
    assert await jobs_in(store) == []


async def test_the_tick_loop_fires_due_items_and_wakes_the_runner(store: MemoryStore) -> None:
    clock = FakeClock(local("2026-11-02T08:59:30"))
    woken: list[str] = []
    scheduler = Scheduler(
        store,
        settings=fast_settings(tick_seconds=0.01),
        owner="sched:1",
        clock=clock,
        on_enqueue=lambda: woken.append("wake"),
    )
    await scheduler.put(
        "sched-plan",
        name="Plan",
        job_type="plan.week",
        timezone="America/New_York",
        rule=daily("09:00"),
    )
    await scheduler.start()
    with pytest.raises(RuntimeError):
        await scheduler.start()
    try:
        assert scheduler.running
        clock.advance(60)

        async def fired() -> bool:
            return len(await jobs_in(store)) == 1

        await eventually(fired)
    finally:
        await scheduler.stop()
    assert not scheduler.running
    assert woken == ["wake"]
    job = await get_job(store, "sched-plan-20261102T140000Z")
    assert job.steps == []  # no registry: the runner fills the steps in when it claims it


async def test_one_broken_item_does_not_stop_the_items_due_after_it(store: MemoryStore) -> None:
    """The broken item is the oldest due one, so it heads every tick: before, its exception
    ended the tick and no item after it ever fired again."""
    clock = FakeClock(utc("2026-11-02T12:00:00"))
    scheduler = make_scheduler(store, clock)
    good = await scheduler.put(
        "sched-good", name="Good", job_type="crawl", timezone="UTC", rule=every(3600)
    )
    broken = {  # as another revision might write it: an interval rule without its anchor
        **good.to_body(),
        "id": "sched-broken",
        "rule": {"kind": "interval", "seconds": 3600},
        "nextRunAt": "2026-11-02T11:00:00.000000Z",
    }
    await store.create("jobs", broken)
    clock.advance(3600)
    report = await scheduler.tick()
    assert report.errors == 1
    assert report.fired == ["sched-good-20261102T130000Z"]
    assert (await scheduler.get("sched-good")).next_run_at == "2026-11-02T14:00:00.000000Z"


class SwapBeforeDelete(DelegatingStore):
    """Runs ``swap`` (another writer) between the scheduler's read and its delete, once."""

    def __init__(self, inner: Store, swap: Callable[[Store, str], Any]) -> None:
        super().__init__(inner)
        self.swap = swap
        self.swapped = False

    @override
    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        if not self.swapped:
            self.swapped = True
            await self.swap(self.inner, id)
        await self.inner.delete(container, id, partition_key=partition_key, if_match=if_match)


async def test_delete_never_removes_a_job_that_took_the_id_in_between(
    store: MemoryStore,
) -> None:
    async def become_a_job(inner: Store, item_id: str) -> None:
        await inner.delete("jobs", item_id)
        await enqueue(inner, "crawl", job_id=item_id)

    clock = FakeClock(local("2026-11-02T08:00:00"))
    racing = make_scheduler(SwapBeforeDelete(store, become_a_job), clock)
    await racing.put(
        "sched-a", name="A", job_type="crawl", timezone="America/New_York", rule=daily("09:00")
    )
    with pytest.raises(NotFound, match="not a schedule item"):
        await racing.delete("sched-a")
    assert (await get_job(store, "sched-a")).status == "queued"  # the job survived


async def test_delete_rechecks_after_a_concurrent_write(store: MemoryStore) -> None:
    async def tick_writes(inner: Store, item_id: str) -> None:
        doc = await inner.read("jobs", item_id)
        await inner.replace("jobs", {**doc.body, "skippedRuns": 1}, if_match=doc.etag)

    clock = FakeClock(local("2026-11-02T08:00:00"))
    racing = make_scheduler(SwapBeforeDelete(store, tick_writes), clock)
    await racing.put(
        "sched-a", name="A", job_type="crawl", timezone="America/New_York", rule=daily("09:00")
    )
    await racing.delete("sched-a")
    with pytest.raises(NotFound):
        await store.read("jobs", "sched-a")


async def test_a_changed_definition_never_ends_a_pause(store: MemoryStore) -> None:
    """Fail closed: a deploy (or a rename) that updates a definition must not restart a
    schedule the owner paused. Only resume does."""
    clock = FakeClock(local("2026-11-02T08:00:00"))
    scheduler = make_scheduler(store, clock)
    await scheduler.put(
        "sched-a", name="A", job_type="crawl", timezone="America/New_York", rule=daily("09:00")
    )
    await scheduler.pause("sched-a")
    changed = await scheduler.put(
        "sched-a", name="A", job_type="crawl", timezone="America/New_York", rule=daily("10:00")
    )
    assert (changed.status, changed.revision, changed.rule) == ("paused", 2, daily("10:00"))
    clock.set(local("2026-11-02T10:00:30"))
    assert (await scheduler.tick()).fired == []
    resumed = await scheduler.resume("sched-a")
    assert resumed.status == "active"
    assert resumed.next_run_at == "2026-11-03T15:00:00.000000Z"  # 10:00 EST, from the new rule


async def test_an_interval_without_an_explicit_anchor_keeps_its_first_one(
    store: MemoryStore,
) -> None:
    """Putting the same definition again (seeding at every boot) used to stamp a new anchor:
    a new revision each time, and the hourly run moved to the minute of the restart."""
    clock = FakeClock(utc("2026-11-26T12:00:00"))
    scheduler = make_scheduler(store, clock)
    arguments: dict[str, Any] = {"name": "Crawl", "job_type": "crawl", "timezone": "UTC"}
    renamed_arguments: dict[str, Any] = {**arguments, "name": "C"}
    first = await scheduler.put("sched-crawl", rule=every(3600), **arguments)
    clock.advance(17 * 60)
    again = await scheduler.put("sched-crawl", rule=every(3600), **arguments)
    assert (again.revision, again.rule, again.next_run_at) == (
        1,
        first.rule,
        "2026-11-26T13:00:00.000000Z",
    )
    renamed = await scheduler.put("sched-crawl", rule=every(3600), **renamed_arguments)
    assert renamed.revision == 2
    assert renamed.rule == first.rule  # a rename keeps the phase
    assert renamed.next_run_at == "2026-11-26T13:00:00.000000Z"
    faster = await scheduler.put("sched-crawl", rule=every(1800), **renamed_arguments)
    assert faster.revision == 3
    assert isinstance(faster.rule, IntervalRule)
    assert faster.rule.anchor == "2026-11-26T12:17:00.000000Z"  # a new period starts from now
    pinned = await scheduler.put(
        "sched-crawl",
        rule=every(1800, anchor=utc("2026-11-26T12:05:00")),
        **renamed_arguments,
    )
    assert pinned.revision == 4
    assert pinned.next_run_at == "2026-11-26T12:35:00.000000Z"
