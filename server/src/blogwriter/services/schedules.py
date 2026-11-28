"""Schedule seeding (contract I-4): every package's ``schedule_entries`` become scheduler items.

:meth:`ScheduleSeeder.reseed` (the :class:`~blogwriter.domain.ports.ScheduleSeeder` port; run at
startup by ``SchedulesLifecycle`` and by kb after the owner saves the cadence):

1. reads :class:`~blogwriter.domain.settings.CadenceSettings` (``read_singleton``: the plan's
   defaults while the owner never saved it);
2. calls every provider (a package's pure ``schedule_entries(cadence)``). A provider that raises
   is logged (``schedule_provider_failed``) and skipped, and then **nothing is deleted** in step 5:
   its items cannot be told apart from stale ones, and a missing item is worse than a stale one;
3. refuses an entry whose job type is not in ``domain.jobtypes.SCHEDULED_JOB_TYPES`` or that
   generates content (``schedule_entry_refused``, with its id), and a second entry with an id
   already taken (``schedule_entry_duplicate``);
4. puts each accepted entry (``Scheduler.put``: an unchanged definition writes nothing and keeps
   its phase; a changed one restarts from the next slot; a paused item stays paused). An entry
   whose job type no runner registered (a wave-B package not shipped yet) is skipped
   (``schedule_entry_unregistered``);
5. deletes every schedule item whose id starts with ``sched-`` and is not one of the entries put
   in step 4 (refused and unregistered entries included: such an item would fire jobs nothing
   may or can run).

Reseeds are serialized by a lock. **A failed reseed is retried in the background** (5 s
doubling to 5 min) until one succeeds, whoever asked for it (the boot, or kb after the owner
saved the cadence: a store blip at that moment must not leave the old times and timezone firing
until the next start); :meth:`ScheduleSeeder.close` stops the retry. After every reseed that
lands, ``on_seeded`` runs (the boot uses it to start the scheduler only once the items match
the cadence). ``seeded_at``, ``retrying`` and ``last_report`` feed the System page's "schedules
out of date". Logs carry schedule ids, job types and counts only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Protocol

from pydantic import JsonValue

from blogwriter.core.logging import describe_exception
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.jobtypes import SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.ports import ScheduleEntry, ScheduleProvider
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock
from blogwriter.jobs.models import JOBS_CONTAINER
from blogwriter.jobs.scheduler import (
    DEFAULT_MAX_LATENESS,
    SCHEDULE_KIND,
    DailyRule,
    IntervalRule,
    ScheduleItem,
    WeeklyRule,
)
from blogwriter.services._logging import logger

__all__ = [
    "RESEED_RETRY_MAX_SECONDS",
    "RESEED_RETRY_SECONDS",
    "SCHEDULE_ID_PREFIX",
    "ScheduleBackend",
    "ScheduleSeeder",
    "SeedReport",
]

SCHEDULE_ID_PREFIX: Final = "sched-"
DEFAULT_STORE_TIMEOUT: Final = 10.0
RESEED_RETRY_SECONDS: Final = 5.0
RESEED_RETRY_MAX_SECONDS: Final = 300.0


class ScheduleBackend(Protocol):
    """What the seeder uses of :class:`blogwriter.jobs.Scheduler`."""

    async def put(
        self,
        item_id: str,
        *,
        name: str,
        job_type: str,
        timezone: str,
        rule: DailyRule | WeeklyRule | IntervalRule,
        job_input: JsonValue = None,
        max_lateness: timedelta = DEFAULT_MAX_LATENESS,
    ) -> ScheduleItem: ...

    async def delete(self, item_id: str) -> None: ...


@dataclass
class SeedReport:
    """What one reseed did (ids only)."""

    seeded: list[str] = field(default_factory=list[str])
    refused: list[str] = field(default_factory=list[str])
    unregistered: list[str] = field(default_factory=list[str])
    deleted: list[str] = field(default_factory=list[str])
    failed_providers: list[str] = field(default_factory=list[str])

    @property
    def complete(self) -> bool:
        """Every provider answered (only then are stale items deleted)."""
        return not self.failed_providers


def _provider_name(provider: ScheduleProvider) -> str:
    module = getattr(provider, "__module__", None)
    name = getattr(provider, "__qualname__", None) or type(provider).__qualname__
    return f"{module}.{name}" if isinstance(module, str) else str(name)


class ScheduleSeeder:
    """Re-puts every package's schedule entries (see the module docs)."""

    def __init__(
        self,
        store: Store,
        scheduler: ScheduleBackend,
        providers: Sequence[ScheduleProvider],
        *,
        clock: Clock = SYSTEM_CLOCK,
        store_timeout: float = DEFAULT_STORE_TIMEOUT,
        retry_seconds: float = RESEED_RETRY_SECONDS,
        retry_max_seconds: float = RESEED_RETRY_MAX_SECONDS,
        on_seeded: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if retry_seconds <= 0 or retry_max_seconds < retry_seconds:
            raise ValueError("retry delays must be positive and max >= initial")
        self._on_seeded = on_seeded
        self._store = store
        self._scheduler = scheduler
        self._providers = tuple(providers)
        self._clock = clock
        self._timeout = store_timeout
        self._lock = asyncio.Lock()
        self._retry_seconds = retry_seconds
        self._retry_max = retry_max_seconds
        self._retry: asyncio.Task[None] | None = None
        self._closed = False
        self.last_report: SeedReport | None = None
        self.seeded_at: datetime | None = None
        """When the last reseed landed (``None`` before the first)."""

    async def reseed(self) -> None:
        """Seed the items (the port; :meth:`run` returns the report). A failure propagates
        (the caller logs it) and leaves a background retry that reseeds until one succeeds."""
        try:
            await self.run()
        except Exception:
            self._retry_later()
            raise

    @property
    def retrying(self) -> bool:
        """A failed reseed is being retried in the background."""
        return self._retry is not None and not self._retry.done()

    def _retry_later(self) -> None:
        if self._closed or self.retrying:
            return
        self._retry = asyncio.create_task(self._reseed_later(), name="schedule-reseed")

    async def _reseed_later(self) -> None:
        delay = self._retry_seconds
        while True:
            await asyncio.sleep(delay)
            try:
                await self.run()
            except Exception as exc:
                logger.error("schedule_reseed_failed", extra=describe_exception(exc))
                delay = min(delay * 2, self._retry_max)
                continue
            logger.info("schedule_reseed_recovered")
            return

    async def close(self) -> None:
        """Stop a background retry (shutdown)."""
        self._closed = True
        retry, self._retry = self._retry, None
        if retry is not None:
            retry.cancel()
            await asyncio.gather(retry, return_exceptions=True)

    async def run(self) -> SeedReport:
        """One reseed. Store errors propagate (the caller retries); nothing half-deletes."""
        async with self._lock:
            report = SeedReport()
            cadence, _ = await read_singleton(
                self._store, CadenceSettings, now=to_iso(self._clock.now())
            )
            entries = self._entries(cadence, report)
            for entry in entries:
                try:
                    await self._scheduler.put(
                        entry.id,
                        name=entry.name,
                        job_type=entry.job_type,
                        timezone=entry.timezone,
                        rule=entry.rule,
                        job_input=entry.job_input,
                        max_lateness=entry.max_lateness,
                    )
                except ValueError:
                    report.unregistered.append(entry.id)
                    logger.warning(
                        "schedule_entry_unregistered",
                        extra={"schedule_id": entry.id, "job_type": entry.job_type},
                    )
                    continue
                report.seeded.append(entry.id)
            if report.complete:
                await self._delete_stale(set(report.seeded), report)
            else:
                logger.error(
                    "schedule_reseed_partial", extra={"count": len(report.failed_providers)}
                )
            logger.info(
                "schedule_reseeded",
                extra={
                    "seeded": len(report.seeded),
                    "refused": len(report.refused),
                    "unregistered": len(report.unregistered),
                    "deleted": len(report.deleted),
                },
            )
            self.last_report = report
            self.seeded_at = self._clock.now()
        await self._seeded()
        return report

    async def _seeded(self) -> None:
        hook = self._on_seeded
        if hook is None:
            return
        try:
            await hook()
        except Exception as exc:  # the items are seeded; starting what waits on them failed
            logger.error("schedule_seeded_hook_failed", extra=describe_exception(exc))

    def _entries(self, cadence: CadenceSettings, report: SeedReport) -> list[ScheduleEntry]:
        accepted: dict[str, ScheduleEntry] = {}
        for provider in self._providers:
            name = _provider_name(provider)
            try:
                produced = list(provider(cadence))
            except Exception as exc:
                report.failed_providers.append(name)
                logger.error(
                    "schedule_provider_failed", extra={"provider": name, **describe_exception(exc)}
                )
                continue
            for entry in produced:
                if not isinstance(entry, ScheduleEntry):  # pyright: ignore[reportUnnecessaryIsInstance]
                    report.failed_providers.append(name)
                    logger.error("schedule_provider_failed", extra={"provider": name})
                    break
                if entry.job_type not in SCHEDULED_JOB_TYPES or generates_content(entry.job_type):
                    report.refused.append(entry.id)
                    logger.error(
                        "schedule_entry_refused",
                        extra={"schedule_id": entry.id, "job_type": entry.job_type},
                    )
                    continue
                if entry.id in accepted:
                    report.refused.append(entry.id)
                    logger.error("schedule_entry_duplicate", extra={"schedule_id": entry.id})
                    continue
                accepted[entry.id] = entry
        return list(accepted.values())

    async def _delete_stale(self, keep: set[str], report: SeedReport) -> None:
        async with asyncio.timeout(self._timeout):
            docs = await self._store.find(JOBS_CONTAINER, [Filter("kind", "==", SCHEDULE_KIND)])
        for doc in sorted(docs, key=lambda d: d.id):
            if not doc.id.startswith(SCHEDULE_ID_PREFIX) or doc.id in keep:
                continue
            try:
                await self._scheduler.delete(doc.id)
            except NotFound:
                continue  # deleted meanwhile (another replica's reseed)
            report.deleted.append(doc.id)
            logger.info("schedule_item_deleted", extra={"schedule_id": doc.id})
