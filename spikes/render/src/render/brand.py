"""Brand kit: colours, identity text, and the WCAG AA contrast guarantee.

Colours are injected into the template CSS, so they are validated as strict ``#RRGGBB`` values (a free
string would be a CSS-injection vector: ``red;background:url(https://...)``). Every foreground/background
pair the templates actually use is checked against WCAG 2.x AA for normal text (4.5:1), even though all
slide text is >= 28px and would qualify as "large" (3:1). A kit that fails is rejected, not rendered.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .text import PlainText

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$", to_upper=True)]

AA_NORMAL_TEXT = 4.5


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


class BrandColors(BaseModel):
    """Semantic colour tokens. Placeholder defaults until the owner supplies the real brand kit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ink: HexColor = "#0E2A24"  # dark surface; primary text on light surfaces
    paper: HexColor = "#F3F5F2"  # light surface; primary text on dark surfaces
    muted: HexColor = "#4A5D56"  # secondary text on paper
    muted_on_ink: HexColor = "#A8C1B8"  # secondary text on ink
    signal: HexColor = "#F2A93B"  # accent on ink (text and fills); text on it is ink
    signal_text: HexColor = "#8A4F00"  # accent text on paper
    rule: HexColor = "#C5D1CB"  # hairlines and tracks on paper (decorative, no contrast duty)
    rule_on_ink: HexColor = "#2E4A43"  # hairlines and tracks on ink (decorative)

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


class BrandKit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    author_name: Annotated[PlainText, Field(max_length=48)] = "Avery Quinn"
    author_headline: Annotated[PlainText, Field(max_length=64)] | None = "Identity and AI security"
    #: Employer-policy footer text, e.g. "Views are my own." Shown on every slide when set.
    disclaimer: Annotated[PlainText, Field(max_length=40)] | None = None
    #: Shown on OG cards only (carousels carry no domain-like tokens).
    site_name: Annotated[PlainText, Field(max_length=40)] | None = "example.com"
    colors: BrandColors = BrandColors()
