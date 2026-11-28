"""Wall-clock helpers for slots and weeks (plan: "Weekly production calendar").

Every local time is stored with its IANA zone, and the UTC instant next to it. The conversion
uses the same DST rule as ``blogwriter.jobs.scheduler``:

- a wall time that does not exist (spring forward: 02:30 on 2026-03-08 in New York) maps to the
  same offset after the gap, 03:30 EDT (PEP 495 ``fold=0``);
- a wall time that happens twice (fall back: 01:30 on 2026-11-01) maps to its first occurrence
  (01:30 EDT).

Weeks start on Monday in the owner's zone (week N+1's plan is approved on Sunday of week N).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blogwriter.domain.base import WEEKDAYS, Weekday, parse_iso, to_iso

__all__ = [
    "DEFAULT_TIMEZONE",
    "add_days",
    "local_date_time",
    "plan_id",
    "slot_utc",
    "week_start",
    "weekday_of",
]

DEFAULT_TIMEZONE: Final = "America/New_York"


def _parse_hhmm(value: str) -> time:
    hours, _, minutes = value.partition(":")
    if len(hours) != 2 or len(minutes) != 2 or not (hours + minutes).isdigit():
        raise ValueError("time of day must be HH:MM")
    return time(int(hours), int(minutes))


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an IANA zone name such as America/New_York") from None


def slot_utc(local_date: str, local_time: str, zone: str) -> str:
    """The UTC timestamp (:func:`~blogwriter.domain.base.to_iso`) of a local date and time.

    ``ValueError`` for an invalid date, time or zone.
    """
    day = date.fromisoformat(local_date)
    moment = datetime.combine(day, _parse_hhmm(local_time), tzinfo=_zone(zone))
    return to_iso(moment.astimezone(UTC))


def local_date_time(utc: str, zone: str) -> tuple[str, str]:
    """The local ``(YYYY-MM-DD, HH:MM)`` of a stored UTC timestamp in ``zone``."""
    local = parse_iso(utc).astimezone(_zone(zone))
    return local.date().isoformat(), f"{local.hour:02d}:{local.minute:02d}"


def weekday_of(local_date: str) -> Weekday:
    """``mon`` ... ``sun`` of a ``YYYY-MM-DD`` date."""
    return WEEKDAYS[date.fromisoformat(local_date).weekday()]


def week_start(local_date: str) -> str:
    """The Monday (``YYYY-MM-DD``) of the week containing ``local_date``."""
    day = date.fromisoformat(local_date)
    return (day - timedelta(days=day.weekday())).isoformat()


def add_days(local_date: str, days: int) -> str:
    """``local_date`` plus ``days`` calendar days."""
    return (date.fromisoformat(local_date) + timedelta(days=days)).isoformat()


def plan_id(week_start_date: str) -> str:
    """The id of the week plan for the week starting on Monday ``week_start_date``."""
    if week_start(week_start_date) != week_start_date:
        raise ValueError("a week plan id needs the Monday that starts the week")
    return f"plan-{week_start_date}"
