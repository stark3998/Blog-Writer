"""Test helpers for ``blogwriter.render`` (used by ``tests/render``; the app never imports this).

Everything here is synchronous and small: sample brands and images, the XSS corpus, PNG, PDF and
markup inspection, and the checks that decide whether the Chromium tests can run on this machine.
"""

from __future__ import annotations

import hashlib
import itertools
import struct
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Final, override

from PIL import Image

from blogwriter.render.brand import RenderBrand
from blogwriter.render.chromium import locate_headless_shell
from blogwriter.render.pdf import extract_text_layer
from blogwriter.render.settings import MERMAID_TINY_SHA256, RenderSettings

__all__ = [
    "ALLOWED_TAGS",
    "PAYLOADS",
    "Canary",
    "chromium_installed",
    "hostile_brand",
    "hostile_spec",
    "make_image",
    "markup_problems",
    "markup_tags",
    "mermaid_bundle_installed",
    "pdf_pages_text",
    "png_chunks",
    "png_size",
    "sample_brand",
]

#: Hostile text for every field (the spike's XSS corpus golden).
PAYLOADS: Final = (
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
)

#: Every tag the templates can produce.
ALLOWED_TAGS: Final = frozenset(
    {
        "html", "head", "meta", "title", "style", "body", "section", "div", "footer", "p",
        "span", "h1", "h2", "ul", "ol", "li", "figure", "figcaption", "img", "blockquote", "main",
    }
)  # fmt: skip
_FORBIDDEN_TAGS: Final = ("script", "iframe", "svg", "a", "base", "link", "object", "embed", "form")
_URL_ATTRS: Final = frozenset({"href", "srcset", "action", "formaction", "background"})


@dataclass
class Canary:
    """A loopback HTTP server's address and every request line it received (should be none)."""

    port: int = 0
    hits: list[str] = field(default_factory=list[str])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def sample_brand(**overrides: Any) -> RenderBrand:
    """A placeholder brand (fictional name) for tests and samples."""
    fields: dict[str, Any] = {
        "name_mark": "Avery Quinn",
        "headline": "Identity and AI security",
        "site_name": "example.com",
    }
    fields.update(overrides)
    return RenderBrand.model_validate(fields)


def hostile_spec(*, diagram: bool) -> dict[str, Any]:
    """The XSS corpus in every text field of every slide type (cut to each field's limit).

    With ``diagram`` a diagram slide is inserted at index 6 (its source stays benign: hostile
    Mermaid has its own tests)."""
    cycle = itertools.cycle(PAYLOADS)

    def p(limit: int) -> str:
        return next(cycle)[:limit]

    slides: list[dict[str, Any]] = [
        {"type": "cover", "kicker": p(40), "hook": p(80), "subtitle": p(110)},
        {"type": "statement", "statement": p(200), "note": p(140)},
        {"type": "list", "title": p(70), "items": [p(100), p(100)]},
        {"type": "steps", "title": p(70),
         "steps": [{"title": p(48), "detail": p(90)}, {"title": p(48)}]},
        {"type": "framework", "title": p(70), "layout": "2x2", "x_axis": p(28), "y_axis": p(28),
         "cells": [{"label": p(28), "text": p(64)} for _ in range(4)]},
        {"type": "framework", "title": p(70), "layout": "layered",
         "cells": [{"label": p(28), "text": p(64)} for _ in range(3)]},
        {"type": "stat", "value": p(10), "label": p(90), "context": p(140), "source": p(90)},
        {"type": "quote", "quote": p(200), "attribution": p(48), "role": p(64)},
        {"type": "recap", "title": p(60), "items": [p(90), p(90)]},
        {"type": "sources", "title": p(40),
         "sources": [{"publisher": p(40), "title": p(100), "date": p(16)}]},
    ]  # fmt: skip
    if diagram:
        slides.insert(
            6,
            {
                "type": "diagram",
                "title": p(70),
                "mermaid": "flowchart TD\n  A[Start] --> B[End]",
                "alt": p(250),
                "caption": p(120),
            },
        )
    return {"title": p(100), "slides": slides}


def hostile_brand() -> RenderBrand:
    return sample_brand(
        name_mark=PAYLOADS[0][:48],
        headline=PAYLOADS[1][:64],
        disclaimer=PAYLOADS[4][:40],
        site_name=PAYLOADS[8][:40],
    )


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def markup_tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    """Every start tag with its (entity-decoded) attributes, in document order."""
    collector = _Collector()
    collector.feed(html)
    collector.close()
    return collector.tags


def markup_problems(html: str, *, images: int) -> list[str]:
    """Why ``html`` is not exactly the templates' markup (empty when it is): only allowed tags,
    no script, links or handlers, one style and four meta elements, ``images`` data: images."""
    problems: list[str] = []
    tags = markup_tags(html)
    names = [t for t, _ in tags]
    problems += [f"tag not allowed: {t}" for t in sorted(set(names) - ALLOWED_TAGS)]
    problems += [f"forbidden tag: {t}" for t in _FORBIDDEN_TAGS if t in names]
    if names.count("style") != 1:
        problems.append(f"{names.count('style')} style elements")
    if names.count("meta") != 4:  # charset, CSP, referrer, color-scheme
        problems.append(f"{names.count('meta')} meta elements")
    for tag, attrs in tags:
        for name, value in attrs.items():
            if name.startswith("on"):
                problems.append(f"event handler {tag}@{name}")
            if name in _URL_ATTRS:
                problems.append(f"URL attribute {tag}@{name}")
            if name == "content" and tag != "meta":
                problems.append(f"content attribute on {tag}")
            if name == "src" and (tag != "img" or not (value or "").startswith("data:image/")):
                problems.append(f"non-data src on {tag}")
    if names.count("img") != images:
        problems.append(f"{names.count('img')} images, expected {images}")
    if "<script" in html.lower():
        problems.append("<script in the document")
    return problems


def png_size(png: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(png)) as im:
        if im.format != "PNG":
            raise AssertionError(f"not a PNG: {im.format}")
        return im.size


def png_chunks(png: bytes) -> list[str]:
    """The chunk types of a PNG, in order."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG")
    i, chunks = 8, list[str]()
    while i < len(png):
        (length,) = struct.unpack(">I", png[i : i + 4])
        chunks.append(png[i + 4 : i + 8].decode("latin-1"))
        i += 12 + length
    return chunks


def pdf_pages_text(pdf: bytes) -> list[str]:
    return list(extract_text_layer(pdf))


def make_image(
    fmt: str = "JPEG",
    size: tuple[int, int] = (400, 300),
    color: tuple[int, int, int] = (200, 30, 30),
    *,
    exif_orientation: int | None = None,
    comment: bytes | None = None,
) -> bytes:
    """A solid-colour image, optionally with an EXIF orientation and a comment (metadata)."""
    im = Image.new("RGB", size, color)
    out = BytesIO()
    params: dict[str, Any] = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        params["exif"] = exif.tobytes()
    if comment is not None and fmt == "JPEG":
        params["comment"] = comment
    im.save(out, format=fmt, **params)
    return out.getvalue()


def chromium_installed() -> bool:
    """Whether Playwright's Chrome Headless Shell for this Playwright build is installed."""
    return locate_headless_shell().executable is not None


def mermaid_bundle_installed(path: Path | None = None) -> bool:
    """Whether the pinned ``mermaid.tiny.js`` is where the settings say (``npm ci`` has run)."""
    target = path if path is not None else RenderSettings().mermaid_js
    if not target.is_file():
        return False
    return hashlib.sha256(target.read_bytes()).hexdigest() == MERMAID_TINY_SHA256
