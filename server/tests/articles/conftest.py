"""Fixtures for the articles tests: fake services, the real app with the articles router, a small
version limit on request, and clean module state between tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import httpx
import pytest

from blogwriter.articles import get_router, get_settings
from blogwriter.articles.preview import clear_diagram_cache
from blogwriter.core.testing import asgi_client, make_settings
from blogwriter.db import Store
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services

type StateReader = Callable[[], Awaitable[dict[str, dict[str, str]]]]


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    """No installed services, cached settings or cached diagram renders leak between tests."""
    uninstall_services()
    get_settings.cache_clear()
    clear_diagram_cache()
    yield
    uninstall_services()
    get_settings.cache_clear()
    clear_diagram_cache()


@pytest.fixture
def keep_three(monkeypatch: pytest.MonkeyPatch) -> int:
    """``BW_ARTICLES_KEEP_VERSIONS=3`` for this test."""
    monkeypatch.setenv("BW_ARTICLES_KEEP_VERSIONS", "3")
    get_settings.cache_clear()
    return 3


@pytest.fixture
def services() -> FakeServices:
    return make_services()


@pytest.fixture
def store(services: FakeServices) -> Store:
    return services.store


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    """The real app (auth, CSRF, problems) with the articles router over ``services``."""
    app = make_app(services, [get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        yield http


@pytest.fixture
def store_state(store: Store) -> StateReader:
    """Every document's etag by container (any write changes one)."""

    async def read() -> dict[str, dict[str, str]]:
        containers = ("articles", "article-versions", "ideas", "settings", "stories", "jobs")
        state: dict[str, dict[str, str]] = {}
        for container in containers:
            docs = await store.find(container)
            state[container] = {f"{doc.partition_key}/{doc.id}": doc.etag for doc in docs}
        return state

    return read
