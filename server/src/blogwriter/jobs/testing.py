"""Test helpers for code that uses the jobs package (no network, no real time for leases).

Example::

    clock = FakeClock(datetime(2026, 11, 26, 12, tzinfo=UTC))
    runner = JobRunner(MemoryStore(), registry, clock=clock, settings=fast_settings())
    ...
    clock.advance(66)          # leases (60 s + 5 s skew) are now expired for this clock
    await eventually(lambda: runner.stats.claimed == 2)
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, override

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.db import Doc, Filter, OrderBy, Store
from blogwriter.jobs.settings import JobsSettings

__all__ = ["DelegatingStore", "FakeClock", "eventually", "fast_settings"]


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        moment = start if start is not None else datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
        if moment.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware start")
        self._now = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float | timedelta) -> datetime:
        """Move forward and return the new time."""
        step = seconds if isinstance(seconds, timedelta) else timedelta(seconds=seconds)
        if step < timedelta(0):
            raise ValueError("a clock does not go backwards")
        self._now += step
        return self._now

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FakeClock needs timezone-aware times")
        self._now = moment.astimezone(UTC)


class _ArgumentsOnlySettings(JobsSettings):
    """``JobsSettings`` that reads only constructor arguments, never the environment."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def fast_settings(**overrides: object) -> JobsSettings:
    """Production lease timing (60/15/5 s, driven by a fake clock) with fast real-time loops.

    The claim loop polls every 10 ms and renewals happen every 15 real seconds, so a test only
    sees renewals when it asks for them (``renew_seconds=...``). Environment variables are
    ignored.
    """
    values: dict[str, object] = {
        "lease_seconds": 60.0,
        "renew_seconds": 15.0,
        "skew_seconds": 5.0,
        "poll_seconds": 0.01,
        "concurrency": 2,
        "retry_base_seconds": 30.0,
        "retry_max_seconds": 900.0,
        "shutdown_grace_seconds": 0.0,
        "store_timeout_seconds": 5.0,
        "replica_name": "test-replica",
    }
    values.update(overrides)
    return _ArgumentsOnlySettings.model_validate(values)


async def eventually(
    condition: Callable[[], bool] | Callable[[], Awaitable[bool]],
    *,
    within: float = 5.0,
    interval: float = 0.01,
) -> None:
    """Wait until ``condition()`` is true; ``AssertionError`` after ``within`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while True:
        result = condition()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        if loop.time() >= deadline:
            raise AssertionError(f"condition not met within {within} s")
        await asyncio.sleep(interval)


class DelegatingStore:
    """A :class:`~blogwriter.db.Store` that forwards every call to ``inner``.

    Subclass it and override a method to inject faults (lost responses, partitions, delays).
    """

    def __init__(self, inner: Store) -> None:
        self.inner = inner

    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        return await self.inner.read(container, id, partition_key=partition_key)

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        return await self.inner.create(container, body)

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        return await self.inner.replace(container, body, if_match=if_match)

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        return await self.inner.upsert(container, body)

    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        await self.inner.delete(container, id, partition_key=partition_key, if_match=if_match)

    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
    ) -> list[Doc]:
        return await self.inner.find(
            container, filters, order_by=order_by, limit=limit, partition_key=partition_key
        )

    async def ensure_containers(self) -> None:
        await self.inner.ensure_containers()

    async def close(self) -> None:
        await self.inner.close()
