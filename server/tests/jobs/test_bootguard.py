"""The crash-loop guard: boot records in ``settings``, safe mode past N boots per hour."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, override

from blogwriter.db import Doc, MemoryStore, Store, StoreUnavailable
from blogwriter.jobs.bootguard import BOOT_LOG_ID, SETTINGS_CONTAINER, BootGuard, BootReport
from blogwriter.jobs.testing import DelegatingStore, FakeClock


def guard(store: Store, clock: FakeClock, max_boots: int = 3) -> BootGuard:
    return BootGuard(store, max_boots=max_boots, window=timedelta(hours=1), clock=clock)


async def test_more_than_n_boots_in_an_hour_start_safe_mode(
    store: MemoryStore, clock: FakeClock
) -> None:
    reports: list[BootReport] = []
    for number in range(4):
        reports.append(await guard(store, clock).record_boot(f"replica:{number}"))
        clock.advance(60)
    assert [(report.boots, report.safe_mode) for report in reports] == [
        (1, False),
        (2, False),
        (3, False),
        (4, True),
    ]
    assert reports[-1].reason == "crash_loop"
    log = (await store.read(SETTINGS_CONTAINER, BOOT_LOG_ID)).body
    assert [entry["owner"] for entry in log["boots"]] == [f"replica:{n}" for n in range(4)]


async def test_old_boots_age_out_of_the_window(store: MemoryStore, clock: FakeClock) -> None:
    for number in range(4):
        await guard(store, clock).record_boot(f"replica:{number}")
    clock.advance(timedelta(hours=1, seconds=1))
    report = await guard(store, clock).record_boot("replica:later")
    assert (report.boots, report.safe_mode) == (1, False)


async def test_reset_clears_the_log(store: MemoryStore, clock: FakeClock) -> None:
    for number in range(4):
        await guard(store, clock).record_boot(f"replica:{number}")
    await guard(store, clock).reset()
    await guard(store, clock).reset()  # nothing to delete: still fine
    assert (await guard(store, clock).record_boot("replica:fresh")).boots == 1


async def test_concurrent_boots_are_all_recorded(store: MemoryStore, clock: FakeClock) -> None:
    reports = await asyncio.gather(
        *(guard(store, clock, max_boots=10).record_boot(f"replica:{n}") for n in range(5))
    )
    assert sorted(report.boots for report in reports) == [1, 2, 3, 4, 5]
    log = (await store.read(SETTINGS_CONTAINER, BOOT_LOG_ID)).body
    assert len(log["boots"]) == 5


async def test_a_store_that_cannot_record_the_boot_fails_closed(
    store: MemoryStore, clock: FakeClock
) -> None:
    class Down(DelegatingStore):
        @override
        async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
            raise StoreUnavailable("down")

        @override
        async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
            raise StoreUnavailable("down")

    report = await guard(Down(store), clock).record_boot("replica:1")
    assert report.safe_mode
    assert report.reason == "boot_log_unavailable"


async def test_garbage_in_the_log_is_ignored(store: MemoryStore, clock: FakeClock) -> None:
    await store.create(
        SETTINGS_CONTAINER,
        {"id": BOOT_LOG_ID, "boots": ["junk", {"at": "yesterday"}, {"owner": "x"}, 7]},
    )
    assert (await guard(store, clock).record_boot("replica:1")).boots == 1


class LostResponses(DelegatingStore):
    """The next ``lost`` boot-log writes land, then their responses are lost (unknown outcome)."""

    def __init__(self, inner: Store, lost: int) -> None:
        super().__init__(inner)
        self.lost = lost

    def _lose(self) -> None:
        if self.lost:
            self.lost -= 1
            raise StoreUnavailable("response lost")

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        doc = await self.inner.create(container, body)
        self._lose()
        return doc

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        doc = await self.inner.replace(container, body, if_match=if_match)
        self._lose()
        return doc


async def test_a_boot_whose_write_outcome_was_unknown_is_counted_once(
    store: MemoryStore, clock: FakeClock
) -> None:
    """The retry used to append the same boot again, so flaky writes reached safe mode early."""
    await guard(store, clock).record_boot("replica:earlier")
    report = await guard(LostResponses(store, lost=2), clock).record_boot("replica:now")
    assert (report.boots, report.safe_mode) == (2, False)
    log = (await store.read(SETTINGS_CONTAINER, BOOT_LOG_ID)).body
    assert [entry["owner"] for entry in log["boots"]] == ["replica:earlier", "replica:now"]
