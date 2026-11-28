"""Fixtures for the stories tests: fake services with saved policies, an app and a job runner.

``services``: every service faked (``domain.testing.make_services``) with the sample blocklist
saved and the AI-use policy confirmed. ``client``: the real app (auth, CSRF, problems) with the
stories router, built with ``core.testing.make_settings()`` (so ``write_headers(make_settings())``
authenticates). ``run_job`` enqueues and runs one job to the end on a real ``JobRunner``;
``drive_job`` runs a job something else enqueued.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.core.testing import asgi_client, make_settings
from blogwriter.domain.ports import services_installed, uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.stories import StoriesSettings, get_router, register_jobs
from blogwriter.stories.testing import save_policies

type RunJob = Callable[[str, JsonValue], Awaitable[Job]]
type DriveJob = Callable[[str], Awaitable[Job]]


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()


@pytest.fixture
async def services() -> FakeServices:
    fake = make_services()
    await save_policies(fake.store)
    return fake


@pytest.fixture
def stories_settings() -> StoriesSettings:
    return StoriesSettings(intake_max_chars=8000, seed_max_chars=60000)


@pytest.fixture
async def client(
    services: FakeServices, stories_settings: StoriesSettings
) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router(stories_settings)], settings=make_settings())
    async with asgi_client(app) as http:
        yield http


async def _drive(services: FakeServices, job_id: str) -> Job:
    registry = JobRegistry()
    register_jobs(registry)
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:

            async def done() -> bool:
                return (await services.jobs.get(job_id)).is_terminal

            await eventually(done, within=10)
        finally:
            await runner.stop()
    return await services.jobs.get(job_id)


@pytest.fixture
def drive_job(services: FakeServices) -> DriveJob:
    """Run the already enqueued job ``job_id`` until it is terminal."""

    async def drive(job_id: str) -> Job:
        return await _drive(services, job_id)

    return drive


@pytest.fixture
def run_job(services: FakeServices) -> RunJob:
    """Enqueue ``job_type`` with ``job_input`` and run it until it is terminal."""

    async def run(job_type: str, job_input: JsonValue) -> Job:
        job = await services.jobs.enqueue(job_type, job_input)
        return await _drive(services, job.id)

    return run
