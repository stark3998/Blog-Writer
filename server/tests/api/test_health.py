"""Tests for blogwriter.api.health (GET /api/health)."""

from __future__ import annotations

import pytest

from blogwriter.api.health import HealthResponse, get_router
from blogwriter.core.app import create_app
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers


async def test_returns_only_status_and_sha() -> None:
    app = create_app(make_settings(build_sha="0123abc"))
    async with asgi_client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "sha": "0123abc"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [{}, principal_headers(OWNER_OID), {"X-MS-CLIENT-PRINCIPAL": "garbage"}, {"Cookie": "x=y"}],
)
async def test_needs_no_authentication(headers: dict[str, str]) -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/health", headers=headers)
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


async def test_is_get_only() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.post("/api/health")
    assert response.status_code == 405


async def test_works_without_an_owner_configured() -> None:
    # Probes must pass even on a fail-closed configuration.
    app = create_app(make_settings(owner_oid=None))
    async with asgi_client(app) as client:
        assert (await client.get("/api/health")).status_code == 200


def test_router_and_model_shape() -> None:
    (route,) = get_router().routes
    assert getattr(route, "path", None) == "/api/health"
    assert set(HealthResponse.model_fields) == {"status", "sha"}
