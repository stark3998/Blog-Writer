"""Fixtures for the illustrations tests: fake services (a scripted model runner that also
generates scripted images), the real app with the illustrations and articles routers, and clean
module state between tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from blogwriter.articles import get_router as articles_router
from blogwriter.articles import get_settings as articles_settings
from blogwriter.core.testing import asgi_client, make_settings
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, make_app, make_services
from blogwriter.illustrations import get_router
from blogwriter.storage import storage_settings


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    """No installed services or cached settings leak between tests."""
    uninstall_services()
    articles_settings.cache_clear()
    storage_settings.cache_clear()
    yield
    uninstall_services()
    articles_settings.cache_clear()
    storage_settings.cache_clear()


@pytest.fixture
def ai() -> ScriptedModelRunner:
    return ScriptedModelRunner()


@pytest.fixture
def services(ai: ScriptedModelRunner) -> FakeServices:
    return make_services(ai=ai)


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    """The real app (auth, CSRF, problems) with the illustrations and articles routers."""
    app = make_app(services, [get_router(), articles_router()], settings=make_settings())
    async with asgi_client(app) as http:
        yield http
