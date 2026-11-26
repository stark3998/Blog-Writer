"""Tests for blogwriter.notify.webpush: Web Push delivery receipts (no network anywhere).

Most tests inject a fake ``webpush`` function that behaves like ``pywebpush.webpush`` (returns
the response, raises ``WebPushException`` above 202). The end-to-end tests run the real
``pywebpush`` against a fake ``requests`` adapter and check the result independently: the body
is decrypted with RFC 8291 and the VAPID JWT signature is verified with ``cryptography``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import cast, override

import pytest
import pywebpush  # pyright: ignore[reportMissingTypeStubs]
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from py_vapid import Vapid02  # pyright: ignore[reportMissingTypeStubs]
from requests.adapters import BaseAdapter

from blogwriter.core.logging import JsonFormatter, allowed_log_fields
from blogwriter.notify import (
    MemorySubscriptionRepo,
    NotificationPayload,
    NotifyConfigError,
    NotifySettings,
    PushSubscription,
)
from blogwriter.notify.webpush import DeliveryReceipt, WebPushNotifier, new_push_session

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SUBJECT = "mailto:owner@example.com"
FCM = "https://fcm.googleapis.com/fcm/send/SECRET-CAPABILITY-1"
APPLE = "https://web.push.apple.com/SECRET-CAPABILITY-2"
MOZILLA = "https://updates.push.services.mozilla.com/wpush/v2/SECRET-CAPABILITY-3"
PAYLOAD = NotificationPayload(title="Post slot in 15 minutes", url="/publish/slot-1", tag="slot-1")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_point(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


class Browser:
    """A subscribed browser: holds the keys needed to decrypt what it receives."""

    def __init__(self, endpoint: str = FCM) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.auth_secret = os.urandom(16)
        self.subscription = PushSubscription.create(
            endpoint,
            p256dh=b64url(public_point(self.private_key)),
            auth=b64url(self.auth_secret),
            now=NOW - timedelta(days=1),
        )

    def decrypt(self, body: bytes) -> bytes:
        """RFC 8291 / RFC 8188 (aes128gcm) decryption of a single-record push message."""
        salt, record_size, id_length = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        sender_public = body[21 : 21 + id_length]
        ciphertext = body[21 + id_length :]
        assert len(ciphertext) <= record_size
        shared = self.private_key.exchange(
            ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
        )
        key_info = b"WebPush: info\x00" + public_point(self.private_key) + sender_public
        ikm = HKDF(hashes.SHA256(), 32, salt=self.auth_secret, info=key_info).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00")
        nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00")
        plaintext = AESGCM(cek.derive(ikm)).decrypt(nonce.derive(ikm), ciphertext, None)
        stripped = plaintext.rstrip(b"\x00")
        assert stripped.endswith(b"\x02")  # the last-record delimiter
        return stripped[:-1]


class FakePush:
    """Behaves like ``pywebpush.webpush``: returns the response, raises above 202."""

    def __init__(self, status: int = 201, *, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.threads: list[int] = []
        self.sessions: list[requests.Session] = []

    def __call__(
        self,
        *,
        subscription_info: Mapping[str, object],
        data: str,
        vapid_private_key: object,
        vapid_claims: dict[str, str | int],
        ttl: int,
        timeout: float,
        headers: dict[str, str | int | float],
        requests_session: requests.Session,
    ) -> object:
        self.threads.append(threading.get_ident())
        self.sessions.append(requests_session)
        self.calls.append(
            {
                "subscription_info": subscription_info,
                "data": data,
                "vapid_private_key": vapid_private_key,
                "vapid_claims": dict(vapid_claims),
                "ttl": ttl,
                "timeout": timeout,
                "headers": dict(headers),
            }
        )
        if self.error is not None:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        if self.status > 202:
            raise pywebpush.WebPushException("Push failed", response=response)
        return response


class RecordingSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @override
    def close(self) -> None:
        self.closed = True
        super().close()


def hardened_recording_session() -> requests.Session:
    session = RecordingSession()
    session.trust_env = False
    session.max_redirects = 0
    return session


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def vapid_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def make_notifier(
    repo: MemorySubscriptionRepo,
    vapid_key: ec.EllipticCurvePrivateKey,
    push: FakePush | None = None,
    *,
    session_factory: Callable[[], requests.Session] = hardened_recording_session,
    clock: Callable[[], datetime] | None = None,
    ttl_seconds: int = 3600,
) -> WebPushNotifier:
    return WebPushNotifier(
        repo=repo,
        vapid_private_key=vapid_key,
        vapid_subject=SUBJECT,
        ttl_seconds=ttl_seconds,
        timeout_seconds=7.5,
        webpush=push if push is not None else FakePush(),
        session_factory=session_factory,
        clock=clock if clock is not None else Clock(),
    )


def json_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    formatter = JsonFormatter()
    return [json.loads(formatter.format(record)) for record in caplog.records]


def events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, object]]:
    return [line for line in json_lines(caplog) if line["event"] == event]


# ----------------------------------------------------------------------------------------------
# Receipts
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [201, 202])
async def test_accepted_pushes_are_delivered(
    status: int, vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    browser = Browser()
    repo = MemorySubscriptionRepo([browser.subscription])
    receipt = await make_notifier(repo, vapid_key, FakePush(status)).send(
        browser.subscription, PAYLOAD
    )
    assert receipt == DeliveryReceipt(browser.subscription.id, "delivered", status)
    assert receipt.delivered
    assert await repo.list_active() == [browser.subscription]
    assert events(caplog, "alert_event") == []


@pytest.mark.parametrize("status", [404, 410])
async def test_gone_subscriptions_are_marked_expired(
    status: int, vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    browser = Browser()
    repo = MemorySubscriptionRepo([browser.subscription])
    clock = Clock()
    clock.now = NOW + timedelta(minutes=5)
    receipt = await make_notifier(repo, vapid_key, FakePush(status), clock=clock).send(
        browser.subscription, PAYLOAD
    )
    assert receipt == DeliveryReceipt(browser.subscription.id, "expired", status)
    assert not receipt.delivered
    stored = await repo.get(browser.subscription.id)
    assert stored is not None
    assert stored.expired_at == NOW + timedelta(minutes=5)
    assert await repo.list_active() == []
    assert events(caplog, "alert_event") == []  # expiry is normal, not an alert


@pytest.mark.parametrize("status", [200, 204, 400, 401, 403, 413, 429, 500, 503])
async def test_other_statuses_fail_and_alert(
    status: int, vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    browser = Browser()
    repo = MemorySubscriptionRepo([browser.subscription])
    receipt = await make_notifier(repo, vapid_key, FakePush(status)).send(
        browser.subscription, PAYLOAD
    )
    assert receipt == DeliveryReceipt(browser.subscription.id, "failed", status)
    assert await repo.list_active() == [browser.subscription]  # still subscribed
    (alert,) = events(caplog, "alert_event")
    assert alert["alert_kind"] == "notification_failed"
    assert alert["ref_id"] == browser.subscription.id


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("connection refused"),
        requests.Timeout("read timed out"),
        requests.TooManyRedirects("redirected"),
        pywebpush.WebPushException("Invalid p256dh key specified"),
        RuntimeError("anything else"),
    ],
)
async def test_errors_without_a_response_fail_and_alert(
    error: Exception, vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    browser = Browser()
    repo = MemorySubscriptionRepo([browser.subscription])
    receipt = await make_notifier(repo, vapid_key, FakePush(error=error)).send(
        browser.subscription, PAYLOAD
    )
    assert receipt == DeliveryReceipt(browser.subscription.id, "failed", None)
    (line,) = events(caplog, "push_receipt")
    assert line["push_outcome"] == "failed"
    assert line["status"] is None
    assert "error_type" in line
    assert len(events(caplog, "alert_event")) == 1


async def test_already_expired_subscriptions_are_not_pushed(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    browser = Browser()
    expired = browser.subscription.model_copy(update={"expired_at": NOW})
    push = FakePush()
    receipt = await make_notifier(MemorySubscriptionRepo([expired]), vapid_key, push).send(
        expired, PAYLOAD
    )
    assert receipt == DeliveryReceipt(expired.id, "expired", None)
    assert push.calls == []


# ----------------------------------------------------------------------------------------------
# What is sent, and how
# ----------------------------------------------------------------------------------------------


async def test_only_title_url_and_tag_are_sent(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    browser = Browser()
    push = FakePush()
    await make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, push).send(
        browser.subscription, PAYLOAD
    )
    (call,) = push.calls
    data = call["data"]
    assert isinstance(data, str)
    assert json.loads(data) == {
        "title": "Post slot in 15 minutes",
        "url": "/publish/slot-1",
        "tag": "slot-1",
    }


async def test_vapid_ttl_timeout_urgency_and_subscription(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    browser = Browser()
    push = FakePush()
    notifier = make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, push)
    await notifier.send(browser.subscription, PAYLOAD, urgency="high")
    await notifier.send(browser.subscription, PAYLOAD, ttl_seconds=60)
    first, second = push.calls
    assert first["subscription_info"] == browser.subscription.subscription_info()
    assert first["vapid_claims"] == {"sub": SUBJECT}
    assert isinstance(first["vapid_private_key"], Vapid02)
    assert (first["ttl"], first["timeout"], first["headers"]) == (3600, 7.5, {"Urgency": "high"})
    assert (second["ttl"], second["headers"]) == (60, {"Urgency": "normal"})


async def test_each_push_uses_a_fresh_hardened_session_that_is_closed(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    browser = Browser()
    push = FakePush()
    notifier = make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, push)
    await notifier.send(browser.subscription, PAYLOAD)
    await notifier.send(browser.subscription, PAYLOAD)
    first, second = push.sessions
    assert first is not second
    for session in push.sessions:
        assert isinstance(session, RecordingSession)
        assert session.closed
        assert session.trust_env is False
        assert session.max_redirects == 0


def test_new_push_session_ignores_the_environment_and_redirects() -> None:
    session = new_push_session()
    try:
        assert session.trust_env is False
        assert session.max_redirects == 0
    finally:
        session.close()


async def test_pushes_run_off_the_event_loop_thread(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    browser = Browser()
    push = FakePush()
    await make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, push).send(
        browser.subscription, PAYLOAD
    )
    assert push.threads != [threading.get_ident()]


async def test_a_slow_push_does_not_block_the_loop(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    browser = Browser()

    class SlowPush(FakePush):
        @override
        def __call__(
            self,
            *,
            subscription_info: Mapping[str, object],
            data: str,
            vapid_private_key: object,
            vapid_claims: dict[str, str | int],
            ttl: int,
            timeout: float,
            headers: dict[str, str | int | float],
            requests_session: requests.Session,
        ) -> object:
            time.sleep(0.2)  # blocking, as requests is
            return super().__call__(
                subscription_info=subscription_info,
                data=data,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
                ttl=ttl,
                timeout=timeout,
                headers=headers,
                requests_session=requests_session,
            )

    notifier = make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, SlowPush())
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await notifier.send(browser.subscription, PAYLOAD)
    finally:
        task.cancel()
    assert ticks >= 5


@pytest.mark.parametrize("ttl_seconds", [-1, 28 * 24 * 3600 + 1])
async def test_ttl_override_is_bounded(
    ttl_seconds: int, vapid_key: ec.EllipticCurvePrivateKey
) -> None:
    browser = Browser()
    notifier = make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key)
    with pytest.raises(ValueError, match="ttl_seconds"):
        await notifier.send(browser.subscription, PAYLOAD, ttl_seconds=ttl_seconds)


# ----------------------------------------------------------------------------------------------
# notify_all
# ----------------------------------------------------------------------------------------------


async def test_notify_all_reaches_every_active_subscription(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    phone, laptop, old = Browser(APPLE), Browser(FCM), Browser(MOZILLA)
    expired = old.subscription.model_copy(update={"expired_at": NOW})
    repo = MemorySubscriptionRepo([phone.subscription, laptop.subscription, expired])
    push = FakePush()
    receipts = await make_notifier(repo, vapid_key, push).notify_all(PAYLOAD, urgency="high")
    assert sorted(receipt.subscription_id for receipt in receipts) == sorted(
        [phone.subscription.id, laptop.subscription.id]
    )
    assert all(receipt.delivered for receipt in receipts)
    infos = [cast(Mapping[str, object], call["subscription_info"]) for call in push.calls]
    assert sorted(str(info["endpoint"]) for info in infos) == sorted([APPLE, FCM])


async def test_notify_all_with_nobody_subscribed(
    vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    push = FakePush()
    receipts = await make_notifier(MemorySubscriptionRepo(), vapid_key, push).notify_all(PAYLOAD)
    assert receipts == []
    assert push.calls == []
    assert len(events(caplog, "push_no_subscriptions")) == 1


async def test_notify_all_finishes_every_push_before_raising_a_repo_error(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    gone, fine = Browser(APPLE), Browser(FCM)

    class BrokenRepo(MemorySubscriptionRepo):
        @override
        async def mark_expired(self, subscription_id: str, *, at: datetime) -> bool:
            raise RuntimeError("store unavailable")

    class PerEndpointPush(FakePush):
        @override
        def __call__(
            self,
            *,
            subscription_info: Mapping[str, object],
            data: str,
            vapid_private_key: object,
            vapid_claims: dict[str, str | int],
            ttl: int,
            timeout: float,
            headers: dict[str, str | int | float],
            requests_session: requests.Session,
        ) -> object:
            self.status = 410 if subscription_info["endpoint"] == APPLE else 201
            return super().__call__(
                subscription_info=subscription_info,
                data=data,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
                ttl=ttl,
                timeout=timeout,
                headers=headers,
                requests_session=requests_session,
            )

    push = PerEndpointPush()
    repo = BrokenRepo([gone.subscription, fine.subscription])
    with pytest.raises(RuntimeError, match="store unavailable"):
        await make_notifier(repo, vapid_key, push).notify_all(PAYLOAD)
    assert len(push.calls) == 2


# ----------------------------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------------------------


async def test_logs_never_carry_the_endpoint_or_the_payload(
    vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    browser = Browser()
    notifier = make_notifier(
        MemorySubscriptionRepo([browser.subscription]), vapid_key, FakePush(500)
    )
    await notifier.send(browser.subscription, PAYLOAD)
    text = "\n".join(json.dumps(line) for line in json_lines(caplog))
    assert "SECRET-CAPABILITY" not in text
    assert "fcm.googleapis.com" not in text
    assert "Post slot" not in text
    assert "/publish/slot-1" not in text
    (line,) = events(caplog, "push_receipt")
    assert line["level"] == "WARNING"
    assert line["subscription_id"] == browser.subscription.id
    assert (line["push_outcome"], line["status"]) == ("failed", 500)
    assert "duration_ms" in line
    assert "dropped_fields" not in line


async def test_successful_receipts_log_at_info(
    vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    browser = Browser()
    await make_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key).send(
        browser.subscription, PAYLOAD
    )
    (line,) = events(caplog, "push_receipt")
    assert (line["level"], line["push_outcome"], line["status"]) == ("INFO", "delivered", 201)


def test_log_fields_are_registered() -> None:
    assert {"subscription_id", "push_outcome"} <= allowed_log_fields()


# ----------------------------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------------------------


def test_from_settings_requires_vapid() -> None:
    with pytest.raises(NotifyConfigError, match="not configured"):
        WebPushNotifier.from_settings(NotifySettings(), MemorySubscriptionRepo())


def test_from_settings_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, vapid_key: ec.EllipticCurvePrivateKey
) -> None:
    raw = vapid_key.private_numbers().private_value.to_bytes(32, "big")
    monkeypatch.setenv("BW_NOTIFY_VAPID_PRIVATE_KEY", b64url(raw))
    monkeypatch.setenv("BW_NOTIFY_VAPID_SUBJECT", SUBJECT)
    notifier = WebPushNotifier.from_settings(None, MemorySubscriptionRepo())
    assert notifier.application_server_key == b64url(public_point(vapid_key))
    assert len(unb64url(notifier.application_server_key)) == 65


def test_from_settings_reports_a_bad_key_without_echoing_it() -> None:
    bad = b64url(b"\x01" * 40)
    settings = NotifySettings.model_validate({"vapid_private_key": bad, "vapid_subject": SUBJECT})
    with pytest.raises(NotifyConfigError) as excinfo:
        WebPushNotifier.from_settings(settings, MemorySubscriptionRepo())
    assert bad not in str(excinfo.value)


def test_constructor_validation(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    repo = MemorySubscriptionRepo()
    with pytest.raises(NotifyConfigError, match="subject"):
        WebPushNotifier(repo=repo, vapid_private_key=vapid_key, vapid_subject="owner@example.com")
    with pytest.raises(NotifyConfigError, match="P-256"):
        WebPushNotifier(
            repo=repo,
            vapid_private_key=ec.generate_private_key(ec.SECP384R1()),
            vapid_subject=SUBJECT,
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        WebPushNotifier(
            repo=repo, vapid_private_key=vapid_key, vapid_subject=SUBJECT, timeout_seconds=0
        )
    with pytest.raises(ValueError, match="ttl_seconds"):
        WebPushNotifier(
            repo=repo, vapid_private_key=vapid_key, vapid_subject=SUBJECT, ttl_seconds=-5
        )


# ----------------------------------------------------------------------------------------------
# End to end through the real pywebpush (fake requests adapter; nothing leaves the process)
# ----------------------------------------------------------------------------------------------


class FakePushService(BaseAdapter):
    """Stands in for the push service at the ``requests`` transport layer."""

    def __init__(self, status: int = 201, headers: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self.status = status
        self.headers = dict(headers or {})
        self.requests: list[requests.PreparedRequest] = []
        self.proxies: list[object] = []
        self.timeouts: list[object] = []

    @override
    def send(
        self,
        request: requests.PreparedRequest,
        stream: object = False,
        timeout: object = None,
        verify: object = True,
        cert: object = None,
        proxies: object = None,
    ) -> requests.Response:
        self.requests.append(request)
        self.proxies.append(proxies)
        self.timeouts.append(timeout)
        response = requests.Response()
        response.status_code = self.status
        response.reason = "Push service reply"
        response.headers.update(self.headers)
        response.url = request.url or ""
        response.request = request
        return response

    @override
    def close(self) -> None:
        return None


def sessions_for(service: FakePushService) -> Callable[[], requests.Session]:
    def factory() -> requests.Session:
        session = new_push_session()
        session.mount("https://", service)
        return session

    return factory


def real_notifier(
    repo: MemorySubscriptionRepo, vapid_key: ec.EllipticCurvePrivateKey, service: FakePushService
) -> WebPushNotifier:
    return WebPushNotifier(
        repo=repo,
        vapid_private_key=vapid_key,
        vapid_subject=SUBJECT,
        ttl_seconds=900,
        timeout_seconds=7.5,
        session_factory=sessions_for(service),
    )


def verify_vapid(authorization: str, public_key: str) -> dict[str, object]:
    """Check the RFC 8292 header's ES256 signature and return the JWT claims."""
    scheme, _, parameters = authorization.partition(" ")
    assert scheme == "vapid"
    fields: dict[str, str] = {}
    for part in parameters.split(","):
        name, _, value = part.strip().partition("=")
        fields[name] = value
    assert fields["k"] == public_key
    signing_input, _, signature_text = fields["t"].rpartition(".")
    signature = unb64url(signature_text)
    assert len(signature) == 64
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(public_key))
    verifier.verify(
        encode_dss_signature(
            int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
        ),
        signing_input.encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    header = json.loads(unb64url(signing_input.split(".")[0]))
    assert header["alg"] == "ES256"
    claims: dict[str, object] = json.loads(unb64url(signing_input.split(".")[1]))
    return claims


async def test_real_pywebpush_encrypts_signs_and_delivers(
    vapid_key: ec.EllipticCurvePrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")  # must be ignored
    browser = Browser()
    service = FakePushService(201)
    notifier = real_notifier(MemorySubscriptionRepo([browser.subscription]), vapid_key, service)
    receipt = await notifier.send(browser.subscription, PAYLOAD, urgency="high")
    assert receipt == DeliveryReceipt(browser.subscription.id, "delivered", 201)

    (request,) = service.requests
    assert (request.method, request.url) == ("POST", FCM)
    assert request.headers["TTL"] == "900"
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["Urgency"] == "high"
    assert service.proxies == [{}]
    assert service.timeouts == [7.5]

    body = request.body
    assert isinstance(body, bytes)
    assert json.loads(browser.decrypt(body)) == {
        "title": "Post slot in 15 minutes",
        "url": "/publish/slot-1",
        "tag": "slot-1",
    }

    authorization = request.headers["Authorization"]
    assert isinstance(authorization, str)
    claims = verify_vapid(authorization, notifier.application_server_key)
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == SUBJECT
    expires = claims["exp"]
    assert isinstance(expires, int)
    assert 0 < expires - time.time() <= 24 * 3600


async def test_real_pywebpush_gone_marks_expired(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    browser = Browser(APPLE)
    repo = MemorySubscriptionRepo([browser.subscription])
    receipt = await real_notifier(repo, vapid_key, FakePushService(410)).send(
        browser.subscription, PAYLOAD
    )
    assert receipt == DeliveryReceipt(browser.subscription.id, "expired", 410)
    assert await repo.list_active() == []


async def test_real_pywebpush_rate_limit_fails(
    vapid_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    browser = Browser()
    receipt = await real_notifier(
        MemorySubscriptionRepo([browser.subscription]), vapid_key, FakePushService(429)
    ).send(browser.subscription, PAYLOAD)
    assert receipt == DeliveryReceipt(browser.subscription.id, "failed", 429)
    assert len(events(caplog, "alert_event")) == 1


async def test_real_pywebpush_never_follows_redirects(
    vapid_key: ec.EllipticCurvePrivateKey,
) -> None:
    browser = Browser()
    service = FakePushService(302, {"Location": "https://169.254.169.254/latest/meta-data/"})
    receipt = await real_notifier(
        MemorySubscriptionRepo([browser.subscription]), vapid_key, service
    ).send(browser.subscription, PAYLOAD)
    assert receipt.outcome == "failed"
    assert len(service.requests) == 1  # the redirect target was never requested
