"""XSS corpus golden test: hostile text in every field renders as inert, literal text."""

from __future__ import annotations

import io
import itertools
import re
from html.parser import HTMLParser

import pytest
from playwright.async_api import Dialog
from pydantic import ValidationError
from pypdf import PdfReader

from render import DeckSpec, RenderSession, render_carousel_in
from render.html import deck_html
from render.mermaid import MermaidRenderer
from render.spec import DiagramSlide

from .conftest import pdf_pages_text

PAYLOADS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(2)>",
    "javascript:alert(3)",
    "</p></section></body></html><script>alert(4)</script>",
    "</title><script>alert(5)</script>",
    "</style><img src=x onerror=alert(6)>",
    '"><svg onload=alert(7)>',
    "'><iframe src=javascript:alert(8)>",
    '<a href="https://evil.example">click</a>',
    "{{ 7*7 }} {% raw %}",
    "<!-- --><base href=//evil.example/>",
    "&lt;script&gt;alert(9)&lt;/script&gt;",
    '<meta http-equiv="refresh" content="0;url=https://evil.example">',
    "<link rel=stylesheet href=https://evil.example/x.css>",
]
_cycle = itertools.cycle(PAYLOADS)


def p(limit: int) -> str:
    """Next payload, cut to the field's limit (cutting keeps it hostile: '<script>al...')."""
    return next(_cycle)[:limit]


def hostile_spec() -> dict[str, object]:
    diagram = "flowchart TD\n  A[Start] --> B[End]"
    return {
        "title": p(100),
        "brand": {"author_name": p(48), "author_headline": p(64), "disclaimer": p(40)},
        "slides": [
            {"type": "cover", "kicker": p(40), "hook": p(80), "subtitle": p(110)},
            {"type": "statement", "statement": p(200), "note": p(140)},
            {"type": "list", "title": p(70), "items": [p(100), p(100)]},
            {"type": "steps", "title": p(70), "steps": [{"title": p(48), "detail": p(90)}, {"title": p(48)}]},
            {"type": "framework", "title": p(70), "layout": "2x2", "x_axis": p(28), "y_axis": p(28),
             "cells": [{"label": p(28), "text": p(64)} for _ in range(4)]},
            {"type": "framework", "title": p(70), "layout": "layered",
             "cells": [{"label": p(28), "text": p(64)} for _ in range(3)]},
            {"type": "diagram", "title": p(70), "mermaid": diagram, "alt": p(250), "caption": p(120)},
            {"type": "stat", "value": p(10), "label": p(90), "context": p(140), "source": p(90)},
            {"type": "quote", "quote": p(200), "attribution": p(48), "role": p(64)},
            {"type": "recap", "title": p(60), "items": [p(90), p(90)]},
            {"type": "sources", "title": p(40),
             "sources": [{"publisher": p(40), "title": p(100), "date": p(16)}]},
        ],
    }  # fmt: skip


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


@pytest.fixture(scope="module")
def hostile_deck() -> DeckSpec:
    return DeckSpec.model_validate(hostile_spec())


@pytest.fixture(scope="module")
async def hostile_html(session: RenderSession, hostile_deck: DeckSpec) -> str:
    async with MermaidRenderer(session) as mr:
        uris = {
            i: (await mr.render(s.mermaid, hostile_deck.brand.colors)).data_uri
            for i, s in enumerate(hostile_deck.slides)
            if isinstance(s, DiagramSlide)
        }
    return deck_html(hostile_deck, uris)


async def test_markup_structure_is_exactly_the_templates(hostile_html: str) -> None:
    c = _Collector()
    c.feed(hostile_html)
    names = {t for t, _ in c.tags}
    assert names <= {
        "html", "head", "meta", "title", "style", "body", "section", "div", "footer", "p", "span", "h1",
        "h2", "ul", "ol", "li", "figure", "figcaption", "img", "blockquote", "main",
    }, names - {"html"}  # fmt: skip
    for forbidden in ("script", "iframe", "svg", "a", "base", "link", "object", "embed", "form", "input"):
        assert forbidden not in names
    assert [t for t, _ in c.tags].count("style") == 1
    assert [t for t, _ in c.tags].count("meta") == 4  # charset, CSP, referrer, color-scheme
    for tag, attrs in c.tags:
        for name, value in attrs.items():
            assert not name.startswith("on"), (tag, name)
            assert (
                name not in {"href", "srcset", "action", "formaction", "background", "content"}
                or tag == "meta"
            )
            if name == "src":
                assert tag == "img" and (value or "").startswith("data:image/svg+xml;base64,")
    assert [t for t, _ in c.tags].count("img") == 1  # the diagram, and nothing a payload made
    assert "<script" not in hostile_html.lower()
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
        counts = await page.evaluate(
            """() => ({
              scripts: document.scripts.length,
              handlers: document.querySelectorAll('[onerror],[onload],[onclick]').length,
              links: document.querySelectorAll('a[href],base,link,iframe,object,embed,svg').length,
              images: document.images.length,
              refresh: document.querySelectorAll('meta[http-equiv=refresh]').length,
            })"""
        )
        assert counts == {"scripts": 0, "handlers": 0, "links": 0, "images": 1, "refresh": 0}
        assert dialogs == []
        assert session.guard.blocked[before:] == []  # no payload even attempted a request
    finally:
        await ctx.close()


async def test_payloads_appear_verbatim_in_the_pdf_text_layer(
    session: RenderSession, hostile_deck: DeckSpec
) -> None:
    out = await render_carousel_in(session, hostile_deck)
    text = " ".join(" ".join(pdf_pages_text(out.pdf)).split())
    squashed = text.replace(" ", "")
    for payload in (
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(2)>",
        "javascript:alert(3)",
        "{{ 7*7 }}",
    ):
        assert payload.replace(" ", "") in squashed, payload
    meta = PdfReader(io.BytesIO(out.pdf)).metadata or {}
    assert meta["/Title"] == hostile_deck.title  # stored as a literal string, not interpreted
    assert out.pdf.count(b"/Annots") == 0


def test_plain_text_strips_invisible_spoofing_characters() -> None:
    deck = DeckSpec.model_validate(
        {
            "title": "Cli\u200bent \u202eevil\u202c name\ufeff",
            "slides": [{"type": "statement", "statement": "a\u2066b\u2069c\tline\nbreak"}],
        }
    )
    assert deck.title == "Client evil name"
    assert deck.slides[0].statement == "abc line break"  # type: ignore[union-attr]


@pytest.mark.parametrize("bad", ["nul\x00byte", "bell\x07", "esc\x1b[31m"])
def test_plain_text_rejects_control_characters(bad: str) -> None:
    with pytest.raises(ValidationError):
        DeckSpec.model_validate({"title": bad, "slides": [{"type": "statement", "statement": "ok"}]})


@pytest.mark.parametrize(
    "color", ["red", "#fff", "#0E2A24;background:url(https://evil.example)", "#0E2A24</style>", "rgb(0,0,0)"]
)
def test_brand_colours_cannot_inject_css(color: str) -> None:
    with pytest.raises(ValidationError):
        DeckSpec.model_validate(
            {
                "title": "t",
                "brand": {"colors": {"ink": color}},
                "slides": [{"type": "statement", "statement": "s"}],
            }
        )
