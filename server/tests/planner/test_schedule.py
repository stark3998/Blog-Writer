"""Schedule entries: the plan-week lead (and its day shift), the veto notice and the scorecard,
fired by the real scheduler math across the New York DST weeks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from blogwriter.domain.jobtypes import SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.settings import CadenceSettings, WeeklyTime
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.jobs.scheduler import WeeklyRule, next_run_after
from blogwriter.planner import schedule_entries
from blogwriter.planner.schedule import (
    PLAN_WEEK_SCHEDULE_ID,
    SCORECARD_SCHEDULE_ID,
    VETO_NOTICE_SCHEDULE_ID,
    plan_week_time,
)
from blogwriter.planner.slots import shift_weekly

CADENCE = CadenceSettings.default(SAMPLE_NOW)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def entry_rules(cadence: CadenceSettings) -> dict[str, tuple[list[str], str]]:
    out: dict[str, tuple[list[str], str]] = {}
    for entry in schedule_entries(cadence):
        assert isinstance(entry.rule, WeeklyRule)
        out[entry.id] = (list(entry.rule.days), entry.rule.at)
    return out


def test_default_entries() -> None:
    entries = {entry.id: entry for entry in schedule_entries(CADENCE)}
    assert list(entries) == [PLAN_WEEK_SCHEDULE_ID, VETO_NOTICE_SCHEDULE_ID, SCORECARD_SCHEDULE_ID]
    assert entry_rules(CADENCE) == {
        "sched-planner-plan-week": (["sun"], "17:00"),
        "sched-planner-veto-notice": (["fri"], "12:00"),
        "sched-planner-scorecard": (["mon"], "07:00"),
    }
    assert {e.id: e.max_lateness for e in entries.values()} == {
        "sched-planner-plan-week": timedelta(hours=6),
        "sched-planner-veto-notice": timedelta(hours=4),
        "sched-planner-scorecard": timedelta(hours=24),
    }
    assert {e.timezone for e in entries.values()} == {"America/New_York"}
    assert {e.job_type for e in entries.values()} == {
        "planner.plan-week",
        "planner.veto-notice",
        "planner.scorecard",
    }
    assert all(e.job_input == {} for e in entries.values())


def test_scheduled_types_are_allowed_and_generate_nothing() -> None:
    for entry in schedule_entries(CADENCE):
        assert entry.job_type in SCHEDULED_JOB_TYPES
        assert not generates_content(entry.job_type)


@pytest.mark.parametrize(
    ("day", "time", "lead", "expected"),
    [
        ("sun", "18:00", 60, ("sun", "17:00")),
        ("mon", "00:30", 60, ("sun", "23:30")),
        ("mon", "00:00", 720, ("sun", "12:00")),
        ("tue", "05:00", 360, ("mon", "23:00")),
        ("sun", "18:00", 0, ("sun", "18:00")),
    ],
)
def test_plan_week_lead_shifts_the_day_across_midnight(
    day: str, time: str, lead: int, expected: tuple[str, str]
) -> None:
    cadence = CADENCE.model_copy(
        update={
            "plan_week": WeeklyTime.model_validate({"day": day, "time": time}),
            "plan_lead_minutes": lead,
        }
    )
    shifted = plan_week_time(cadence)
    assert (shifted.day, shifted.time) == expected
    assert entry_rules(cadence)["sched-planner-plan-week"] == ([expected[0]], expected[1])


def test_shift_weekly_wraps_the_week() -> None:
    assert shift_weekly(WeeklyTime(day="mon", time="00:10"), -20) == WeeklyTime(
        day="sun", time="23:50"
    )
    assert shift_weekly(WeeklyTime(day="sun", time="23:50"), 20) == WeeklyTime(
        day="mon", time="00:10"
    )


def test_entries_follow_the_cadence_zone_and_veto_time() -> None:
    cadence = CADENCE.model_copy(
        update={"timezone": "Europe/Berlin", "veto_notice": WeeklyTime(day="thu", time="16:30")}
    )
    entries = {entry.id: entry for entry in schedule_entries(cadence)}
    assert {e.timezone for e in entries.values()} == {"Europe/Berlin"}
    assert entry_rules(cadence)["sched-planner-veto-notice"] == (["thu"], "16:30")


@pytest.mark.parametrize(
    ("schedule_id", "after", "expected"),
    [
        # Spring forward: Sunday 2026-03-08 02:00 EST -> 03:00 EDT.
        ("sched-planner-plan-week", "2026-03-01T12:00:00", "2026-03-01T22:00:00"),  # EST
        ("sched-planner-plan-week", "2026-03-02T00:00:00", "2026-03-08T21:00:00"),  # EDT
        ("sched-planner-veto-notice", "2026-03-02T00:00:00", "2026-03-06T17:00:00"),
        ("sched-planner-veto-notice", "2026-03-07T00:00:00", "2026-03-13T16:00:00"),
        ("sched-planner-scorecard", "2026-03-01T00:00:00", "2026-03-02T12:00:00"),
        ("sched-planner-scorecard", "2026-03-03T00:00:00", "2026-03-09T11:00:00"),
        # Fall back: Sunday 2026-11-01 02:00 EDT -> 01:00 EST.
        ("sched-planner-plan-week", "2026-10-20T00:00:00", "2026-10-25T21:00:00"),  # EDT
        ("sched-planner-plan-week", "2026-10-26T00:00:00", "2026-11-01T22:00:00"),  # EST
        ("sched-planner-veto-notice", "2026-10-26T00:00:00", "2026-10-30T16:00:00"),
        ("sched-planner-veto-notice", "2026-10-31T00:00:00", "2026-11-06T17:00:00"),
        ("sched-planner-scorecard", "2026-10-27T00:00:00", "2026-11-02T12:00:00"),
    ],
)
def test_schedule_fires_at_local_times_across_dst(
    schedule_id: str, after: str, expected: str
) -> None:
    entry = next(e for e in schedule_entries(CADENCE) if e.id == schedule_id)
    fired = next_run_after(entry.rule, ZoneInfo(entry.timezone), at(after))
    assert fired == at(expected)


def test_a_plan_time_in_the_spring_gap_fires_after_it() -> None:
    cadence = CADENCE.model_copy(
        update={"plan_week": WeeklyTime(day="sun", time="03:30"), "plan_lead_minutes": 60}
    )
    entry = next(e for e in schedule_entries(cadence) if e.id == PLAN_WEEK_SCHEDULE_ID)
    assert isinstance(entry.rule, WeeklyRule)
    assert entry.rule.at == "02:30"
    # 02:30 does not exist on 2026-03-08 in New York: the scheduler fires at 03:30 EDT.
    fired = next_run_after(entry.rule, ZoneInfo(entry.timezone), at("2026-03-02T00:00:00"))
    assert fired == at("2026-03-08T07:30:00")
