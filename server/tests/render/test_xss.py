"""XSS corpus golden test in Chromium: hostile text in every field renders as inert, literal text,
even with JavaScript on and the CSP removed. (The markup-only checks run in test_html.py.)"""

from __future__ import annotations

import io
import re

import pytest
import pytest_asyncio
from playwright.async_api import Dialog
from pypdf import PdfReader

from blogwriter.render import CarouselSpec, Renderer
from blogwriter.render.browser import RenderSession
from blogwriter.render.testing import (
    hostile_brand,
    hostile_spec,
    markup_problems,
    pdf_pages_text,
)

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

COUNTS_JS = """() => ({
  scripts: document.scripts.length,
  handlers: document.querySelectorAll('[onerror],[onload],[onclick]').length,
  links: document.querySelectorAll('a[href],base,link,iframe,object,embed,svg').length,
  images: document.images.length,
  refresh: document.querySelectorAll('meta[http-equiv=refresh]').length,
})"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def hostile_html(renderer: Renderer) -> str:
    spec = CarouselSpec.model_validate(hostile_spec(diagram=True))
    return await renderer.carousel_preview_html(spec, hostile_brand())


async def test_markup_structure_is_exactly_the_templates(hostile_html: str) -> None:
    assert markup_problems(hostile_html, images=1) == []  # the diagram, nothing a payload made
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in hostile_html  # escaped, not dropped


async def test_payloads_do_not_execute_even_with_javascript_on_and_no_csp(
    session: RenderSession, hostile_html: str
) -> None:
    """Defence in depth: escaping alone must hold, independent of JS-off and the CSP."""
    without_csp = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", hostile_html)
    assert "Content-Security-Policy" not in without_csp
    ctx = await session.new_context(width=1080, height=1350, javascript=True)
    try:
        page = await ctx.new_page()
        dialogs: list[str] = []

        async def on_dialog(dialog: Dialog) -> None:
            dialogs.append(dialog.message)
            await dialog.dismiss()

        page.on("dialog", on_dialog)
        before = len(session.guard.blocked)
        await page.set_content(without_csp, wait_until="load")
        counts: object = await page.evaluate(COUNTS_JS)
        assert counts == {"scripts": 0, "handlers": 0, "links": 0, "images": 1, "refresh": 0}
        assert dialogs == []
        assert session.guard.blocked[before:] == []  # no payload even attempted a request
    finally:
        await ctx.close()


async def test_payloads_appear_verbatim_in_the_pdf_text_layer(renderer: Renderer) -> None:
    spec = CarouselSpec.model_validate(hostile_spec(diagram=True))
    out = await renderer.render_carousel(spec, hostile_brand())
    text = " ".join(" ".join(pdf_pages_text(out.pdf)).split())
    squashed = text.replace(" ", "")
    for payload in (
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(2)>",
        "javascript:alert(3)",
        "{{ 7*7 }}",
    ):
        assert payload.replace(" ", "") in squashed, payload
    meta = PdfReader(io.BytesIO(out.pdf)).metadata
    assert meta is not None
    assert meta["/Title"] == spec.title  # stored as a literal string, not interpreted
    assert meta["/Author"] == hostile_brand().name_mark
    assert out.pdf.count(b"/Annots") == 0
