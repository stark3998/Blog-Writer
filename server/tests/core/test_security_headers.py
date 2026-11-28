"""Tests for blogwriter.core.security_headers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter
from starlette.responses import PlainTextResponse

from blogwriter.core.app import create_app
from blogwriter.core.security_headers import (
    API_CSP,
    HSTS,
    PERMISSIONS_POLICY,
    SPA_CSP,
    is_api_path,
)
from blogwriter.core.settings import CoreSettings
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers

OWNER = principal_headers(OWNER_OID)

EXPECTED_SPA_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'; worker-src 'self'; manifest-src 'self'"
)


def spa_settings(tmp_path: Path, **overrides: object) -> CoreSettings:
    (tmp_path / "index.html").write_text("<!doctype html><title>Blog Writer</title>")
    return make_settings(spa_dir=tmp_path, **overrides)


def custom_router() -> APIRouter:
    router = APIRouter()

    async def preview() -> PlainTextResponse:
        return PlainTextResponse(
            "preview",
            headers={
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=60",
            },
        )

    router.add_api_route("/api/preview", preview, methods=["GET"])
    return router


def assert_common_headers(response: httpx.Response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"


def test_spa_csp_is_exactly_the_planned_policy() -> None:
    assert SPA_CSP == EXPECTED_SPA_CSP
    assert "unsafe-inline" not in SPA_CSP
    assert "unsafe-eval" not in SPA_CSP


async def test_spa_responses_get_the_strict_spa_csp(tmp_path: Path) -> None:
    app = create_app(spa_settings(tmp_path))
    async with asgi_client(app) as client:
        response = await client.get("/studio/42", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == EXPECTED_SPA_CSP
    assert_common_headers(response)
    assert "strict-transport-security" not in response.headers


async def test_api_responses_get_the_api_csp_and_no_store() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/me", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == API_CSP
    assert response.headers["cache-control"] == "no-store"
    assert_common_headers(response)


@pytest.mark.parametrize("path", ["/api/me", "/", "/api/health"])
async def test_denied_and_exempt_responses_get_the_headers_too(path: str, tmp_path: Path) -> None:
    app = create_app(spa_settings(tmp_path))
    async with asgi_client(app) as client:
        response = await client.get(path)
    assert response.status_code in (200, 401)
    assert_common_headers(response)
    assert "content-security-policy" in response.headers


async def test_hsts_only_in_prod(tmp_path: Path) -> None:
    prod = create_app(spa_settings(tmp_path, env="prod"))
    test = create_app(spa_settings(tmp_path, env="test"))
    async with asgi_client(prod) as prod_client, asgi_client(test) as test_client:
        for path in ("/", "/api/me", "/api/health"):
            assert (await prod_client.get(path)).headers["strict-transport-security"] == HSTS
            assert "strict-transport-security" not in (await test_client.get(path)).headers


async def test_headers_set_by_a_route_win() -> None:
    app = create_app(make_settings(), routers=[custom_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/preview", headers=OWNER)
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["cache-control"] == "private, max-age=60"
    assert_common_headers(response)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/api", True), ("/api/", True), ("/api/me", True), ("/apix", False), ("/", False)],
)
def test_is_api_path(path: str, expected: bool) -> None:
    assert is_api_path(path) is expected
