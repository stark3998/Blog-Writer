"""Core settings: one ``pydantic-settings`` class, environment prefix ``BW_``.

Every field reads ``BW_<FIELD>`` (for example ``BW_ENV``, ``BW_OWNER_OID``, ``BW_COOKIE_KEY``).
Defaults are safe for local tests: the default environment is ``test``, and with no owner
configured every authenticated route is denied.

``env='prod'`` is strict and the app refuses to start without its essentials:

* ``owner_oid``: the owner's Entra object ID. Without it nobody could ever be the owner.
* ``cookie_key``: at least 32 bytes. It signs the ``__Host-bw`` cookie.
* ``public_origin``: an ``https`` origin. The CSRF check compares ``Origin`` against it.

Inside Azure Container Apps (``CONTAINER_APP_NAME`` is set by the platform) ``env`` must be
``prod``. That keeps a stray ``BW_ENV=local`` from enabling the development principal in a
deployed app.

``site_hosts`` and ``site_upstream`` (``BW_SITE_HOSTS``, ``BW_SITE_UPSTREAM``) are set together:
the public portfolio site served from the same app (:mod:`blogwriter.core.site`). Blog Writer's
own host (``public_origin``) can never be one of the site hosts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request

from blogwriter.core.site import normalize_site_hosts, normalize_site_upstream

__all__ = [
    "MIN_COOKIE_KEY_BYTES",
    "CoreSettings",
    "Env",
    "normalize_oid",
    "normalize_origin",
    "settings_from_request",
]

Env = Literal["local", "test", "prod"]

#: Minimum length of ``BW_COOKIE_KEY`` in bytes (UTF-8).
MIN_COOKIE_KEY_BYTES: Final = 32

#: Environment variable Azure Container Apps sets in every replica.
ACA_MARKER_ENV: Final = "CONTAINER_APP_NAME"

_OID_RE: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA_RE: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}


def normalize_oid(value: str) -> str | None:
    """Return ``value`` as a lowercase GUID, or ``None`` if it is not a GUID.

    Entra object IDs are GUIDs. Comparisons always use this canonical form.
    """
    candidate = value.strip().lower()
    return candidate if _OID_RE.fullmatch(candidate) else None


def normalize_origin(value: str) -> str | None:
    """Return the canonical ``scheme://host[:port]`` form of an origin, or ``None``.

    Accepts ``http`` and ``https`` origins only. A path (other than a bare ``/``), query,
    fragment or user info makes the value invalid. Default ports are dropped and the scheme
    and host are lowercased, matching what browsers send in the ``Origin`` header. Browsers
    serialize origins in ASCII (IDNA hosts as ``xn--``), so any other character is refused:
    Unicode lowercasing would otherwise equate distinct strings (``"\\u212a".lower() == "k"``).
    """
    if not value.isascii():
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


class CoreSettings(BaseSettings):
    """Settings for ``blogwriter.core`` (environment prefix ``BW_``)."""

    model_config = SettingsConfigDict(
        env_prefix="BW_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        # A rejected BW_COOKIE_KEY would otherwise be printed in the ValidationError, which
        # uvicorn's factory call lets reach stderr and so the console log.
        hide_input_in_errors=True,
    )

    env: Env = "test"
    """``local`` (developer machine), ``test`` (default; unit tests) or ``prod``."""

    owner_oid: str | None = None
    """The owner's Entra object ID. Unset means nobody is the owner (every request denied)."""

    cookie_key: SecretStr | None = None
    """HMAC key for ``__Host-bw``. Required in prod; outside prod a random per-process key is
    used when unset, so cookies do not survive a restart."""

    build_sha: str = "dev"
    """Build identifier reported by ``/api/health``."""

    public_origin: str | None = None
    """Public origin of the app, for example ``https://writer.jatinmadan.com``."""

    spa_dir: Path | None = None
    """Directory with the built SPA (``index.html`` plus ``assets/``). Unset: no SPA serving."""

    dev_principal_oid: str | None = None
    """Object ID injected as the Easy Auth principal. Honoured only when ``env='local'``, and
    only for requests whose ``Host`` is loopback or the ``public_origin`` host."""

    cookie_ttl_hours: int = Field(default=12, ge=1, le=24)
    """Lifetime of the ``__Host-bw`` cookie."""

    watchdog_threshold_ms: int = Field(default=500, ge=10, le=60_000)
    """Event-loop stalls longer than this are logged."""

    site_hosts: str | None = None
    """Host names of the public portfolio site this app also serves, comma separated, canonical
    first (``example.com,www.example.com``). Requests for them go to ``site_upstream`` and never
    reach Blog Writer's routes (:mod:`blogwriter.core.site`). Unset: no public site."""

    site_upstream: str | None = None
    """The portfolio server in this replica, loopback only (``http://127.0.0.1:3000``)."""

    @field_validator("owner_oid", "dev_principal_oid")
    @classmethod
    def _check_oid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_oid(value)
        if normalized is None:
            raise ValueError("must be an Entra object ID (a GUID)")
        return normalized

    @field_validator("cookie_key")
    @classmethod
    def _check_cookie_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < MIN_COOKIE_KEY_BYTES:
            raise ValueError(f"must be at least {MIN_COOKIE_KEY_BYTES} bytes")
        return value

    @field_validator("build_sha")
    @classmethod
    def _check_build_sha(cls, value: str) -> str:
        if not _SHA_RE.fullmatch(value):
            raise ValueError("must be 1-64 characters of [0-9A-Za-z._-]")
        return value

    @field_validator("public_origin")
    @classmethod
    def _check_public_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_origin(value)
        if normalized is None:
            raise ValueError("must be an http(s) origin such as https://writer.example.com")
        return normalized

    @field_validator("site_hosts")
    @classmethod
    def _check_site_hosts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ",".join(normalize_site_hosts(value)) or None

    @field_validator("site_upstream")
    @classmethod
    def _check_site_upstream(cls, value: str | None) -> str | None:
        return normalize_site_upstream(value)

    @model_validator(mode="after")
    def _check_environment(self) -> Self:
        if self.env != "prod" and os.environ.get(ACA_MARKER_ENV):
            raise ValueError(
                f"BW_ENV must be 'prod' inside Azure Container Apps ({ACA_MARKER_ENV} is set)"
            )
        if self.env == "prod":
            missing = [
                f"BW_{name.upper()}"
                for name in ("owner_oid", "cookie_key", "public_origin")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"env='prod' requires {', '.join(missing)}")
            if self.public_origin is not None and not self.public_origin.startswith("https://"):
                raise ValueError("env='prod' requires an https BW_PUBLIC_ORIGIN")
        if (self.site_hosts is None) != (self.site_upstream is None):
            raise ValueError("set BW_SITE_HOSTS and BW_SITE_UPSTREAM together")
        if self.site_hosts is not None and self.public_origin is not None:
            own_host = (urlsplit(self.public_origin).hostname or "").lower()
            if own_host in self.site_host_list:
                raise ValueError(
                    "the public origin (Blog Writer's own host) cannot be one of BW_SITE_HOSTS"
                )
        return self

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def site_host_list(self) -> tuple[str, ...]:
        """``site_hosts`` as a tuple (canonical first); empty without a public site."""
        return normalize_site_hosts(self.site_hosts)

    @property
    def cookie_ttl_seconds(self) -> int:
        return self.cookie_ttl_hours * 3600

    @property
    def effective_dev_principal_oid(self) -> str | None:
        """The development principal, or ``None`` outside ``env='local'``."""
        return self.dev_principal_oid if self.env == "local" else None


async def settings_from_request(request: Request) -> CoreSettings:
    """FastAPI dependency: the :class:`CoreSettings` the running app was built with.

    ``async`` so FastAPI resolves it on the event loop instead of a worker thread.
    """
    settings: object = request.app.state.settings
    if not isinstance(settings, CoreSettings):  # pragma: no cover - create_app always sets it
        raise RuntimeError(
            "app.state.settings is not a CoreSettings; build the app with create_app"
        )
    return settings
