"""Web Push delivery (RFC 8030, 8291 and 8292) through ``pywebpush``.

``pywebpush`` is synchronous (``requests``), so every push runs in a worker thread
(``asyncio.to_thread``). Each push yields one :class:`DeliveryReceipt`:

* ``delivered``: the push service accepted it (``201 Created`` or ``202 Accepted``).
* ``expired``: ``404`` or ``410``; the subscription is gone. It is marked expired in the
  :class:`~blogwriter.notify.subscriptions.SubscriptionRepo` and receives nothing further.
* ``failed``: anything else (other statuses, timeouts, connection or encryption errors). A
  failed receipt also emits the ``notification_failed`` out-of-band alert
  (:func:`blogwriter.notify.alerts.emit_alert`).

Only a :class:`~blogwriter.notify.payload.NotificationPayload` can be sent: title, same-origin
path and tag; there is no field for draft text. Requests go out with a fresh ``requests``
session that ignores proxy and ``.netrc`` settings and follows no redirects, and only to
allowlisted push services (see :data:`~blogwriter.notify.subscriptions.PUSH_SERVICE_HOSTS`).
The endpoint is a capability and is never logged; log lines carry the subscription id, status,
outcome and duration.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

import pywebpush  # pyright: ignore[reportMissingTypeStubs]
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02  # pyright: ignore[reportMissingTypeStubs]

from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.notify.alerts import emit_alert
from blogwriter.notify.payload import NotificationPayload
from blogwriter.notify.settings import (
    DEFAULT_PUSH_TIMEOUT_SECONDS,
    DEFAULT_PUSH_TTL_SECONDS,
    MAX_PUSH_TTL_SECONDS,
    NotifyConfigError,
    NotifySettings,
    load_vapid_private_key,
    validate_vapid_subject,
)
from blogwriter.notify.subscriptions import PushSubscription, SubscriptionRepo

__all__ = [
    "DELIVERED_STATUSES",
    "EXPIRED_STATUSES",
    "DeliveryOutcome",
    "DeliveryReceipt",
    "Urgency",
    "WebPushFunction",
    "WebPushNotifier",
    "new_push_session",
]

type DeliveryOutcome = Literal["delivered", "expired", "failed"]
#: RFC 8030 ``Urgency``: ``high`` wakes a sleeping phone for time-critical pushes.
type Urgency = Literal["very-low", "low", "normal", "high"]

DELIVERED_STATUSES: Final = frozenset({201, 202})
EXPIRED_STATUSES: Final = frozenset({404, 410})

_logger: Final = logging.getLogger("blogwriter.notify.webpush")
register_log_fields("subscription_id", "push_outcome")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """What happened to one push."""

    subscription_id: str
    outcome: DeliveryOutcome
    status: int | None = None
    """HTTP status from the push service; ``None`` if no response arrived."""

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered"


class WebPushFunction(Protocol):
    """The part of ``pywebpush.webpush`` this module uses (tests inject a fake).

    Returns the ``requests`` response; raises ``pywebpush.WebPushException`` (carrying the
    response) when the push service answers with a status above 202.
    """

    def __call__(
        self,
        *,
        subscription_info: Mapping[str, object],
        data: str,
        vapid_private_key: Vapid02,
        vapid_claims: dict[str, str | int],
        ttl: int,
        timeout: float,
        headers: dict[str, str | int | float],
        requests_session: requests.Session,
    ) -> object: ...


def _pywebpush() -> WebPushFunction:
    # pywebpush ships no type information; its webpush signature matches WebPushFunction.
    return pywebpush.webpush  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def new_push_session() -> requests.Session:
    """A ``requests`` session for one push: no environment proxies or ``.netrc``, no redirects."""
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 0
    return session


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _status_of(response: object) -> int | None:
    status: object = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def _outcome(status: int | None) -> DeliveryOutcome:
    if status in DELIVERED_STATUSES:
        return "delivered"
    if status in EXPIRED_STATUSES:
        return "expired"
    return "failed"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class WebPushNotifier:
    """Sends notifications to the owner's subscribed browsers and keeps the repository current."""

    def __init__(
        self,
        *,
        repo: SubscriptionRepo,
        vapid_private_key: ec.EllipticCurvePrivateKey,
        vapid_subject: str,
        ttl_seconds: int = DEFAULT_PUSH_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_PUSH_TIMEOUT_SECONDS,
        webpush: WebPushFunction | None = None,
        session_factory: Callable[[], requests.Session] = new_push_session,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(vapid_private_key.curve, ec.SECP256R1):
            raise NotifyConfigError("VAPID private key must be an EC key on P-256")
        try:
            self._subject = validate_vapid_subject(vapid_subject)
        except ValueError as exc:
            raise NotifyConfigError(f"VAPID subject {exc}") from None
        self._ttl = self._check_ttl(ttl_seconds)
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be more than 0 and at most 60")
        self._timeout = timeout_seconds
        self._repo = repo
        self._vapid = Vapid02(private_key=vapid_private_key)
        public_point = vapid_private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self._application_server_key = _b64url(public_point)
        self._webpush = webpush if webpush is not None else _pywebpush()
        self._session_factory = session_factory
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: NotifySettings | None,
        repo: SubscriptionRepo,
        *,
        webpush: WebPushFunction | None = None,
        session_factory: Callable[[], requests.Session] = new_push_session,
        clock: Callable[[], datetime] = _utc_now,
    ) -> WebPushNotifier:
        """Build from ``BW_NOTIFY_*`` (read from the environment when ``settings`` is ``None``).

        Raises :class:`NotifyConfigError` when VAPID is not configured or the key is invalid.
        """
        resolved = settings if settings is not None else NotifySettings()
        if resolved.vapid_private_key is None or resolved.vapid_subject is None:
            raise NotifyConfigError(
                "web push is not configured (set BW_NOTIFY_VAPID_PRIVATE_KEY and "
                "BW_NOTIFY_VAPID_SUBJECT)"
            )
        return cls(
            repo=repo,
            vapid_private_key=load_vapid_private_key(resolved.vapid_private_key.get_secret_value()),
            vapid_subject=resolved.vapid_subject,
            ttl_seconds=resolved.push_ttl_seconds,
            timeout_seconds=resolved.push_timeout_seconds,
            webpush=webpush,
            session_factory=session_factory,
            clock=clock,
        )

    @property
    def application_server_key(self) -> str:
        """VAPID public key (base64url uncompressed point) for ``PushManager.subscribe``."""
        return self._application_server_key

    async def send(
        self,
        subscription: PushSubscription,
        payload: NotificationPayload,
        *,
        urgency: Urgency = "normal",
        ttl_seconds: int | None = None,
    ) -> DeliveryReceipt:
        """Push ``payload`` to one subscription. Never raises for delivery problems."""
        ttl = self._ttl if ttl_seconds is None else self._check_ttl(ttl_seconds)
        if not subscription.active:
            return DeliveryReceipt(subscription.id, "expired")
        data = payload.to_json()
        started = time.perf_counter()
        error_fields: dict[str, object] = {}
        try:
            status = await asyncio.to_thread(self._post, subscription, data, urgency, ttl)
        except Exception as exc:  # timeouts, connection and encryption errors: a failed receipt
            status = None
            error_fields = dict(describe_exception(exc))
        outcome = _outcome(status)
        if outcome == "expired":
            await self._repo.mark_expired(subscription.id, at=self._clock())
        _logger.log(
            logging.WARNING if outcome == "failed" else logging.INFO,
            "push_receipt",
            extra={
                "subscription_id": subscription.id,
                "push_outcome": outcome,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                **error_fields,
            },
        )
        if outcome == "failed":
            emit_alert("notification_failed", subscription.id)
        return DeliveryReceipt(subscription.id, outcome, status)

    async def notify_all(
        self,
        payload: NotificationPayload,
        *,
        urgency: Urgency = "normal",
        ttl_seconds: int | None = None,
    ) -> list[DeliveryReceipt]:
        """Push ``payload`` to every active subscription concurrently; one receipt each."""
        subscriptions = await self._repo.list_active()
        if not subscriptions:
            _logger.warning("push_no_subscriptions")
            return []
        results = await asyncio.gather(
            *(
                self.send(subscription, payload, urgency=urgency, ttl_seconds=ttl_seconds)
                for subscription in subscriptions
            ),
            return_exceptions=True,
        )
        receipts: list[DeliveryReceipt] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result  # the repository failed; every push has finished by now
            receipts.append(result)
        return receipts

    @staticmethod
    def _check_ttl(ttl_seconds: int) -> int:
        if not 0 <= ttl_seconds <= MAX_PUSH_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be between 0 and {MAX_PUSH_TTL_SECONDS}")
        return ttl_seconds

    def _post(
        self, subscription: PushSubscription, data: str, urgency: Urgency, ttl: int
    ) -> int | None:
        """Runs in a worker thread: one push request; the response status, if any."""
        session = self._session_factory()
        try:
            result = self._webpush(
                subscription_info=subscription.subscription_info(),
                data=data,
                vapid_private_key=self._vapid,
                vapid_claims={"sub": self._subject},  # pywebpush adds aud and exp to this dict
                ttl=ttl,
                timeout=self._timeout,
                headers={"Urgency": urgency},
                requests_session=session,
            )
        except pywebpush.WebPushException as exc:
            response: object = getattr(exc, "response", None)
            status = _status_of(response)
            if status is None:
                raise  # no response (bad keys, encryption): send() logs the error type
            return status
        finally:
            session.close()
        return _status_of(result)
