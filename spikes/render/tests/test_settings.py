"""Settings: production defaults, bounds, env overrides."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError

from render import RenderError, RenderSession, RenderSettings, render_carousel, render_og_card
from render.browser import HOME_PREFIX, RenderLimiter, minimal_env, sweep_stale_homes


def test_production_defaults() -> None:
    s = RenderSettings()
    assert s.max_concurrent_jobs == 1
    assert s.chromium_sandbox is True
    assert s.max_pdf_bytes == 10 * 1024 * 1024
    assert s.mermaid_bundle.name == "mermaid.tiny.js"


@pytest.mark.parametrize("jobs", [0, 3])
def test_concurrency_is_bounded_to_the_plan(jobs: int) -> None:
    with pytest.raises(ValueError, match="1 or 2"):
        RenderSettings(max_concurrent_jobs=jobs)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BW_RENDER_MAX_CONCURRENT_JOBS", "2")
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", "0")
    monkeypatch.setenv("BW_RENDER_MERMAID_JS", str(tmp_path / "tiny.js"))
    s = RenderSettings.from_env()
    assert s.max_concurrent_jobs == 2
    assert s.chromium_sandbox is False
    assert s.mermaid_bundle == tmp_path / "tiny.js"


def test_minimal_env_contains_only_allowlisted_keys(tmp_path: Path) -> None:
    env = minimal_env(tmp_path)
    assert set(env) <= {"PATH", "HOME", "TMPDIR", "LANG", "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
    assert env["HOME"] == str(tmp_path)


async def test_limiter_counts_in_flight_jobs() -> None:
    limiter = RenderLimiter(1)
    async with limiter.slot():
        assert limiter.in_flight == 1
    assert limiter.in_flight == 0 and limiter.completed == 1 and limiter.max_in_flight == 1


def test_sweeper_removes_only_stale_job_homes(tmp_path: Path) -> None:
    stale = tmp_path / f"{HOME_PREFIX}old"
    fresh = tmp_path / f"{HOME_PREFIX}new"
    other = tmp_path / "unrelated"
    for d in (stale, fresh, other):
        d.mkdir()
    old = time.time() - 7200
    os.utime(stale, (old, old))
    os.utime(other, (old, old))
    assert sweep_stale_homes(max_age_s=3600, root=tmp_path) == 1
    assert not stale.exists() and fresh.exists() and other.exists()


class _NoSandboxChromium:
    async def launch(self, **_: object) -> None:
        raise PlaywrightError("Launch failed: No usable sandbox! Update your kernel or see docs")


async def test_sandbox_failure_is_reported_clearly() -> None:
    fake_pw = SimpleNamespace(chromium=_NoSandboxChromium())
    with pytest.raises(RenderError, match="sandbox"):
        async with RenderSession(playwright=fake_pw):  # type: ignore[arg-type]
            pass


async def test_job_timeout_becomes_a_render_error(sample_spec: dict[str, object]) -> None:
    settings = replace(RenderSettings(), job_timeout_s=0.01)
    with pytest.raises(RenderError, match="exceeded"):
        await render_carousel(sample_spec, settings=settings)


# --- Reviewer additions -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", False), ("false", False), ("No", False), (" off ", False),
        ("1", True), ("TRUE", True), ("", True),
    ],
)  # fmt: skip
def test_sandbox_flag_accepts_only_known_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", raw)
    assert RenderSettings.from_env().chromium_sandbox is expected


@pytest.mark.parametrize("raw", ["ture", "enabled", "disable", "2"])
def test_sandbox_flag_typo_is_an_error_not_a_silent_off(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Before the fix every unrecognised value (a typo, "enabled") turned the sandbox OFF."""
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", raw)
    with pytest.raises(ValueError, match="BW_RENDER_CHROMIUM_SANDBOX"):
        RenderSettings.from_env()


class _HangingChromium:
    async def launch(self, **_: object) -> None:
        await asyncio.sleep(3600)


@pytest.mark.parametrize("kind", ["carousel", "og"])
async def test_job_timeout_also_bounds_a_hung_browser_launch(
    sample_spec: dict[str, object], kind: str
) -> None:
    """Before the fix the deadline only covered rendering, so a hung launch held the job slot forever."""
    settings = replace(RenderSettings(), job_timeout_s=0.3)
    limiter = RenderLimiter(1)
    fake_pw = SimpleNamespace(chromium=_HangingChromium())
    t0 = time.perf_counter()
    with pytest.raises(RenderError, match="exceeded"):
        if kind == "carousel":
            await render_carousel(sample_spec, settings=settings, limiter=limiter, playwright=fake_pw)  # type: ignore[arg-type]
        else:
            await render_og_card({"title": "t"}, settings=settings, limiter=limiter, playwright=fake_pw)  # type: ignore[arg-type]
    assert time.perf_counter() - t0 < 5
    assert limiter.in_flight == 0 and limiter.completed == 1  # the slot is free for the next job
