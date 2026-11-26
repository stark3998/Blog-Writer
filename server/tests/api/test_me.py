"""Tests for blogwriter.api.me (GET /api/me)."""

from __future__ import annotations

from fastapi import FastAPI

from blogwriter.api.me import MeResponse, get_router
from blogwriter.core.app import create_app
from blogwriter.core.errors import exception_handlers
from blogwriter.core.testing import (
    OTHER_OID,
    OWNER_OID,
    asgi_client,
    make_settings,
    owner_cookie,
    principal_headers,
)


async def test_returns_oid_and_name_from_the_principal() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/me", headers=principal_headers(OWNER_OID, name="Jay"))
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "name": "Jay"}
    assert "set-cookie" in response.headers


async def test_name_is_null_when_only_the_cookie_authenticates() -> None:
    settings = make_settings()
    app = create_app(settings)
    async with asgi_client(app) as client:
        response = await client.get("/api/me", headers={"Cookie": owner_cookie(settings)})
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "name": None}


async def test_requires_the_owner() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        anonymous = await client.get("/api/me")
        other = await client.get("/api/me", headers=principal_headers(OTHER_OID))
    assert anonymous.status_code == 401
    assert other.status_code == 403


async def test_without_auth_middleware_the_route_fails_closed() -> None:
    bare = FastAPI(exception_handlers=exception_handlers())
    bare.include_router(get_router())
    async with asgi_client(bare) as client:
        response = await client.get("/api/me", headers=principal_headers(OWNER_OID))
    assert response.status_code == 401


def test_model_shape() -> None:
    assert set(MeResponse.model_fields) == {"oid", "name"}
