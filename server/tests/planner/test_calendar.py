"""Calendar goldens: the weekly production calendar in America/New_York around DST.

For each week the golden file records, in local time and UTC: when the plan job runs, the post
slots per mode, research start (the flagship pipeline's ``run_after`` on approval), interview
due, draft day, the veto notice, the flagship's publish time, each slot's staging time
(slot - 60 min) and missed-slot grace (slot + 2 h), and the reminders of a post that goes out
at the week's first slot (+15/+60/+180 min, +48 h/+7 d). Spring forward is Sunday 2026-03-08,
fall back Sunday 2026-11-01.

The file changes only deliberately: ``BW_PLANNER_UPDATE_GOLDENS=1`` rewrites it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.planner import WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import CadenceSettings, WeeklyTime
from blogwriter.domain.slots import next_article_publish, weekly_slot
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.domain.weeks import add_days, local_date_time, plan_id
from blogwriter.jobs.scheduler import next_run_after
from blogwriter.planner import reminders_for_go_live, schedule_entries
from blogwriter.planner.slots import free_week_slots
from blogwriter.planner.testing import run_job

GOLDEN = Path(__file__).parent / "goldens" / "calendar.json"
NY = "America/New_York"
CADENCE = CadenceSettings.default(SAMPLE_NOW)
WEEKS = ("2026-03-02", "2026-03-09", "2026-10-26", "2026-11-02", "2026-11-30")


def local(utc: str) -> str:
    day, clock = local_date_time(utc, NY)
    return f"{day} {clock}"


def moment(utc: str) -> dict[str, JsonValue]:
    return {"local": local(utc), "utc": utc}


def weekly(week: str, at: WeeklyTime) -> dict[str, JsonValue]:
    return moment(weekly_slot(week, at, NY).utc)


def calendar_week(week: str) -> dict[str, JsonValue]:
    plan_entry = next(e for e in schedule_entries(CADENCE) if e.id == "sched-planner-plan-week")
    previous_monday = datetime.fromisoformat(add_days(week, -7)).replace(tzinfo=UTC)
    plan_run = next_run_after(plan_entry.rule, ZoneInfo(NY), previous_monday)
    draft = weekly_slot(week, CADENCE.draft_day, NY)
    publish = next_article_publish(
        CADENCE, after=parse_iso(draft.utc), min_lead=timedelta(hours=CADENCE.min_veto_lead_hours)
    )
    normal = free_week_slots(CADENCE, week, 3)
    light = free_week_slots(CADENCE, week, 2)
    stage = timedelta(minutes=CADENCE.stage_lead_minutes)
    grace = timedelta(minutes=CADENCE.missed_slot_grace_minutes)
    post = sample_document(Post, lane="native", pillar="agents", format="text")
    reminders = reminders_for_go_live(post, CADENCE, normal[0].utc, now=parse_iso(normal[0].utc))
    return {
        "planJobRuns": moment(to_iso(plan_run)),
        "slots": {
            "normal": [
                {
                    **moment(slot.utc),
                    "kind": slot.kind,
                    "stage": local(to_iso(parse_iso(slot.utc) - stage)),
                    "missedAfter": local(to_iso(parse_iso(slot.utc) + grace)),
                }
                for slot in normal
            ],
            "light": [moment(slot.utc) for slot in light],
            "away": [],
        },
        "researchStart": weekly(week, CADENCE.research_start),
        "interviewDue": weekly(week, CADENCE.interview_due),
        "draftDay": moment(draft.utc),
        "vetoNotice": weekly(week, CADENCE.veto_notice),
        "flagshipPublish": moment(publish.utc),
        "remindersAfterFirstSlot": [
            {"kind": r.reminder_kind, **moment(r.due_at), "expires": local(r.expires_at)}
            for r in reminders
        ],
    }


def test_calendar_goldens() -> None:
    actual: dict[str, JsonValue] = {week: calendar_week(week) for week in WEEKS}
    if os.environ.get("BW_PLANNER_UPDATE_GOLDENS") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected


def test_dst_shifts_are_visible_in_utc() -> None:
    before = calendar_week("2026-03-02")
    after = calendar_week("2026-03-09")
    slots_before = before["slots"]
    slots_after = after["slots"]
    assert isinstance(slots_before, dict)
    assert isinstance(slots_after, dict)
    first_before = slots_before["normal"]
    first_after = slots_after["normal"]
    assert isinstance(first_before, list)
    assert isinstance(first_after, list)
    assert [s["utc"] for s in first_before if isinstance(s, dict)] == [
        "2026-03-03T15:00:00.000000Z",
        "2026-03-04T15:00:00.000000Z",
        "2026-03-05T15:00:00.000000Z",
    ]
    assert [s["utc"] for s in first_after if isinstance(s, dict)] == [
        "2026-03-10T14:00:00.000000Z",
        "2026-03-11T14:00:00.000000Z",
        "2026-03-12T14:00:00.000000Z",
    ]
    # The plan for the spring-forward week runs on the DST Sunday itself: 17:00 EDT.
    assert before["planJobRuns"] == {
        "local": "2026-03-01 17:00",
        "utc": "2026-03-01T22:00:00.000000Z",
    }
    assert after["planJobRuns"] == {
        "local": "2026-03-08 17:00",
        "utc": "2026-03-08T21:00:00.000000Z",
    }
    fall = calendar_week("2026-11-02")
    assert fall["researchStart"] == {
        "local": "2026-11-02 08:00",
        "utc": "2026-11-02T13:00:00.000000Z",
    }
    assert fall["planJobRuns"] == {
        "local": "2026-11-01 17:00",
        "utc": "2026-11-01T22:00:00.000000Z",
    }


async def test_the_plan_job_lays_out_the_spring_forward_week(services: FakeServices) -> None:
    services.ai = None  # deterministic plan
    services.clock.set(datetime(2026, 3, 8, 21, 0, tzinfo=UTC))  # Sunday 17:00 EDT
    job = await run_job(
        services,
        "planner.plan-week",
        {},
        job_id="sched-planner-plan-week-20260308T210000Z",
        scheduled_for=datetime(2026, 3, 8, 21, 0, tzinfo=UTC),
    )
    assert job.status == "succeeded"
    plan = (await read_doc(services.store, WeekPlan, plan_id("2026-03-09"))).doc
    golden = calendar_week("2026-03-09")
    slots = golden["slots"]
    assert isinstance(slots, dict)
    normal = slots["normal"]
    assert isinstance(normal, list)
    assert [s.slot.utc for s in plan.slots] == [s["utc"] for s in normal if isinstance(s, dict)]
    assert plan.flagship is not None
    publish = golden["flagshipPublish"]
    assert isinstance(publish, dict)
    assert plan.flagship.publish_at == publish["utc"]
