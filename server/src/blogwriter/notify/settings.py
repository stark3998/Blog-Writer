"""Notification settings (environment prefix ``BW_NOTIFY_``).

``BW_NOTIFY_VAPID_PRIVATE_KEY``
    The VAPID signing key (P-256): base64url of the raw 32-byte private value (what
    ``npx web-push generate-vapid-keys`` prints), base64url DER, or PEM. Key Vault holds it.
``BW_NOTIFY_VAPID_SUBJECT``
    Contact for push services: ``mailto:owner@example.com`` or ``https://host``.
``BW_NOTIFY_PUSH_TTL_SECONDS``
    How long a push service keeps an undelivered message (default 12 h, at most 28 days).
``BW_NOTIFY_PUSH_TIMEOUT_SECONDS``
    Connect and read timeout for one push request (default 10 s).

The key and subject are set together or not at all; unset means push is off
(:meth:`blogwriter.notify.webpush.WebPushNotifier.from_settings` raises
:class:`NotifyConfigError`). The key itself is parsed by :func:`load_vapid_private_key`, not by
pydantic, because pydantic keeps raw input in ``ValidationError.errors()``.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final, Self

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "DEFAULT_PUSH_TIMEOUT_SECONDS",
    "DEFAULT_PUSH_TTL_SECONDS",
    "MAX_PUSH_TTL_SECONDS",
    "NotifyConfigError",
    "NotifySettings",
    "load_vapid_private_key",
    "validate_vapid_subject",
]

DEFAULT_PUSH_TTL_SECONDS: Final = 12 * 3600
#: RFC 8030 leaves the maximum to the push service; FCM keeps messages for 28 days at most.
MAX_PUSH_TTL_SECONDS: Final = 28 * 24 * 3600
DEFAULT_PUSH_TIMEOUT_SECONDS: Final = 10.0

# Accepted by py_vapid's own claim check (it refuses a path or port on https subjects).
_MAILTO_RE: Final = re.compile(r"mailto:[^\s@/]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_HTTPS_RE: Final = re.compile(r"https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_B64URL_RE: Final = re.compile(r"[A-Za-z0-9_+/-]+={0,2}")


class NotifyConfigError(Exception):
    """Push is not configured, or its configuration is invalid. Messages hold no key material."""


def validate_vapid_subject(value: str) -> str:
    """Return the subject if it is ``mailto:<address>`` or ``https://<host>``."""
    subject = value.strip()
    if not (_MAILTO_RE.fullmatch(subject) or _HTTPS_RE.fullmatch(subject)):
        raise ValueError("must be mailto:<address> or https://<host> (no path)")
    return subject


def load_vapid_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """Parse a VAPID key: base64url raw 32-byte value, base64url DER or PEM; P-256 only."""
    text = value.strip()
    try:
        if text.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
        else:
            compact = "".join(text.split())
            if not _B64URL_RE.fullmatch(compact):
                raise NotifyConfigError("VAPID private key is not base64url or PEM")
            unpadded = compact.rstrip("=").replace("+", "-").replace("/", "_")
            raw = base64.urlsafe_b64decode(unpadded + "=" * (-len(unpadded) % 4))
            if len(raw) == 32:
                key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
            else:
                key = serialization.load_der_private_key(raw, password=None)
    except NotifyConfigError:
        raise
    except (ValueError, TypeError, binascii.Error, UnicodeEncodeError, UnsupportedAlgorithm):
        raise NotifyConfigError("VAPID private key could not be parsed") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise NotifyConfigError("VAPID private key must be an EC key on P-256")
    return key


class NotifySettings(BaseSettings):
    """Settings for ``blogwriter.notify`` (environment prefix ``BW_NOTIFY_``)."""

    model_config = SettingsConfigDict(
        env_prefix="BW_NOTIFY_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    vapid_private_key: SecretStr | None = None
    vapid_subject: str | None = None
    push_ttl_seconds: int = Field(default=DEFAULT_PUSH_TTL_SECONDS, ge=0, le=MAX_PUSH_TTL_SECONDS)
    push_timeout_seconds: float = Field(default=DEFAULT_PUSH_TIMEOUT_SECONDS, gt=0, le=60)

    @field_validator("vapid_subject")
    @classmethod
    def _check_subject(cls, value: str | None) -> str | None:
        return None if value is None else validate_vapid_subject(value)

    @model_validator(mode="after")
    def _check_pair(self) -> Self:
        if (self.vapid_private_key is None) != (self.vapid_subject is None):
            raise ValueError(
                "BW_NOTIFY_VAPID_PRIVATE_KEY and BW_NOTIFY_VAPID_SUBJECT must be set together"
            )
        return self

    @property
    def push_configured(self) -> bool:
        return self.vapid_private_key is not None and self.vapid_subject is not None
