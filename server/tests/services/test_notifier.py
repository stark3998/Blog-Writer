"""The ``Notifier`` port over web push (contract I-3): receipts become ``NotifyResult``.

Plan: "Planner" > veto notices (auto-publish needs a delivery receipt) and "CI/CD and
operations" > out-of-band alerts on a failed notification receipt. The sender is the real
``WebPushNotifier``; only ``pywebpush.webpush`` is replaced (no network).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import override

import pytest

from blogwriter.core.logging import JsonFormatter
from blogwriter.db import MemoryStore, StoreUnavailable
from blogwriter.domain.ports import Notifier, NotifyResult
from blogwriter.notify.payload import NotificationPayload
from blogwriter.notify.settings import load_vapid_private_key
from blogwriter.notify.subscriptions import MemorySubscriptionRepo, PushSubscription
from blogwriter.notify.webpush import WebPushNotifier
from blogwriter.services.notifier import NullNotifier, PushNotifier
from blogwriter.services.push_repo import CosmosSubscriptionRepo
from blogwriter.services.testing import RecordingPush, browser_subscription, vapid_private_key_b64

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
PAYLOAD = NotificationPayload(title="Post at 09:00: tap to publish", url="/today", tag="slot:1")


def subscribed(name: str) -> PushSubscription:
    return PushSubscription.from_browser(browser_subscription(name), now=T0)


def notifier(
    repo: MemorySubscriptionRepo | CosmosSubscriptionRepo, push: RecordingPush
) -> PushNotifier:
    sender = WebPushNotifier(
        repo=repo,
        vapid_private_key=load_vapid_private_key(vapid_private_key_b64()),
        vapid_subject="mailto:owner@example.com",
        webpush=push,
        clock=lambda: T0,
    )
    return PushNotifier(sender, repo)


async def test_receipts_are_counted_by_outcome_and_expired_devices_are_marked() -> None:
    ok, gone, broken = subscribed("ok"), subscribed("gone"), subscribed("broken")
    repo = MemorySubscriptionRepo([ok, gone, broken])
    push = RecordingPush(statuses={gone.endpoint: 410, broken.endpoint: 500})
    port: Notifier = notifier(repo, push)
    result = await port.notify(PAYLOAD, urgency="high")
    assert result == NotifyResult(delivered=1, failed=1, expired=1)
    assert result.any_delivered
    assert [sub.id for sub in await repo.list_active()] == sorted([ok.id, broken.id])
    assert {json.loads(data)["title"] for _, data in push.sent} == {PAYLOAD.title}
    assert all(set(json.loads(data)) == {"title", "url", "tag"} for _, data in push.sent)


async def test_no_subscription_means_no_receipt() -> None:
    result = await notifier(MemorySubscriptionRepo(), RecordingPush()).notify(PAYLOAD)
    assert result == NotifyResult()
    assert not result.any_delivered


async def test_the_cosmos_repo_backs_the_notifier() -> None:
    repo = CosmosSubscriptionRepo(MemoryStore(), clock=lambda: T0)
    device = subscribed("phone")
    await repo.upsert(device)
    push = RecordingPush()
    result = await notifier(repo, push).notify(PAYLOAD)
    assert result.delivered == 1
    assert [endpoint for endpoint, _ in push.sent] == [device.endpoint]


async def test_a_repository_failure_never_raises_and_raises_the_alert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Down(MemorySubscriptionRepo):
        @override
        async def list_active(self) -> list[PushSubscription]:
            raise StoreUnavailable("down")

    push = RecordingPush()
    with caplog.at_level(logging.INFO):
        result = await notifier(Down(), push).notify(PAYLOAD)
    assert result == NotifyResult(failed=1)
    assert not result.any_delivered
    assert push.sent == []
    formatter = JsonFormatter()
    lines = [json.loads(formatter.format(record)) for record in caplog.records]
    events = [line["event"] for line in lines]
    assert "push_notify_failed" in events
    alert = next(line for line in lines if line["event"] == "alert_event")
    assert alert["alert_kind"] == "notification_failed"


async def test_the_push_notifier_exposes_the_key_and_the_repository() -> None:
    repo = MemorySubscriptionRepo()
    port = notifier(repo, RecordingPush())
    assert port.subscriptions is repo
    assert len(port.application_server_key) == 87  # 65-byte point, base64url


async def test_the_null_notifier_sends_nothing_and_reports_no_delivery() -> None:
    port: Notifier = NullNotifier()
    result = await port.notify(PAYLOAD)
    assert result == NotifyResult()
    assert not result.any_delivered
