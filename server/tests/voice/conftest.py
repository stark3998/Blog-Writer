"""Fixtures for the voice tests: fake services at a fixed time, the app with the voice router,
and request headers. Typed helpers live in :mod:`blogwriter.voice.testing`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs.testing import FakeClock
from blogwriter.voice import get_router

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SETTINGS = make_settings()


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()


@pytest.fixture
def services() -> FakeServices:
    return make_services(clock=FakeClock(T0))


@pytest.fixture
def read_headers() -> dict[str, str]:
    return principal_headers(OWNER_OID)


@pytest.fixture
def write_headers() -> dict[str, str]:
    return {**principal_headers(OWNER_OID), **core_write_headers(SETTINGS)}


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        yield http
