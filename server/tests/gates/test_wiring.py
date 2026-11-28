"""Schedule entries, the lifecycle, settings and the public API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import ValidationError

import blogwriter.gates as gates
from blogwriter.domain.jobtypes import SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.ports import SlopChecker
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.gates import GatesLifecycle, GatesSettings, SlopDetectors, schedule_entries
from blogwriter.gates.settings import gates_settings
from blogwriter.jobs.scheduler import WeeklyRule


def test_the_standards_refresh_is_scheduled_weekly() -> None:
    cadence = CadenceSettings.default(SAMPLE_NOW).model_copy(update={"timezone": "Europe/Berlin"})
    (entry,) = schedule_entries(cadence)
    assert entry.id == "sched-gates-standards"
    assert entry.job_type == "gates.standards-refresh"
    assert entry.job_type in SCHEDULED_JOB_TYPES
    assert not generates_content(entry.job_type)
    assert isinstance(entry.rule, WeeklyRule)
    assert (entry.rule.days, entry.rule.at) == (["sat"], "05:00")
    assert entry.timezone == "Europe/Berlin"
    assert entry.max_lateness == timedelta(hours=48)
    assert entry.job_input == {}
    assert schedule_entries(cadence) == schedule_entries(cadence)  # pure, deterministic


async def test_the_lifecycle_installs_the_slop_port() -> None:
    app = FastAPI()
    lifecycle = GatesLifecycle()
    await lifecycle.startup(app)
    slop: SlopChecker = app.state.slop
    assert isinstance(slop, SlopDetectors)
    assert slop.check("Security isn't a tool, it's a mindset.")[0].code == "isnt-x-its-y"
    await lifecycle.shutdown(app)
    assert app.state.slop is None
    own = SlopDetectors()
    await GatesLifecycle(own).startup(app)
    assert app.state.slop is own


def test_settings_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = GatesSettings()
    assert (
        settings.max_links_checked,
        settings.link_timeout_seconds,
        settings.novelty_corpus,
        settings.atlas_url,
        settings.site_origin,
        settings.site_host,
    ) == (25, 10.0, 50, None, None, None)
    monkeypatch.setenv("BW_GATES_SITE_ORIGIN", "https://Owner-Site.Example/")
    monkeypatch.setenv("BW_GATES_MAX_LINKS_CHECKED", "5")
    gates_settings.cache_clear()
    configured = gates_settings()
    assert configured.site_origin == "https://owner-site.example"
    assert configured.site_host == "owner-site.example"
    assert configured.max_links_checked == 5
    assert gates_settings() is configured
    for bad in ("http://owner-site.example", "https://owner-site.example/blog", "not a url"):
        with pytest.raises(ValidationError):
            GatesSettings(site_origin=bad)
    with pytest.raises(ValidationError):
        GatesSettings(atlas_url="http://example.org/ATLAS.yaml")


def test_the_public_api() -> None:
    for name in (
        "SlopDetectors",
        "RUBRIC_DIMENSIONS",
        "RUBRIC_OVERALL_MIN",
        "RUBRIC_DIMENSION_MIN",
        "RUBRIC_SPECIFICITY_MIN",
        "run_gates",
        "save_report",
        "lint_text",
        "validate_standard_ids",
        "GatesLifecycle",
        "get_router",
        "register_jobs",
        "schedule_entries",
    ):
        assert hasattr(gates, name), name
    paths = {route.path for route in gates.get_router().routes if isinstance(route, APIRoute)}
    assert paths == {
        "/api/articles/{article_id}/gates/run",
        "/api/articles/{article_id}/gates",
        "/api/articles/{article_id}/gates/{gate}/override",
        "/api/gates/lint",
        "/api/gates/standards",
        "/api/gates/standards/refresh",
    }
