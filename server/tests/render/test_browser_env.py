"""Per-job isolation in Chromium: no inherited secrets, nothing left behind, and the service
finding the browser at start-up and logging one line per job."""

from __future__ import annotations

import asyncio
import json
import logging

import psutil
import pytest

from blogwriter.core.logging import JsonFormatter
from blogwriter.render import RenderBrand, Renderer, RenderSettings
from blogwriter.render.browser import RenderSession

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

SECRETS = {
    "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
    "IDENTITY_HEADER": "must-not-leak-identity",
    "AZURE_CLIENT_SECRET": "must-not-leak-secret",
    "BW_RENDER_TEST_CANARY": "must-not-leak-bw",
}
ALLOWED = {"PATH", "HOME", "TMPDIR", "LANG", "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
#: Variables Chromium or the OS add on their own (macOS adds the last four to every process).
SELF_ADDED = ("CHROME_", "GOOGLE_", "__CF", "XPC_", "MallocNano", "MACH_PORT_RENDEZVOUS")


def _browser_processes() -> list[psutil.Process]:
    procs: list[psutil.Process] = []
    for child in psutil.Process().children(recursive=True):
        try:
            name = child.name().lower()
        except psutil.Error:
            continue
        if "chrom" in name or "headless" in name:
            procs.append(child)
    return procs


async def test_chromium_does_not_inherit_app_secrets(
    monkeypatch: pytest.MonkeyPatch, render_settings: RenderSettings
) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    async with RenderSession(render_settings) as session:
        assert session.browser.is_connected()
        procs = _browser_processes()
        assert procs, "no Chromium processes found under this test process"
        checked = 0
        for proc in procs:
            try:
                env = proc.environ()
            except (psutil.AccessDenied, psutil.ZombieProcess, psutil.NoSuchProcess):
                continue
            checked += 1
            for value in SECRETS.values():
                assert value not in env.values()
            unexpected = {k for k in env if k not in ALLOWED and not k.startswith(SELF_ADDED)}
            assert unexpected == set()
        if checked == 0:  # pragma: no cover - OS policy
            pytest.skip("this OS does not let us read child process environments")


async def test_session_cleans_up_driver_browser_and_temp_home(
    render_settings: RenderSettings,
) -> None:
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    session = RenderSession(render_settings)
    async with session:
        home = session.home
        assert home is not None
        assert await asyncio.to_thread(home.is_dir)
        assert home.name.startswith("bw-render-")
        pids = {p.pid for p in psutil.Process().children(recursive=True)} - before
        assert pids  # the driver and the browser
    assert session.home is None
    assert not await asyncio.to_thread(home.exists)
    alive = [
        pid
        for pid in pids
        if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    ]
    assert alive == []


async def test_start_finds_chromium_and_jobs_log_numbers_only(
    render_settings: RenderSettings, brand: RenderBrand, caplog: pytest.LogCaptureFixture
) -> None:
    renderer = Renderer(render_settings)
    with caplog.at_level(logging.INFO, logger="blogwriter.render"):
        status = await renderer.start()
        await renderer.render_og_image("Title", None, None, brand)
    assert status.available
    assert status.chromium is not None
    assert status.chromium.is_file()
    assert (
        status.mermaid_sha256 == "c41f5236091b83f524c4940f97a16b2b14a76f21ff84aea4fd6b66421d8a2a9a"
    )
    records = {r.getMessage(): r for r in caplog.records}
    assert "render_ready" in records
    line = json.loads(JsonFormatter().format(records["render_job"]))
    assert line["outcome"] == "ok"
    assert line["render_kind"] == "og_image"
    assert line["output_bytes"] > 0
    assert line["tree_rss_mib"] > 0
    assert line["chromium_processes"] >= 1
    assert "dropped_fields" not in line
    assert {s.split(":")[0] for s in line["stages"]} >= {
        "driver_start",
        "browser_launch",
        "set_content",
        "png",
        "teardown",
    }
    assert "Title" not in json.dumps(line)  # no content in the log line
