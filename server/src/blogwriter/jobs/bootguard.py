"""Crash-loop guard: boot records in the ``settings`` container (plan: "Reliability model").

Every start appends a boot record (time + claim owner) to one document,
``settings/jobs-boot-log``, with a CAS write. When the boots inside the window (default: the
last hour), this one included, exceed ``crash_loop_max_boots``, the app starts in **safe mode**:
the API and UI work, but the runner claims nothing and the scheduler does not tick. A job that
crashes the process on every attempt can therefore not keep the app in a restart loop.

Safe mode ends by itself on a later boot once the old boots have aged out of the window, or
when the owner resets the log (:meth:`BootGuard.reset`, wired to
``JobsService.exit_safe_mode``). If the boot record cannot be written at all, the guard fails
closed and reports safe mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal, cast

from blogwriter.db import Conflict, NotFound, PreconditionFailed, Store, StoreError
from blogwriter.jobs._logging import logger
from blogwriter.jobs.claims import bounded
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock, is_timestamp, parse_iso, to_iso

__all__ = ["BOOT_LOG_ID", "SETTINGS_CONTAINER", "BootGuard", "BootReport"]

SETTINGS_CONTAINER: Final = "settings"
BOOT_LOG_ID: Final = "jobs-boot-log"
_ROUNDS: Final = 5
_RETRY_SECONDS: Final = 0.2


@dataclass(frozen=True, slots=True)
class BootReport:
    """The guard's verdict for this boot."""

    boots: int
    """Boots inside the window, this one included (0 if the log could not be written)."""
    safe_mode: bool
    reason: Literal["crash_loop", "boot_log_unavailable"] | None = None


class BootGuard:
    """Records boots and decides whether this one starts in safe mode."""

    def __init__(
        self,
        store: Store,
        *,
        max_boots: int,
        window: timedelta,
        clock: Clock = SYSTEM_CLOCK,
        store_timeout: float = 10.0,
    ) -> None:
        if max_boots < 1:
            raise ValueError("max_boots must be at least 1")
        self._store = store
        self._max_boots = max_boots
        self._window = window
        self._clock = clock
        self._timeout = store_timeout

    async def record_boot(self, owner: str) -> BootReport:
        """Append this boot and report whether the app must start in safe mode."""
        for round_number in range(_ROUNDS):
            try:
                boots = await self._append(owner)
            except (PreconditionFailed, Conflict):
                continue  # another replica booted at the same moment; read again
            except (StoreError, TimeoutError) as exc:
                logger.warning(
                    "jobs_boot_log_failed",
                    extra={"error_type": type(exc).__name__, "attempt": round_number + 1},
                )
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            safe_mode = boots > self._max_boots
            report = BootReport(
                boots=boots, safe_mode=safe_mode, reason="crash_loop" if safe_mode else None
            )
            log = logger.error if safe_mode else logger.info
            log("jobs_boot_recorded", extra={"boots": boots, "safe_mode": safe_mode})
            return report
        logger.error("jobs_boot_log_unavailable", extra={"safe_mode": True})
        return BootReport(boots=0, safe_mode=True, reason="boot_log_unavailable")

    async def reset(self) -> None:
        """Forget every boot record (the owner leaves safe mode)."""
        try:
            await bounded(self._store.delete(SETTINGS_CONTAINER, BOOT_LOG_ID), self._timeout)
        except NotFound:
            return
        logger.info("jobs_boot_log_reset")

    async def _append(self, owner: str) -> int:
        now = self._clock.now()
        cutoff = now - self._window
        try:
            doc = await bounded(self._store.read(SETTINGS_CONTAINER, BOOT_LOG_ID), self._timeout)
        except NotFound:
            doc = None
        recent: list[dict[str, object]] = []
        if doc is not None:
            raw: object = doc.body.get("boots")
            entries = cast(list[object], raw) if isinstance(raw, list) else []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                record = cast(dict[str, object], entry)
                at = record.get("at")
                if isinstance(at, str) and is_timestamp(at) and parse_iso(at) > cutoff:
                    recent.append({"at": at, "owner": record.get("owner")})
        if any(record["owner"] == owner for record in recent):
            # An earlier attempt's write landed although its response was lost: the owner
            # (replica + boot UUID) names this boot, so it is not counted twice.
            return len(recent)
        recent.append({"at": to_iso(now), "owner": owner})
        keep = max(2 * self._max_boots, 20)
        body: dict[str, object] = {
            "id": BOOT_LOG_ID,
            "kind": "jobs-boot-log",
            "boots": recent[-keep:],
            "updatedAt": to_iso(now),
        }
        if doc is None:
            await bounded(self._store.create(SETTINGS_CONTAINER, body), self._timeout)
        else:
            await bounded(
                self._store.replace(SETTINGS_CONTAINER, body, if_match=doc.etag), self._timeout
            )
        return len(recent)
