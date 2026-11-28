"""Render telemetry: per-stage timings and process-tree memory (plus cgroup v2 on Linux).

Sampling runs in a daemon thread (psutil calls block), and :meth:`MemorySampler.stop` joins it
from a worker thread, so the event loop never waits on it. In the container the number that
matters is cgroup ``memory.peak`` (what the 2 GiB limit enforces); RSS sums double-count shared
pages and per-process "unique" figures (macOS phys_footprint, Linux PSS) are the better estimate,
so both are reported. Everything here is ids and numbers: no content.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import ctypes.util
import sys
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import psutil

__all__ = ["MemoryPeaks", "MemorySampler", "StageTimer", "unique_bytes"]

_CGROUP: Final = Path("/sys/fs/cgroup")
MiB: Final = 1024 * 1024


@dataclass
class StageTimer:
    """Collects wall-clock seconds per named stage. Cheap enough to leave on in production."""

    stages: dict[str, float] = field(default_factory=dict[str, float])

    @contextlib.contextmanager
    def stage(self, name: str) -> Generator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + time.perf_counter() - t0

    @property
    def total(self) -> float:
        return sum(self.stages.values())

    def as_log_list(self) -> list[str]:
        """``["driver_start:162", ...]``: stage name and milliseconds, for one log field."""
        return [f"{name}:{round(seconds * 1000)}" for name, seconds in self.stages.items()]


def _kind(proc: psutil.Process) -> str | None:
    try:
        name = proc.name().lower()
        if "node" in name:
            return "driver"
        if "chrom" not in name and "headless" not in name:
            return None
        cmd = " ".join(proc.cmdline())
    except psutil.Error:
        return None
    if "--type=renderer" in cmd:
        return "renderer"
    if "--type=gpu-process" in cmd:
        return "gpu"
    if "--type=utility" in cmd:
        return "network" if "NetworkService" in cmd else "utility"
    if "--type=" in cmd:
        return "other"
    return "browser"


class _RusageInfoV2(ctypes.Structure):
    _fields_ = (
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_rest", ctypes.c_uint64 * 10),
    )


def _load_libproc() -> ctypes.CDLL | None:
    if sys.platform != "darwin":
        return None
    try:
        return ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")
    except OSError:  # pragma: no cover - libproc is part of macOS
        return None


_LIBPROC: Final = _load_libproc()


def unique_bytes(proc: psutil.Process) -> int | None:
    """Memory attributable to one process: phys_footprint on macOS (what Activity Monitor
    shows), PSS on Linux (shared pages split between sharers). ``None`` when the OS refuses."""
    if _LIBPROC is not None:
        info = _RusageInfoV2()
        if _LIBPROC.proc_pid_rusage(proc.pid, 2, ctypes.byref(info)) == 0:
            return int(info.ri_phys_footprint)
        return None
    try:
        return int(getattr(proc.memory_full_info(), "pss", 0)) or None
    except psutil.Error:
        return None


def _cgroup_bytes(name: str) -> int | None:
    try:
        raw = (_CGROUP / name).read_text().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _mib(value: int | None) -> float | None:
    return None if value is None else round(value / MiB, 1)


@dataclass
class MemoryPeaks:
    samples: int = 0
    python_rss: int = 0
    driver_rss: int = 0
    chromium_rss: int = 0
    """Whole Chromium tree, sum of RSS (an upper bound: shared pages are counted per process)."""
    chromium_unique: int = 0
    """Whole Chromium tree: macOS phys_footprint / Linux PSS (the best estimate)."""
    driver_unique: int = 0
    tree_rss: int = 0
    """Python + driver + Chromium."""
    chromium_processes: int = 0
    by_kind_rss: dict[str, int] = field(default_factory=dict[str, int])
    cgroup_current: int | None = None
    cgroup_peak: int | None = None
    cgroup_max: int | None = None

    def log_fields(self) -> dict[str, float | int | None]:
        """Numbers only, in MiB, for one structured log line."""
        return {
            "memory_samples": self.samples,
            "python_rss_mib": _mib(self.python_rss),
            "driver_rss_mib": _mib(self.driver_rss),
            "driver_unique_mib": _mib(self.driver_unique),
            "chromium_rss_mib": _mib(self.chromium_rss),
            "chromium_unique_mib": _mib(self.chromium_unique),
            "chromium_processes": self.chromium_processes,
            "tree_rss_mib": _mib(self.tree_rss),
            "cgroup_current_mib": _mib(self.cgroup_current),
            "cgroup_peak_mib": _mib(self.cgroup_peak),
            "cgroup_max_mib": _mib(self.cgroup_max),
        }


class MemorySampler:
    """Tracks peak memory of this process and its Playwright/Chromium children during one job.

    ``interval`` 0 disables process sampling; the cgroup figures are still read at the end.
    """

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.peaks = MemoryPeaks()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._me = psutil.Process()

    def start(self) -> None:
        """Start the sampling thread (non-blocking)."""
        if self.interval > 0 and self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="render-memory-sampler", daemon=True
            )
            self._thread.start()

    async def stop(self) -> MemoryPeaks:
        """Stop sampling, take a final sample and return the peaks (joins off the event loop)."""
        await asyncio.to_thread(self._finish)
        return self.peaks

    def _finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self.sample()  # final sample, so short jobs still record something
        else:
            self._sample_cgroup()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def sample(self) -> None:
        p = self.peaks
        try:
            python_rss = self._me.memory_info().rss
            children = self._me.children(recursive=True)
        except psutil.Error:  # pragma: no cover - our own process
            return
        driver = driver_unique = chromium_rss = chromium_unique = count = 0
        kinds: dict[str, int] = {}
        for child in children:
            kind = _kind(child)
            if kind is None:
                continue
            try:
                rss = child.memory_info().rss
            except psutil.Error:
                continue
            unique = unique_bytes(child) or 0
            if kind == "driver":
                driver += rss
                driver_unique += unique
                continue
            count += 1
            chromium_rss += rss
            chromium_unique += unique
            kinds[kind] = kinds.get(kind, 0) + rss
        p.samples += 1
        p.python_rss = max(p.python_rss, python_rss)
        p.driver_rss = max(p.driver_rss, driver)
        p.chromium_rss = max(p.chromium_rss, chromium_rss)
        p.chromium_unique = max(p.chromium_unique, chromium_unique)
        p.driver_unique = max(p.driver_unique, driver_unique)
        p.tree_rss = max(p.tree_rss, python_rss + driver + chromium_rss)
        p.chromium_processes = max(p.chromium_processes, count)
        for k, v in kinds.items():
            p.by_kind_rss[k] = max(p.by_kind_rss.get(k, 0), v)
        self._sample_cgroup()

    def _sample_cgroup(self) -> None:
        p = self.peaks
        current = _cgroup_bytes("memory.current")
        if current is not None:
            p.cgroup_current = max(p.cgroup_current or 0, current)
        p.cgroup_peak = _cgroup_bytes("memory.peak")
        p.cgroup_max = _cgroup_bytes("memory.max")
