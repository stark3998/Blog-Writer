"""Fixtures for the signals tests: every service faked, a recording egress, a client.

No network, no Azure: the store is a ``MemoryStore``, the model a ``ScriptedModelRunner`` (it
enforces the real sensitivity policy), time a ``FakeClock`` at 2026-11-26 12:00 UTC. Helpers
live in :mod:`blogwriter.signals.testing`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.core.testing import asgi_client
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.signals import get_router
from blogwriter.signals.settings import SignalsSettings
from blogwriter.signals.testing import T0, RecordingHttp, SignalsSeed, settings_for_tests


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No developer ``BW_SIGNALS_*`` variables and no services left installed between tests."""
    for name in list(os.environ):
        if name.startswith("BW_SIGNALS_"):
            monkeypatch.delenv(name)
    uninstall_services()
    yield
    uninstall_services()


@pytest.fixture
def http() -> RecordingHttp:
    return RecordingHttp()


@pytest.fixture
def services(http: RecordingHttp) -> FakeServices:
    built = make_services()
    built.clock.set(T0)
    built.egress = http  # pyright: ignore[reportAttributeAccessIssue]
    return built


@pytest.fixture
def settings() -> SignalsSettings:
    return settings_for_tests()


@pytest.fixture
def seed(services: FakeServices) -> SignalsSeed:
    return SignalsSeed(services)


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router()])
    async with asgi_client(app) as http_client:
        yield http_client


type Emitted = list[tuple[str, dict[str, JsonValue]]]


@pytest.fixture
def emitted() -> Emitted:
    return []


@pytest.fixture
def emit(emitted: Emitted) -> Callable[[str, Mapping[str, JsonValue]], Awaitable[None]]:
    async def record(event: str, data: Mapping[str, JsonValue]) -> None:
        emitted.append((event, dict(data)))

    return record
