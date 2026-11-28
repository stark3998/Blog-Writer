"""The stories package logger and the log fields it may write (ids, counts and codes only).

Never log owner text, proposals, prompts, model output, excerpts or blocklist terms: log lines
carry document and job ids, counts, durations and fixed codes.
"""

from __future__ import annotations

import logging
from typing import Final

from blogwriter.core.logging import register_log_fields

__all__ = ["logger"]

logger: Final = logging.getLogger("blogwriter.stories")

register_log_fields(
    "batches",
    "created_count",
    "doc_id",
    "existing",
    "intake_purpose",
    "job_id",
    "policy_rule",
    "skipped",
    "texts",
)
