"""Web Push subscriptions and where they are kept.

A :class:`PushSubscription` is what the browser's ``PushManager.subscribe()`` returns
(``endpoint`` plus the ``p256dh`` and ``auth`` keys). Its :attr:`~PushSubscription.id` is derived
from the endpoint, so subscribing the same browser again updates one record.

The endpoint is a bearer capability: anyone holding it can push to the device. It is never
logged, and only push services on :data:`PUSH_SERVICE_HOSTS` are accepted. That allowlist also
keeps the push sender (``pywebpush`` over ``requests``, outside the egress helper) from being
pointed at an internal address.

:class:`SubscriptionRepo` is the storage protocol; :class:`MemorySubscriptionRepo` implements it
for tests and local runs (the Cosmos implementation is wired later).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Final, Protocol, Self, cast
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "PUSH_SERVICE_HOSTS",
    "MemorySubscriptionRepo",
    "PushSubscription",
    "SubscriptionRepo",
    "subscription_id_for",
    "validate_push_endpoint",
]

#: Push services a subscription endpoint may point at (the host or a subdomain of it):
#: Chrome, Android and other Chromium browsers (FCM), Firefox (Mozilla autopush), Edge (WNS)
#: and Safari on macOS and iOS (Apple).
PUSH_SERVICE_HOSTS: Final = (
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)
MAX_ENDPOINT_CHARS: Final = 2048

_B64URL_RE: Final = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_ID_RE: Final = re.compile(r"[0-9a-f]{32}")
_P256_POINT_BYTES: Final = 65
_AUTH_SECRET_BYTES: Final = 16


def _b64url_decode(value: str) -> bytes | None:
    if not _B64URL_RE.fullmatch(value):
        return None
    text = value.rstrip("=")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def validate_push_endpoint(endpoint: str) -> str:
    """Return ``endpoint`` if it is an ``https`` URL on an allowlisted push service.

    Raises ``ValueError`` (whose message never repeats the endpoint).
    """
    if len(endpoint) > MAX_ENDPOINT_CHARS or not endpoint.isascii() or not endpoint.isprintable():
        raise ValueError("push endpoint must be printable ASCII of at most 2048 characters")
    if any(char.isspace() for char in endpoint) or "\\" in endpoint:
        raise ValueError("push endpoint must not contain whitespace or backslashes")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise ValueError("push endpoint is not a valid URL") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise ValueError("push endpoint must be an https URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("push endpoint must not contain credentials")
    if port not in (None, 443):
        raise ValueError("push endpoint must use the default https port")
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in PUSH_SERVICE_HOSTS):
        raise ValueError("push endpoint is not on a known push service")
    return endpoint


def subscription_id_for(endpoint: str) -> str:
    """Stable id for an endpoint: the first 32 hex digits of its SHA-256 (safe to log)."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:32]


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("must be timezone-aware")
    if offset != timedelta(0):
        raise ValueError("must be in UTC")
    return value


class PushSubscription(BaseModel):
    """One browser's Web Push subscription. Build it with :meth:`create` or :meth:`from_browser`."""

    # The endpoint is a capability: keep it out of ValidationError messages too.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: str
    endpoint: str
    p256dh: str
    """The browser's P-256 public key: base64url of the 65-byte uncompressed point."""
    auth: str
    """The 16-byte authentication secret, base64url."""
    created_at: datetime
    expired_at: datetime | None = None
    """Set when the push service answered 404 or 410; expired subscriptions get no pushes."""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("must be 32 lowercase hex digits (see subscription_id_for)")
        return value

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        return validate_push_endpoint(value)

    @field_validator("p256dh")
    @classmethod
    def _check_p256dh(cls, value: str) -> str:
        raw = _b64url_decode(value)
        if raw is None or len(raw) != _P256_POINT_BYTES or raw[0] != 0x04:
            raise ValueError("must be a base64url uncompressed P-256 point (65 bytes)")
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError:
            raise ValueError("is not a point on P-256") from None
        return value

    @field_validator("auth")
    @classmethod
    def _check_auth(cls, value: str) -> str:
        raw = _b64url_decode(value)
        if raw is None or len(raw) != _AUTH_SECRET_BYTES:
            raise ValueError("must be a base64url 16-byte secret")
        return value

    @field_validator("created_at", "expired_at")
    @classmethod
    def _check_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    @classmethod
    def create(cls, endpoint: str, *, p256dh: str, auth: str, now: datetime) -> Self:
        """A new, active subscription whose id is derived from ``endpoint``."""
        return cls(
            id=subscription_id_for(endpoint),
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            created_at=now,
        )

    @classmethod
    def from_browser(cls, data: Mapping[str, object], *, now: datetime) -> Self:
        """Build from ``PushSubscription.toJSON()``: ``{"endpoint", "keys": {"p256dh", "auth"}}``.

        Other members (``expirationTime``) are ignored. Raises ``ValueError`` on a bad shape.
        """
        endpoint = data.get("endpoint")
        keys = data.get("keys")
        if not isinstance(endpoint, str) or not isinstance(keys, Mapping):
            raise ValueError("expected {'endpoint': str, 'keys': {'p256dh': str, 'auth': str}}")
        key_map = cast(Mapping[object, object], keys)
        p256dh = key_map.get("p256dh")
        auth = key_map.get("auth")
        if not isinstance(p256dh, str) or not isinstance(auth, str):
            raise ValueError("expected keys.p256dh and keys.auth strings")
        return cls.create(endpoint, p256dh=p256dh, auth=auth, now=now)

    @property
    def active(self) -> bool:
        return self.expired_at is None

    def subscription_info(self) -> dict[str, object]:
        """The ``subscription_info`` mapping ``pywebpush`` expects."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}

    def __repr__(self) -> str:  # the endpoint and keys are capabilities: keep them out of reprs
        return f"PushSubscription(id={self.id!r}, active={self.active})"

    __str__ = __repr__


class SubscriptionRepo(Protocol):
    """Where push subscriptions live (Cosmos in production, memory in tests)."""

    async def upsert(self, subscription: PushSubscription) -> None:
        """Insert or replace by id (re-subscribing a browser re-activates it)."""
        ...

    async def get(self, subscription_id: str) -> PushSubscription | None: ...

    async def list_active(self) -> list[PushSubscription]:
        """Subscriptions without ``expired_at``, oldest first."""
        ...

    async def mark_expired(self, subscription_id: str, *, at: datetime) -> bool:
        """Set ``expired_at`` (kept if already set). ``False`` if the id is unknown."""
        ...

    async def delete(self, subscription_id: str) -> bool:
        """Remove a subscription. ``False`` if the id is unknown."""
        ...


class MemorySubscriptionRepo:
    """In-process :class:`SubscriptionRepo` for tests and local runs."""

    def __init__(self, subscriptions: Iterable[PushSubscription] = ()) -> None:
        self._items: dict[str, PushSubscription] = {item.id: item for item in subscriptions}

    async def upsert(self, subscription: PushSubscription) -> None:
        self._items[subscription.id] = subscription

    async def get(self, subscription_id: str) -> PushSubscription | None:
        return self._items.get(subscription_id)

    async def list_active(self) -> list[PushSubscription]:
        active = [item for item in self._items.values() if item.active]
        return sorted(active, key=lambda item: (item.created_at, item.id))

    async def mark_expired(self, subscription_id: str, *, at: datetime) -> bool:
        current = self._items.get(subscription_id)
        if current is None:
            return False
        if current.expired_at is None:
            self._items[subscription_id] = PushSubscription.model_validate(
                {**current.model_dump(), "expired_at": at}
            )
        return True

    async def delete(self, subscription_id: str) -> bool:
        return self._items.pop(subscription_id, None) is not None
