"""The render package logger and the log fields it may write (ids, codes, counts, numbers)."""

from __future__ import annotations

import logging
from typing import Final

from blogwriter.core.logging import register_log_fields

__all__ = ["logger"]

logger: Final = logging.getLogger("blogwriter.render")

register_log_fields(
    "render_job",
    "render_kind",
    "outcome",
    "slides",
    "warnings",
    "output_bytes",
    "blocked_requests",
    "stages",
    "memory_samples",
    "python_rss_mib",
    "driver_rss_mib",
    "driver_unique_mib",
    "chromium_rss_mib",
    "chromium_unique_mib",
    "chromium_processes",
    "tree_rss_mib",
    "cgroup_current_mib",
    "cgroup_peak_mib",
    "cgroup_max_mib",
    "scheme",
    "resource_type",
    "concurrency",
    "sandbox",
    "removed_homes",
    "render_dumpable_hardened",
)
