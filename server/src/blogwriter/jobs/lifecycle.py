"""Wiring: the jobs service on ``app.state.jobs`` and its lifecycle (ARCHITECTURE rule 7).

:class:`JobsLifecycle` (or the module-level :func:`startup`/:func:`shutdown`, which use
:func:`default_registry`) builds, per process: one :class:`~blogwriter.jobs.broker.Broker`, the
crash-loop guard, one :class:`~blogwriter.jobs.runner.JobRunner` and one
:class:`~blogwriter.jobs.scheduler.Scheduler`, all sharing one claim owner (replica name + boot
UUID). It needs the store on ``app.state.store``, so list it after ``blogwriter.db``::

    create_app(routers=[jobs.get_router()], lifecycles=[db, JobsLifecycle(registry)])

Startup records the boot; in safe mode (crash loop) neither the runner nor the scheduler starts.
When the boot log cannot be written at startup the service also starts in safe mode (fail
closed), but keeps trying in the background and leaves safe mode by itself once this boot is
recorded and is not part of a crash loop: a store blip during a deploy does not leave the job
system off until the owner notices.
Shutdown stops the scheduler, then the runner (it stops claiming at once, gives running steps
``shutdown_grace_seconds`` and releases the rest to ``queued``), then closes the broker, which
ends the open SSE streams.

"Stop claiming immediately on shutdown": uvicorn waits for open connections, SSE streams
included, before it runs the lifespan shutdown. The runner and the scheduler therefore also poll
:func:`server_exit_requested`, which turns true the moment uvicorn receives SIGTERM or SIGINT.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from fastapi import FastAPI
from pydantic import JsonValue
from sse_starlette.sse import AppStatus
from starlette.requests import Request

from blogwriter.core.errors import ProblemError
from blogwriter.db import Store
from blogwriter.jobs._logging import logger
from blogwriter.jobs.bootguard import BootGuard, BootReport
from blogwriter.jobs.broker import Broker
from blogwriter.jobs.claims import new_owner
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock
from blogwriter.jobs.models import Job
from blogwriter.jobs.queue import enqueue, get_job, provide_input, request_cancel, retry_job
from blogwriter.jobs.runner import JobRegistry, JobRunner, QuarantineHook
from blogwriter.jobs.scheduler import Scheduler
from blogwriter.jobs.settings import JobsSettings, replica_name

__all__ = [
    "JobsLifecycle",
    "JobsService",
    "default_registry",
    "get_jobs_service",
    "server_exit_requested",
    "shutdown",
    "startup",
]

_DEFAULT_REGISTRY = JobRegistry()
_BOOT_RETRY_SECONDS: Final = 5.0
"""First pause before recording the boot again after the boot log was unavailable."""
_BOOT_RETRY_MAX_SECONDS: Final = 300.0


def default_registry() -> JobRegistry:
    """The process-wide registry the module-level :func:`startup` uses.

    Packages register their job types into it at import time.
    """
    return _DEFAULT_REGISTRY


def server_exit_requested() -> bool:
    """Whether uvicorn has been asked to exit (SIGTERM/SIGINT received).

    sse-starlette patches ``uvicorn.Server.handle_exit`` to set ``AppStatus.should_exit``; when
    uvicorn installed its signal handlers before that patch, the handler is the server's bound
    ``handle_exit`` and the server's own ``should_exit`` flag is read instead.
    """
    if AppStatus.should_exit:
        return True
    handler: object = signal.getsignal(signal.SIGTERM)
    server: object = getattr(handler, "__self__", None)
    flag: object = getattr(server, "should_exit", False)
    return flag is True


class JobsService:
    """The jobs subsystem of one process, on ``app.state.jobs``."""

    def __init__(
        self,
        *,
        store: Store,
        registry: JobRegistry,
        settings: JobsSettings,
        clock: Clock,
        owner: str,
        broker: Broker,
        runner: JobRunner,
        scheduler: Scheduler,
        boot_guard: BootGuard,
    ) -> None:
        self.store = store
        self.registry = registry
        self.settings = settings
        self.clock = clock
        self.owner = owner
        self.broker = broker
        self.runner = runner
        self.scheduler = scheduler
        self._boot_guard = boot_guard
        self._boot: BootReport | None = None
        self._started = False
        self._mode_lock = asyncio.Lock()
        self._boot_retry: asyncio.Task[None] | None = None

    @property
    def safe_mode(self) -> bool:
        """True when the crash-loop guard kept the runner and the scheduler off."""
        return self._boot is not None and self._boot.safe_mode and not self._started

    @property
    def boot(self) -> BootReport | None:
        return self._boot

    async def start(self) -> None:
        """Record the boot, then start the runner and the scheduler unless in safe mode."""
        self._boot = await self._boot_guard.record_boot(self.owner)
        if self._boot.safe_mode:
            logger.error("jobs_safe_mode", extra={"boots": self._boot.boots, "safe_mode": True})
            if self._boot.reason == "boot_log_unavailable":
                self._boot_retry = asyncio.create_task(
                    self._record_boot_later(), name="jobs-boot-retry"
                )
            return
        await self._start_loops()

    async def _record_boot_later(self) -> None:
        """Record this boot once the boot log is reachable again (backoff up to 5 min), then
        leave safe mode unless the boot turns out to be part of a crash loop."""
        delay = _BOOT_RETRY_SECONDS
        while True:
            await asyncio.sleep(delay)
            report = await self._boot_guard.record_boot(self.owner)
            if report.reason == "boot_log_unavailable":
                delay = min(delay * 2, _BOOT_RETRY_MAX_SECONDS)
                continue
            async with self._mode_lock:
                if self._started:
                    return  # the owner left safe mode meanwhile
                self._boot = report
                if report.safe_mode:
                    logger.error("jobs_safe_mode", extra={"boots": report.boots, "safe_mode": True})
                    return
                await self._start_loops()
            logger.warning("jobs_safe_mode_exited", extra={"boots": report.boots})
            return

    async def _start_loops(self) -> None:
        await self.runner.start()
        await self.scheduler.start()
        self._started = True

    async def exit_safe_mode(self) -> None:
        """Owner action: clear the boot log and start the runner and the scheduler.

        Idempotent: concurrent calls (a double click) start the loops once.
        """
        async with self._mode_lock:
            if not self.safe_mode:
                return
            await self._boot_guard.reset()
            await self._start_loops()
        logger.warning("jobs_safe_mode_exited", extra={"safe_mode": False})

    async def stop(self) -> None:
        """Stop the scheduler, release running jobs, close the broker (ends SSE streams)."""
        if self._boot_retry is not None:
            self._boot_retry.cancel()
            await asyncio.gather(self._boot_retry, return_exceptions=True)
        await self.scheduler.stop()
        await self.runner.stop(release=True)
        self.broker.close()

    # Job operations (each publishes its events and nudges the local runner) -------------------

    async def enqueue(
        self,
        job_type: str,
        input: JsonValue = None,
        *,
        job_id: str | None = None,
        run_after: datetime | None = None,
    ) -> Job:
        """Queue a job of a registered type (``ValueError`` for unknown types)."""
        definition = self.registry.get(job_type)
        if definition is None:
            raise ValueError(f"job type {job_type!r} is not registered")
        job = await enqueue(
            self.store,
            job_type,
            input,
            job_id=job_id,
            steps=definition.step_names,
            run_after=run_after,
            max_attempts=self.settings.max_attempts,
            clock=self.clock,
            broker=self.broker,
            store_timeout=self.settings.store_timeout_seconds,
        )
        self.runner.wake()
        return job

    async def get(self, job_id: str) -> Job:
        return await get_job(self.store, job_id, store_timeout=self.settings.store_timeout_seconds)

    async def cancel(self, job_id: str) -> Job:
        job = await request_cancel(
            self.store,
            job_id,
            clock=self.clock,
            broker=self.broker,
            store_timeout=self.settings.store_timeout_seconds,
        )
        if job.status == "running" and job.cancel_requested:
            self.runner.cancel_local(job_id)
        return job

    async def provide_input(self, job_id: str, value: JsonValue, *, key: str | None = None) -> Job:
        job = await provide_input(
            self.store,
            job_id,
            value,
            key=key,
            clock=self.clock,
            broker=self.broker,
            store_timeout=self.settings.store_timeout_seconds,
        )
        self.runner.wake()
        return job

    async def retry(self, job_id: str) -> Job:
        job = await retry_job(
            self.store,
            job_id,
            clock=self.clock,
            broker=self.broker,
            store_timeout=self.settings.store_timeout_seconds,
        )
        self.runner.wake()
        return job


class JobsLifecycle:
    """``startup(app)`` / ``shutdown(app)`` for :func:`blogwriter.core.app.create_app`."""

    def __init__(
        self,
        registry: JobRegistry | None = None,
        *,
        settings: JobsSettings | None = None,
        clock: Clock = SYSTEM_CLOCK,
        on_quarantine: QuarantineHook | None = None,
        stop_requested: Callable[[], bool] | None = server_exit_requested,
    ) -> None:
        self._registry = registry if registry is not None else default_registry()
        self._settings = settings
        self._clock = clock
        self._on_quarantine = on_quarantine
        self._stop_requested = stop_requested

    async def startup(self, app: FastAPI) -> None:
        store: object = getattr(app.state, "store", None)
        if not isinstance(store, Store):
            raise RuntimeError("blogwriter.jobs needs app.state.store; start blogwriter.db first")
        settings = self._settings if self._settings is not None else JobsSettings()
        owner = new_owner(replica_name(settings))
        broker = Broker(
            queue_size=settings.broker_queue_size,
            max_subscribers=settings.broker_max_subscribers,
        )
        runner = JobRunner(
            store,
            self._registry,
            settings=settings,
            owner=owner,
            clock=self._clock,
            broker=broker,
            on_quarantine=self._on_quarantine,
            stop_requested=self._stop_requested,
        )
        scheduler = Scheduler(
            store,
            settings=settings,
            owner=owner,
            clock=self._clock,
            registry=self._registry,
            broker=broker,
            on_enqueue=runner.wake,
            stop_requested=self._stop_requested,
        )
        guard = BootGuard(
            store,
            max_boots=settings.crash_loop_max_boots,
            window=timedelta(seconds=settings.crash_loop_window_seconds),
            clock=self._clock,
            store_timeout=settings.store_timeout_seconds,
        )
        service = JobsService(
            store=store,
            registry=self._registry,
            settings=settings,
            clock=self._clock,
            owner=owner,
            broker=broker,
            runner=runner,
            scheduler=scheduler,
            boot_guard=guard,
        )
        app.state.jobs = service
        await service.start()

    async def shutdown(self, app: FastAPI) -> None:
        service: object = getattr(app.state, "jobs", None)
        if isinstance(service, JobsService):
            await service.stop()


async def startup(app: FastAPI) -> None:
    """Start the jobs subsystem with :func:`default_registry` and ``BW_JOBS_*`` settings."""
    await JobsLifecycle().startup(app)


async def shutdown(app: FastAPI) -> None:
    """Stop the jobs subsystem started by :func:`startup` (or a :class:`JobsLifecycle`)."""
    await JobsLifecycle().shutdown(app)


async def get_jobs_service(request: Request) -> JobsService:
    """FastAPI dependency: the running jobs service (503 problem if it is not started).

    ``async`` so FastAPI resolves it on the event loop instead of a worker thread.
    """
    service: object = getattr(request.app.state, "jobs", None)
    if not isinstance(service, JobsService):
        raise ProblemError(503, code="jobs_unavailable", detail="The job service is not running.")
    return service
