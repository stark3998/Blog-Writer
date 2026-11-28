"""Push routes (contract I-2): the VAPID key, subscribe and unsubscribe.

Plan: "Security model" (push carries a title and a same-origin path only; subscription
endpoints are capabilities, never echoed or logged) and section 12 (push on the phone needs the
VAPID keys plus a subscription).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.api.push import get_router
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.notify.settings import load_vapid_private_key
from blogwriter.notify.subscriptions import subscription_id_for
from blogwriter.notify.webpush import WebPushNotifier
from blogwriter.services.notifier import PushNotifier
from blogwriter.services.push_repo import CosmosSubscriptionRepo
from blogwriter.services.testing import RecordingPush, browser_subscription, vapid_private_key_b64

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def push_notifier(services: FakeServices) -> PushNotifier:
    repo = CosmosSubscriptionRepo(services.store, clock=lambda: T0)
    sender = WebPushNotifier(
        repo=repo,
        vapid_private_key=load_vapid_private_key(vapid_private_key_b64()),
        vapid_subject="mailto:owner@example.com",
        webpush=RecordingPush(),
    )
    return PushNotifier(sender, repo)


@asynccontextmanager
async def client(
    services: FakeServices, *, configured: bool = True
) -> AsyncGenerator[tuple[httpx.AsyncClient, PushNotifier | None]]:
    app = make_app(services, [get_router()], settings=SETTINGS)
    push = push_notifier(services) if configured else None
    app.state.push = push
    async with asgi_client(app) as http:
        yield http, push


async def test_the_key_is_the_vapid_public_point() -> None:
    async with client(make_services()) as (http, push):
        response = await http.get("/api/push/key", headers=READ)
    assert response.status_code == 200
    assert push is not None
    key = response.json()["applicationServerKey"]
    assert key == push.application_server_key
    point = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    assert len(point) == 65
    assert point[0] == 0x04


async def test_a_browser_subscribes_and_unsubscribes() -> None:
    services = make_services()
    subscription = browser_subscription("phone")
    async with client(services) as (http, push):
        created = await http.post("/api/push/subscriptions", json=subscription, headers=WRITE)
        again = await http.post("/api/push/subscriptions", json=subscription, headers=WRITE)
        assert push is not None
        active = await push.subscriptions.list_active()
        subscription_id = created.json()["id"]
        deleted = await http.delete(f"/api/push/subscriptions/{subscription_id}", headers=WRITE)
        gone = await http.delete(f"/api/push/subscriptions/{subscription_id}", headers=WRITE)
    assert created.status_code == 201
    body = created.json()
    assert body == {"id": subscription_id, "active": True, "createdAt": body["createdAt"]}
    assert subscription_id == subscription_id_for(str(subscription["endpoint"]))
    assert "endpoint" not in json.dumps(body)
    assert again.json()["id"] == subscription_id
    assert [item.id for item in active] == [subscription_id]
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert gone.status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint": "https://evil.example/push/1"},
        {"endpoint": "http://fcm.googleapis.com/fcm/send/1"},
        {"keys": {"p256dh": "AAAA", "auth": "AAAA"}},
    ],
)
async def test_subscriptions_off_the_known_push_services_are_refused(
    change: dict[str, object],
) -> None:
    services = make_services()
    body = {**browser_subscription(), **change}
    async with client(services) as (http, push):
        response = await http.post("/api/push/subscriptions", json=body, headers=WRITE)
        assert push is not None
        assert await push.subscriptions.list_active() == []
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "invalid_subscription"
    assert "evil.example" not in json.dumps(problem)


async def test_unknown_members_are_refused() -> None:
    body = {**browser_subscription(), "extra": True}
    async with client(make_services()) as (http, _push):
        response = await http.post("/api/push/subscriptions", json=body, headers=WRITE)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_without_vapid_keys_every_push_route_is_503() -> None:
    async with client(make_services(), configured=False) as (http, _push):
        key = await http.get("/api/push/key", headers=READ)
        sub = await http.post("/api/push/subscriptions", json=browser_subscription(), headers=WRITE)
        delete = await http.delete(f"/api/push/subscriptions/{'a' * 32}", headers=WRITE)
    for response in (key, sub, delete):
        assert response.status_code == 503
        assert response.json()["code"] == "push_unavailable"
        assert response.headers["retry-after"] == "5"


async def test_writes_need_csrf() -> None:
    async with client(make_services()) as (http, push):
        response = await http.post(
            "/api/push/subscriptions", json=browser_subscription(), headers=READ
        )
        assert push is not None
        assert await push.subscriptions.list_active() == []
    assert response.status_code == 403
