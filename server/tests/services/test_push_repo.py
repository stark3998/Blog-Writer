"""Push subscriptions in the ``settings`` container (``CosmosSubscriptionRepo``, contract I-3).

Plan: "Security model" (push carries a title and a same-origin path only; endpoints are
capabilities). The repo implements ``notify.SubscriptionRepo``; every test runs on MemoryStore,
which validates writes exactly like Cosmos.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from blogwriter.core.logging import JsonFormatter
from blogwriter.db import MemoryStore
from blogwriter.notify.subscriptions import PushSubscription
from blogwriter.services.push_repo import (
    PUSH_SUBSCRIPTION_KIND,
    CosmosSubscriptionRepo,
    subscription_doc_id,
)
from blogwriter.services.testing import browser_subscription

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def subscription(name: str = "device-1", *, at: datetime = T0) -> PushSubscription:
    return PushSubscription.from_browser(browser_subscription(name), now=at)


async def test_a_subscription_round_trips_under_a_prefixed_id() -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store, clock=lambda: T0)
    sub = subscription()
    await repo.upsert(sub)
    assert await repo.get(sub.id) == sub
    doc = await store.read("settings", subscription_doc_id(sub.id))
    assert doc.id == f"push-{sub.id}"
    assert doc.body["kind"] == PUSH_SUBSCRIPTION_KIND
    assert doc.body["subscriptionId"] == sub.id
    assert doc.body["createdAt"] == "2026-11-26T12:00:00.000000Z"
    assert doc.body["expiredAt"] is None


async def test_list_active_is_oldest_first_and_skips_expired_and_other_kinds() -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store)
    older = subscription("older", at=T0)
    newer = subscription("newer", at=T0 + timedelta(hours=1))
    gone = subscription("gone", at=T0 - timedelta(days=1))
    for sub in (newer, older, gone):
        await repo.upsert(sub)
    assert await repo.mark_expired(gone.id, at=T0)
    await store.create("settings", {"id": "brand-kit", "kind": "brand_kit"})
    assert await repo.list_active() == [older, newer]


async def test_mark_expired_keeps_the_first_expiry_and_reports_unknown_ids() -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store)
    sub = subscription()
    await repo.upsert(sub)
    assert await repo.mark_expired(sub.id, at=T0)
    assert await repo.mark_expired(sub.id, at=T0 + timedelta(days=1))
    stored = await repo.get(sub.id)
    assert stored is not None
    assert stored.expired_at == T0
    assert not stored.active
    assert not await repo.mark_expired("0" * 32, at=T0)
    assert not await repo.mark_expired("not-an-id", at=T0)


async def test_subscribing_again_reactivates_the_same_record() -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store)
    first = subscription()
    await repo.upsert(first)
    await repo.mark_expired(first.id, at=T0)
    again = PushSubscription.from_browser(browser_subscription(), now=T0 + timedelta(days=2))
    assert again.id == first.id
    await repo.upsert(again)
    assert await repo.list_active() == [again]


async def test_delete_removes_only_push_subscriptions() -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store)
    sub = subscription()
    await repo.upsert(sub)
    assert await repo.delete(sub.id)
    assert await repo.get(sub.id) is None
    assert not await repo.delete(sub.id)
    assert not await repo.delete("../brand-kit")
    impostor = "a" * 32
    await store.create("settings", {"id": subscription_doc_id(impostor), "kind": "other"})
    assert not await repo.delete(impostor)
    assert await repo.get(impostor) is None
    assert (await store.read("settings", subscription_doc_id(impostor))).body["kind"] == "other"


async def test_an_unreadable_body_is_skipped_and_logged_without_its_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = MemoryStore()
    repo = CosmosSubscriptionRepo(store)
    good = subscription()
    await repo.upsert(good)
    bad_id = "b" * 32
    await store.create(
        "settings",
        {
            "id": subscription_doc_id(bad_id),
            "kind": PUSH_SUBSCRIPTION_KIND,
            "subscriptionId": bad_id,
            "endpoint": "https://evil.example/push/secret-capability",
            "p256dh": "x",
            "auth": "y",
            "createdAt": "2026-11-26T12:00:00.000000Z",
        },
    )
    with caplog.at_level(logging.WARNING, logger="blogwriter.services"):
        assert await repo.list_active() == [good]
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in caplog.records]
    assert any(json.loads(line)["event"] == "push_subscription_invalid" for line in lines)
    assert all("secret-capability" not in line for line in lines)


async def test_invalid_ids_never_reach_the_store() -> None:
    repo = CosmosSubscriptionRepo(MemoryStore())
    assert await repo.get("x/y") is None
    assert await repo.get("A" * 32) is None
    with pytest.raises(ValueError, match="positive"):
        CosmosSubscriptionRepo(MemoryStore(), store_timeout=0)
