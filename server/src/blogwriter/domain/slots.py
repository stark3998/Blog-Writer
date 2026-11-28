"""Slot math every package that schedules something must compute the same way.

Planner (week plans, missed slots), linkedin posts (approve: ``slot_conflict``), the pipeline
(auto path: ``publish.publishAt``) and the publish job (approve: the default ``publishAt``) are
built in parallel and may not import each other, so the rules live here. Pure functions over
:class:`~blogwriter.domain.settings.CadenceSettings`: no I/O, no clock (pass ``after``).

Local times use the DST rule of :mod:`blogwriter.domain.weeks` (the scheduler's). Weeks start
on Monday in the cadence's zone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Final, Literal

from blogwriter.domain.base import WEEKDAYS, Weekday, parse_iso, to_iso
from blogwriter.domain.common import Finding
from blogwriter.domain.posts import Slot
from blogwriter.domain.settings import CadenceSettings, SlotTemplate, WeeklyTime, WeekMode
from blogwriter.domain.weeks import add_days, local_date_time, week_start, weekday_of

__all__ = [
    "SAME_DAY_CODE",
    "SEARCH_WEEKS",
    "TOO_CLOSE_CODE",
    "conflicting_slots",
    "next_article_publish",
    "next_free_slot",
    "posts_for_mode",
    "slot_at",
    "slot_from_utc",
    "spacing_findings",
    "template_kind",
    "week_slots",
    "weekly_slot",
]

SAME_DAY_CODE: Final = "slot-same-day"
TOO_CLOSE_CODE: Final = "slot-too-close"
SEARCH_WEEKS: Final = 8
"""How far ahead :func:`next_free_slot` looks before it gives up."""


def _check_monday(week_start_date: str) -> None:
    if week_start(week_start_date) != week_start_date:
        raise ValueError("week_start must be a Monday (YYYY-MM-DD)")


def _slot_in_week(
    week_start_date: str,
    day: Weekday,
    time: str,
    zone: str,
    kind: Literal["primary", "optional", "custom"],
) -> Slot:
    return Slot.at(add_days(week_start_date, WEEKDAYS.index(day)), time, zone, kind)


def weekly_slot(week_start_date: str, weekly: WeeklyTime, zone: str) -> Slot:
    """The occurrence of ``weekly`` (``cadence.research_start``, ``article_publish``...) in the
    week starting on Monday ``week_start_date``."""
    _check_monday(week_start_date)
    return _slot_in_week(week_start_date, weekly.day, weekly.time, zone, "custom")


def posts_for_mode(cadence: CadenceSettings, mode: WeekMode) -> int:
    """Posts in a week of ``mode`` (normal 3, light 2, away 0 by default)."""
    return {
        "normal": cadence.posts_per_week.normal,
        "light": cadence.posts_per_week.light,
        "away": cadence.posts_per_week.away,
    }[mode]


def _template_key(template: SlotTemplate) -> tuple[int, str]:
    return WEEKDAYS.index(template.day), template.time


def week_slots(
    cadence: CadenceSettings, week_start_date: str, mode: WeekMode, posts: int | None = None
) -> list[Slot]:
    """The post slots of one week, in time order.

    Primary templates are used first (in week order), optional ones only when ``posts`` is
    larger than the number of primaries; ``away`` gives none. ``posts`` defaults to
    :func:`posts_for_mode` and is capped at ``posts_per_week.max`` and the number of templates.
    """
    _check_monday(week_start_date)
    if mode == "away":
        return []
    wanted = posts_for_mode(cadence, mode) if posts is None else posts
    wanted = max(0, min(wanted, cadence.posts_per_week.max, len(cadence.post_slots)))
    primary = sorted((t for t in cadence.post_slots if t.kind == "primary"), key=_template_key)
    optional = sorted((t for t in cadence.post_slots if t.kind == "optional"), key=_template_key)
    chosen = [*primary, *optional][:wanted]
    slots = [
        _slot_in_week(week_start_date, t.day, t.time, cadence.timezone, t.kind) for t in chosen
    ]
    return sorted(slots, key=lambda slot: slot.utc)


def template_kind(
    cadence: CadenceSettings, local_date: str, local_time: str
) -> Literal["primary", "optional", "custom"]:
    """``primary``/``optional`` when a cadence template has this weekday and time, else
    ``custom`` (a time the owner picked)."""
    day = weekday_of(local_date)
    for template in cadence.post_slots:
        if template.day == day and template.time == local_time:
            return template.kind
    return "custom"


def slot_at(cadence: CadenceSettings, local_date: str, local_time: str) -> Slot:
    """A slot in the cadence's zone (``ValueError`` for an invalid date or time)."""
    return Slot.at(
        local_date, local_time, cadence.timezone, template_kind(cadence, local_date, local_time)
    )


def slot_from_utc(utc: str, cadence: CadenceSettings) -> Slot:
    """The slot at the local minute of a UTC timestamp (seconds dropped)."""
    day, clock = local_date_time(utc, cadence.timezone)
    return slot_at(cadence, day, clock)


def _local_day(slot: Slot, zone: str) -> str:
    return local_date_time(slot.utc, zone)[0]


def spacing_findings(slots: Sequence[Slot], *, cadence: CadenceSettings) -> list[Finding]:
    """Spacing problems between ``slots`` (any order): two on the same local day in the
    cadence's zone block (``slot-same-day``; a warning if ``same_day_block`` is off), and less
    than ``min_gap_hours_warn`` apart warns (``slot-too-close``). ``ref`` is the later slot's
    UTC time."""
    ordered = sorted(slots, key=lambda slot: slot.utc)
    findings: list[Finding] = []
    for earlier, later in pairwise(ordered):
        if _local_day(earlier, cadence.timezone) == _local_day(later, cadence.timezone):
            findings.append(
                Finding(
                    code=SAME_DAY_CODE,
                    severity="block" if cadence.same_day_block else "warn",
                    message="Two posts on the same day.",
                    ref=later.utc,
                )
            )
            continue
        gap = parse_iso(later.utc) - parse_iso(earlier.utc)
        if gap < timedelta(hours=cadence.min_gap_hours_warn):
            findings.append(
                Finding(
                    code=TOO_CLOSE_CODE,
                    severity="warn",
                    message=f"Posts less than {cadence.min_gap_hours_warn} hours apart.",
                    ref=later.utc,
                )
            )
    return findings


def conflicting_slots(slot: Slot, taken: Sequence[Slot], *, cadence: CadenceSettings) -> list[Slot]:
    """The ``taken`` slots on the same local day as ``slot`` (posts approve and re-slot answer
    409 ``slot_conflict`` when there is one: posts are never stacked)."""
    day = _local_day(slot, cadence.timezone)
    return [other for other in taken if _local_day(other, cadence.timezone) == day]


def next_free_slot(
    taken: Sequence[Slot], cadence: CadenceSettings, *, after: datetime
) -> Slot | None:
    """The first template slot (primary or optional) strictly after ``after`` on a local day
    without a taken slot, within :data:`SEARCH_WEEKS` weeks (``None`` if there is none).

    The missed-slot policy offers it; the caller drops it for a time-sensitive post whose
    ``expiresAt`` comes first.
    """
    taken_days = {_local_day(slot, cadence.timezone) for slot in taken}
    first_week = week_start(local_date_time(to_iso(after), cadence.timezone)[0])
    for offset in range(SEARCH_WEEKS + 1):
        monday = add_days(first_week, 7 * offset)
        candidates = sorted(
            (
                _slot_in_week(monday, t.day, t.time, cadence.timezone, t.kind)
                for t in cadence.post_slots
            ),
            key=lambda slot: slot.utc,
        )
        for candidate in candidates:
            if parse_iso(candidate.utc) <= after:
                continue
            if _local_day(candidate, cadence.timezone) not in taken_days:
                return candidate
    return None


def next_article_publish(
    cadence: CadenceSettings, *, after: datetime, min_lead: timedelta = timedelta(0)
) -> Slot:
    """The first ``cadence.article_publish`` time at or after ``after + min_lead``.

    The approve route uses no lead (the next Monday 09:00); the pipeline's auto path passes
    ``timedelta(hours=cadence.min_veto_lead_hours)`` so the veto notice can go out first.
    """
    if min_lead < timedelta(0):
        raise ValueError("min_lead must not be negative")
    earliest = after + min_lead
    monday = week_start(local_date_time(to_iso(earliest), cadence.timezone)[0])
    for offset in range(3):
        slot = weekly_slot(add_days(monday, 7 * offset), cadence.article_publish, cadence.timezone)
        if parse_iso(slot.utc) >= earliest:
            return slot
    raise AssertionError("unreachable: a weekly time recurs within two weeks")  # pragma: no cover
