"""The compensating control for running Chromium without its sandbox (PR_SET_DUMPABLE=0)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi import FastAPI

from blogwriter.render import lifecycle as render_lifecycle
from blogwriter.render.hardening import harden_process, is_dumpable
from blogwriter.render.settings import RenderSettings

LINUX = sys.platform.startswith("linux")


@pytest.mark.skipif(not LINUX, reason="prctl is Linux-only")
def test_harden_process_makes_a_process_non_dumpable() -> None:
    # Run in a child: the flag cannot be turned back on in the test process itself.
    code = (
        "from blogwriter.render.hardening import harden_process, is_dumpable;"
        "assert is_dumpable() is True; assert harden_process() is True;"
        "assert is_dumpable() is False; print('ok')"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv: this interpreter and a literal
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "ok"


@pytest.mark.skipif(LINUX, reason="off Linux the call is a no-op")
def test_harden_process_is_a_noop_off_linux() -> None:
    assert harden_process() is False
    assert is_dumpable() is None


class _FakeRenderer:
    def __init__(self, settings: RenderSettings) -> None:
        self.settings = settings
        self.started = False

    async def start(self) -> None:
        self.started = True


@pytest.mark.parametrize(("sandbox", "expected_calls"), [(True, 0), (False, 1)])
async def test_startup_hardens_only_when_the_sandbox_is_off(
    monkeypatch: pytest.MonkeyPatch, sandbox: bool, expected_calls: int
) -> None:
    calls: list[int] = []

    def fake_harden() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(render_lifecycle, "harden_process", fake_harden)

    def no_default(_renderer: object) -> None:
        return None

    monkeypatch.setattr(render_lifecycle, "set_default_renderer", no_default)
    renderer = _FakeRenderer(RenderSettings(chromium_sandbox=sandbox))
    lc = render_lifecycle.RenderLifecycle(renderer=renderer)  # pyright: ignore[reportArgumentType]
    await lc.startup(FastAPI())
    assert renderer.started
    assert len(calls) == expected_calls


async def test_startup_refuses_when_hardening_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing() -> bool:
        raise OSError(1, "prctl(PR_SET_DUMPABLE, 0) failed")

    monkeypatch.setattr(render_lifecycle, "harden_process", failing)
    renderer = _FakeRenderer(RenderSettings(chromium_sandbox=False))
    lc = render_lifecycle.RenderLifecycle(renderer=renderer)  # pyright: ignore[reportArgumentType]
    with pytest.raises(OSError, match="PR_SET_DUMPABLE"):
        await lc.startup(FastAPI())
    assert not renderer.started
