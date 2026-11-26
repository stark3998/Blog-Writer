"""Self-contained HTML for slides and OG cards (Jinja2, autoescape on).

The same document is used for the sandboxed Studio preview and for Chromium's PDF/PNG output, so it
carries its own Content-Security-Policy: nothing but inline styles, ``data:`` images and ``data:``
fonts may load, and no script may run, whatever renders it.

Text is sized from its length here, in Python, because pages render with JavaScript disabled; the
renderer then measures every ``[data-fit]`` box and refuses to ship clipped content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any, Final, Literal, cast

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from .brand import BrandColors, BrandKit
from .fonts import font_face_css
from .spec import DeckSpec, OgSpec, Slide

SLIDE_W: Final = 1080
SLIDE_H: Final = 1350
OG_W: Final = 1200
OG_H: Final = 630

#: For pages that must never run script or fetch anything. ``data:`` only for images and fonts.
STATIC_CSP: Final = (
    "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)

Surface = Literal["ink", "paper"]
_HEX: Final = re.compile(r"#[0-9A-Fa-f]{6}")
_INK_SLIDES: Final = frozenset({"cover", "statement", "stat"})

#: (max characters, px) steps per text role, then the floor size. The floor of every role is chosen
#: so the longest value the spec allows still fits (verified by tests/test_layout.py).
_FIT: Final[dict[str, tuple[tuple[tuple[int, int], ...], int]]] = {
    "hook": (((28, 112), (52, 96)), 80),
    "statement": (((60, 80), (110, 66)), 56),
    "title": (((32, 64), (52, 56)), 50),
    "quote": (((80, 60), (140, 52)), 46),
    "stat": (((4, 280), (6, 210), (8, 160)), 128),
    "og_title": (((40, 84), (72, 70)), 58),
}


def fit(text: str, role: str) -> int:
    """Font size in px for ``text`` in ``role`` (longer text steps down)."""
    steps, floor = _FIT[role]
    n = len(text)
    for max_len, px in steps:
        if n <= max_len:
            return px
    return floor


@dataclass(frozen=True, slots=True)
class SlideView:
    n: int
    total: int
    slide: Slide
    brand: BrandKit
    surface: Surface
    diagram_uri: str | None = None

    @property
    def progress_pct(self) -> str:
        return f"{self.n / self.total * 100:.3f}"


@cache
def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("render", "templates"),
        autoescape=True,  # every template, every expression; never select_autoescape by extension
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )
    env.globals.update({"fit": cast(Any, fit)})
    return env


def _css_vars(colors: BrandColors) -> Markup:
    # Values are validated #RRGGBB, so this cannot break out of the declaration or the <style>.
    pairs = {
        "ink": colors.ink,
        "paper": colors.paper,
        "muted": colors.muted,
        "muted-on-ink": colors.muted_on_ink,
        "signal": colors.signal,
        "signal-text": colors.signal_text,
        "rule": colors.rule,
        "rule-on-ink": colors.rule_on_ink,
    }
    body = ";".join(f"--{k}:{v}" for k, v in pairs.items())
    css = ":root{" + body + "}"
    if "<" in css or ">" in css:  # pragma: no cover - guarded by HexColor
        raise ValueError("unsafe CSS variable value")
    return Markup(css)  # noqa: S704 - built only from validated hex colours


def surface_for(slide: Slide) -> Surface:
    return "ink" if slide.type in _INK_SLIDES else "paper"


def deck_html(spec: DeckSpec, diagram_uris: dict[int, str] | None = None) -> str:
    """Render the whole deck as one self-contained HTML document (one ``<section>`` per slide).

    ``diagram_uris`` maps a 0-based slide index to the ``data:`` URI of its sanitised diagram.
    """
    diagram_uris = diagram_uris or {}
    total = len(spec.slides)
    views = [
        SlideView(
            n=i + 1,
            total=total,
            slide=slide,
            brand=spec.brand,
            surface=surface_for(slide),
            diagram_uri=diagram_uris.get(i),
        )
        for i, slide in enumerate(spec.slides)
    ]
    for v in views:
        if v.slide.type == "diagram" and not (v.diagram_uri or "").startswith("data:image/"):
            raise ValueError(f"slide {v.n}: diagram has not been rendered to a data: image")
    return (
        _env()
        .get_template("deck.html.j2")
        .render(
            title=spec.title,
            lang=spec.lang,
            csp=STATIC_CSP,
            font_css=Markup(font_face_css()),  # noqa: S704 - verified fonts, no markup characters
            css_vars=_css_vars(spec.brand.colors),
            views=views,
        )
    )


def og_html(spec: OgSpec) -> str:
    return (
        _env()
        .get_template("og.html.j2")
        .render(
            spec=spec,
            brand=spec.brand,
            lang=spec.lang,
            csp=STATIC_CSP,
            font_css=Markup(font_face_css()),  # noqa: S704 - verified fonts, no markup characters
            css_vars=_css_vars(spec.brand.colors),
        )
    )


def image_page_html(image_uri: str, width: int, height: int, background: str) -> str:
    """A bare page that shows one ``data:`` image at a fixed size (used to rasterise SVG to PNG)."""
    if not image_uri.startswith("data:image/"):
        raise ValueError("image_page_html only accepts data: images")
    if not _HEX.fullmatch(background):
        raise ValueError("background must be #RRGGBB")
    return (
        _env()
        .get_template("image.html.j2")
        .render(csp=STATIC_CSP, image_uri=image_uri, width=width, height=height, background=background)
    )
