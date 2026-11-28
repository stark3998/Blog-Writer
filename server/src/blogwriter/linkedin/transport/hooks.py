"""Jobs the transport starts in another package (registered by planner): ``planner.post-live``
when a post goes live, ``planner.post-scheduled`` when the owner schedules it natively.

A hook never undoes what already happened on LinkedIn: an unregistered type (planner not
shipped yet) logs ``job_hook_unregistered``, any other failure ``job_hook_failed``, and the
caller carries on. Because the hook is best effort (a crash or a store error right after the
post went live loses it), :func:`ensure_followups` re-derives both from the posts themselves:
the daily ``linkedin.introspect`` sweep enqueues them again for every post that went live in the
reminder window and every natively scheduled kit post (deterministic ids: an existing job is
reused, so nothing runs twice).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Final

from pydantic import JsonValue

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import (
    PLANNER_POST_LIVE,
    PLANNER_POST_SCHEDULED,
    PostLiveInput,
    PostScheduledInput,
    post_live_job_id,
    post_scheduled_job_id,
)
from blogwriter.domain.ports import Services
from blogwriter.domain.posts import Post
from blogwriter.domain.states import KitState, PostStatus

__all__ = ["FOLLOWUP_WINDOW", "enqueue_post_live", "enqueue_post_scheduled", "ensure_followups"]

FOLLOWUP_WINDOW: Final = timedelta(days=10)
"""Posts that went live this recently still have reminders to create (the +7 d snapshot is due
until +10 d)."""

_logger: Final = logging.getLogger("blogwriter.linkedin.hooks")
register_log_fields("job_type", "post_id")


async def _enqueue(services: Services, job_type: str, job_id: str, payload: JsonValue) -> bool:
    try:
        await enqueue_once(services.jobs, job_type, payload, job_id=job_id)
    except ValueError:
        _logger.warning("job_hook_unregistered", extra={"job_type": job_type})
        return False
    except Exception as exc:  # the post is live either way; the hook is secondary
        _logger.warning(
            "job_hook_failed", extra={"job_type": job_type, "error_type": type(exc).__name__}
        )
        return False
    return True


async def enqueue_post_live(services: Services, post_id: str, go_live_at: str) -> bool:
    """``planner.post-live`` (reply reminders, snapshots) for a post that went live."""
    payload = PostLiveInput(post_id=post_id, go_live_at=go_live_at, origin="owner").to_json()
    return await _enqueue(services, PLANNER_POST_LIVE, post_live_job_id(post_id), payload)


async def enqueue_post_scheduled(services: Services, post_id: str, scheduled_at: str) -> bool:
    """``planner.post-scheduled`` for a kit post the owner scheduled natively at ``T``."""
    payload = PostScheduledInput(
        post_id=post_id, scheduled_at=scheduled_at, origin="owner"
    ).to_json()
    return await _enqueue(
        services, PLANNER_POST_SCHEDULED, post_scheduled_job_id(post_id, scheduled_at), payload
    )


async def ensure_followups(services: Services, *, now: datetime) -> int:
    """Enqueue ``planner.post-live`` for every post that went live since
    :data:`FOLLOWUP_WINDOW` and ``planner.post-scheduled`` for every natively scheduled kit post,
    again (``reuse``: what exists is kept). Returns how many posts it covered."""
    live = await find_docs(
        services.store,
        Post,
        [
            Filter("status", "==", PostStatus.POSTED.value),
            Filter("postedAt", ">=", to_iso(now - FOLLOWUP_WINDOW)),
        ],
    )
    covered = 0
    for stored in live:
        post = stored.doc
        if post.posted_at is not None and await enqueue_post_live(
            services, post.id, post.posted_at
        ):
            covered += 1
    kits = await find_docs(services.store, Post, [Filter("status", "==", PostStatus.KIT.value)])
    for stored in kits:
        kit = stored.doc.kit
        if kit is None or kit.state is not KitState.NATIVELY_SCHEDULED or not kit.scheduled_at:
            continue
        if await enqueue_post_scheduled(services, stored.doc.id, kit.scheduled_at):
            covered += 1
    return covered
