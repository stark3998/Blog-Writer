"""Fixtures for the pipeline tests: fake services at :data:`T0` whose model runner streams like
the router, the real app with the pipeline router, and clean module state between tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from blogwriter.articles import get_settings as articles_settings
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, ScriptedModelRunner, make_app, make_services
from blogwriter.gates import gates_settings
from blogwriter.jobs.testing import FakeClock
from blogwriter.pipeline import get_router, pipeline_settings
from blogwriter.pipeline.testing import T0, RouterLikeRunner

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **core_write_headers(make_settings())}


@pytest.fixture(autouse=True)
def clean_state() -> Iterator[None]:
    """No installed services or cached settings leak between tests."""
    uninstall_services()
    pipeline_settings.cache_clear()
    gates_settings.cache_clear()
    articles_settings.cache_clear()
    yield
    uninstall_services()
    pipeline_settings.cache_clear()
    gates_settings.cache_clear()
    articles_settings.cache_clear()


@pytest.fixture
def ai() -> ScriptedModelRunner:
    return ScriptedModelRunner()


@pytest.fixture
def services(ai: ScriptedModelRunner) -> FakeServices:
    return make_services(clock=FakeClock(T0), ai=RouterLikeRunner(ai))


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    """The real app (auth, CSRF, problems) with the pipeline router over ``services``."""
    app = make_app(services, [get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        yield http
