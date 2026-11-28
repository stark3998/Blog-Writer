"""The Playwright driver starts with the minimal environment (needs Playwright's bundled Node,
not Chromium). Chromium's own environment is tested in test_browser_env.py."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import psutil
import pytest

from blogwriter.render.browser import install_driver_env_guard, start_driver

SECRETS = {
    "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
    "IDENTITY_HEADER": "must-not-leak-identity",
    "AZURE_CLIENT_ID": "must-not-leak-client",
    "AZURE_CLIENT_SECRET": "must-not-leak-secret",
    "BW_RENDER_TEST_CANARY": "must-not-leak-bw",
}
ALLOWED = {
    "PATH", "HOME", "TMPDIR", "LANG", "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    "PLAYWRIGHT_BROWSERS_PATH", "PW_LANG_NAME", "PW_LANG_NAME_VERSION", "PW_CLI_DISPLAY_VERSION",
}  # fmt: skip
#: Variables the OS itself adds to every process on macOS.
OS_ADDED = ("__CF", "XPC_", "MallocNano", "MACH_PORT_RENDEZVOUS")


def _driver_processes(before: set[int]) -> list[psutil.Process]:
    found: list[psutil.Process] = []
    for child in psutil.Process().children(recursive=True):
        if child.pid in before:
            continue
        try:
            if "run-driver" in " ".join(child.cmdline()):
                found.append(child)
        except psutil.Error:
            continue
    return found


async def test_driver_inherits_no_app_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    home = tmp_path / "home"
    home.mkdir()
    browsers = tmp_path / "browsers"
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    pw = await start_driver(home, browsers)
    try:
        drivers = _driver_processes(before)
        assert len(drivers) == 1
        try:
            env = drivers[0].environ()
        except (psutil.AccessDenied, psutil.ZombieProcess):  # pragma: no cover - OS policy
            pytest.skip("this OS does not let us read a child process environment")
        unexpected = {k for k in env if k not in ALLOWED and not k.startswith(OS_ADDED)}
        assert unexpected == set()
        for value in SECRETS.values():
            assert value not in env.values()
        assert env["HOME"] == str(home)
        assert env["TMPDIR"] == str(home)
        assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers)
        assert env["PW_LANG_NAME"] == "python"
    finally:
        await pw.stop()


def test_other_playwright_users_keep_the_original_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hook only applies while a render session starts its driver."""
    assert install_driver_env_guard()
    assert install_driver_env_guard()  # idempotent
    monkeypatch.setenv("BW_RENDER_TEST_CANARY", "visible")
    transport = importlib.import_module("playwright._impl.transport")
    get_env = cast(Callable[[], dict[str, str]], transport.get_driver_env)
    env = get_env()
    assert env["BW_RENDER_TEST_CANARY"] == "visible"
    assert env["PATH"] == os.environ["PATH"]
    assert env["PW_LANG_NAME"] == "python"
