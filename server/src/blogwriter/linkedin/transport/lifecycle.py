"""App wiring of the LinkedIn transport (:class:`TransportLifecycle`) and the stale-intent sweep.

``startup``:

- reads ``BW_LINKEDIN_*`` (a malformed value stops the boot) into ``app.state.linkedin_settings``;
- installs the process-wide :class:`~blogwriter.linkedin.transport.client.LinkedInClient` that
  routes and job steps use (it works without credentials; OAuth calls refuse then);
- **sweeps stale intents**: a post still ``publishing`` with an intent older than
  :data:`STALE_INTENT_AGE` belongs to an attempt whose process died between the tap and the
  answer (a restart with an intent but no URN). It becomes ``needs_attention`` (intent
  ``unknown``), with an out-of-band alert and a push: LinkedIn may have the post, and only the
  owner can check. A tap never stays ``publishing`` that long (one 30 s attempt), so a live
  attempt on another replica during a revision overlap is left alone. The sweep needs only the
  store; a failure is logged and the boot goes on (the daily ``linkedin.introspect`` job sweeps
  again).

``shutdown`` uninstalls and closes the client it installed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Final, cast

from fastapi import FastAPI

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs, update_doc
from blogwriter.domain.ports import Notifier
from blogwriter.domain.posts import Post, settle_publish_intent
from blogwriter.domain.states import PostStatus
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock
from blogwriter.linkedin.settings import LinkedInSettings, linkedin_settings
from blogwriter.linkedin.transport.client import LinkedInClient, install_client, uninstall_client
from blogwriter.linkedin.transport.tokens import notify_owner
from blogwriter.notify import emit_alert

__all__ = [
    "STALE_INTENT_AGE",
    "TransportLifecycle",
    "shutdown",
    "startup",
    "sweep_stale_intents",
]

STALE_INTENT_AGE: Final = timedelta(minutes=2)
INTERRUPTED: Final = "publish_interrupted"

_logger: Final = logging.getLogger("blogwriter.linkedin.lifecycle")
register_log_fields("post_id", "count")


def _go_live_path(post: Post) -> str:
    return f"/studio/{post.article_id or post.id}/go-live?post={post.id}"


async def sweep_stale_intents(
    store: Store, *, now: datetime, notifier: Notifier | None = None
) -> list[str]:
    """Settle every ``publishing`` post whose intent is older than :data:`STALE_INTENT_AGE` as
    ``unknown`` (``needs_attention``); returns their ids. Never publishes anything."""
    cutoff = to_iso(now - STALE_INTENT_AGE)
    stamp = to_iso(now)
    found = await find_docs(store, Post, [Filter("status", "==", PostStatus.PUBLISHING.value)])
    swept: list[str] = []
    for stored in found:
        intent = stored.doc.intent
        if intent is None or intent.status != "pending" or intent.created_at > cutoff:
            continue
        intent_id = intent.id

        def mutate(post: Post, intent_id: str = intent_id) -> Post | None:
            current = post.intent
            if post.status is not PostStatus.PUBLISHING or current is None:
                return None
            if current.id != intent_id:
                return None
            return settle_publish_intent(post, "unknown", now=stamp, error_code=INTERRUPTED)

        try:
            updated = await update_doc(store, Post, stored.doc.id, mutate)
        except Exception as exc:  # one post must not stop the sweep (or the boot)
            _logger.warning(
                "linkedin_sweep_failed",
                extra={"post_id": stored.doc.id, "error_type": type(exc).__name__},
            )
            continue
        if updated.doc.status is not PostStatus.NEEDS_ATTENTION:
            continue
        swept.append(updated.doc.id)
        emit_alert("needs_attention", updated.doc.id)
        await notify_owner(
            notifier,
            title="Check LinkedIn: did your post go out?",
            url=_go_live_path(updated.doc),
            tag=f"publish:{updated.doc.id}",
        )
    if swept:
        _logger.warning("linkedin_intents_swept", extra={"count": len(swept)})
    return swept


class TransportLifecycle:
    """Settings, the shared client and the startup sweep (see the module docs)."""

    def __init__(
        self, settings: LinkedInSettings | None = None, *, client: LinkedInClient | None = None
    ) -> None:
        self._settings = settings
        self._client = client
        self._installed: LinkedInClient | None = None
        self._owned = False

    async def startup(self, app: FastAPI) -> None:
        settings = self._settings if self._settings is not None else linkedin_settings()
        app.state.linkedin_settings = settings
        client = self._client
        self._owned = client is None
        if client is None:
            client = LinkedInClient(settings)
        install_client(client)
        self._installed = client
        if not settings.configured:
            _logger.warning("linkedin_not_configured")
        store = cast(Store | None, getattr(app.state, "store", None))
        if store is None:
            _logger.warning("linkedin_sweep_skipped", extra={"reason": "store_unavailable"})
            return
        clock = cast(Clock | None, getattr(app.state, "clock", None)) or SYSTEM_CLOCK
        notifier = cast(Notifier | None, getattr(app.state, "notifier", None))
        try:
            await sweep_stale_intents(store, now=clock.now(), notifier=notifier)
        except Exception as exc:
            _logger.warning("linkedin_sweep_failed", extra={"error_type": type(exc).__name__})

    async def shutdown(self, app: FastAPI) -> None:
        client = self._installed
        self._installed = None
        if client is None:
            return
        uninstall_client(client)
        if self._owned:
            await client.aclose()


_default = TransportLifecycle()


async def startup(app: FastAPI) -> None:
    """:meth:`TransportLifecycle.startup` with ``BW_LINKEDIN_*`` settings."""
    await _default.startup(app)


async def shutdown(app: FastAPI) -> None:
    await _default.shutdown(app)
