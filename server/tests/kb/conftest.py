"""Fixtures for the kb tests: every service faked, the real app with the kb router, a client."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from blogwriter.core.testing import asgi_client
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.kb import get_router


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No developer ``BW_AI_*`` variables (the models view reads ``AISettings``) and no
    services left installed between tests."""
    for name in list(os.environ):
        if name.startswith("BW_AI_"):
            monkeypatch.delenv(name)
    uninstall_services()
    yield
    uninstall_services()


@pytest.fixture
def services() -> FakeServices:
    return make_services()


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router()])
    async with asgi_client(app) as http:
        yield http
