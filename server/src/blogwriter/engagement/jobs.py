"""The ``engagement.purge`` job: delete every item past its 48 hours.

One step, ``purge`` (:func:`~blogwriter.engagement.helpers.purge_expired`). It is idempotent
(a resumed or repeated run deletes what is left), calls no model and runs whatever the autonomy
pause says, and whenever it runs: lateness is what it handles. Its output is a count, never
content, and it emits no entity events (expired items are already hidden from every list).
Started only by the schedule (``sched-engagement-purge``, every 6 hours).
"""

from __future__ import annotations

import logging
from typing import Final

from pydantic import JsonValue

from blogwriter.core.logging import register_log_fields
from blogwriter.domain.jobflow import step_services
from blogwriter.domain.jobtypes import ENGAGEMENT_PURGE
from blogwriter.engagement.helpers import purge_expired
from blogwriter.jobs import JobRegistry, Step, StepContext

__all__ = ["PURGE_STEP", "purge_step", "register_jobs"]

PURGE_STEP: Final = "purge"

_logger: Final = logging.getLogger("blogwriter.engagement")
register_log_fields("job_id", "deleted_count")


async def purge_step(ctx: StepContext) -> JsonValue:
    """Delete the expired items; returns ``{"deleted": n}``."""
    services = step_services()
    deleted = await purge_expired(services.store, now=services.clock.now())
    _logger.info("engagement_purged", extra={"job_id": ctx.job_id, "deleted_count": deleted})
    return {"deleted": deleted}


def register_jobs(registry: JobRegistry) -> None:
    """Register ``engagement.purge`` (step ``purge``)."""
    registry.register(ENGAGEMENT_PURGE, [Step(PURGE_STEP, purge_step)])
