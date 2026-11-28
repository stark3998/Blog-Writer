"""Tests for blogwriter.notify.subscriptions: subscription validation and the memory repo."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from blogwriter.notify import MemorySubscriptionRepo, PushSubscription, SubscriptionRepo
from blogwriter.notify.subscriptions import subscription_id_for, validate_push_endpoint

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
FCM = "https://fcm.googleapis.com/fcm/send/dGVzdC10b2tlbg:APA91b-test"
MOZILLA = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABtest"
APPLE = "https://web.push.apple.com/QGuQyavXutnMhd6tLPAPABCDEF"
WNS = "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB%2btest"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def browser_keys() -> tuple[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    point = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64url(point), b64url(os.urandom(16))


def subscription(endpoint: str = FCM, *, created_at: datetime = NOW) -> PushSubscription:
    p256dh, auth = browser_keys()
    return PushSubscription.create(endpoint, p256dh=p256dh, auth=auth, now=created_at)


# ----------------------------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", [FCM, MOZILLA, APPLE, WNS, "https://fcm.googleapis.com:443/x"])
def test_known_push_services_are_accepted(endpoint: str) -> None:
    assert validate_push_endpoint(endpoint) == endpoint
    assert subscription(endpoint).endpoint == endpoint


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("http://fcm.googleapis.com/fcm/send/x", "https"),
        ("https://evil.example/fcm.googleapis.com", "known push service"),
        ("https://fcm.googleapis.com.evil.example/x", "known push service"),
        ("https://notfcm.googleapis.com/x", "known push service"),
        ("https://169.254.169.254/latest/meta-data/", "known push service"),
        ("https://localhost/push", "known push service"),
        ("https://user:pw@fcm.googleapis.com/x", "credentials"),
        ("https://fcm.googleapis.com:8443/x", "default https port"),
        ("https://fcm.googleapis.com:99999/x", "valid URL"),
        ("https://fcm.googleapis.com/a b", "whitespace"),
        ("https://fcm.googleapis.com\\@evil.example/", "backslashes"),
        ("https://fcm.googleapis.com/é", "ASCII"),
        ("https://fcm.googleapis.com/" + "x" * 2048, "2048"),
        ("fcm.googleapis.com/fcm/send/x", "https"),
    ],
)
def test_other_endpoints_are_refused(endpoint: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as excinfo:
        validate_push_endpoint(endpoint)
    assert endpoint not in str(excinfo.value)


def test_id_is_derived_from_the_endpoint() -> None:
    first, second = subscription(FCM), subscription(FCM)
    assert first.id == second.id == subscription_id_for(FCM)
    assert len(first.id) == 32
    assert subscription(MOZILLA).id != first.id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("p256dh", "not base64!"),
        ("p256dh", b64url(b"\x04" + b"\x01" * 63)),  # 64 bytes
        ("p256dh", b64url(b"\x02" + b"\x01" * 64)),  # not an uncompressed point
        ("p256dh", b64url(b"\x04" + b"\x01" * 64)),  # not on the curve
        ("auth", b64url(os.urandom(15))),
        ("auth", b64url(os.urandom(17))),
        ("auth", "?"),
    ],
)
def test_bad_keys_are_refused(field: str, value: str) -> None:
    p256dh, auth = browser_keys()
    values = {"p256dh": p256dh, "auth": auth, field: value}
    with pytest.raises(ValidationError):
        PushSubscription.create(FCM, p256dh=values["p256dh"], auth=values["auth"], now=NOW)


def test_padded_keys_are_accepted() -> None:
    p256dh, auth = browser_keys()
    created = PushSubscription.create(FCM, p256dh=p256dh + "=", auth=auth + "==", now=NOW)
    assert created.active


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 11, 26, 12, 0),
        datetime(2026, 11, 26, 7, 0, tzinfo=timezone(timedelta(hours=-5))),
    ],
)
def test_timestamps_must_be_utc(when: datetime) -> None:
    with pytest.raises(ValidationError):
        subscription(created_at=when)


def test_ids_must_match_the_derivation_format() -> None:
    p256dh, auth = browser_keys()
    with pytest.raises(ValidationError):
        PushSubscription(id="ID", endpoint=FCM, p256dh=p256dh, auth=auth, created_at=NOW)


def test_from_browser_json() -> None:
    p256dh, auth = browser_keys()
    created = PushSubscription.from_browser(
        {"endpoint": APPLE, "expirationTime": None, "keys": {"p256dh": p256dh, "auth": auth}},
        now=NOW,
    )
    assert (created.endpoint, created.p256dh, created.auth) == (APPLE, p256dh, auth)
    assert created.created_at == NOW
    assert created.active


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"endpoint": FCM},
        {"endpoint": FCM, "keys": "nope"},
        {"endpoint": 1, "keys": {}},
        {"endpoint": FCM, "keys": {"p256dh": 1, "auth": "x"}},
        {"endpoint": FCM, "keys": {"auth": "x"}},
    ],
)
def test_from_browser_rejects_bad_shapes(document: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - shape errors and pydantic errors both qualify
        PushSubscription.from_browser(document, now=NOW)


def test_subscription_info_is_what_pywebpush_expects() -> None:
    created = subscription()
    assert created.subscription_info() == {
        "endpoint": FCM,
        "keys": {"p256dh": created.p256dh, "auth": created.auth},
    }


def test_repr_hides_the_endpoint_and_keys() -> None:
    created = subscription()
    for text in (repr(created), str(created)):
        assert "fcm.googleapis.com" not in text
        assert created.auth not in text
        assert created.id in text


def test_validation_errors_do_not_print_the_endpoint() -> None:
    p256dh, auth = browser_keys()
    capability_url = "https://evil.example/SECRET-CAPABILITY"
    with pytest.raises(ValidationError) as excinfo:
        PushSubscription.create(capability_url, p256dh=p256dh, auth=auth, now=NOW)
    assert "SECRET-CAPABILITY" not in str(excinfo.value)


# ----------------------------------------------------------------------------------------------
# Memory repository
# ----------------------------------------------------------------------------------------------


def test_memory_repo_satisfies_the_protocol() -> None:
    repo: SubscriptionRepo = MemorySubscriptionRepo()
    assert repo is not None


async def test_upsert_get_and_list() -> None:
    older = subscription(MOZILLA, created_at=NOW - timedelta(days=1))
    newer = subscription(FCM)
    repo = MemorySubscriptionRepo([newer])
    await repo.upsert(older)
    assert await repo.get(older.id) == older
    assert await repo.get("0" * 32) is None
    assert await repo.list_active() == [older, newer]


async def test_mark_expired_removes_from_active_and_keeps_the_first_time() -> None:
    first = subscription(FCM)
    second = subscription(APPLE)
    repo = MemorySubscriptionRepo([first, second])
    assert await repo.mark_expired(first.id, at=NOW + timedelta(hours=1))
    assert await repo.mark_expired(first.id, at=NOW + timedelta(hours=2))
    assert await repo.list_active() == [second]
    stored = await repo.get(first.id)
    assert stored is not None
    assert stored.expired_at == NOW + timedelta(hours=1)
    assert not stored.active
    assert not await repo.mark_expired("f" * 32, at=NOW)


async def test_mark_expired_requires_utc() -> None:
    created = subscription()
    repo = MemorySubscriptionRepo([created])
    with pytest.raises(ValidationError):
        await repo.mark_expired(created.id, at=datetime(2026, 11, 26, 13, 0))


async def test_resubscribing_reactivates() -> None:
    created = subscription(FCM)
    repo = MemorySubscriptionRepo([created])
    await repo.mark_expired(created.id, at=NOW)
    assert await repo.list_active() == []
    again = subscription(FCM, created_at=NOW + timedelta(days=1))
    await repo.upsert(again)
    assert await repo.list_active() == [again]


async def test_delete() -> None:
    created = subscription()
    repo = MemorySubscriptionRepo([created])
    assert await repo.delete(created.id)
    assert not await repo.delete(created.id)
    assert await repo.get(created.id) is None
