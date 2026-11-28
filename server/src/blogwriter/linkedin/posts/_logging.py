"""The posts logger and its log fields: ids, counts, codes and similarity scores only.

Never log post text, hooks, slides, prompts, model output, blocklist terms or URLs.
"""

from __future__ import annotations

import logging
from typing import Final

from blogwriter.core.logging import register_log_fields

__all__ = ["logger"]

logger: Final = logging.getLogger("blogwriter.linkedin.posts")

register_log_fields(
    "post_id",
    "article_id",
    "job_id",
    "job_type",
    "post_lane",
    "post_format",
    "check_name",
    "findings_count",
    "error_code",
    "posts_count",
)
