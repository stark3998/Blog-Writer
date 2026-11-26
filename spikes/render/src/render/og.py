"""1200x630 Open Graph card as PNG (LinkedIn and X cannot render SVG OG images)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from playwright.async_api import Playwright

from .browser import RenderLimiter, RenderSession, default_limiter, find_overflows, wait_for_fonts
from .errors import LayoutOverflowError, RenderError
from .html import OG_H, OG_W, og_html
from .settings import RenderSettings
from .spec import OgSpec


def parse_og(spec: OgSpec | Mapping[str, Any] | str | bytes) -> OgSpec:
    if isinstance(spec, OgSpec):
        return spec
    if isinstance(spec, str | bytes):
        return OgSpec.model_validate_json(spec)
    return OgSpec.model_validate(dict(spec))


async def render_og_card(
    spec: OgSpec | Mapping[str, Any] | str | bytes,
    *,
    scale: int = 1,
    settings: RenderSettings | None = None,
    limiter: RenderLimiter | None = None,
    playwright: Playwright | None = None,
) -> bytes:
    og = parse_og(spec)
    settings = settings or RenderSettings.from_env()
    limiter = limiter or default_limiter(settings)
    async with limiter.slot():
        try:
            # Covers driver start and browser launch too, and is lifted before teardown (see render_carousel).
            async with (
                asyncio.timeout(settings.job_timeout_s) as deadline,
                RenderSession(settings, playwright) as session,
            ):
                png = await render_og_card_in(session, og, scale=scale)
                deadline.reschedule(None)
                return png
        except TimeoutError as exc:
            raise RenderError(f"render job exceeded {settings.job_timeout_s:g}s") from exc


async def render_og_card_in(session: RenderSession, og: OgSpec, *, scale: int = 1) -> bytes:
    """PNG of ``OG_W*scale`` x ``OG_H*scale`` pixels."""
    if scale not in (1, 2):
        raise ValueError("scale must be 1 or 2")
    async with session.static_page(width=OG_W, height=OG_H, scale=scale) as page:
        await page.set_content(og_html(og), wait_until="load")
        await wait_for_fonts(page)
        overflows = await find_overflows(page)
        if overflows:
            raise LayoutOverflowError(overflows)
        return await page.screenshot(type="png", animations="disabled", caret="hide")
