"""Test helpers for code that uses :mod:`blogwriter.services` (the app never imports this).

- :class:`RecordingPush`: a ``pywebpush.webpush`` stand-in (answers by endpoint, records what
  was sent) for :class:`~blogwriter.notify.webpush.WebPushNotifier`;
- :func:`vapid_private_key_b64`, :func:`browser_subscription`: real key material for tests;
- :class:`FakeEmbeddingRouter`: answers ``embed`` with deterministic vectors.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02  # pyright: ignore[reportMissingTypeStubs]

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import EmbeddingResult, ServedBy
from blogwriter.ai.types import Embeddings, Task, Usage

__all__ = [
    "FakeEmbeddingRouter",
    "FakePushResponse",
    "RecordingPush",
    "b64url",
    "browser_subscription",
    "vapid_private_key_b64",
]

FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send/"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def vapid_private_key_b64() -> str:
    """A fresh VAPID key: the raw 32-byte private value, base64url (``BW_NOTIFY_VAPID_*``)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def browser_subscription(name: str = "device-1") -> dict[str, object]:
    """``PushSubscription.toJSON()`` of a browser on FCM with real P-256 and auth keys."""
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return {
        "endpoint": f"{FCM_ENDPOINT}{name}",
        "expirationTime": None,
        "keys": {"p256dh": b64url(point), "auth": b64url(os.urandom(16))},
    }


@dataclass(frozen=True, slots=True)
class FakePushResponse:
    status_code: int


@dataclass
class RecordingPush:
    """A ``pywebpush.webpush`` stand-in: answers ``statuses[endpoint]`` (default 201)."""

    statuses: dict[str, int] = field(default_factory=dict[str, int])
    default: int = 201
    sent: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    """``(endpoint, payload JSON)`` of every push."""

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
    ) -> object:
        endpoint = str(subscription_info["endpoint"])
        self.sent.append((endpoint, data))
        return FakePushResponse(self.statuses.get(endpoint, self.default))


class FakeEmbeddingRouter:
    """``embed`` like ``AIRouter.embed``: deterministic unit vectors, every call recorded."""

    def __init__(self, *, short_by: int = 0) -> None:
        self.calls: list[tuple[tuple[str, ...], Origin, str | None, int | None]] = []
        self.short_by = short_by

    async def embed(
        self,
        texts: Sequence[str],
        *,
        origin: Origin,
        job_id: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        await asyncio.sleep(0)
        self.calls.append((tuple(texts), origin, job_id, dimensions))
        size = dimensions or 8
        vectors = [_vector(text, size) for text in texts][: max(1, len(texts) - self.short_by)]
        usage = Usage(input=sum(len(text.split()) for text in texts))
        return EmbeddingResult(
            embeddings=Embeddings(vectors=tuple(vectors), usage=usage, model="fake-embedding"),
            served_by=ServedBy(
                task=Task.EMBED,
                model="text-embedding-3-large",
                deployment="fake",
                provider="openai",
                effort=None,
            ),
            usage=usage,
            cost_usd=Decimal(0),
        )


def _vector(text: str, size: int) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode()).digest()
    return tuple(digest[index % len(digest)] / 255 for index in range(size))
