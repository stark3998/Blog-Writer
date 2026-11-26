"""Tests for blogwriter.core.watchdog."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from blogwriter.core.app import create_app
from blogwriter.core.testing import make_settings
from blogwriter.core.watchdog import LoopWatchdog

WATCHDOG_LOGGER = "blogwriter.core.watchdog"


def block_the_loop(seconds: float) -> None:
    """A deliberate blocking call: busy-wait without yielding to the event loop."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        pass


def stall_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "event_loop_stall"]


async def test_logs_a_stall_from_a_blocking_call(caplog: pytest.LogCaptureFixture) -> None:
    watchdog = LoopWatchdog(threshold_ms=50, interval_s=0.01)
    with caplog.at_level(logging.WARNING, logger=WATCHDOG_LOGGER):
        watchdog.start()
        await asyncio.sleep(0.05)  # let it settle into its loop
        block_the_loop(0.3)
        await asyncio.sleep(0.05)  # let it wake up and report
        await watchdog.stop()
    (record,) = stall_records(caplog)
    assert record.levelno == logging.WARNING
    lag_ms = record.__dict__["lag_ms"]
    assert 250 <= lag_ms <= 1000
    assert record.__dict__["threshold_ms"] == 50
    assert watchdog.stalls == 1
    assert watchdog.max_lag_ms >= 250


async def test_an_idle_loop_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    watchdog = LoopWatchdog(threshold_ms=1000, interval_s=0.01)
    with caplog.at_level(logging.WARNING, logger=WATCHDOG_LOGGER):
        watchdog.start()
        await asyncio.sleep(0.1)
        await watchdog.stop()
    assert stall_records(caplog) == []
    assert watchdog.stalls == 0


async def test_start_and_stop() -> None:
    watchdog = LoopWatchdog(threshold_ms=500)
    assert not watchdog.running
    watchdog.start()
    assert watchdog.running
    with pytest.raises(RuntimeError, match="already running"):
        watchdog.start()
    await watchdog.stop()
    assert not watchdog.running
    await watchdog.stop()  # idempotent


@pytest.mark.parametrize(
    ("threshold_ms", "interval_s"), [(500, 0.1), (10_000, 0.1), (100, 0.02), (10, 0.01)]
)
def test_interval_scales_with_the_threshold(threshold_ms: int, interval_s: float) -> None:
    assert LoopWatchdog(threshold_ms=threshold_ms).interval_s == pytest.approx(interval_s)


def test_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="threshold_ms"):
        LoopWatchdog(threshold_ms=0)
    with pytest.raises(ValueError, match="interval_s"):
        LoopWatchdog(threshold_ms=500, interval_s=0)


async def test_app_lifespan_runs_the_watchdog() -> None:
    app = create_app(make_settings(watchdog_threshold_ms=750))
    watchdog = app.state.watchdog
    assert isinstance(watchdog, LoopWatchdog)
    assert watchdog.threshold_ms == 750
    assert not watchdog.running
    async with app.router.lifespan_context(app):
        assert watchdog.running
    assert not watchdog.running
