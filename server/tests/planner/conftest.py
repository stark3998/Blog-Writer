"""Fixtures for the planner tests: every service faked, a fixed clock, the app and headers.

No network, no Azure: the store is a ``MemoryStore``, the model a ``ScriptedModelRunner`` (it
enforces the real sensitivity policy), time a ``FakeClock`` at Thursday 2026-11-26 12:00 UTC
(07:00 in New York). Typed helpers live in :mod:`blogwriter.planner.testing`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs.testing import FakeClock
from blogwriter.planner import get_router
from blogwriter.planner.testing import T0

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
