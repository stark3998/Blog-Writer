"""The jobs package logger and the log fields it may write (ids, counts and codes only)."""

from __future__ import annotations

import logging
from typing import Final

from blogwriter.core.logging import register_log_fields

__all__ = ["logger"]

logger: Final = logging.getLogger("blogwriter.jobs")

register_log_fields(
    "attempt",
    "boots",
    "fence",
    "job_id",
    "job_status",
    "job_step",
    "job_type",
    "outcome",
    "owner",
    "safe_mode",
    "schedule_id",
    "slot",
    "subscribers",
)
