"""Render telemetry: per-stage timings and process-tree memory (plus cgroup v2 on Linux).

Sampling runs in a daemon thread (psutil calls block), so it never stalls the event loop. In the
container the number that matters is cgroup ``memory.current``/``memory.peak`` (what the 2 GiB limit
enforces); RSS sums double-count shared pages and USS sums miss them, so both are reported.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import psutil

_CGROUP = Path("/sys/fs/cgroup")
MiB = 1024 * 1024


@dataclass
class StageTimer:
    """Collects wall-clock seconds per named stage. Cheap enough to leave on in production."""

    stages: dict[str, float] = field(default_factory=dict)

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
    _fields_ = [
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
    ]


_LIBPROC: ctypes.CDLL | None = (
    ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib") if sys.platform == "darwin" else None
)


def unique_bytes(proc: psutil.Process) -> int | None:
    """Memory attributable to one process: phys_footprint on macOS (what Activity Monitor shows),
    PSS on Linux (shared pages split between sharers). None when the OS refuses."""
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
    return None if raw == "max" else int(raw)


@dataclass
class MemoryPeaks:
    samples: int = 0
    python_rss: int = 0
    driver_rss: int = 0
    chromium_rss: int = 0  # whole Chromium tree, sum of RSS (upper bound: shared pages counted N times)
    chromium_unique: int = 0  # whole Chromium tree: macOS phys_footprint / Linux PSS (best estimate)
    driver_unique: int = 0
    tree_rss: int = 0  # python + driver + chromium
    chromium_processes: int = 0
    by_kind_rss: dict[str, int] = field(default_factory=dict)
    cgroup_current: int | None = None
    cgroup_peak: int | None = None
    cgroup_max: int | None = None

    def as_mib(self) -> dict[str, float | int | dict[str, float] | None]:
        def m(v: int | None) -> float | None:
            return None if v is None else round(v / MiB, 1)

        return {
            "samples": self.samples,
            "python_rss_mib": m(self.python_rss),
            "driver_rss_mib": m(self.driver_rss),
            "chromium_rss_mib": m(self.chromium_rss),
            "chromium_unique_mib": m(self.chromium_unique),
            "driver_unique_mib": m(self.driver_unique),
            "tree_rss_mib": m(self.tree_rss),
            "chromium_processes": self.chromium_processes,
            "by_kind_rss_mib": {k: round(v / MiB, 1) for k, v in sorted(self.by_kind_rss.items())},
            "cgroup_current_mib": m(self.cgroup_current),
            "cgroup_peak_mib": m(self.cgroup_peak),
            "cgroup_max_mib": m(self.cgroup_max),
        }


class MemorySampler:
    """Context manager that tracks peak memory of this process and its Playwright/Chromium children."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.peaks = MemoryPeaks()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="render-memory-sampler", daemon=True)
        self._me = psutil.Process()

    def __enter__(self) -> MemorySampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.sample()  # final sample so short runs still record something

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def sample(self) -> None:
        p = self.peaks
        python_rss = self._me.memory_info().rss
        driver = driver_unique = chromium_rss = chromium_unique = count = 0
        kinds: dict[str, int] = {}
        for child in self._me.children(recursive=True):
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
        current = _cgroup_bytes("memory.current")
        if current is not None:
            p.cgroup_current = max(p.cgroup_current or 0, current)
            p.cgroup_peak = _cgroup_bytes("memory.peak")
            p.cgroup_max = _cgroup_bytes("memory.max")
