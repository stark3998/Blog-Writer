"""The render brand: colours, typeface choice, name mark, headshot and footer text.

:class:`RenderBrand` is what every render call takes. The integrator maps the knowledge-base
brand kit onto it; nothing here reads settings or storage.

Colours are injected into the template CSS, so they are validated as strict ``#RRGGBB`` values (a
free string would be a CSS-injection vector: ``red;background:url(https://...)``). Every
foreground/background pair the templates actually use is checked against WCAG 2.x AA for normal
text (4.5:1), even though all slide text is >= 28px and would qualify as "large" (3:1). A brand
that fails is rejected, not rendered.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from blogwriter.render.text import PlainText

__all__ = [
    "AA_NORMAL_TEXT",
    "MAX_HEADSHOT_BYTES",
    "BrandColors",
    "FontChoice",
    "HexColor",
    "RenderBrand",
    "contrast_ratio",
    "relative_luminance",
    "sniff_image_type",
]

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$", to_upper=True)]

AA_NORMAL_TEXT: Final = 4.5

#: Upper bound for the headshot as supplied (it is re-encoded to a small PNG before use).
MAX_HEADSHOT_BYTES: Final = 5 * 1024 * 1024

FontChoice = Literal["inter"]
"""Typefaces the renderer can embed. Only Inter is bundled (OFL, SHA-256 pinned package data):
Inter Display for headings, Inter for text. A brand kit naming any other font maps to ``inter``
until that font is added to ``render/fonts`` with its licence and manifest entry."""


def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of ``#RRGGBB``."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    la, lb = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def sniff_image_type(data: bytes) -> Literal["png", "jpeg", "webp"] | None:
    """The image type from its magic bytes, or ``None``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class BrandColors(BaseModel):
    """Semantic colour tokens. The defaults are a neutral placeholder palette (WCAG AA)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ink: HexColor = "#0E2A24"
    """Dark surface; primary text on light surfaces."""
    paper: HexColor = "#F3F5F2"
    """Light surface; primary text on dark surfaces."""
    muted: HexColor = "#4A5D56"
    """Secondary text on paper."""
    muted_on_ink: HexColor = "#A8C1B8"
    """Secondary text on ink."""
    signal: HexColor = "#F2A93B"
    """Accent on ink (text and fills); text on it is ink."""
    signal_text: HexColor = "#8A4F00"
    """Accent text on paper."""
    rule: HexColor = "#C5D1CB"
    """Hairlines and tracks on paper (decorative, no contrast duty)."""
    rule_on_ink: HexColor = "#2E4A43"
    """Hairlines and tracks on ink (decorative)."""

    def text_pairs(self) -> dict[str, tuple[str, str]]:
        """Every (foreground, background) pair that carries text in the templates."""
        return {
            "ink on paper": (self.ink, self.paper),
            "muted on paper": (self.muted, self.paper),
            "signal_text on paper": (self.signal_text, self.paper),
            "paper on ink": (self.paper, self.ink),
            "muted_on_ink on ink": (self.muted_on_ink, self.ink),
            "signal on ink": (self.signal, self.ink),
            "ink on signal": (self.ink, self.signal),
        }

    @model_validator(mode="after")
    def _wcag_aa(self) -> BrandColors:
        failures = [
            f"{name} = {contrast_ratio(fg, bg):.2f}:1"
            for name, (fg, bg) in self.text_pairs().items()
            if contrast_ratio(fg, bg) < AA_NORMAL_TEXT
        ]
        if failures:
            raise ValueError("brand colours fail WCAG AA (4.5:1): " + ", ".join(failures))
        return self


class RenderBrand(BaseModel):
    """Everything brand-specific a render needs. Passed to every render call.

    In JSON, ``headshot`` is base64.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64"
    )

    name_mark: Annotated[PlainText, Field(max_length=48)]
    """The author's name as shown on the cover byline, in the slide footer and on OG images. It is
    also the PDF ``/Author``."""
    headline: Annotated[PlainText, Field(max_length=64)] | None = None
    """Shown under the name on the cover, e.g. "Identity and AI security"."""
    disclaimer: Annotated[PlainText, Field(max_length=40)] | None = None
    """Employer-policy footer text, e.g. "Views are my own." Shown on every slide and card."""
    site_name: Annotated[PlainText, Field(max_length=40)] | None = None
    """Shown on OG images only (carousels and cards carry no domain-like tokens)."""
    colors: BrandColors = BrandColors()
    fonts: FontChoice = "inter"
    headshot: Annotated[bytes, Field(max_length=MAX_HEADSHOT_BYTES)] | None = None
    """Optional PNG, JPEG or WebP. Re-encoded (EXIF dropped, square crop, 256 px) before use and
    shown on the cover byline and OG images."""

    @field_validator("headshot")
    @classmethod
    def _check_headshot(cls, value: bytes | None) -> bytes | None:
        if value is None:
            return None
        if sniff_image_type(value) is None:
            raise ValueError("headshot must be a PNG, JPEG or WebP image")
        return value
