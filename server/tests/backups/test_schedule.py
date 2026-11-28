"""The backups schedule entry (contract section 8) and the job registration."""

from __future__ import annotations

from datetime import timedelta

from blogwriter.backups import BackupsSettings, register_jobs, schedule_entries
from blogwriter.domain.jobtypes import BACKUPS_DUMP, SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.ports import ScheduleProvider
from blogwriter.domain.settings import CadenceSettings
from blogwriter.jobs import DailyRule, JobRegistry

NOW = "2026-11-26T12:00:00.000000Z"


def test_one_daily_entry_at_the_backup_time() -> None:
    (entry,) = schedule_entries(CadenceSettings.default(NOW))
    assert entry.id == "sched-backups-dump"
    assert entry.job_type == BACKUPS_DUMP
    assert entry.rule == DailyRule(at="03:30")
    assert entry.timezone == "America/New_York"
    assert entry.max_lateness == timedelta(hours=12)
    assert entry.job_input is None


def test_the_entry_follows_the_cadence() -> None:
    cadence = CadenceSettings.default(NOW).model_copy(
        update={"backup_time": "01:15", "timezone": "Europe/Berlin"}
    )
    (entry,) = schedule_entries(cadence)
    assert entry.rule == DailyRule(at="01:15")
    assert entry.timezone == "Europe/Berlin"


def test_the_scheduled_job_never_generates_content() -> None:
    provider: ScheduleProvider = schedule_entries
    for entry in provider(CadenceSettings.default(NOW)):
        assert entry.job_type in SCHEDULED_JOB_TYPES
        assert not generates_content(entry.job_type)


def test_the_entries_are_pure() -> None:
    cadence = CadenceSettings.default(NOW)
    assert schedule_entries(cadence) == schedule_entries(cadence)


def test_register_jobs_registers_only_backups_dump() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    assert registry.names() == (BACKUPS_DUMP,)
    job_type = registry.get(BACKUPS_DUMP)
    assert job_type is not None
    assert job_type.step_names == ("dump", "manifest")


def test_settings_default_page_size() -> None:
    assert BackupsSettings().page_size == 200
    assert BackupsSettings(page_size=50).page_size == 50
