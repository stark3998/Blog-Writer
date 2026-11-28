"""Self-contained HTML for slides, image cards and OG images (Jinja2, autoescape on).

The same document is used for the sandboxed Studio preview and for Chromium's PDF/PNG output, so it
carries its own Content-Security-Policy: nothing but inline styles, ``data:`` images and ``data:``
fonts may load, and no script may run, whatever renders it.

Text is sized from its length here, in Python, because pages render with JavaScript disabled; the
renderer then measures every ``[data-fit]`` box and refuses to ship clipped content.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, Final, Literal, cast

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from blogwriter.render.brand import BrandColors, RenderBrand
from blogwriter.render.fonts import font_face_css
from blogwriter.render.lint import slide_text
from blogwriter.render.spec import CarouselSpec, FrameworkSlide, OgImageSpec, Slide

__all__ = [
    "DENSE_BODY_CHARS",
    "FIT_STEPS",
    "OG_H",
    "OG_W",
    "PREVIEW_CSP",
    "SLIDE_H",
    "SLIDE_W",
    "STATIC_CSP",
    "SlideView",
    "deck_html",
    "fit",
    "image_card_html",
    "image_page_html",
    "is_dense",
    "og_html",
    "surface_for",
    "warm_templates",
]

SLIDE_W: Final = 1080
SLIDE_H: Final = 1350
OG_W: Final = 1200
OG_H: Final = 630

#: For pages that must never run script or fetch anything. ``data:`` only for images and fonts.
STATIC_CSP: Final = (
    "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)
#: The policy a route serving :func:`~blogwriter.render.service.Renderer.carousel_preview_html`
#: should also send as a response header (the document carries it as a ``<meta>`` already).
PREVIEW_CSP: Final = STATIC_CSP

Surface = Literal["ink", "paper"]
_HEX: Final = re.compile(r"#[0-9A-Fa-f]{6}")
_INK_SLIDES: Final = frozenset({"cover", "statement", "stat"})
_TEMPLATES: Final = (
    "deck.html.j2",
    "og.html.j2",
    "image.html.j2",
)

#: Body characters (all text but the title) above which a list, steps, layered framework or
#: sources slide is set one type step smaller (``slide--dense`` in slides.css, never below 28px).
#: At their spec maximums these layouts overflow by up to ~56px with realistic words; below the
#: thresholds they fit at full size. Verified across word rotations by the layout tests.
DENSE_BODY_CHARS: Final[dict[str, int]] = {
    "list": 300,
    "steps": 320,
    "framework": 240,
    "sources": 380,
}

#: (max characters, px) steps per text role, then the floor size. The floor of every role is
#: chosen so the longest value the spec allows still fits (verified by the layout tests).
FIT_STEPS: Final[dict[str, tuple[tuple[tuple[int, int], ...], int]]] = {
    "hook": (((28, 112), (52, 96)), 80),
    "statement": (((60, 80), (110, 66)), 56),
    "title": (((32, 64), (52, 56)), 50),
    "quote": (((80, 60), (140, 52)), 46),
    "stat": (((4, 280), (6, 210), (8, 160)), 128),
    "og_title": (((40, 84), (72, 70)), 58),
    "og_title_sub": (((40, 76), (72, 62)), 52),
}


def fit(text: str, role: str) -> int:
    """Font size in px for ``text`` in ``role`` (longer text steps down)."""
    steps, floor = FIT_STEPS[role]
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
    brand: RenderBrand
    surface: Surface
    diagram_uri: str | None = None
    headshot_uri: str | None = None
    show_number: bool = True

    @property
    def progress_pct(self) -> str:
        return f"{self.n / self.total * 100:.3f}"

    @property
    def dense(self) -> bool:
        return is_dense(self.slide)


@cache
def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("blogwriter.render", "templates"),
        autoescape=True,  # every template, every expression; never select_autoescape by extension
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )
    env.globals.update({"fit": cast(Any, fit)})
    return env


def warm_templates() -> None:
    """Load and compile every template (blocking I/O: run it in a worker thread at start-up)."""
    env = _env()
    for name in env.list_templates():
        env.get_template(name)
    for name in _TEMPLATES:
        env.get_template(name)


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
    if not all(_HEX.fullmatch(v) for v in pairs.values()):  # pragma: no cover - HexColor
        raise ValueError("unsafe CSS variable value")
    body = ";".join(f"--{k}:{v}" for k, v in pairs.items())
    return Markup(":root{" + body + "}")  # noqa: S704 - built only from validated hex colours


def _check_headshot_uri(uri: str | None) -> None:
    if uri is not None and not uri.startswith("data:image/png;base64,"):
        raise ValueError("headshot_uri must be a data:image/png;base64 URI")


def is_dense(slide: Slide) -> bool:
    """Whether ``slide`` is set one type step smaller (see :data:`DENSE_BODY_CHARS`)."""
    limit = DENSE_BODY_CHARS.get(slide.type)
    if limit is None:
        return False
    if isinstance(slide, FrameworkSlide) and slide.layout != "layered":
        return False
    body = slide_text(slide)[1:]  # the title is sized on its own
    return sum(len(t) for t in body) > limit


def surface_for(slide: Slide) -> Surface:
    return "ink" if slide.type in _INK_SLIDES else "paper"


def _check_views(views: list[SlideView]) -> None:
    for v in views:
        if v.slide.type == "diagram" and not (v.diagram_uri or "").startswith(
            "data:image/svg+xml;base64,"
        ):
            raise ValueError(f"slide {v.n}: diagram has not been rendered to a data: image")


def _render_slides(title: str, lang: str, brand: RenderBrand, views: list[SlideView]) -> str:
    _check_views(views)
    return (
        _env()
        .get_template("deck.html.j2")
        .render(
            title=title,
            lang=lang,
            csp=STATIC_CSP,
            font_css=Markup(font_face_css()),  # noqa: S704 - verified fonts, no markup characters
            css_vars=_css_vars(brand.colors),
            views=views,
        )
    )


def deck_html(
    spec: CarouselSpec,
    brand: RenderBrand,
    *,
    diagram_uris: Mapping[int, str] | None = None,
    headshot_uri: str | None = None,
) -> str:
    """The whole deck as one self-contained HTML document (one ``<section>`` per slide).

    ``diagram_uris`` maps a 0-based slide index to the ``data:`` URI of its sanitised diagram;
    ``headshot_uri`` is the prepared headshot (a ``data:image/png`` URI) or ``None``.
    """
    _check_headshot_uri(headshot_uri)
    uris = diagram_uris or {}
    total = len(spec.slides)
    views = [
        SlideView(
            n=i + 1,
            total=total,
            slide=slide,
            brand=brand,
            surface=surface_for(slide),
            diagram_uri=uris.get(i),
            headshot_uri=headshot_uri,
        )
        for i, slide in enumerate(spec.slides)
    ]
    return _render_slides(spec.title, spec.lang, brand, views)


def image_card_html(
    slide: Slide,
    brand: RenderBrand,
    *,
    lang: str = "en",
    diagram_uri: str | None = None,
    headshot_uri: str | None = None,
) -> str:
    """One slide as a single-image card: the same templates, no slide number, a full rail."""
    _check_headshot_uri(headshot_uri)
    view = SlideView(
        n=1,
        total=1,
        slide=slide,
        brand=brand,
        surface=surface_for(slide),
        diagram_uri=diagram_uri,
        headshot_uri=headshot_uri,
        show_number=False,
    )
    return _render_slides(brand.name_mark, lang, brand, [view])


def og_html(spec: OgImageSpec, brand: RenderBrand, *, headshot_uri: str | None = None) -> str:
    _check_headshot_uri(headshot_uri)
    return (
        _env()
        .get_template("og.html.j2")
        .render(
            spec=spec,
            brand=brand,
            lang=spec.lang,
            csp=STATIC_CSP,
            font_css=Markup(font_face_css()),  # noqa: S704 - verified fonts, no markup characters
            css_vars=_css_vars(brand.colors),
            headshot_uri=headshot_uri,
        )
    )


def image_page_html(image_uri: str, width: int, height: int, background: str) -> str:
    """A bare page that shows one ``data:`` image at a fixed size (rasterising SVG to PNG)."""
    if not image_uri.startswith("data:image/"):
        raise ValueError("image_page_html only accepts data: images")
    if not _HEX.fullmatch(background):
        raise ValueError("background must be #RRGGBB")
    return (
        _env()
        .get_template("image.html.j2")
        .render(
            csp=STATIC_CSP, image_uri=image_uri, width=width, height=height, background=background
        )
    )
