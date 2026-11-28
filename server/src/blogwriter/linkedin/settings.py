"""LinkedIn settings (env prefix ``BW_LINKEDIN_``), the one settings class of the package.

``BW_LINKEDIN_CLIENT_ID`` / ``BW_LINKEDIN_CLIENT_SECRET``
    The LinkedIn app's credentials (the secret is a Key Vault reference). Unset by default:
    LinkedIn is off, ``POST /api/linkedin/oauth/start`` answers 503 ``linkedin_not_configured``
    and posts go out through the ready-to-post kit, which needs no API.
``BW_LINKEDIN_REDIRECT_URI``
    The OAuth redirect URI registered on the LinkedIn app. Its path must be
    :data:`CALLBACK_PATH`; ``https`` only (``http`` only for a loopback host during local
    development). Unset: ``BW_PUBLIC_ORIGIN`` + :data:`CALLBACK_PATH`.
``BW_LINKEDIN_TIMEOUT_SECONDS``
    Connect/read/write timeout of every LinkedIn call (20).
``BW_LINKEDIN_CONFIRM_TOKEN_TTL_SECONDS``
    How long a go-live page's confirm token stays valid (600). The token key is derived from
    core's ``BW_COOKIE_KEY``: there is no LinkedIn-specific secret for it.
``BW_LINKEDIN_UPLOAD_POLL_SECONDS``
    How long an upload waits for LinkedIn to report a document or image ``AVAILABLE`` (120).

Build the settings lazily (:func:`linkedin_settings`) so tests construct their own. The client
secret is a ``SecretStr`` and never logged.
"""

from __future__ import annotations

import functools
import ipaddress
import re
from typing import Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "CALLBACK_PATH",
    "LinkedInSettings",
    "linkedin_settings",
    "redirect_uri_for",
    "validate_redirect_uri",
]

CALLBACK_PATH: Final = "/api/linkedin/oauth/callback"
"""The OAuth callback route; the LinkedIn app has exactly this path registered."""

_CLIENT_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_redirect_uri(value: str) -> str:
    """Return ``value`` if it is a usable redirect URI, else raise ``ValueError``.

    ``https://<host>[:port]/api/linkedin/oauth/callback`` with no credentials, query or fragment;
    ``http`` is accepted for a loopback host only (local development).
    """
    uri = value.strip()
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    if not host or parts.username is not None or parts.password is not None:
        raise ValueError("the redirect URI needs a host and no credentials")
    if parts.scheme != "https" and not (parts.scheme == "http" and _is_loopback(host)):
        raise ValueError("the redirect URI must use https (http only for localhost)")
    if parts.path != CALLBACK_PATH or parts.query or parts.fragment:
        raise ValueError(f"the redirect URI path must be exactly {CALLBACK_PATH}")
    return uri


def redirect_uri_for(public_origin: str | None) -> str | None:
    """``<public origin>`` + :data:`CALLBACK_PATH`, or ``None`` without an origin."""
    if not public_origin:
        return None
    return validate_redirect_uri(public_origin.rstrip("/") + CALLBACK_PATH)


class LinkedInSettings(BaseSettings):
    """LinkedIn app credentials, timeouts and limits. Defaults are safe: nothing configured."""

    model_config = SettingsConfigDict(
        env_prefix="BW_LINKEDIN_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    redirect_uri: str | None = None
    timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    confirm_token_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    upload_poll_seconds: int = Field(default=120, ge=5, le=600)

    @field_validator("client_id")
    @classmethod
    def _check_client_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _CLIENT_ID_RE.fullmatch(value.strip()):
            raise ValueError("must be 1-100 letters, digits, '-' or '_'")
        return value.strip()

    @field_validator("client_secret")
    @classmethod
    def _check_client_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("redirect_uri")
    @classmethod
    def _check_redirect_uri(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_redirect_uri(value)

    @property
    def configured(self) -> bool:
        """Client id and secret are set (the redirect URI may come from the public origin)."""
        return self.client_id is not None and self.client_secret is not None

    def resolved_redirect_uri(self, public_origin: str | None) -> str | None:
        """The configured redirect URI, else the one derived from ``BW_PUBLIC_ORIGIN``."""
        if self.redirect_uri is not None:
            return self.redirect_uri
        try:
            return redirect_uri_for(public_origin)
        except ValueError:
            return None


@functools.cache
def linkedin_settings() -> LinkedInSettings:
    """The process-wide settings from the environment (built once, on first use)."""
    return LinkedInSettings()
