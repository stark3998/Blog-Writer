"""Tests for blogwriter.core.app: composition, routers, lifecycles and the uvicorn factory."""

from __future__ import annotations

import asyncio
import logging
import types
from typing import Any, cast

import pytest
from fastapi import APIRouter, FastAPI
from pydantic import SecretStr

from blogwriter.core import app as app_module
from blogwriter.core.app import OPENAPI_URL, Lifecycle, create_app
from blogwriter.core.auth import COOKIE_NAME
from blogwriter.core.settings import CoreSettings
from blogwriter.core.testing import (
    COOKIE_KEY,
    ORIGIN,
    OWNER_OID,
    asgi_client,
    make_settings,
    owner_cookie,
    principal_headers,
)

OWNER = principal_headers(OWNER_OID)


class Recorder:
    """A lifecycle that records calls into a shared journal."""

    def __init__(
        self,
        name: str,
        journal: list[str],
        *,
        fail_startup: bool = False,
        fail_shutdown: bool = False,
        hang_shutdown: bool = False,
    ) -> None:
        self.name = name
        self.journal = journal
        self.fail_startup = fail_startup
        self.fail_shutdown = fail_shutdown
        self.hang_shutdown = hang_shutdown
        self.apps: list[FastAPI] = []

    async def startup(self, app: FastAPI) -> None:
        self.apps.append(app)
        self.journal.append(f"start:{self.name}")
        if self.fail_startup:
            raise RuntimeError("SECRET startup failure")

    async def shutdown(self, app: FastAPI) -> None:
        self.journal.append(f"stop:{self.name}")
        if self.fail_shutdown:
            raise RuntimeError("SECRET shutdown failure")
        if self.hang_shutdown:
            await asyncio.sleep(60)


def named_router(path: str) -> APIRouter:
    router = APIRouter()

    async def endpoint() -> dict[str, str]:
        return {"path": path}

    router.add_api_route(path, endpoint, methods=["GET"], operation_id=path.strip("/"))
    return router


# ----------------------------------------------------------------------------------------------
# Lifecycles
# ----------------------------------------------------------------------------------------------


async def test_lifecycles_start_in_order_and_stop_in_reverse() -> None:
    journal: list[str] = []
    first, second = Recorder("first", journal), Recorder("second", journal)
    app = create_app(make_settings(), lifecycles=[first, second])
    async with app.router.lifespan_context(app):
        assert journal == ["start:first", "start:second"]
    assert journal == ["start:first", "start:second", "stop:second", "stop:first"]
    assert first.apps == [app]


async def test_failed_startup_stops_what_already_started(
    caplog: pytest.LogCaptureFixture,
) -> None:
    journal: list[str] = []
    lifecycles = [
        Recorder("db", journal),
        Recorder("jobs", journal, fail_startup=True),
        Recorder("ai", journal),
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    with pytest.raises(RuntimeError, match="startup failure"):
        async with app.router.lifespan_context(app):
            pytest.fail("the app must not start")
    assert journal == ["start:db", "start:jobs", "stop:db"]
    (record,) = [r for r in caplog.records if r.getMessage() == "lifecycle_startup_failed"]
    assert record.__dict__["lifecycle"] == "Recorder"
    assert record.__dict__["error_type"] == "RuntimeError"


async def test_shutdown_errors_and_timeouts_do_not_stop_other_shutdowns(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "SHUTDOWN_TIMEOUT_S", 0.05)
    journal: list[str] = []
    lifecycles = [
        Recorder("a", journal),
        Recorder("b", journal, fail_shutdown=True),
        Recorder("c", journal, hang_shutdown=True),
    ]
    app = create_app(make_settings(), lifecycles=lifecycles)
    async with app.router.lifespan_context(app):
        pass
    assert journal[3:] == ["stop:c", "stop:b", "stop:a"]
    failures = [r for r in caplog.records if r.getMessage() == "lifecycle_shutdown_failed"]
    assert [r.__dict__["error_type"] for r in failures] == ["TimeoutError", "RuntimeError"]


async def test_startup_is_logged_with_env_and_sha(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(make_settings(build_sha="abc1234"), lifecycles=[Recorder("x", [])])
    with caplog.at_level(logging.INFO, logger="blogwriter.core.app"):
        async with app.router.lifespan_context(app):
            pass
    (started,) = [r for r in caplog.records if r.getMessage() == "app_started"]
    assert (started.__dict__["env"], started.__dict__["sha"], started.__dict__["count"]) == (
        "test",
        "abc1234",
        1,
    )


async def test_a_module_can_be_a_lifecycle(caplog: pytest.LogCaptureFixture) -> None:
    # Packages may pass themselves: module-level startup(app)/shutdown(app) functions.
    journal: list[str] = []
    module = types.ModuleType("fake_package")

    async def startup(app: FastAPI) -> None:
        journal.append("start")

    async def shutdown(app: FastAPI) -> None:
        journal.append("stop")
        raise RuntimeError("SECRET")

    module.__dict__.update(startup=startup, shutdown=shutdown)
    app = create_app(make_settings(), lifecycles=[cast(Lifecycle, module)])
    async with app.router.lifespan_context(app):
        pass
    assert journal == ["start", "stop"]
    (failure,) = [r for r in caplog.records if r.getMessage() == "lifecycle_shutdown_failed"]
    assert failure.__dict__["lifecycle"] == "fake_package"


# ----------------------------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------------------------


async def test_package_routers_are_mounted_behind_auth() -> None:
    app = create_app(make_settings(), routers=[named_router("/api/alpha"), named_router("/api/b")])
    async with asgi_client(app) as client:
        anonymous = await client.get("/api/alpha")
        alpha = await client.get("/api/alpha", headers=OWNER)
        beta = await client.get("/api/b", headers=OWNER)
    assert anonymous.status_code == 401
    assert alpha.json() == {"path": "/api/alpha"}
    assert beta.json() == {"path": "/api/b"}


def test_duplicate_routes_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate route: GET /api/health"):
        create_app(make_settings(), routers=[named_router("/api/health")])
    with pytest.raises(ValueError, match="duplicate route: GET /api/x"):
        create_app(make_settings(), routers=[named_router("/api/x"), named_router("/api/x")])


async def test_openapi_is_authenticated_and_docs_pages_are_off() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        anonymous = await client.get(OPENAPI_URL)
        schema = await client.get(OPENAPI_URL, headers=OWNER)
        docs = await client.get("/docs", headers=OWNER)
        redoc = await client.get("/redoc", headers=OWNER)
    assert anonymous.status_code == 401
    assert schema.status_code == 200
    assert set(schema.json()["paths"]) == {"/api/health", "/api/me"}
    assert docs.status_code == redoc.status_code == 404


async def test_trailing_slashes_are_not_redirected() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/me/", headers=OWNER)
    assert response.status_code == 404


# ----------------------------------------------------------------------------------------------
# Settings handling and the uvicorn factory
# ----------------------------------------------------------------------------------------------


async def test_without_a_cookie_key_each_app_uses_its_own_ephemeral_key() -> None:
    settings = make_settings(cookie_key=None)
    first, second = create_app(settings), create_app(settings)
    async with asgi_client(first) as client:
        issued = (await client.get("/api/me", headers=OWNER)).cookies[COOKIE_NAME]
        assert (
            await client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={issued}"})
        ).is_success
    async with asgi_client(second) as client:
        response = await client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={issued}"})
    assert response.status_code == 401


async def test_configured_cookie_key_survives_a_restart() -> None:
    settings = make_settings()
    cookie = owner_cookie(settings)
    for _ in range(2):
        async with asgi_client(create_app(settings)) as client:
            assert (await client.get("/api/me", headers={"Cookie": cookie})).status_code == 200


def test_prod_settings_build_an_app(caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(env="prod", dev_principal_oid=OWNER_OID)
    assert isinstance(create_app(settings), FastAPI)
    assert any(r.getMessage() == "dev_principal_ignored" for r in caplog.records)


@pytest.mark.parametrize("missing", ["owner_oid", "cookie_key", "public_origin"])
def test_create_app_refuses_unvalidated_prod_settings(missing: str) -> None:
    values: dict[str, Any] = {
        "env": "prod",
        "owner_oid": OWNER_OID,
        "cookie_key": SecretStr(COOKIE_KEY),
        "public_origin": ORIGIN,
        missing: None,
    }
    with pytest.raises(RuntimeError, match="refusing to start"):
        create_app(CoreSettings.model_construct(**values))


def test_create_app_refuses_an_unvalidated_http_origin_in_prod() -> None:
    settings = CoreSettings.model_construct(
        env="prod",
        owner_oid=OWNER_OID,
        cookie_key=SecretStr(COOKIE_KEY),
        public_origin="http://writer.test",
    )
    with pytest.raises(RuntimeError, match="https BW_PUBLIC_ORIGIN"):
        create_app(settings)


@pytest.mark.parametrize("env", ["local", "test"])
def test_create_app_refuses_unvalidated_non_prod_settings_inside_container_apps(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    # CoreSettings refuses this too; model_construct() skips that check, and env='local' would
    # switch the development principal on in a deployed app.
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-blog-writer-dev")
    settings = CoreSettings.model_construct(
        env=env, owner_oid=OWNER_OID, dev_principal_oid=OWNER_OID, public_origin=ORIGIN
    )
    with pytest.raises(RuntimeError, match="inside Azure Container Apps"):
        create_app(settings)


def test_prod_settings_still_build_inside_container_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-blog-writer-dev")
    assert isinstance(create_app(make_settings(env="prod")), FastAPI)


async def test_uvicorn_factory_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_OWNER_OID", OWNER_OID)
    monkeypatch.setenv("BW_BUILD_SHA", "fromenv1")
    application = app_module.app()
    async with asgi_client(application) as client:
        health = await client.get("/api/health")
        me = await client.get("/api/me", headers=OWNER)
    assert health.json() == {"status": "ok", "sha": "fromenv1"}
    assert me.status_code == 200


def test_create_app_defaults_to_environment_settings() -> None:
    application = create_app()
    settings = application.state.settings
    assert isinstance(settings, CoreSettings)
    assert settings.env == "test"
    assert settings.owner_oid is None
