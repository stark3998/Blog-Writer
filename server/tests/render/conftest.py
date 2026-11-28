"""Fixtures for the blogwriter.render tests.

Tests marked ``chromium`` launch a real Chrome Headless Shell. They are skipped (with the reason)
when Playwright's headless shell for this Playwright build is not installed, or when the pinned
Mermaid bundle is missing. To run them::

    uv run playwright install --only-shell chromium      # or set PLAYWRIGHT_BROWSERS_PATH
    (cd vendor && npm ci --ignore-scripts)                # server/vendor: @mermaid-js/tiny

Chromium test modules hold only async tests and run them on one module-scoped event loop
(``pytest.mark.asyncio(loop_scope="module")``), so a module shares one driver and browser
(``session``) and one started :class:`Renderer` (``renderer``). Tests that exercise the
per-job lifecycle create their own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from blogwriter.render import CarouselSpec, RenderBrand, Renderer, RenderSettings
from blogwriter.render.browser import RenderSession
from blogwriter.render.mermaid import MermaidBundle, load_bundle
from blogwriter.render.settings import MERMAID_TINY_SHA256
from blogwriter.render.testing import (
    Canary,
    chromium_installed,
    mermaid_bundle_installed,
    sample_brand,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _isolated_settings() -> RenderSettings:
    """``RenderSettings()`` as the defaults, whatever ``BW_RENDER_*`` the developer has set."""
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("BW_RENDER_"):
                mp.delenv(name)
        return RenderSettings()


def _missing_prerequisite() -> str | None:
    if not chromium_installed():
        return (
            "Chromium headless shell is not installed for this Playwright build "
            "(`uv run playwright install --only-shell chromium`, or set PLAYWRIGHT_BROWSERS_PATH)"
        )
    if not mermaid_bundle_installed(_isolated_settings().mermaid_js):
        return "the pinned Mermaid bundle is missing (`npm ci --ignore-scripts` in server/vendor)"
    return None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "chromium: launches Chrome Headless Shell; skipped when it or the Mermaid bundle is "
        "not installed",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    marked = [item for item in items if item.get_closest_marker("chromium") is not None]
    if not marked:
        return
    reason = _missing_prerequisite()
    if reason is None:
        return
    for item in marked:
        item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's BW_* variables and the Container Apps marker from every test."""
    for name in list(os.environ):
        if name.startswith("BW_") or name == "CONTAINER_APP_NAME":
            monkeypatch.delenv(name)


@pytest.fixture(scope="module")
def render_settings() -> RenderSettings:
    return _isolated_settings()


@pytest.fixture(scope="module")
def bundle(render_settings: RenderSettings) -> MermaidBundle:
    if not mermaid_bundle_installed(render_settings.mermaid_js):
        pytest.skip("the pinned Mermaid bundle is missing (`npm ci --ignore-scripts` in vendor)")
    return load_bundle(render_settings.mermaid_js, MERMAID_TINY_SHA256)


@pytest.fixture(scope="session")
def sample_spec() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "sample_carousel.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture(scope="session")
def sample_carousel(sample_spec: dict[str, Any]) -> CarouselSpec:
    return CarouselSpec.model_validate(sample_spec)


@pytest.fixture(scope="session")
def brand() -> RenderBrand:
    return sample_brand()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session(render_settings: RenderSettings) -> AsyncIterator[RenderSession]:
    """One driver and Chromium for the tests that do not exercise the per-job lifecycle."""
    async with RenderSession(render_settings) as s:
        yield s


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def renderer(render_settings: RenderSettings) -> Renderer:
    """A started renderer (one job per call, its own limiter)."""
    r = Renderer(render_settings)
    await r.start()
    return r


@pytest_asyncio.fixture(loop_scope="module")
async def canary() -> AsyncIterator[Canary]:
    state = Canary()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            state.hits.append(line.decode("latin-1").strip())
            body = b"\x89PNG\r\n\x1a\n"
            head = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\n\r\n"
            writer.write(head % len(body))
            writer.write(body)
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()
