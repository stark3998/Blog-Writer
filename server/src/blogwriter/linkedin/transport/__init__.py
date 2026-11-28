"""LinkedIn transport (contract W-A9): OAuth, the encrypted token, the REST client, media
uploads, the publisher, the ready-to-post kit, live edits and the token lifecycle.

Public Python API (for linkedin posts and planner)::

    from blogwriter.linkedin.transport import (
        LinkedInClient, CreateOutcome, ReconnectRequired, access_token,
        upload_post_media, post_body, kit_transition, post_url_for,
        introspect_connection, current_client,   # T-60 staging token check
    )

The publisher (:mod:`blogwriter.linkedin.transport.publisher`) is deliberately **not**
re-exported, and importing this package does not load it: only the publish route reaches it.

Wiring (``blogwriter.main``): ``get_router()``, ``register_jobs(registry)``,
``schedule_entries`` (``sched-linkedin-introspect``) and :class:`TransportLifecycle` (after
``db`` and the core services, before the jobs lifecycle). Settings: ``BW_LINKEDIN_*``
(:class:`blogwriter.linkedin.settings.LinkedInSettings`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blogwriter.linkedin.transport.client import (
    CreateOutcome,
    Introspection,
    LinkedInClient,
    LinkedInError,
    MediaKind,
    TokenGrant,
    client_installed,
    current_client,
    install_client,
    uninstall_client,
)
from blogwriter.linkedin.transport.content import (
    PostBodyError,
    linkedin_media,
    post_body,
    post_url_for,
)
from blogwriter.linkedin.transport.jobs import register_jobs
from blogwriter.linkedin.transport.kit import kit_transition, resolve_attention, set_go_live_mode
from blogwriter.linkedin.transport.lifecycle import TransportLifecycle, sweep_stale_intents
from blogwriter.linkedin.transport.schedule import schedule_entries
from blogwriter.linkedin.transport.tokens import (
    ConnectionView,
    IntrospectResult,
    ReconnectRequired,
    access_token,
    connection_usable,
    introspect_connection,
)
from blogwriter.linkedin.transport.upload import UploadFailed, upload_post_media

if TYPE_CHECKING:
    from fastapi import APIRouter

__all__ = [
    "ConnectionView",
    "CreateOutcome",
    "IntrospectResult",
    "Introspection",
    "LinkedInClient",
    "LinkedInError",
    "MediaKind",
    "PostBodyError",
    "ReconnectRequired",
    "TokenGrant",
    "TransportLifecycle",
    "UploadFailed",
    "access_token",
    "client_installed",
    "connection_usable",
    "current_client",
    "get_router",
    "install_client",
    "introspect_connection",
    "kit_transition",
    "linkedin_media",
    "post_body",
    "post_url_for",
    "register_jobs",
    "resolve_attention",
    "schedule_entries",
    "set_go_live_mode",
    "sweep_stale_intents",
    "uninstall_client",
    "upload_post_media",
]


def get_router() -> APIRouter:
    """The transport routes. Imported lazily: loading this package never loads the publisher,
    which only the publish route uses."""
    from blogwriter.linkedin.transport.routes import get_router as routes_router

    return routes_router()
