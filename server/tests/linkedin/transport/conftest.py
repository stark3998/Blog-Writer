"""Fixtures for the LinkedIn transport tests: a scripted LinkedIn behind ``httpx.MockTransport``
(no network), faked services on a fake clock, and the real app with the transport routes.

Helpers live in :mod:`blogwriter.linkedin.transport.testing` (test files import them there)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from blogwriter.core.testing import asgi_client, make_settings
from blogwriter.domain.ports import uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs.testing import FakeClock
from blogwriter.linkedin.settings import LinkedInSettings
from blogwriter.linkedin.transport import get_router
from blogwriter.linkedin.transport.client import LinkedInClient, client_installed
from blogwriter.linkedin.transport.testing import FakeLinkedIn, fake_client, make_linkedin_settings

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_services_left_installed() -> Iterator[None]:
    yield
    uninstall_services()


@pytest.fixture
def linkedin() -> FakeLinkedIn:
    return FakeLinkedIn()


@pytest.fixture
def li_settings() -> LinkedInSettings:
    return make_linkedin_settings()


@pytest.fixture
async def li_client(
    linkedin: FakeLinkedIn, li_settings: LinkedInSettings
) -> AsyncIterator[LinkedInClient]:
    client = fake_client(linkedin, li_settings)
    with client_installed(client):
        yield client
    await client.aclose()


@pytest.fixture
def services() -> FakeServices:
    return make_services(clock=FakeClock(NOW))


@pytest.fixture
def app(services: FakeServices, li_settings: LinkedInSettings) -> FastAPI:
    application = make_app(services, routers=[get_router()], settings=make_settings())
    application.state.linkedin_settings = li_settings
    return application


@pytest.fixture
async def http(app: FastAPI, li_client: LinkedInClient) -> AsyncIterator[httpx.AsyncClient]:
    del li_client  # installed for the routes and steps
    async with asgi_client(app) as client:
        yield client
