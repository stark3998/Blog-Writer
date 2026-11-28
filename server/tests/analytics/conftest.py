"""Fixtures for the analytics tests: fake services at a fixed time, the app with the analytics
router (the beta import on, fixed brake parameters) and request headers. Typed helpers live in
:mod:`blogwriter.analytics.testing`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI

from blogwriter.analytics import get_router
from blogwriter.analytics.routes import import_settings, spend_params
from blogwriter.analytics.settings import AnalyticsSettings
from blogwriter.analytics.spend import BrakeParams
from blogwriter.analytics.testing import TEST_NOW
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs.testing import FakeClock

SETTINGS = make_settings()
PARAMS = BrakeParams(
    daily_cap_usd=Decimal(25),
    multiplier=Decimal(3),
    window_days=7,
    timezone="America/New_York",
    primary_models={"synthesis": "gpt-6.2-sol", "draft": "claude-opus-5-5"},
)


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()


@pytest.fixture
def services() -> FakeServices:
    return make_services(clock=FakeClock(TEST_NOW))


@pytest.fixture
def read_headers() -> dict[str, str]:
    return principal_headers(OWNER_OID)


@pytest.fixture
def write_headers() -> dict[str, str]:
    return {**principal_headers(OWNER_OID), **core_write_headers(SETTINGS)}


@pytest.fixture
def app(services: FakeServices) -> FastAPI:
    built = make_app(services, routers=[get_router()], settings=SETTINGS)
    built.dependency_overrides[import_settings] = lambda: AnalyticsSettings(
        import_beta=True, import_max_bytes=64 * 1024
    )
    built.dependency_overrides[spend_params] = lambda: PARAMS
    return built


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(app) as http:
        yield http
