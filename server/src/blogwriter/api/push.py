"""Web push routes of Settings > Integrations (contract I-2).

::

    GET    /api/push/key                   {applicationServerKey}  (the VAPID public key)
    POST   /api/push/subscriptions         201 {id, active, createdAt}
                                           body = the browser's PushSubscription.toJSON()
    DELETE /api/push/subscriptions/{id}    204 (404 when unknown)

Push is configured by ``BW_NOTIFY_VAPID_*`` (``CoreServicesLifecycle`` puts the
:class:`~blogwriter.services.PushNotifier` on ``app.state.push``); without it every route
answers 503 ``push_unavailable``. Only endpoints on the known push services are accepted
(``notify.subscriptions.PUSH_SERVICE_HOSTS``), and the endpoint, a capability, is never echoed
back or logged: the subscription is known by its derived id.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from blogwriter.core.errors import ProblemError
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import to_iso
from blogwriter.domain.deps import ClockDep, problems
from blogwriter.notify.subscriptions import MAX_ENDPOINT_CHARS, PushSubscription
from blogwriter.services.notifier import PushNotifier

__all__ = [
    "PushKeyView",
    "PushKeysIn",
    "PushSubscriptionIn",
    "PushSubscriptionView",
    "add_routes",
    "get_router",
]


async def _push(request: Request) -> PushNotifier:
    push: object = getattr(request.app.state, "push", None)
    if not isinstance(push, PushNotifier):
        raise ProblemError(
            503,
            code="push_unavailable",
            detail="Push notifications are not configured on this server.",
            headers={"Retry-After": "5"},
        )
    return push


PushDep = Annotated[PushNotifier, Depends(_push)]


class PushKeyView(ApiModel):
    application_server_key: str


class PushKeysIn(ApiModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscriptionIn(ApiModel):
    """``PushSubscription.toJSON()``: ``{endpoint, expirationTime, keys: {p256dh, auth}}``."""

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_CHARS)
    expiration_time: float | None = None
    keys: PushKeysIn


class PushSubscriptionView(ApiModel):
    id: str
    active: bool
    created_at: str


async def push_key(push: PushDep) -> PushKeyView:
    return PushKeyView(application_server_key=push.application_server_key)


async def subscribe(
    body: PushSubscriptionIn, push: PushDep, clock: ClockDep
) -> PushSubscriptionView:
    """Store this browser's subscription (subscribing again re-activates the same record)."""
    try:
        keys = {"p256dh": body.keys.p256dh, "auth": body.keys.auth}
        subscription = PushSubscription.from_browser(
            {"endpoint": body.endpoint, "keys": keys}, now=clock.now()
        )
    except ValueError:
        raise ProblemError(
            422,
            code="invalid_subscription",
            detail="This push subscription is not from a supported push service or is malformed.",
        ) from None
    with problems():
        await push.subscriptions.upsert(subscription)
    return PushSubscriptionView(
        id=subscription.id, active=True, created_at=to_iso(subscription.created_at)
    )


async def unsubscribe(subscription_id: str, push: PushDep) -> Response:
    with problems():
        deleted = await push.subscriptions.delete(subscription_id)
    if not deleted:
        raise ProblemError(404, code="not_found")
    return Response(status_code=204)


def add_routes(router: APIRouter) -> None:
    """Add this module's routes to ``router`` (the system router mounts them directly, so the
    app factory's duplicate-route check sees every path)."""
    router.add_api_route(
        "/api/push/key",
        push_key,
        methods=["GET"],
        response_model=PushKeyView,
        summary="The VAPID public key for PushManager.subscribe",
    )
    router.add_api_route(
        "/api/push/subscriptions",
        subscribe,
        methods=["POST"],
        status_code=201,
        response_model=PushSubscriptionView,
        summary="Subscribe this browser to push notifications",
    )
    router.add_api_route(
        "/api/push/subscriptions/{subscription_id}",
        unsubscribe,
        methods=["DELETE"],
        status_code=204,
        response_class=Response,
        summary="Remove a push subscription",
    )


def get_router() -> APIRouter:
    router = APIRouter(tags=["system"])
    add_routes(router)
    return router
