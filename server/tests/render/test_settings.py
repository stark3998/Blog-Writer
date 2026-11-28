"""Settings, launch flags, environments, the limiter and the stale-home sweeper (no browser)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from blogwriter.render import RenderSettings
from blogwriter.render.browser import (
    DEAD_END_PROXY,
    HOME_PREFIX,
    RenderLimiter,
    chromium_args,
    driver_env,
    minimal_env,
    sweep_stale_homes,
)
from blogwriter.render.pdf import MAX_PDF_BYTES
from blogwriter.render.settings import DEFAULT_MERMAID_JS

ALLOWED_ENV = {"PATH", "HOME", "TMPDIR", "LANG", "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}


def test_production_defaults() -> None:
    s = RenderSettings()
    assert s.concurrency == 1
    assert s.chromium_sandbox is True
    assert s.max_pdf_bytes == MAX_PDF_BYTES == 10 * 1024 * 1024
    assert s.js_heap_limit_mb == 256
    assert s.mermaid_js == DEFAULT_MERMAID_JS
    assert s.mermaid_js.name == "mermaid.tiny.js"


def test_default_bundle_path_is_in_server_vendor() -> None:
    parts = DEFAULT_MERMAID_JS.parts
    assert parts[-7:] == (
        "server",
        "vendor",
        "node_modules",
        "@mermaid-js",
        "tiny",
        "dist",
        "mermaid.tiny.js",
    )


@pytest.mark.parametrize("jobs", ["0", "3"])
def test_concurrency_is_bounded_to_the_plan(monkeypatch: pytest.MonkeyPatch, jobs: str) -> None:
    monkeypatch.setenv("BW_RENDER_CONCURRENCY", jobs)
    with pytest.raises(ValidationError, match="concurrency"):
        RenderSettings()


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BW_RENDER_CONCURRENCY", "2")
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", "0")
    monkeypatch.setenv("BW_RENDER_MERMAID_JS", str(tmp_path / "tiny.js"))
    monkeypatch.setenv("BW_RENDER_JOB_TIMEOUT_S", "30")
    s = RenderSettings()
    assert s.concurrency == 2
    assert s.chromium_sandbox is False
    assert s.mermaid_js == tmp_path / "tiny.js"
    assert s.job_timeout_s == 30


def test_pdf_limit_can_only_be_lowered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_RENDER_MAX_PDF_BYTES", str(MAX_PDF_BYTES + 1))
    with pytest.raises(ValidationError):
        RenderSettings()
    monkeypatch.setenv("BW_RENDER_MAX_PDF_BYTES", "1000")
    assert RenderSettings().max_pdf_bytes == 1000


def test_jitless_cannot_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_RENDER_JS_JITLESS", "0")
    assert any("--jitless" in arg for arg in chromium_args(RenderSettings()))
    with pytest.raises(ValidationError):
        RenderSettings(js_jitless=False)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", False), ("false", False), ("No", False), (" off ", False),
        ("1", True), ("TRUE", True), ("", True), ("   ", True),
    ],
)  # fmt: skip
def test_sandbox_flag_accepts_only_known_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", raw)
    assert RenderSettings().chromium_sandbox is expected


@pytest.mark.parametrize("raw", ["ture", "enabled", "disable", "2"])
def test_sandbox_flag_typo_is_an_error_not_a_silent_off(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """Spike finding: every unrecognised value used to turn the sandbox OFF."""
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", raw)
    with pytest.raises(ValidationError, match="chromium_sandbox"):
        RenderSettings()


def test_rejected_input_is_not_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_RENDER_CHROMIUM_SANDBOX", "secret-looking-typo")
    with pytest.raises(ValidationError) as exc:
        RenderSettings()
    assert "secret-looking-typo" not in str(exc.value)


def test_hardening_flags_are_present() -> None:
    args = chromium_args(RenderSettings())
    assert "--host-resolver-rules=MAP * ~NOTFOUND" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--js-flags=--max-old-space-size=256 --jitless" in args
    assert DEAD_END_PROXY == "http://127.0.0.1:9"


def test_minimal_env_contains_only_allowlisted_keys(tmp_path: Path) -> None:
    env = minimal_env(tmp_path)
    assert set(env) <= ALLOWED_ENV
    assert env["HOME"] == env["TMPDIR"] == str(tmp_path)
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_driver_env_adds_only_the_browser_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("IDENTITY_HEADER", "must-not-leak")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "must-not-leak")
    env = driver_env(tmp_path / "home", tmp_path / "browsers")
    assert set(env) <= ALLOWED_ENV | {"PLAYWRIGHT_BROWSERS_PATH"}
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "browsers")
    assert "must-not-leak" not in env.values()


async def test_limiter_counts_in_flight_jobs() -> None:
    limiter = RenderLimiter(1)
    async with limiter.slot():
        assert limiter.in_flight == 1
    assert limiter.in_flight == 0
    assert limiter.completed == 1
    assert limiter.max_in_flight == 1


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
    assert not stale.exists()
    assert fresh.exists()
    assert other.exists()
