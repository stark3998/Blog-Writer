"""Fixtures for the engagement tests: fake services with saved policies, the app, a job runner.

``services``: every service faked (``domain.testing.make_services``) with the test blocklist
saved and the AI-use policy confirmed. ``client``: the real app (auth, CSRF, problems) with the
engagement router; send ``READ`` on GET and ``WRITE`` on every other request. ``run_purge``
enqueues ``engagement.purge`` and runs it on a real ``JobRunner``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import httpx
import pytest

from blogwriter.core.testing import asgi_client, make_settings
from blogwriter.domain.jobtypes import ENGAGEMENT_PURGE
from blogwriter.domain.ports import services_installed, uninstall_services
from blogwriter.domain.testing import FakeServices, make_app, make_services
from blogwriter.engagement import get_router, register_jobs
from blogwriter.engagement.testing import save_policies
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.queue import enqueue, get_job
from blogwriter.jobs.testing import eventually, fast_settings

type RunPurge = Callable[[], Awaitable[Job]]


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
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        yield http


@pytest.fixture
def run_purge(services: FakeServices) -> RunPurge:
    """Enqueue ``engagement.purge`` (as the scheduler would) and run it to the end."""
    counter = iter(range(1, 1000))

    async def run() -> Job:
        registry = JobRegistry()
        register_jobs(registry)
        job_type = registry.get(ENGAGEMENT_PURGE)
        assert job_type is not None
        job_id = f"job-purge-{next(counter)}"
        await enqueue(
            services.store,
            ENGAGEMENT_PURGE,
            {},
            job_id=job_id,
            steps=job_type.step_names,
            clock=services.clock,
        )
        runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
        with services_installed(services):
            await runner.start()
            try:

                async def done() -> bool:
                    return (await get_job(services.store, job_id)).is_terminal

                await eventually(done, within=10)
            finally:
                await runner.stop()
        return await get_job(services.store, job_id)

    return run
