"""The scheduler logs ``schedule_tick`` after every tick (contract I-5).

Plan: "CI/CD and operations" > out-of-band alerts: "no ``scheduler_tick`` in 15 minutes". The
alert can only be enabled when an idle tick logs too; it used to log only busy ticks.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from blogwriter.core.logging import JsonFormatter
from blogwriter.db import MemoryStore
from blogwriter.jobs import JobRegistry, Scheduler, Step, StepContext, TickReport, every
from blogwriter.jobs.scheduler import log_tick
from blogwriter.jobs.testing import FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def tick_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    formatter = JsonFormatter()
    lines: list[dict[str, object]] = [
        json.loads(formatter.format(record)) for record in caplog.records
    ]
    return [line for line in lines if line.get("event") == "schedule_tick"]


async def test_an_idle_scheduler_logs_every_tick(caplog: pytest.LogCaptureFixture) -> None:
    scheduler = Scheduler(
        MemoryStore(),
        settings=fast_settings(tick_seconds=0.01),
        clock=FakeClock(T0),
    )
    with caplog.at_level(logging.INFO, logger="blogwriter.jobs"):
        await scheduler.start()
        try:
            await eventually(lambda: len(tick_lines(caplog)) >= 3)
        finally:
            await scheduler.stop()
    for line in tick_lines(caplog)[:3]:
        assert line["level"] == "INFO"
        assert (line["count"], line["fired"], line["parked"], line["errors"]) == (0, 0, 0, 0)
        assert "dropped_fields" not in line


async def test_a_busy_tick_counts_what_it_did(caplog: pytest.LogCaptureFixture) -> None:
    async def work(ctx: StepContext) -> None:
        return None

    registry = JobRegistry()
    registry.register("t.work", [Step("work", work)])
    clock = FakeClock(T0)
    store = MemoryStore()
    scheduler = Scheduler(
        store, settings=fast_settings(tick_seconds=0.01), clock=clock, registry=registry
    )
    await scheduler.put(
        "sched-test-work", name="Work", job_type="t.work", timezone="UTC", rule=every(60)
    )
    clock.advance(61)
    with caplog.at_level(logging.INFO, logger="blogwriter.jobs"):
        await scheduler.start()
        try:
            await eventually(lambda: any(line["fired"] == 1 for line in tick_lines(caplog)))
        finally:
            await scheduler.stop()


def test_the_tick_line_carries_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    report = TickReport(fired=["sched-a-20261126T120000Z"], needs_confirmation=["sched-b"])
    report.errors = 2
    with caplog.at_level(logging.INFO, logger="blogwriter.jobs"):
        log_tick(report)
    (line,) = tick_lines(caplog)
    assert (line["count"], line["fired"], line["parked"], line["errors"]) == (2, 1, 1, 2)
    assert "sched-a" not in json.dumps(line)
