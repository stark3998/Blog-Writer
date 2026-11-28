"""The services package logger and the log fields it may write (ids, counts and codes only)."""

from __future__ import annotations

import logging
from typing import Final

from blogwriter.core.logging import register_log_fields

__all__ = ["logger"]

logger: Final = logging.getLogger("blogwriter.services")

register_log_fields(
    "configured",
    "deleted",
    "delivered",
    "job_type",
    "missing",
    "provider",
    "refused",
    "render_failure",
    "schedule_id",
    "seeded",
    "subscription_id",
    "unregistered",
)
