"""Wiring: the jobs lifecycle in the app factory, safe mode, the service and shutdown signals."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, override

import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from sse_starlette.sse import AppStatus

from blogwriter.core.app import create_app
from blogwriter.core.testing import make_settings
from blogwriter.db import Doc, MemoryStore, Store, StoreUnavailable
from blogwriter.jobs import (
    JobRegistry,
    JobsLifecycle,
    JobsService,
    JobsSettings,
    Step,
    StepContext,
    default_registry,
    get_job,
    server_exit_requested,
    shutdown,
    startup,
)
from blogwriter.jobs.bootguard import BootGuard
from blogwriter.jobs.testing import DelegatingStore, eventually, fast_settings


@asynccontextmanager
async def lifespan(
    store: Store, registry: JobRegistry, settings: JobsSettings
) -> AsyncGenerator[JobsService]:
    lifecycle = JobsLifecycle(registry, settings=settings, stop_requested=None)
    app = create_app(make_settings(), lifecycles=[lifecycle])
    app.state.store = store
    async with app.router.lifespan_context(app):
        service: object = app.state.jobs
        assert isinstance(service, JobsService)
        yield service


def has_status(service: JobsService, job_id: str, expected: str) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return (await service.get(job_id)).status == expected

    return check


def gated_registry(started: asyncio.Event, gate: asyncio.Event) -> JobRegistry:
    async def work(ctx: StepContext) -> JsonValue:
        started.set()
        await gate.wait()
        return "done"

    async def ask(ctx: StepContext) -> JsonValue:
        return await ctx.wait_for_input("Which angle?")

    async def flaky(ctx: StepContext) -> JsonValue:
        if ctx.attempt == 1:
            raise RuntimeError("first try fails")
        return "second try"

    registry = JobRegistry()
    registry.register("work", [Step("work", work)])
    registry.register("ask", [Step("ask", ask)])
    registry.register("flaky", [Step("call", flaky)])
    return registry


async def test_the_lifecycle_runs_jobs_and_releases_them_on_shutdown(store: MemoryStore) -> None:
    started, gate = asyncio.Event(), asyncio.Event()
    registry = gated_registry(started, gate)
    async with lifespan(store, registry, fast_settings()) as service:
        assert not service.safe_mode
        assert service.runner.claiming
        assert service.scheduler.running
        job = await service.enqueue("work", {"topic": "x"})
        await asyncio.wait_for(started.wait(), 5)
        assert (await service.get(job.id)).status == "running"
    released = await get_job(store, job.id)  # the app stopped mid-step
    assert (released.status, released.owner, released.attempt) == ("queued", None, 0)
    assert not service.runner.claiming
    assert not service.scheduler.running
    assert service.broker.closed


async def test_service_operations_publish_and_wake_the_runner(store: MemoryStore) -> None:
    started, gate = asyncio.Event(), asyncio.Event()
    registry = gated_registry(started, gate)
    settings = fast_settings(poll_seconds=60, retry_base_seconds=0)  # only wake-ups start work
    async with lifespan(store, registry, settings) as service:
        subscription = service.broker.subscribe()
        asked = await service.enqueue("ask")
        await eventually(has_status(service, asked.id, "waiting_input"))
        await service.provide_input(asked.id, "the cost angle")
        await eventually(has_status(service, asked.id, "succeeded"))
        assert (await service.get(asked.id)).result == "the cost angle"

        flaky = await service.enqueue("flaky")  # retried at once (no backoff) via a wake-up
        await eventually(has_status(service, flaky.id, "succeeded"))

        running = await service.enqueue("work")
        await asyncio.wait_for(started.wait(), 5)
        cancelled = await service.cancel(running.id)
        assert cancelled.cancel_requested
        await eventually(has_status(service, running.id, "cancelled"))
        retried = await service.retry(running.id)
        assert retried.status == "queued"
        gate.set()
        await eventually(has_status(service, running.id, "succeeded"))

        with pytest.raises(ValueError, match="not registered"):
            await service.enqueue("unknown")
        published = 0
        while await subscription.get(max_wait=0) is not None:
            published += 1
        assert published > 10


async def test_a_crash_loop_starts_safe_mode_until_the_owner_exits_it(
    store: MemoryStore,
) -> None:
    settings = fast_settings(crash_loop_max_boots=2)
    guard = BootGuard(store, max_boots=2, window=timedelta(hours=1))  # the lifecycle's clock
    for number in range(2):
        await guard.record_boot(f"earlier:{number}")
    started, gate = asyncio.Event(), asyncio.Event()
    gate.set()
    async with lifespan(store, gated_registry(started, gate), settings) as service:
        assert service.safe_mode
        assert service.boot is not None
        assert service.boot.reason == "crash_loop"
        assert not service.runner.claiming
        assert not service.scheduler.running
        job = await service.enqueue("work")
        await asyncio.sleep(0.1)
        assert (await service.get(job.id)).status == "queued"  # nothing runs in safe mode

        await service.exit_safe_mode()
        assert not service.safe_mode
        await eventually(has_status(service, job.id, "succeeded"))
        await service.exit_safe_mode()  # already out: no-op
    report = await BootGuard(store, max_boots=2, window=timedelta(hours=1)).record_boot("next")
    assert report.boots == 1  # the owner's exit cleared the log


async def test_exiting_safe_mode_twice_at_once_starts_the_loops_once(store: MemoryStore) -> None:
    """A double click on "exit safe mode" used to start the runner twice (RuntimeError)."""
    guard = BootGuard(store, max_boots=1, window=timedelta(hours=1))
    await guard.record_boot("earlier:0")
    async with lifespan(store, JobRegistry(), fast_settings(crash_loop_max_boots=1)) as service:
        assert service.safe_mode
        await asyncio.gather(service.exit_safe_mode(), service.exit_safe_mode())
        assert not service.safe_mode
        assert service.runner.claiming
        assert service.scheduler.running


class BootLogDown(DelegatingStore):
    """The ``settings`` container (the boot log) fails until ``down`` is cleared."""

    down = True

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        if self.down and container == "settings":
            raise StoreUnavailable("down")
        return await self.inner.read(container, id, partition_key=partition_key)

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        if self.down and container == "settings":
            raise StoreUnavailable("down")
        return await self.inner.create(container, body)


async def test_an_unwritable_boot_log_fails_closed_then_recovers_on_its_own(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store blip at boot used to leave the job system off until the owner noticed."""
    monkeypatch.setattr("blogwriter.jobs.lifecycle._BOOT_RETRY_SECONDS", 0.05)
    monkeypatch.setattr("blogwriter.jobs.bootguard._RETRY_SECONDS", 0.01)
    flaky = BootLogDown(store)
    started, gate = asyncio.Event(), asyncio.Event()
    gate.set()
    async with lifespan(flaky, gated_registry(started, gate), fast_settings()) as service:
        assert service.safe_mode
        assert service.boot is not None
        assert service.boot.reason == "boot_log_unavailable"
        job = await service.enqueue("work")
        await asyncio.sleep(0.2)
        assert service.safe_mode  # still failing: still closed
        assert (await service.get(job.id)).status == "queued"
        flaky.down = False
        await eventually(lambda: not service.safe_mode)
        assert service.boot is not None
        assert (service.boot.boots, service.boot.reason) == (1, None)
        await eventually(has_status(service, job.id, "succeeded"))


async def test_a_recovered_boot_log_that_shows_a_crash_loop_stays_in_safe_mode(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("blogwriter.jobs.lifecycle._BOOT_RETRY_SECONDS", 0.05)
    monkeypatch.setattr("blogwriter.jobs.bootguard._RETRY_SECONDS", 0.01)
    guard = BootGuard(store, max_boots=1, window=timedelta(hours=1))
    await guard.record_boot("earlier:0")
    flaky = BootLogDown(store)
    settings = fast_settings(crash_loop_max_boots=1)
    async with lifespan(flaky, JobRegistry(), settings) as service:
        assert service.safe_mode
        flaky.down = False
        await eventually(lambda: service.boot is not None and service.boot.reason == "crash_loop")
        assert service.safe_mode
        assert not service.runner.claiming


async def test_startup_needs_the_store() -> None:
    app = FastAPI()
    with pytest.raises(RuntimeError, match=r"app\.state\.store"):
        await JobsLifecycle(JobRegistry()).startup(app)
    await JobsLifecycle().shutdown(app)  # nothing started: nothing to stop


async def test_module_level_hooks_use_the_default_registry(store: MemoryStore) -> None:
    app = FastAPI()
    app.state.store = store
    await startup(app)
    try:
        service: object = app.state.jobs
        assert isinstance(service, JobsService)
        assert service.registry is default_registry()
    finally:
        await shutdown(app)
    assert service.broker.closed


def test_server_exit_requested_reads_uvicorn_state(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not server_exit_requested()
    monkeypatch.setattr(AppStatus, "should_exit", True)
    assert server_exit_requested()
    monkeypatch.setattr(AppStatus, "should_exit", False)

    class FakeServer:
        should_exit = False

        def handle_exit(self, sig: int, frame: object) -> None:
            self.should_exit = True

    server = FakeServer()

    def getsignal(signalnum: int) -> object:
        return server.handle_exit

    monkeypatch.setattr(signal, "getsignal", getsignal)
    assert not server_exit_requested()
    server.handle_exit(signal.SIGTERM, None)
    assert server_exit_requested()
