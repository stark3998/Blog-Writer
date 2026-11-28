"""The render service and lifecycle without a browser: availability, asset checks, the job
deadline and slot, input validation and the job log line."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright
from pydantic import ValidationError
from starlette.requests import Request

from blogwriter.core.logging import JsonFormatter
from blogwriter.render import (
    AssetIntegrityError,
    CarouselSpec,
    MermaidError,
    RenderBrand,
    Renderer,
    RenderError,
    RenderLifecycle,
    RenderSettings,
    RenderTimeout,
    RenderUnavailable,
    get_default_renderer,
    get_renderer,
    render_og_image,
    set_default_renderer,
)
from blogwriter.render import service as service_module
from blogwriter.render.browser import RenderLimiter
from blogwriter.render.chromium import ChromiumLocation
from blogwriter.render.spec import CoverSlide, StatementSlide

STATEMENT_DECK = {"title": "t", "slides": [{"type": "statement", "statement": "A statement."}]}
DIAGRAM_DECK = {
    "title": "t",
    "slides": [{"type": "diagram", "title": "t", "mermaid": "flowchart TD\n A-->B", "alt": "a"}],
}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RenderSettings:
    """Settings whose Mermaid bundle is a small file pinned for this test (no npm needed)."""
    path = tmp_path / "mermaid.tiny.js"
    path.write_text("globalThis.mermaid = {};\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(service_module, "MERMAID_TINY_SHA256", digest)
    return RenderSettings(mermaid_js=path, memory_sample_interval_s=0)


@pytest.fixture
def no_chromium(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    location = ChromiumLocation(browsers_path=tmp_path / "pw", revision="1255", executable=None)
    monkeypatch.setattr(service_module, "locate_headless_shell", lambda: location)


@pytest.fixture(autouse=True)
def clean_default_renderer() -> None:
    set_default_renderer(None)


class _NoSandboxChromium:
    async def launch(self, **_: object) -> None:
        raise PlaywrightError("Launch failed: No usable sandbox! Update your kernel or see docs")


class _HangingChromium:
    async def launch(self, **_: object) -> None:
        await asyncio.sleep(3600)


def fake_playwright(chromium: object) -> Playwright:
    return cast(Playwright, SimpleNamespace(chromium=chromium))


@pytest.mark.usefixtures("no_chromium")
async def test_missing_chromium_turns_rendering_off_but_start_succeeds(
    settings: RenderSettings, brand: RenderBrand, caplog: pytest.LogCaptureFixture
) -> None:
    renderer = Renderer(settings)
    assert renderer.status is None
    with caplog.at_level(logging.WARNING, logger="blogwriter.render"):
        status = await renderer.start()
    assert not status.available
    assert status.reason_code == "chromium_missing"
    assert status.reason is not None
    assert "playwright install --only-shell chromium" in status.reason
    assert [r.getMessage() for r in caplog.records] == ["render_unavailable"]
    spec = CarouselSpec.model_validate(STATEMENT_DECK)
    with pytest.raises(RenderUnavailable, match="not installed"):
        await renderer.render_carousel(spec, brand)
    with pytest.raises(RenderUnavailable):
        await renderer.render_og_image("Title", None, None, brand)
    with pytest.raises(RenderUnavailable):
        await renderer.render_image_card(StatementSlide(statement="s"), brand)
    with pytest.raises(RenderUnavailable):
        await renderer.render_mermaid_svg("flowchart TD\n A-->B")


@pytest.mark.usefixtures("no_chromium")
async def test_preview_of_a_deck_without_diagrams_needs_no_browser(
    settings: RenderSettings, brand: RenderBrand
) -> None:
    renderer = Renderer(settings)
    html = await renderer.carousel_preview_html(CarouselSpec.model_validate(STATEMENT_DECK), brand)
    assert html.startswith("<!doctype html>")
    assert "A statement." in html
    with pytest.raises(RenderUnavailable):
        await renderer.carousel_preview_html(CarouselSpec.model_validate(DIAGRAM_DECK), brand)


async def test_missing_bundle_fails_start_with_a_clear_message(tmp_path: Path) -> None:
    renderer = Renderer(RenderSettings(mermaid_js=tmp_path / "nowhere" / "mermaid.tiny.js"))
    with pytest.raises(AssetIntegrityError, match="npm ci --ignore-scripts"):
        await renderer.start()


async def test_tampered_bundle_fails_start(tmp_path: Path) -> None:
    path = tmp_path / "mermaid.tiny.js"
    path.write_text("globalThis.mermaid = {render: () => 'pwned'};", encoding="utf-8")
    with pytest.raises(AssetIntegrityError, match="sha256 mismatch"):
        await Renderer(RenderSettings(mermaid_js=path)).start()


@pytest.mark.usefixtures("no_chromium")
async def test_lifecycle_installs_and_uninstalls_the_renderer(
    settings: RenderSettings, brand: RenderBrand
) -> None:
    app = FastAPI()
    renderer = Renderer(settings)
    lifecycle = RenderLifecycle(renderer=renderer)
    await lifecycle.startup(app)
    assert app.state.render is renderer
    assert get_default_renderer() is renderer
    assert get_renderer(Request({"type": "http", "app": app})) is renderer
    with pytest.raises(RenderUnavailable):  # the module-level API uses the installed renderer
        await render_og_image("Title", None, None, brand)
    await lifecycle.shutdown(app)
    assert app.state.render is None
    assert get_default_renderer() is not renderer
    with pytest.raises(RuntimeError, match="startup"):
        get_renderer(Request({"type": "http", "app": app}))


async def test_lifecycle_start_fails_closed_on_a_bad_bundle(tmp_path: Path) -> None:
    lifecycle = RenderLifecycle(RenderSettings(mermaid_js=tmp_path / "missing.js"))
    with pytest.raises(AssetIntegrityError):
        await lifecycle.startup(FastAPI())


async def test_sandbox_failure_is_reported_clearly(
    settings: RenderSettings, brand: RenderBrand
) -> None:
    renderer = Renderer(settings, playwright=fake_playwright(_NoSandboxChromium()))
    with pytest.raises(RenderError, match="sandbox"):
        await renderer.render_og_image("Title", None, None, brand)


@pytest.mark.parametrize("kind", ["carousel", "og"])
async def test_job_timeout_also_bounds_a_hung_browser_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: RenderBrand,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    """Spike finding: the deadline once covered rendering only, so a hung launch held the job
    slot (and so every later render) forever."""
    path = tmp_path / "mermaid.tiny.js"
    path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(service_module, "MERMAID_TINY_SHA256", hashlib.sha256(b"x").hexdigest())
    settings = RenderSettings(mermaid_js=path, job_timeout_s=0.3, memory_sample_interval_s=0.05)
    limiter = RenderLimiter(1)
    renderer = Renderer(settings, limiter=limiter, playwright=fake_playwright(_HangingChromium()))
    job = (
        renderer.render_carousel(CarouselSpec.model_validate(STATEMENT_DECK), brand)
        if kind == "carousel"
        else renderer.render_og_image("Title", None, None, brand)
    )
    t0 = time.perf_counter()
    with (
        caplog.at_level(logging.INFO, logger="blogwriter.render"),
        pytest.raises(RenderTimeout, match="exceeded"),
    ):
        await job
    assert time.perf_counter() - t0 < 5
    assert limiter.in_flight == 0
    assert limiter.completed == 1  # the slot is free for the next job
    jobs = [r for r in caplog.records if r.getMessage() == "render_job"]
    assert len(jobs) == 1
    line = json.loads(JsonFormatter().format(jobs[0]))
    assert line["outcome"] == "timeout"
    assert line["render_kind"] == ("carousel" if kind == "carousel" else "og_image")
    assert line["error_type"] == "blogwriter.render.errors.RenderTimeout"
    assert "dropped_fields" not in line  # every field is allowlisted
    assert isinstance(line["memory_samples"], int)
    assert any(s.startswith("browser_launch:") for s in line["stages"])


async def test_inputs_are_validated_before_any_job(
    settings: RenderSettings, brand: RenderBrand
) -> None:
    renderer = Renderer(settings)  # never started: these fail before a job could begin
    for bad in ("", "   ", "flowchart TD\n A-->B\x00", "x" * 3001):
        with pytest.raises(MermaidError, match="1-3000 characters"):
            await renderer.render_mermaid(bad)
    with pytest.raises(ValueError, match="font_size"):
        await renderer.render_mermaid("flowchart TD\n A-->B", font_size=8)
    with pytest.raises(ValueError, match="png width"):
        await renderer.render_mermaid_png("flowchart TD\n A-->B", 0)
    with pytest.raises(ValueError, match="png width"):
        await renderer.render_mermaid_png("flowchart TD\n A-->B", 20_000)
    with pytest.raises(ValueError, match="lang"):
        await renderer.render_image_card(CoverSlide(hook="h"), brand, lang="en; x")
    with pytest.raises(ValidationError):
        await renderer.render_og_image("x" * 111, None, None, brand)
    with pytest.raises(ValidationError):
        await renderer.render_og_image("t", None, None, brand, lang="EN")
    assert renderer.status is None
