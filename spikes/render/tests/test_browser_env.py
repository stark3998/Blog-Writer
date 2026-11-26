"""Chromium gets a minimal environment (no app secrets) and leaves nothing behind."""

from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest

from render import RenderSession

pytestmark = pytest.mark.slow


def _browser_processes() -> list[psutil.Process]:
    procs = []
    for child in psutil.Process().children(recursive=True):
        try:
            name = child.name().lower()
        except psutil.Error:
            continue
        if "chrom" in name or "headless" in name:
            procs.append(child)
    return procs


async def test_chromium_does_not_inherit_app_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_TEST_SECRET_CANARY", "must-not-leak")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "must-not-leak-either")
    async with RenderSession() as session:
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
            assert "BW_TEST_SECRET_CANARY" not in env
            assert "AZURE_CLIENT_SECRET" not in env
            assert set(env) <= {
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "TZ",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
            } | {
                k
                for k in env
                if k.startswith(("CHROME_", "GOOGLE_", "__CF", "XPC_", "MallocNano", "MACH_PORT_RENDEZVOUS"))
            }, sorted(env)
        if checked == 0:
            pytest.skip("this OS does not let us read child process environments")


async def test_session_cleans_up_browser_and_temp_home() -> None:
    session = RenderSession()
    async with session:
        home = session._home
        assert home is not None and home.is_dir()
        pids = {p.pid for p in _browser_processes()}
        assert pids
    assert not Path(home).exists()
    alive = [
        pid for pid in pids if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    ]
    assert alive == []
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH")  # tests run against the spike-local browser
