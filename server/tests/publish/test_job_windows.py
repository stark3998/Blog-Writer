"""Publish windows: the server's copy of the deploy freeze matches ``scripts/deploy-v2.sh`` and
covers the default article publish time and post slots (plan: "No deploys run during publish
windows")."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blogwriter.domain.base import parse_iso
from blogwriter.domain.settings import CadenceSettings, WeeklyTime
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.publish.windows import (
    DEPLOY_FREEZE_WINDOWS,
    FREEZE_TIMEZONE,
    article_publish_frozen,
    freeze_window_at,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "deploy-v2.sh"
DAYS = {"1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


def _hhmm(value: str) -> str:
    return f"{int(value) // 100:02d}:{int(value) % 100:02d}"


def test_the_windows_match_the_deploy_script() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert f'readonly TIMEZONE="{FREEZE_TIMEZONE}"' in script
    rules = re.findall(
        r'\[\[ "\$day" == \[?([0-9]+)\]? \]\] && \(\(hhmm >= ([0-9]+) && hhmm < ([0-9]+)\)\)',
        script,
    )
    windows = [
        (tuple(DAYS[digit] for digit in days), _hhmm(start), _hhmm(end))
        for days, start, end in rules
    ]
    assert windows == [(w.days, w.start, w.end) for w in DEPLOY_FREEZE_WINDOWS]


@pytest.mark.parametrize(
    ("moment", "label"),
    [
        (datetime(2026, 11, 30, 14, 0, tzinfo=UTC), "Monday 08:30-09:30"),  # Mon 09:00 EST
        (datetime(2026, 11, 30, 13, 30, tzinfo=UTC), "Monday 08:30-09:30"),  # 08:30 inclusive
        (datetime(2026, 11, 30, 14, 30, tzinfo=UTC), None),  # 09:30 already outside
        (datetime(2026, 12, 1, 15, 0, tzinfo=UTC), "Tuesday-Thursday 09:30-12:30"),
        (datetime(2026, 12, 4, 15, 0, tzinfo=UTC), None),  # Friday
        (datetime(2026, 7, 6, 13, 0, tzinfo=UTC), "Monday 08:30-09:30"),  # Mon 09:00 EDT
    ],
)
def test_freeze_window_at(moment: datetime, label: str | None) -> None:
    window = freeze_window_at(moment)
    assert (window.label if window is not None else None) == label


def test_the_default_publish_time_and_post_slots_are_frozen() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW)
    assert article_publish_frozen(cadence, after=parse_iso(SAMPLE_NOW))
    for template in cadence.post_slots:
        if template.kind != "primary":
            continue
        slot = weekly_slot(
            "2026-11-30", WeeklyTime(day=template.day, time=template.time), "America/New_York"
        )
        assert freeze_window_at(parse_iso(slot.utc)) is not None


def test_a_moved_publish_time_is_reported() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW).model_copy(
        update={"article_publish": WeeklyTime(day="fri", time="15:00")}
    )
    assert not article_publish_frozen(cadence, after=parse_iso(SAMPLE_NOW))
    with pytest.raises(ValueError, match="aware"):
        freeze_window_at(datetime(2026, 11, 30, 14, 0))
