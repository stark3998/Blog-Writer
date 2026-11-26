"""Event-loop lag watchdog.

A background task sleeps for a short interval and measures how late it wakes up. A wake-up
later than the threshold means something blocked the loop (a sync call, CPU-bound work) for
at least that long, and is logged as ``event_loop_stall`` with ``lag_ms`` and
``threshold_ms``. The measured lag can undershoot the real stall by up to one interval, so
the interval is a fifth of the threshold (between 10 ms and 100 ms).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Final

__all__ = ["LoopWatchdog"]

_logger: Final = logging.getLogger("blogwriter.core.watchdog")
_MIN_INTERVAL_S: Final = 0.01
_MAX_INTERVAL_S: Final = 0.1


class LoopWatchdog:
    """Logs event-loop stalls longer than ``threshold_ms``. Start and stop it on the loop."""

    def __init__(self, *, threshold_ms: int, interval_s: float | None = None) -> None:
        if threshold_ms <= 0:
            raise ValueError("threshold_ms must be positive")
        if interval_s is None:
            interval_s = min(max(threshold_ms / 5000, _MIN_INTERVAL_S), _MAX_INTERVAL_S)
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.threshold_ms = threshold_ms
        self.interval_s = interval_s
        self.stalls = 0
        self.max_lag_ms = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start watching the running loop. Must be called from a coroutine."""
        if self.running:
            raise RuntimeError("watchdog already running")
        self._task = asyncio.get_running_loop().create_task(
            self._watch(), name="blogwriter-loop-watchdog"
        )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _watch(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await asyncio.sleep(self.interval_s)
            lag_ms = (loop.time() - started - self.interval_s) * 1000
            if lag_ms >= self.threshold_ms:
                self.stalls += 1
                self.max_lag_ms = max(self.max_lag_ms, lag_ms)
                _logger.warning(
                    "event_loop_stall",
                    extra={"lag_ms": round(lag_ms, 1), "threshold_ms": self.threshold_ms},
                )
