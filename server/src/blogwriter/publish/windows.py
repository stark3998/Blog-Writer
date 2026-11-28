"""Publish windows: when deploys of Blog Writer must not run (plan: "No deploys run during
publish windows").

A deploy restarts the app, and a publish caught by a restart resumes from its last checkpoint
(remote ids recorded first, crash-only), but the plan keeps deploys out of the windows anyway.
The deploy freeze itself is enforced in CI by ``scripts/deploy-v2.sh freeze`` (run first by
``.github/workflows/deploy.yml``); :data:`DEPLOY_FREEZE_WINDOWS` mirrors its windows exactly so
the server can say whether a moment, or the cadence's article publish time, falls inside one
(a test keeps the two in step and checks that the default article publish time and post slots
are covered).

The windows are in ``America/New_York`` like the script: Monday 08:30-09:30 (the article
publish at 09:00) and Tuesday-Thursday 09:30-12:30 (the post slots). Half-open: the end minute
is already outside.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from blogwriter.domain.base import WEEKDAYS, Weekday, parse_iso, to_iso
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.weeks import local_date_time, week_start

__all__ = [
    "DEPLOY_FREEZE_WINDOWS",
    "FREEZE_TIMEZONE",
    "FreezeWindow",
    "article_publish_frozen",
    "freeze_window_at",
]

FREEZE_TIMEZONE: Final = "America/New_York"


@dataclass(frozen=True, slots=True)
class FreezeWindow:
    """Local ``[start, end)`` on ``days`` in :data:`FREEZE_TIMEZONE`."""

    label: str
    days: tuple[Weekday, ...]
    start: str
    end: str

    def contains(self, day: Weekday, hhmm: str) -> bool:
        return day in self.days and self.start <= hhmm < self.end


DEPLOY_FREEZE_WINDOWS: Final = (
    FreezeWindow("Monday 08:30-09:30", ("mon",), "08:30", "09:30"),
    FreezeWindow("Tuesday-Thursday 09:30-12:30", ("tue", "wed", "thu"), "09:30", "12:30"),
)


def freeze_window_at(moment: datetime) -> FreezeWindow | None:
    """The deploy-freeze window ``moment`` (timezone-aware) falls in, or ``None``."""
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    local = moment.astimezone(ZoneInfo(FREEZE_TIMEZONE))
    day = WEEKDAYS[local.weekday()]
    hhmm = f"{local:%H:%M}"
    return next((window for window in DEPLOY_FREEZE_WINDOWS if window.contains(day, hhmm)), None)


def article_publish_frozen(cadence: CadenceSettings, *, after: datetime) -> bool:
    """Whether the cadence's weekly article publish time (the week of ``after``) falls inside a
    deploy-freeze window, so no deploy can restart the app while it publishes."""
    monday = week_start(local_date_time(to_iso(after), cadence.timezone)[0])
    slot = weekly_slot(monday, cadence.article_publish, cadence.timezone)
    return freeze_window_at(parse_iso(slot.utc)) is not None
