"""Helpers for tests that call the app through ``httpx.ASGITransport`` (no network).

Example::

    settings = make_settings()
    app = create_app(settings, routers=[my_router()])
    async with asgi_client(app) as client:
        await client.get("/api/me", headers=principal_headers(OWNER_OID))
        await client.post("/api/things", headers=write_headers(settings))

``make_settings`` ignores the process environment (only its arguments count), so a
developer's ``BW_*`` variables cannot leak into tests.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping
from typing import Final, override

import httpx
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from starlette.types import ASGIApp

from blogwriter.core.auth import COOKIE_NAME, CookieSigner
from blogwriter.core.settings import CoreSettings

__all__ = [
    "COOKIE_KEY",
    "ORIGIN",
    "OTHER_OID",
    "OWNER_OID",
    "asgi_client",
    "encode_principal",
    "make_settings",
    "owner_cookie",
    "principal_headers",
    "write_headers",
]

#: Owner object ID used by tests.
OWNER_OID: Final = "0b5d7a52-3c1e-4f7a-9a51-6c2d8e4f1a30"
#: A second, non-owner account.
OTHER_OID: Final = "7f3c2b1a-9d8e-4c6b-a5f4-3e2d1c0b9a87"
ORIGIN: Final = "https://writer.test"
#: Cookie key for tests only (at least 32 bytes).
COOKIE_KEY: Final = "test-only-cookie-key-0123456789abcdef"

_OID_CLAIM: Final = "http://schemas.microsoft.com/identity/claims/objectidentifier"


class _ArgumentsOnlySettings(CoreSettings):
    """``CoreSettings`` that reads only constructor arguments, never the environment."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: object) -> CoreSettings:
    """Valid ``env='test'`` settings for :data:`OWNER_OID`; keyword arguments override them."""
    values: dict[str, object] = {
        "env": "test",
        "owner_oid": OWNER_OID,
        "cookie_key": COOKIE_KEY,
        "public_origin": ORIGIN,
        "build_sha": "test-sha",
    }
    values.update(overrides)
    return _ArgumentsOnlySettings.model_validate(values)


def asgi_client(app: ASGIApp, *, base_url: str = ORIGIN) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` wired straight to ``app`` (use it as an async context manager)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


def encode_principal(document: Mapping[str, object]) -> str:
    """Base64-encode an Easy Auth principal document the way Easy Auth does."""
    return base64.b64encode(json.dumps(document).encode()).decode()


def principal_headers(
    oid: str,
    *,
    name: str | None = "Owner",
    idp: str = "aad",
    with_id_headers: bool = True,
) -> dict[str, str]:
    """Easy Auth principal headers for ``oid`` (as the platform injects them)."""
    claims: list[dict[str, str]] = [{"typ": _OID_CLAIM, "val": oid}]
    if name is not None:
        claims.append({"typ": "name", "val": name})
    headers = {
        "X-MS-CLIENT-PRINCIPAL": encode_principal(
            {"auth_typ": idp, "claims": claims, "name_typ": "name", "role_typ": "roles"}
        )
    }
    if with_id_headers:
        headers["X-MS-CLIENT-PRINCIPAL-ID"] = oid
        headers["X-MS-CLIENT-PRINCIPAL-IDP"] = idp
        if name is not None:
            headers["X-MS-CLIENT-PRINCIPAL-NAME"] = name
    return headers


def owner_cookie(settings: CoreSettings, *, clock: Callable[[], float] = time.time) -> str:
    """A ``Cookie`` header value with a valid ``__Host-bw`` for the configured owner."""
    if settings.cookie_key is None or settings.owner_oid is None:
        raise ValueError("owner_cookie needs settings with cookie_key and owner_oid")
    signer = CookieSigner(
        settings.cookie_key.get_secret_value().encode(),
        ttl_seconds=settings.cookie_ttl_seconds,
        clock=clock,
    )
    return f"{COOKIE_NAME}={signer.issue(settings.owner_oid).value}"


def write_headers(settings: CoreSettings) -> dict[str, str]:
    """Headers a same-origin SPA request carries on POST/PUT/PATCH/DELETE."""
    return {"Cookie": owner_cookie(settings), "Sec-Fetch-Site": "same-origin"}
