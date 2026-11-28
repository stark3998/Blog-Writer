"""The scheduler: schedule items fired by a 60 s tick (plan: "Weekly production calendar").

A schedule item is a claim document in the ``jobs`` container (``kind: "schedule"``). It names a
job type to enqueue and a local-time rule in an IANA time zone:

- ``daily``: every day at ``HH:MM`` local time;
- ``weekly``: on the given weekdays at ``HH:MM`` local time;
- ``interval``: every N seconds from an anchor (absolute time, so DST does not apply).

``nextRunAt`` is the next slot in UTC, computed with :mod:`zoneinfo` and DST-safe:

- a local time that does not exist (spring forward: 02:30 on 2026-03-08 in New York) fires at
  the same offset after the gap, 03:30 EDT (PEP 495 ``fold=0``), once;
- a local time that happens twice (fall back: 01:30 on 2026-11-01) fires once, at its first
  occurrence (01:30 EDT).

Every ``tick_seconds`` the scheduler finds the due items, claims each with the shared claim
mechanism (:mod:`blogwriter.jobs.claims`), enqueues its job with a deterministic id
(``<itemId>-<slot>``; a retry after a crash gets ``Conflict`` instead of a second job) and
advances ``nextRunAt`` while releasing the claim. Two replicas ticking at once still enqueue
one job per slot.

Missed slots (downtime):

- several missed slots collapse into the most recent one (no burst of catch-up runs; the rest
  are counted in ``skippedRuns``);
- when even that slot is more than ``maxLatenessSeconds`` late (default 2 h, the plan's grace
  period) the item turns ``needs_confirmation`` instead of firing. Nothing runs until the owner
  calls :meth:`Scheduler.confirm` (run the missed slot now, or skip it).

The clock is injected, so tests drive :meth:`Scheduler.tick` through any date.

Liveness: the loop logs one ``schedule_tick`` INFO line after **every** completed tick, idle ones
included (``count`` = jobs fired plus items parked, and ``fired``/``parked``/``errors``), so the
"no scheduler tick for 15 minutes" alert can tell a quiet scheduler from a dead one. A tick that
fails logs ``schedule_tick_failed`` (store) or ``schedule_tick_crashed`` instead.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, Field, JsonValue, ValidationError, model_validator

from blogwriter.core.logging import describe_exception
from blogwriter.db import (
    Conflict,
    Doc,
    Filter,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
    StoreError,
    StoreUnavailable,
)
from blogwriter.jobs._logging import logger
from blogwriter.jobs.broker import Broker
from blogwriter.jobs.claims import (
    MAX_CAS_ROUNDS,
    Body,
    ClaimLost,
    LeasePolicy,
    acquire,
    bounded,
    cas_update,
    new_owner,
)
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock, parse_iso, to_iso
from blogwriter.jobs.models import (
    JOBS_CONTAINER,
    DocId,
    DocModel,
    Name,
    Timestamp,
    check_json,
)
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.runner import JobRegistry
from blogwriter.jobs.settings import JobsSettings, replica_name

__all__ = [
    "DEFAULT_MAX_LATENESS",
    "SCHEDULE_KIND",
    "WEEKDAYS",
    "DailyRule",
    "IntervalRule",
    "ScheduleItem",
    "ScheduleRule",
    "ScheduleStateError",
    "ScheduleStatus",
    "Scheduler",
    "TickReport",
    "Weekday",
    "WeeklyRule",
    "daily",
    "every",
    "latest_due",
    "log_tick",
    "next_run_after",
    "scheduled_job_id",
    "weekly",
]

SCHEDULE_KIND: Final = "schedule"
DEFAULT_MAX_LATENESS: Final = timedelta(hours=2)
MAX_ITEM_ID_CHARS: Final = 200
_TICK_BATCH: Final = 100
_MAX_CATCH_UP: Final = 10_000
_HHMM: Final = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

type Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: Final[tuple[Weekday, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""Index = ``date.weekday()``."""

type ScheduleStatus = Literal["active", "needs_confirmation", "paused"]


class ScheduleStateError(Exception):
    """The schedule item is not in a state that allows the operation (``code`` says why)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _check_hhmm(value: str) -> str:
    if not _HHMM.fullmatch(value):
        raise ValueError("time of day must be HH:MM (00:00-23:59)")
    return value


def _check_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an IANA zone name such as America/New_York") from None
    return value


type TimeOfDay = Annotated[str, AfterValidator(_check_hhmm)]
type ZoneName = Annotated[str, AfterValidator(_check_zone)]


class DailyRule(DocModel):
    """Every day at ``at`` (``HH:MM``) local time."""

    kind: Literal["daily"] = "daily"
    at: TimeOfDay


class WeeklyRule(DocModel):
    """On ``days`` at ``at`` (``HH:MM``) local time."""

    kind: Literal["weekly"] = "weekly"
    days: list[Weekday] = Field(min_length=1, max_length=7)
    at: TimeOfDay

    @model_validator(mode="after")
    def _unique_days(self) -> Self:
        if len(set(self.days)) != len(self.days):
            raise ValueError("weekdays must be unique")
        return self


class IntervalRule(DocModel):
    """Every ``seconds`` seconds from ``anchor`` (UTC; set when the item is saved if unset)."""

    kind: Literal["interval"] = "interval"
    seconds: int = Field(ge=60, le=31 * 86_400)
    anchor: Timestamp | None = None


type ScheduleRule = Annotated[DailyRule | WeeklyRule | IntervalRule, Field(discriminator="kind")]


def daily(at: str) -> DailyRule:
    """``daily("09:00")``."""
    return DailyRule(at=at)


def weekly(days: Sequence[Weekday], at: str) -> WeeklyRule:
    """``weekly(["sun"], "18:00")``."""
    return WeeklyRule(days=list(days), at=at)


def every(seconds: int, *, anchor: datetime | None = None) -> IntervalRule:
    """``every(3600)``: hourly from ``anchor`` (default: when the item is saved)."""
    return IntervalRule(seconds=seconds, anchor=to_iso(anchor) if anchor is not None else None)


class ScheduleItem(DocModel):
    """A schedule item document (``kind: "schedule"`` in the ``jobs`` container)."""

    id: DocId
    kind: Literal["schedule"] = "schedule"
    name: str = Field(min_length=1, max_length=200)
    job_type: Name
    job_input: JsonValue = None
    timezone: ZoneName
    rule: ScheduleRule
    max_lateness_seconds: int = Field(
        default=int(DEFAULT_MAX_LATENESS.total_seconds()), ge=0, le=7 * 86_400
    )
    status: ScheduleStatus = "active"
    revision: int = Field(default=1, ge=1)
    """Bumped whenever the definition changes; a tick that claimed an older revision does not
    move ``nextRunAt``."""
    next_run_at: Timestamp
    pending_run_at: Timestamp | None = None
    """The missed slot waiting for the owner's confirmation."""
    last_run_at: Timestamp | None = None
    last_job_id: str | None = None
    skipped_runs: int = Field(default=0, ge=0)
    owner: str | None = None
    lease_until: Timestamp | None = None
    fence: int = Field(default=0, ge=0)
    write_id: str | None = None
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> ScheduleItem:
        return cls.model_validate(dict(body))

    def to_body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def max_lateness(self) -> timedelta:
        return timedelta(seconds=self.max_lateness_seconds)


def _hhmm(value: str) -> time:
    match = _HHMM.fullmatch(value)
    if match is None:  # pragma: no cover - validated by the model
        raise ValueError("time of day must be HH:MM")
    return time(int(match.group(1)), int(match.group(2)))


def _anchor(rule: IntervalRule) -> datetime:
    if rule.anchor is None:
        raise ValueError("an interval rule needs an anchor (set when the item is saved)")
    return parse_iso(rule.anchor)


def _local_slot(day: date, at: time, zone: ZoneInfo) -> datetime:
    """``day`` at ``at`` in ``zone`` as UTC (``fold=0``: gaps shift forward, folds pick the
    first occurrence)."""
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


def next_run_after(rule: ScheduleRule, zone: ZoneInfo, after: datetime) -> datetime:
    """The first slot of ``rule`` strictly after ``after``, in UTC."""
    after_utc = after.astimezone(UTC)
    match rule:
        case IntervalRule():
            anchor = _anchor(rule)
            if after_utc < anchor:
                return anchor
            step = timedelta(seconds=rule.seconds)
            return anchor + ((after_utc - anchor) // step + 1) * step
        case DailyRule() | WeeklyRule():
            at = _hhmm(rule.at)
            days = None if isinstance(rule, DailyRule) else {WEEKDAYS.index(d) for d in rule.days}
            # Comparisons happen in UTC: aware datetimes in one zone compare by wall time and
            # ignore fold, which would get the repeated hour wrong.
            first_day = after_utc.astimezone(zone).date()
            for offset in range(8):
                day = first_day + timedelta(days=offset)
                if days is not None and day.weekday() not in days:
                    continue
                candidate = _local_slot(day, at, zone)
                if candidate > after_utc:
                    return candidate
            raise RuntimeError("no slot within 8 days")  # pragma: no cover - rules are validated


def latest_due(
    rule: ScheduleRule, zone: ZoneInfo, first: datetime, now: datetime
) -> tuple[datetime, int]:
    """The latest slot at or before ``now``, starting from the due slot ``first`` (<= now),
    and how many slots before it were skipped."""
    if isinstance(rule, IntervalRule):
        step = timedelta(seconds=rule.seconds)
        missed = (now - first) // step
        return first + missed * step, missed
    latest, skipped = first, 0
    for _ in range(_MAX_CATCH_UP):
        following = next_run_after(rule, zone, latest)
        if following > now:
            break
        latest, skipped = following, skipped + 1
    return latest, skipped


def scheduled_job_id(item_id: str, slot: datetime) -> str:
    """The deterministic id of the job an item enqueues for ``slot``."""
    return f"{item_id}-{slot.astimezone(UTC):%Y%m%dT%H%M%SZ}"


@dataclass
class TickReport:
    """What one tick did."""

    fired: list[str] = field(default_factory=list[str])
    """Ids of the jobs enqueued (or found already enqueued) for due slots."""
    needs_confirmation: list[str] = field(default_factory=list[str])
    """Ids of the items that were too late and now wait for the owner."""
    errors: int = 0


def _never() -> bool:
    return False


def log_tick(report: TickReport) -> None:
    """The ``schedule_tick`` liveness line, written after every completed tick."""
    logger.info(
        "schedule_tick",
        extra={
            "count": len(report.fired) + len(report.needs_confirmation),
            "fired": len(report.fired),
            "parked": len(report.needs_confirmation),
            "errors": report.errors,
        },
    )


class Scheduler:
    """Fires schedule items (see the module docs)."""

    def __init__(
        self,
        store: Store,
        *,
        settings: JobsSettings | None = None,
        owner: str | None = None,
        clock: Clock = SYSTEM_CLOCK,
        registry: JobRegistry | None = None,
        broker: Broker | None = None,
        on_enqueue: Callable[[], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._settings = settings if settings is not None else JobsSettings()
        self._policy = LeasePolicy.from_settings(self._settings)
        self._owner = owner or new_owner(replica_name(self._settings))
        self._clock = clock
        self._registry = registry
        self._broker = broker
        self._on_enqueue = on_enqueue
        self._stop_requested = stop_requested or _never
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # Definitions ------------------------------------------------------------------------------

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
    ) -> ScheduleItem:
        """Create or update an item. A changed definition restarts it from the next slot after
        now: ``active`` again (a pending confirmation is dropped), except that a paused item
        stays paused. Only :meth:`resume` ends a pause, so a deploy that updates a definition
        can never restart a schedule the owner stopped."""
        if len(item_id) > MAX_ITEM_ID_CHARS:
            raise ValueError(f"schedule item ids have at most {MAX_ITEM_ID_CHARS} characters")
        if self._registry is not None and job_type not in self._registry:
            raise ValueError(f"job type {job_type!r} is not registered")
        payload = check_json(job_input, what="job input")
        _check_zone(timezone)
        now = self._clock.now()
        stamp = to_iso(now)
        zone = ZoneInfo(timezone)
        default_anchor = isinstance(rule, IntervalRule) and rule.anchor is None
        if isinstance(rule, IntervalRule) and rule.anchor is None:
            rule = rule.model_copy(update={"anchor": stamp})
        draft = ScheduleItem(
            id=item_id,
            name=name,
            job_type=job_type,
            job_input=payload,
            timezone=timezone,
            rule=rule,
            max_lateness_seconds=int(max_lateness.total_seconds()),
            next_run_at=to_iso(next_run_after(rule, zone, now)),
            created_at=stamp,
            updated_at=stamp,
        )
        definition = ("name", "job_type", "job_input", "timezone", "rule", "max_lateness_seconds")

        def change(body: Body) -> Body | None:
            current = ScheduleItem.from_body(body)
            target = draft
            stored = current.rule
            if (
                default_anchor
                and isinstance(stored, IntervalRule)
                and stored == draft.rule.model_copy(update={"anchor": stored.anchor})
            ):
                # No anchor given: the item keeps the one it got when first saved, so putting
                # the same definition again (seeding at every boot) changes nothing and the
                # schedule keeps its phase.
                target = draft.model_copy(
                    update={
                        "rule": stored,
                        "next_run_at": to_iso(next_run_after(stored, zone, now)),
                    }
                )
            if all(getattr(current, key) == getattr(target, key) for key in definition):
                return None
            updated = target.model_copy(
                update={
                    "status": "paused" if current.status == "paused" else "active",
                    "revision": current.revision + 1,
                    "created_at": current.created_at,
                    "last_run_at": current.last_run_at,
                    "last_job_id": current.last_job_id,
                    "skipped_runs": current.skipped_runs,
                    "owner": current.owner,
                    "lease_until": current.lease_until,
                    "fence": current.fence,
                    "write_id": current.write_id,
                }
            )
            return updated.to_body()

        timeout = self._policy.store_timeout
        for _ in range(3):
            try:
                stored = await cas_update(
                    self._store, JOBS_CONTAINER, item_id, change, store_timeout=timeout
                )
            except NotFound:
                try:
                    created = await bounded(
                        self._store.create(JOBS_CONTAINER, draft.to_body()), timeout
                    )
                except Conflict:
                    continue
                return ScheduleItem.from_body(created.body)
            if stored is None:
                return await self.get(item_id)
            return ScheduleItem.from_body(stored.body)
        raise StoreError(f"{JOBS_CONTAINER}/{item_id}: could not save the schedule item")

    async def get(self, item_id: str) -> ScheduleItem:
        """Read an item; :class:`NotFound` if there is none."""
        doc = await bounded(self._store.read(JOBS_CONTAINER, item_id), self._policy.store_timeout)
        if doc.body.get("kind") != SCHEDULE_KIND:
            raise NotFound(f"read {JOBS_CONTAINER}/{item_id}: not a schedule item")
        return ScheduleItem.from_body(doc.body)

    async def list_items(self) -> list[ScheduleItem]:
        """Every schedule item, soonest first."""
        docs = await bounded(
            self._store.find(
                JOBS_CONTAINER,
                [Filter("kind", "==", SCHEDULE_KIND)],
                order_by=OrderBy("nextRunAt"),
            ),
            self._policy.store_timeout,
        )
        return [ScheduleItem.from_body(doc.body) for doc in docs]

    async def delete(self, item_id: str) -> None:
        """Delete an item; :class:`NotFound` if there is none (or the id names a job).

        The kind check and the delete are one CAS (``If-Match``), so a job that took the id in
        between can never be deleted by mistake.
        """
        timeout = self._policy.store_timeout
        for _ in range(MAX_CAS_ROUNDS):
            doc = await bounded(self._store.read(JOBS_CONTAINER, item_id), timeout)
            if doc.body.get("kind") != SCHEDULE_KIND:
                raise NotFound(f"read {JOBS_CONTAINER}/{item_id}: not a schedule item")
            try:
                await bounded(
                    self._store.delete(JOBS_CONTAINER, item_id, if_match=doc.etag), timeout
                )
            except PreconditionFailed:
                continue  # a tick or an edit wrote first; check again
            return
        raise StoreUnavailable(
            f"{JOBS_CONTAINER}/{item_id}: gave up after {MAX_CAS_ROUNDS} conflicting writes"
        )

    async def pause(self, item_id: str) -> ScheduleItem:
        """Stop firing the item (``paused``) until :meth:`resume`."""

        def change(item: ScheduleItem, _now: datetime) -> bool:
            if item.status == "paused":
                return False
            item.status = "paused"
            item.pending_run_at = None
            return True

        return await self._update(item_id, change)

    async def resume(self, item_id: str) -> ScheduleItem:
        """Fire the item again from the next slot after now (no catch-up)."""

        def change(item: ScheduleItem, now: datetime) -> bool:
            if item.status != "paused":
                raise ScheduleStateError("not_paused", "the schedule item is not paused")
            item.status = "active"
            item.next_run_at = to_iso(next_run_after(item.rule, item.zone, now))
            return True

        return await self._update(item_id, change)

    async def confirm(self, item_id: str, *, run: bool) -> ScheduleItem:
        """Settle a ``needs_confirmation`` item: ``run`` enqueues the missed slot's job now
        (same deterministic id, so a double click is harmless); otherwise it is skipped. Either
        way the item is active again from the next slot after now."""
        item = await self.get(item_id)
        if item.status != "needs_confirmation" or item.pending_run_at is None:
            raise ScheduleStateError("not_pending", "the schedule item has nothing to confirm")
        slot = parse_iso(item.pending_run_at)
        job_id: str | None = None
        if run:
            job_id = await self._enqueue(item, slot)

        def change(current: ScheduleItem, now: datetime) -> bool:
            if current.status != "needs_confirmation" or current.pending_run_at is None:
                raise ScheduleStateError("not_pending", "the schedule item has nothing to confirm")
            current.status = "active"
            current.pending_run_at = None
            current.next_run_at = to_iso(next_run_after(current.rule, current.zone, now))
            if job_id is not None:
                current.last_run_at = to_iso(slot)
                current.last_job_id = job_id
            else:
                current.skipped_runs += 1
            return True

        updated = await self._update(item_id, change)
        logger.info(
            "schedule_confirmed",
            extra={"schedule_id": item_id, "outcome": "run" if run else "skipped"},
        )
        return updated

    async def _update(
        self, item_id: str, change: Callable[[ScheduleItem, datetime], bool]
    ) -> ScheduleItem:
        unchanged: list[ScheduleItem] = []

        def mutate(body: Body) -> Body | None:
            if body.get("kind") != SCHEDULE_KIND:
                raise NotFound(f"read {JOBS_CONTAINER}/{item_id}: not a schedule item")
            item = ScheduleItem.from_body(body)
            now = self._clock.now()
            if not change(item, now):
                unchanged[:] = [item]
                return None
            item.updated_at = to_iso(now)
            return item.to_body()

        doc = await cas_update(
            self._store,
            JOBS_CONTAINER,
            item_id,
            mutate,
            store_timeout=self._policy.store_timeout,
        )
        return unchanged[0] if doc is None else ScheduleItem.from_body(doc.body)

    # Tick -------------------------------------------------------------------------------------

    async def tick(self) -> TickReport:
        """Fire every due item once (see the module docs)."""
        report = TickReport()
        if self._stop_requested():
            return report
        now = self._clock.now()
        due = await bounded(
            self._store.find(
                JOBS_CONTAINER,
                [
                    Filter("kind", "==", SCHEDULE_KIND),
                    Filter("status", "==", "active"),
                    Filter("nextRunAt", "<=", to_iso(now)),
                ],
                order_by=OrderBy("nextRunAt"),
                limit=_TICK_BATCH,
            ),
            self._policy.store_timeout,
        )
        for doc in due:
            if self._stop_requested():
                break
            try:
                await self._fire(doc, now, report)
            except ClaimLost:
                continue
            except (StoreError, TimeoutError) as exc:
                report.errors += 1
                logger.warning(
                    "schedule_fire_failed", extra={"schedule_id": doc.id, **describe_exception(exc)}
                )
            except Exception as exc:
                # One bad item (say, a document another revision wrote) must not stop the items
                # after it: it is the oldest due one, so it would head every tick from now on.
                # Its claim, if taken, lapses after one lease, which spaces out the retries.
                report.errors += 1
                logger.error(
                    "schedule_fire_crashed",
                    extra={"schedule_id": doc.id, **describe_exception(exc)},
                )
        return report

    async def _fire(self, doc: Doc, now: datetime, report: TickReport) -> None:
        try:
            item = ScheduleItem.from_body(doc.body)
        except ValidationError:
            report.errors += 1
            logger.warning("schedule_invalid_document", extra={"schedule_id": doc.id})
            return
        claim = await acquire(
            self._store,
            JOBS_CONTAINER,
            doc,
            owner=self._owner,
            policy=self._policy,
            clock=self._clock,
        )
        if claim is None:
            return
        zone = item.zone
        latest, skipped = latest_due(item.rule, zone, parse_iso(item.next_run_at), now)
        revision = item.revision
        if now - latest > item.max_lateness:

            def park(body: Body) -> Body:
                current = ScheduleItem.from_body(body)
                if current.revision == revision and current.status == "active":
                    current.status = "needs_confirmation"
                    current.pending_run_at = to_iso(latest)
                    current.skipped_runs += skipped
                    current.updated_at = to_iso(self._clock.now())
                return current.to_body()

            await claim.release(park)
            report.needs_confirmation.append(item.id)
            logger.warning(
                "schedule_needs_confirmation",
                extra={"schedule_id": item.id, "slot": to_iso(latest)},
            )
            return
        job_id = await self._enqueue(item, latest)
        following = next_run_after(item.rule, zone, latest)

        def advance(body: Body) -> Body:
            current = ScheduleItem.from_body(body)
            if current.revision == revision and current.status == "active":
                current.next_run_at = to_iso(following)
                current.last_run_at = to_iso(latest)
                current.last_job_id = job_id
                current.skipped_runs += skipped
                current.updated_at = to_iso(self._clock.now())
            return current.to_body()

        await claim.release(advance)
        report.fired.append(job_id)
        logger.info(
            "schedule_fired",
            extra={"schedule_id": item.id, "job_id": job_id, "slot": to_iso(latest)},
        )

    async def _enqueue(self, item: ScheduleItem, slot: datetime) -> str:
        """Enqueue the job of ``item`` for ``slot`` (idempotent: the id is deterministic)."""
        job_id = scheduled_job_id(item.id, slot)
        steps: tuple[str, ...] = ()
        if self._registry is not None:
            job_type = self._registry.get(item.job_type)
            steps = job_type.step_names if job_type is not None else ()
        try:
            await enqueue(
                self._store,
                item.job_type,
                item.job_input,
                job_id=job_id,
                steps=steps,
                schedule_id=item.id,
                scheduled_for=slot,
                max_attempts=self._settings.max_attempts,
                clock=self._clock,
                broker=self._broker,
                store_timeout=self._policy.store_timeout,
            )
        except Conflict:
            return job_id  # enqueued by an earlier attempt that did not get to advance the item
        if self._on_enqueue is not None:
            self._on_enqueue()
        return job_id

    # Loop -------------------------------------------------------------------------------------

    async def start(self) -> None:
        """Tick now and then every ``tick_seconds``."""
        if self._task is not None:
            raise RuntimeError("a scheduler can be started once")
        self._task = asyncio.create_task(self._loop(), name="jobs-scheduler")
        logger.info("jobs_scheduler_started", extra={"owner": self._owner})

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        logger.info("jobs_scheduler_stopped", extra={"owner": self._owner})

    async def _loop(self) -> None:
        while not self._stop_requested():
            try:
                report = await self.tick()
            except (StoreError, TimeoutError) as exc:
                logger.warning("schedule_tick_failed", extra=describe_exception(exc))
            except Exception as exc:
                logger.error("schedule_tick_crashed", extra=describe_exception(exc))
            else:
                log_tick(report)
            await asyncio.sleep(self._settings.tick_seconds)
