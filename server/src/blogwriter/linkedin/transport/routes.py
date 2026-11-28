"""HTTP routes of the LinkedIn transport (contract W-A9). Owner-only; every non-GET passed CSRF.

| Method | Path | |
|---|---|---|
| GET | ``/api/linkedin/connection`` | :class:`~.tokens.ConnectionView` (never the token) |
| POST | ``/api/linkedin/oauth/start`` | ``{authorizeUrl}``; the SPA navigates top-level |
| GET | ``/api/linkedin/oauth/callback`` | always ``303 Location: /settings/integrations`` |
| POST | ``/api/linkedin/disconnect``, ``/api/linkedin/introspect`` | ``ConnectionView`` |
| GET | ``/api/posts/{id}/go-live`` | :class:`GoLiveView` (no side effects) |
| POST | ``/api/posts/{id}/publish`` | the one route that reaches the publisher |
| POST | ``/api/posts/{id}/mode``, ``/kit``, ``/resolve`` | kit and go-live states |
| GET | ``/api/posts/{id}/kit`` | :class:`~.kit.KitView` |
| PATCH | ``/api/posts/{id}/linkedin`` | edit the live post's text |
| POST | ``/api/posts/{id}/linkedin/delete`` | delete the live post |

The callback is the one GET that changes state (the OAuth protocol): it consumes its own
single-use state and nothing else, reflects nothing from the request and never fails visibly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Final, Literal, Self, cast

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AwareDatetime, Field, field_validator, model_validator

from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.settings import settings_from_request
from blogwriter.db import Store
from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.base import HttpsUrl, to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.deps import IfMatch, ServicesDep, VaultDep, problems, versioned
from blogwriter.domain.docstore import read_doc, read_singleton
from blogwriter.domain.golive import enqueue_slot_jobs
from blogwriter.domain.ports import Services
from blogwriter.domain.posts import GoLiveMode, Post
from blogwriter.domain.settings import CadenceSettings, LinkedInConnection
from blogwriter.domain.states import ConnectionStatus, KitState, PostStatus
from blogwriter.linkedin.lint import fold_index
from blogwriter.linkedin.settings import CALLBACK_PATH
from blogwriter.linkedin.transport.client import LinkedInError, current_client
from blogwriter.linkedin.transport.content import (
    content_problem,
    is_linkedin_url,
    linkedin_media,
)
from blogwriter.linkedin.transport.deps import (
    CoreSettingsDep,
    LinkedInClientDep,
    LinkedInSettingsDep,
    get_linkedin_settings,
    not_configured,
)
from blogwriter.linkedin.transport.hooks import enqueue_post_live, enqueue_post_scheduled
from blogwriter.linkedin.transport.kit import (
    KitView,
    kit_transition,
    kit_view,
    resolve_attention,
    set_go_live_mode,
)
from blogwriter.linkedin.transport.live import (
    LiveActionRefused,
    delete_live_post,
    edit_live_post,
)
from blogwriter.linkedin.transport.oauth import (
    RETURN_PATH,
    CallbackParams,
    complete_oauth,
    start_oauth,
)
from blogwriter.linkedin.transport.publisher import (
    PUBLISHABLE_STATUSES,
    OwnerClick,
    confirm_key,
    issue_confirm_token,
    owner_click,
    publish_post,
    reconnect_problem,
    verify_confirm_token,
)
from blogwriter.linkedin.transport.tokens import (
    ConnectionView,
    ReconnectRequired,
    connection_usable,
    connection_view,
    disconnect,
    effective_status,
    introspect_connection,
    read_connection,
)

__all__ = [
    "ConnectionBrief",
    "GoLiveView",
    "KitIn",
    "LiveDeleteIn",
    "LiveEditIn",
    "ModeIn",
    "OAuthStartOut",
    "ResolveIn",
    "get_router",
    "publish_blockers",
]

_logger: Final = logging.getLogger("blogwriter.linkedin.routes")

router = APIRouter(prefix="/api", tags=["linkedin"])


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------


class OAuthStartOut(ApiModel):
    authorize_url: str


class ConnectionBrief(ApiModel):
    status: ConnectionStatus
    token_day: int | None = None


class GoLiveView(ApiModel):
    """``GET /api/posts/{id}/go-live``: the exact final render's ingredients and the tap's
    preconditions. ``confirmToken`` is set whenever the post has an approval (publish, and edit
    or delete once it is live)."""

    post: Versioned[Post]
    fold_index: int
    cover: MediaRef | None = None
    document_title: str | None = None
    alt_texts: list[str | None]
    approval_sha256: str | None = None
    approval_current: bool
    connection: ConnectionBrief
    confirm_token: str | None = None
    confirm_expires_at: str | None = None
    can_publish: bool
    reasons: list[str]


class ModeIn(ApiModel):
    mode: GoLiveMode


def _linkedin_url(value: str | None) -> str | None:
    if value is not None and not is_linkedin_url(value):
        raise ValueError("must be the post's https://www.linkedin.com/... link")
    return value


class KitIn(ApiModel):
    """``natively_scheduled`` needs ``scheduledAt`` (again: the new time); ``posted`` needs
    ``postUrl`` and ``finalText`` (the web prefills it with the display text)."""

    to: Literal["native_draft_saved", "natively_scheduled", "posted"]
    scheduled_at: AwareDatetime | None = None
    post_url: HttpsUrl | None = None
    final_text: str | None = Field(default=None, min_length=1, max_length=6000)

    @field_validator("post_url")
    @classmethod
    def _check_post_url(cls, value: str | None) -> str | None:
        return _linkedin_url(value)

    @model_validator(mode="after")
    def _check_needs(self) -> Self:
        if self.to == "natively_scheduled" and self.scheduled_at is None:
            raise ValueError("natively_scheduled needs scheduledAt")
        if self.to == "posted" and (self.post_url is None or self.final_text is None):
            raise ValueError("posted needs postUrl and finalText")
        return self


class ResolveIn(ApiModel):
    outcome: Literal["posted", "not_posted"]
    post_url: HttpsUrl | None = None

    @field_validator("post_url")
    @classmethod
    def _check_post_url(cls, value: str | None) -> str | None:
        return _linkedin_url(value)


class LiveEditIn(ApiModel):
    display_text: str = Field(min_length=1, max_length=6000)
    confirm_token: str = Field(min_length=1, max_length=200)


class LiveDeleteIn(ApiModel):
    confirm_token: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def publish_blockers(
    post: Post, conn: LinkedInConnection, now: datetime, *, vault_configured: bool
) -> list[str]:
    """Why the tap would be refused now (empty: it may go ahead). Media that is not on
    LinkedIn yet is not a blocker: the tap uploads it first."""
    reasons: list[str] = []
    if post.status not in PUBLISHABLE_STATUSES:
        reasons.append("not_approved")
    if post.approval is None or not post.approval_current:
        reasons.append("approval_stale")
    if post.expires_at is not None and post.expires_at <= to_iso(now):
        reasons.append("post_expired")
    problem = content_problem(post)
    if problem is not None:
        reasons.append(problem)
    if not vault_configured:
        reasons.append("vault_unavailable")
    if not connection_usable(conn, now):
        reasons.append("reconnect_required")
    return reasons


def _cover(post: Post) -> MediaRef | None:
    for item in post.media:
        if item.role == "carousel_cover":
            return item
    for item in linkedin_media(post):
        if item.content_type.startswith("image/"):
            return item
    return None


def _linkedin_problem(exc: LinkedInError) -> ProblemError:
    if exc.transient:
        return ProblemError(
            503,
            code="linkedin_unavailable",
            detail="LinkedIn did not answer. Try again in a minute.",
            headers={"Retry-After": "30"},
        )
    return ProblemError(
        409,
        code="linkedin_rejected",
        detail="LinkedIn refused the change.",
        extensions={"reason": exc.code},
    )


def _live_refused(exc: LiveActionRefused) -> ProblemError:
    extensions: dict[str, JsonValue] | None = None
    if exc.findings:
        extensions = {
            "findings": [
                cast(JsonValue, finding.model_dump(mode="json", exclude_none=True))
                for finding in exc.findings
            ]
        }
    return ProblemError(409, code=exc.code, detail=exc.detail, extensions=extensions)


def _check_live_click(
    post: Post, token: str, *, now: datetime, key: bytes, ttl_seconds: int
) -> None:
    approval = post.approval
    if approval is None or not verify_confirm_token(
        token, post.id, approval.sha256, now=now, key=key, ttl_seconds=ttl_seconds
    ):
        raise ProblemError(
            409,
            code="confirm_invalid",
            detail="This page is out of date. Reload it and try again.",
        )


# --------------------------------------------------------------------------------------------
# The connection and OAuth
# --------------------------------------------------------------------------------------------


@router.get("/linkedin/connection")
async def get_connection(services: ServicesDep, settings: LinkedInSettingsDep) -> ConnectionView:
    with problems():
        now = services.clock.now()
        conn = await read_connection(services.store, now)
    return connection_view(conn, now, configured=settings.configured)


@router.post("/linkedin/oauth/start")
async def post_oauth_start(
    services: ServicesDep,
    settings: LinkedInSettingsDep,
    core: CoreSettingsDep,
    _vault: VaultDep,
) -> OAuthStartOut:
    redirect_uri = settings.resolved_redirect_uri(core.public_origin)
    if not settings.configured or settings.client_id is None or redirect_uri is None:
        raise not_configured()
    with problems():
        url = await start_oauth(
            services.store,
            client_id=settings.client_id,
            redirect_uri=redirect_uri,
            now=services.clock.now(),
        )
    return OAuthStartOut(authorize_url=url)


@router.get(
    CALLBACK_PATH.removeprefix("/api"),
    status_code=303,
    response_class=Response,
    responses={303: {"description": "Always redirects to /settings/integrations"}},
)
async def get_oauth_callback(request: Request) -> Response:
    """LinkedIn's redirect back. Reflects nothing; failures land in ``connection.lastError``."""
    try:
        query = request.query_params
        params = CallbackParams.parse(
            query.getlist("code"), query.getlist("state"), query.getlist("error")
        )
        services = cast(Services | None, getattr(request.app.state, "services", None))
        if services is not None:
            settings = await get_linkedin_settings(request)
            core = await settings_from_request(request)
            await complete_oauth(
                services,
                current_client(),
                params,
                redirect_uri=settings.resolved_redirect_uri(core.public_origin),
                now=services.clock.now(),
            )
        else:
            _logger.warning("linkedin_oauth_callback_failed", extra={"reason": "no_services"})
    except Exception as exc:  # the callback always redirects; the reason is in the log
        _logger.warning("linkedin_oauth_callback_failed", extra={"error_type": type(exc).__name__})
    return Response(status_code=303, headers={"Location": RETURN_PATH})


@router.post("/linkedin/disconnect")
async def post_disconnect(services: ServicesDep, settings: LinkedInSettingsDep) -> ConnectionView:
    with problems():
        now = services.clock.now()
        conn = await disconnect(services.store, now=now)
    return connection_view(conn, now, configured=settings.configured)


@router.post("/linkedin/introspect")
async def post_introspect(
    services: ServicesDep, settings: LinkedInSettingsDep, client: LinkedInClientDep
) -> ConnectionView:
    if not settings.configured:
        raise not_configured()
    with problems():
        now = services.clock.now()
        result = await introspect_connection(services, client, now=now)
    return connection_view(result.connection, now, configured=True)


# --------------------------------------------------------------------------------------------
# Go-live and publishing
# --------------------------------------------------------------------------------------------


@router.get("/posts/{post_id}/go-live")
async def get_go_live(
    post_id: str,
    services: ServicesDep,
    core: CoreSettingsDep,
    settings: LinkedInSettingsDep,
    response: Response,
) -> GoLiveView:
    with problems():
        store: Store = services.store
        now = services.clock.now()
        stored = await read_doc(store, Post, post_id)
        conn = await read_connection(store, now)
    post = stored.doc
    token: str | None = None
    expires_at: str | None = None
    if post.approval is not None:
        token, expires_at = issue_confirm_token(
            post.id,
            post.approval.sha256,
            now=now,
            key=confirm_key(core),
            ttl_seconds=settings.confirm_token_ttl_seconds,
        )
    status = effective_status(conn, now)
    reasons = publish_blockers(post, conn, now, vault_configured=services.vault is not None)
    media = linkedin_media(post)
    return GoLiveView(
        post=versioned(stored, response),
        fold_index=fold_index(post.display_text),
        cover=_cover(post),
        document_title=post.document_title,
        alt_texts=[item.alt_text for item in media],
        approval_sha256=post.approval.sha256 if post.approval is not None else None,
        approval_current=post.approval_current,
        connection=ConnectionBrief(
            status=status,
            token_day=conn.token_day(now) if status is not ConnectionStatus.DISCONNECTED else None,
        ),
        confirm_token=token,
        confirm_expires_at=expires_at,
        can_publish=not reasons,
        reasons=reasons,
    )


@router.post("/posts/{post_id}/publish")
async def post_publish(
    post_id: str,
    if_match: IfMatch,
    click: Annotated[OwnerClick, Depends(owner_click)],
    services: ServicesDep,
    client: LinkedInClientDep,
    response: Response,
) -> Versioned[Post]:
    """The owner's tap: one attempt; read ``status`` in the answer."""
    with problems():
        stored = await publish_post(
            services, post_id, click=click, if_match=if_match, client=client
        )
    return versioned(stored, response)


@router.post("/posts/{post_id}/mode")
async def post_mode(
    post_id: str, body: ModeIn, services: ServicesDep, if_match: IfMatch, response: Response
) -> Versioned[Post]:
    now = services.clock.now()
    with problems():
        stored = await set_go_live_mode(
            services.store, post_id, body.mode, if_match=if_match, now=now
        )
        post = stored.doc
        if body.mode == "one_click" and post.status is PostStatus.APPROVED:
            # Back from the kit: the slot needs its staging and slot check again.
            cadence, _ = await read_singleton(services.store, CadenceSettings, now=to_iso(now))
            await enqueue_slot_jobs(services.jobs, post, cadence=cadence)
    return versioned(stored, response)


@router.get("/posts/{post_id}/kit")
async def get_kit(post_id: str, services: ServicesDep) -> KitView:
    with problems():
        stored = await read_doc(services.store, Post, post_id)
    return kit_view(stored.doc)


@router.post("/posts/{post_id}/kit")
async def post_kit(
    post_id: str, body: KitIn, services: ServicesDep, if_match: IfMatch, response: Response
) -> Versioned[Post]:
    scheduled = to_iso(body.scheduled_at) if body.scheduled_at is not None else None
    with problems():
        stored = await kit_transition(
            services.store,
            post_id,
            KitState(body.to),
            if_match=if_match,
            now=services.clock.now(),
            scheduled_at=scheduled,
            post_url=body.post_url,
            final_text=body.final_text,
        )
    post = stored.doc
    if body.to == "natively_scheduled" and scheduled is not None:
        await enqueue_post_scheduled(services, post_id, scheduled)
    elif body.to == "posted" and post.status is PostStatus.POSTED and post.posted_at is not None:
        await enqueue_post_live(services, post_id, post.posted_at)
    return versioned(stored, response)


@router.post("/posts/{post_id}/resolve")
async def post_resolve(
    post_id: str, body: ResolveIn, services: ServicesDep, if_match: IfMatch, response: Response
) -> Versioned[Post]:
    with problems():
        stored = await resolve_attention(
            services.store,
            post_id,
            body.outcome,
            if_match=if_match,
            now=services.clock.now(),
            post_url=body.post_url,
        )
    post = stored.doc
    if post.status is PostStatus.POSTED and post.posted_at is not None:
        await enqueue_post_live(services, post_id, post.posted_at)
    return versioned(stored, response)


@router.patch("/posts/{post_id}/linkedin")
async def patch_live_post(
    post_id: str,
    body: LiveEditIn,
    services: ServicesDep,
    core: CoreSettingsDep,
    settings: LinkedInSettingsDep,
    client: LinkedInClientDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[Post]:
    """Edit a live post's text (lint must pass; one ``PARTIAL_UPDATE``)."""
    with problems():
        stored = await read_doc(services.store, Post, post_id)
        _check_live_click(
            stored.doc,
            body.confirm_token,
            now=services.clock.now(),
            key=confirm_key(core),
            ttl_seconds=settings.confirm_token_ttl_seconds,
        )
        try:
            updated = await edit_live_post(
                services, client, post_id, body.display_text, if_match=if_match
            )
        except LiveActionRefused as exc:
            raise _live_refused(exc) from None
        except ReconnectRequired as exc:
            raise reconnect_problem(exc) from None
        except LinkedInError as exc:
            raise _linkedin_problem(exc) from None
    return versioned(updated, response)


@router.post("/posts/{post_id}/linkedin/delete")
async def post_delete_live_post(
    post_id: str,
    body: LiveDeleteIn,
    services: ServicesDep,
    core: CoreSettingsDep,
    settings: LinkedInSettingsDep,
    client: LinkedInClientDep,
    if_match: IfMatch,
    response: Response,
) -> Versioned[Post]:
    """Delete a live post on LinkedIn; the post becomes ``deleted``."""
    with problems():
        stored = await read_doc(services.store, Post, post_id)
        _check_live_click(
            stored.doc,
            body.confirm_token,
            now=services.clock.now(),
            key=confirm_key(core),
            ttl_seconds=settings.confirm_token_ttl_seconds,
        )
        try:
            updated = await delete_live_post(services, client, post_id, if_match=if_match)
        except LiveActionRefused as exc:
            raise _live_refused(exc) from None
        except ReconnectRequired as exc:
            raise reconnect_problem(exc) from None
        except LinkedInError as exc:
            raise _linkedin_problem(exc) from None
    return versioned(updated, response)


def get_router() -> APIRouter:
    """Every transport route (full paths under ``/api``)."""
    return router
