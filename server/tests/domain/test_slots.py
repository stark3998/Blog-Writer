"""Slot math goldens: week slots across DST, spacing, the next free slot, the publish time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blogwriter.domain.posts import Slot
from blogwriter.domain.settings import CadenceSettings, SlotTemplate, WeeklyTime
from blogwriter.domain.slots import (
    SAME_DAY_CODE,
    TOO_CLOSE_CODE,
    conflicting_slots,
    next_article_publish,
    next_free_slot,
    posts_for_mode,
    spacing_findings,
    week_slots,
    weekly_slot,
)
from blogwriter.domain.testing import SAMPLE_NOW

NEW_YORK = "America/New_York"
CADENCE = CadenceSettings.default(SAMPLE_NOW)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def utcs(slots: list[Slot]) -> list[str]:
    return [slot.utc for slot in slots]


@pytest.mark.parametrize(
    ("monday", "expected"),
    [
        # Normal winter week.
        (
            "2026-11-30",
            [
                "2026-12-01T15:00:00.000000Z",
                "2026-12-02T15:00:00.000000Z",
                "2026-12-03T15:00:00.000000Z",
            ],
        ),
        # The week before spring forward (Sun 2026-03-08): still EST.
        (
            "2026-03-02",
            [
                "2026-03-03T15:00:00.000000Z",
                "2026-03-04T15:00:00.000000Z",
                "2026-03-05T15:00:00.000000Z",
            ],
        ),
        # The week after: EDT, one hour earlier in UTC.
        (
            "2026-03-09",
            [
                "2026-03-10T14:00:00.000000Z",
                "2026-03-11T14:00:00.000000Z",
                "2026-03-12T14:00:00.000000Z",
            ],
        ),
        # The week before fall back (Sun 2026-11-01): EDT.
        (
            "2026-10-26",
            [
                "2026-10-27T14:00:00.000000Z",
                "2026-10-28T14:00:00.000000Z",
                "2026-10-29T14:00:00.000000Z",
            ],
        ),
        # The week after: EST.
        (
            "2026-11-02",
            [
                "2026-11-03T15:00:00.000000Z",
                "2026-11-04T15:00:00.000000Z",
                "2026-11-05T15:00:00.000000Z",
            ],
        ),
    ],
)
def test_normal_weeks_use_the_primary_slots_across_dst(monday: str, expected: list[str]) -> None:
    slots = week_slots(CADENCE, monday, "normal")
    assert utcs(slots) == expected
    assert {slot.kind for slot in slots} == {"primary"}
    assert {slot.local_time for slot in slots} == {"10:00"}


def test_optional_slots_only_above_the_primary_count() -> None:
    four = week_slots(CADENCE, "2026-03-02", "normal", posts=4)
    assert [(s.local_date, s.local_time, s.kind) for s in four] == [
        ("2026-03-02", "12:00", "optional"),
        ("2026-03-03", "10:00", "primary"),
        ("2026-03-04", "10:00", "primary"),
        ("2026-03-05", "10:00", "primary"),
    ]
    five = week_slots(CADENCE, "2026-03-02", "normal", posts=5)
    assert five[-1].local_date == "2026-03-06"
    assert five[-1].utc == "2026-03-06T20:00:00.000000Z"
    assert len(week_slots(CADENCE, "2026-03-02", "normal", posts=9)) == CADENCE.posts_per_week.max


def test_modes() -> None:
    assert [posts_for_mode(CADENCE, mode) for mode in ("normal", "light", "away")] == [3, 2, 0]
    light = week_slots(CADENCE, "2026-11-30", "light")
    assert [s.local_date for s in light] == ["2026-12-01", "2026-12-02"]
    assert week_slots(CADENCE, "2026-11-30", "away", posts=3) == []
    with pytest.raises(ValueError, match="Monday"):
        week_slots(CADENCE, "2026-12-01", "normal")


def test_weekly_slot_on_the_dst_day() -> None:
    sunday_gap = WeeklyTime(day="sun", time="02:30")
    assert weekly_slot("2026-03-02", sunday_gap, NEW_YORK).utc == "2026-03-08T07:30:00.000000Z"
    research = weekly_slot("2026-11-30", CADENCE.research_start, NEW_YORK)
    assert (research.local_date, research.local_time) == ("2026-11-30", "08:00")


def test_spacing_same_day_blocks_and_close_posts_warn() -> None:
    tue = Slot.at("2026-12-01", "10:00", NEW_YORK)
    tue_late = Slot.at("2026-12-01", "17:00", NEW_YORK)
    wed_early = Slot.at("2026-12-02", "06:00", NEW_YORK)
    wed = Slot.at("2026-12-02", "10:00", NEW_YORK)
    same_day = spacing_findings([tue_late, tue], cadence=CADENCE)
    assert [(f.code, f.severity, f.ref) for f in same_day] == [
        (SAME_DAY_CODE, "block", tue_late.utc)
    ]
    close = spacing_findings([tue, wed_early], cadence=CADENCE)
    assert [(f.code, f.severity) for f in close] == [(TOO_CLOSE_CODE, "warn")]
    assert spacing_findings([tue, wed], cadence=CADENCE) == []
    relaxed = CADENCE.model_copy(update={"same_day_block": False})
    assert spacing_findings([tue, tue_late], cadence=relaxed)[0].severity == "warn"
    assert conflicting_slots(tue_late, [tue, wed], cadence=CADENCE) == [tue]
    assert conflicting_slots(wed_early, [tue], cadence=CADENCE) == []


def test_next_free_slot_never_stacks() -> None:
    taken = week_slots(CADENCE, "2026-11-30", "normal")
    missed_tue = at("2026-12-01T17:00:00")
    friday = next_free_slot(taken, CADENCE, after=missed_tue)
    assert friday is not None
    assert (friday.local_date, friday.local_time, friday.kind) == (
        "2026-12-04",
        "15:00",
        "optional",
    )
    monday = next_free_slot([*taken, friday], CADENCE, after=missed_tue)
    assert monday is not None
    assert (monday.local_date, monday.local_time) == ("2026-12-07", "12:00")
    only_mondays = CADENCE.model_copy(
        update={"post_slots": [SlotTemplate(day="mon", time="09:00")]}
    )
    mondays = ["2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28", "2027-01-04"]
    mondays += ["2027-01-11", "2027-01-18", "2027-01-25", "2027-02-01"]
    every_monday = [Slot.at(day, "09:00", NEW_YORK) for day in mondays]
    assert next_free_slot(every_monday, only_mondays, after=at("2026-12-02T00:00:00")) is None


def test_next_article_publish_respects_the_veto_lead() -> None:
    lead = timedelta(hours=CADENCE.min_veto_lead_hours)
    thursday = at("2026-12-03T12:00:00")
    assert next_article_publish(CADENCE, after=thursday).utc == "2026-12-07T14:00:00.000000Z"
    assert next_article_publish(CADENCE, after=thursday, min_lead=lead).utc == (
        "2026-12-07T14:00:00.000000Z"
    )
    saturday_late = at("2026-12-05T15:00:00")
    assert next_article_publish(CADENCE, after=saturday_late, min_lead=lead).utc == (
        "2026-12-14T14:00:00.000000Z"
    )
    exactly = at("2026-12-07T14:00:00")
    assert next_article_publish(CADENCE, after=exactly).utc == "2026-12-07T14:00:00.000000Z"
    # Across spring forward: Monday 09:00 EDT is 13:00 UTC.
    assert next_article_publish(CADENCE, after=at("2026-03-05T12:00:00")).utc == (
        "2026-03-09T13:00:00.000000Z"
    )
    with pytest.raises(ValueError, match="negative"):
        next_article_publish(CADENCE, after=thursday, min_lead=timedelta(hours=-1))
