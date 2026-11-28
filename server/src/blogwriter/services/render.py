"""The ``RenderService`` port over :mod:`blogwriter.render` (contract I-3).

:class:`RenderServiceAdapter` wraps the process's :class:`blogwriter.render.Renderer` (the one the
render lifecycle starts) and speaks domain terms:

- **Brand.** The kb brand kit (:class:`~blogwriter.domain.settings.BrandKit`) becomes a
  :class:`~blogwriter.render.RenderBrand` (:func:`render_brand`): ``authorName`` -> ``name_mark``
  (missing: :class:`~blogwriter.domain.ports.RenderFailed` ``brand_incomplete``),
  ``authorHeadline`` -> ``headline``, ``siteName``, ``colors`` (WCAG AA re-checked by render),
  the employer policy's short disclaimer, and the headshot bytes read from Blob storage and
  checked against the SHA-256 of their media reference. The kit's ``nameMark`` image is not
  used: the renderer sets the name mark as text.
- **Specs.** The opaque carousel spec and slide JSON stored on posts are validated into
  :class:`~blogwriter.render.CarouselSpec` / ``Slide``; an invalid one is ``invalid_spec``.
- **Errors.** Every :class:`~blogwriter.render.RenderError` becomes ``RenderFailed`` with a stable
  code and a short owner-facing detail built from structured fields only (slide numbers), never
  from an exception message (Mermaid's parse errors quote the source).
- **Results.** ``RenderedDeck`` becomes :class:`~blogwriter.domain.ports.RenderedCarousel`; its
  warnings become findings (the editorial lint rules ``block``, diagram notes ``warn``; ``ref``
  ``slide-<n>``). ``render_mermaid_svg`` returns UTF-8 bytes.

Rendering itself (isolation, concurrency 1, time limits) is the render package's business.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Final

from pydantic import TypeAdapter, ValidationError

from blogwriter.domain.common import Finding
from blogwriter.domain.ports import BlobNotFound, BlobStore, RenderedCarousel, RenderFailed
from blogwriter.domain.settings import BrandKit
from blogwriter.render import (
    AssetIntegrityError,
    BrandColors,
    CarouselSpec,
    DiagramLabelError,
    HeadshotError,
    LayoutOverflowError,
    MermaidError,
    OutputLimitError,
    RenderBrand,
    Renderer,
    RenderError,
    RenderStatus,
    RenderTimeout,
    RenderUnavailable,
    RenderWarning,
    Slide,
    UnsafeSvgError,
    lint_carousel,
)
from blogwriter.render.brand import MAX_HEADSHOT_BYTES
from blogwriter.services._logging import logger

__all__ = [
    "BlobsResolver",
    "RenderServiceAdapter",
    "carousel_spec",
    "render_brand",
    "render_failed",
    "slide_spec",
]

type BlobsResolver = Callable[[], BlobStore | None]
"""Returns the current blob store (``app.state.blobs`` at call time), or ``None``."""

_SLIDE: Final = TypeAdapter[Slide](Slide)
_OVERFLOW_SLIDE: Final = re.compile(r"^slide (\d+)\b")
_DIAGRAM_NAME_MARK: Final = "Diagram"
"""A placeholder name mark for diagram-only renders: Mermaid uses the brand's colours only."""


# --------------------------------------------------------------------------------------------
# Specs and brand
# --------------------------------------------------------------------------------------------


def carousel_spec(spec: Mapping[str, object]) -> CarouselSpec:
    """The stored spec JSON as a :class:`CarouselSpec`; ``RenderFailed("invalid_spec")``."""
    try:
        return CarouselSpec.model_validate(dict(spec))
    except (ValidationError, TypeError, ValueError):
        raise RenderFailed(
            "invalid_spec", "The carousel does not match the slide format."
        ) from None


def slide_spec(slide: Mapping[str, object]) -> Slide:
    """One slide's JSON as a render ``Slide``; ``RenderFailed("invalid_spec")``."""
    try:
        return _SLIDE.validate_python(dict(slide))
    except (ValidationError, TypeError, ValueError):
        raise RenderFailed("invalid_spec", "The slide does not match the slide format.") from None


def _colors(kit: BrandKit) -> BrandColors:
    palette = kit.colors
    return BrandColors(
        ink=palette.ink,
        paper=palette.paper,
        muted=palette.muted,
        muted_on_ink=palette.muted_on_ink,
        signal=palette.signal,
        signal_text=palette.signal_text,
        rule=palette.rule,
        rule_on_ink=palette.rule_on_ink,
    )


def _require_author(kit: BrandKit) -> str:
    if not kit.author_name:
        raise RenderFailed(
            "brand_incomplete", "Add your author name in Library > Brand before rendering."
        )
    return kit.author_name


def render_brand(
    kit: BrandKit,
    *,
    disclaimer: str | None = None,
    headshot: bytes | None = None,
    name_mark: str | None = None,
) -> RenderBrand:
    """The :class:`RenderBrand` of a brand kit.

    ``name_mark`` overrides the author name (diagram renders, which use only the colours).
    ``RenderFailed("brand_incomplete")`` without an author name, and when render refuses the
    kit (colours below WCAG AA, text with control characters, an unusable headshot).
    """
    mark = name_mark if name_mark is not None else _require_author(kit)
    try:
        return RenderBrand(
            name_mark=mark,
            headline=kit.author_headline,
            disclaimer=disclaimer,
            site_name=kit.site_name,
            colors=_colors(kit),
            fonts="inter",
            headshot=headshot,
        )
    except (ValidationError, ValueError):
        raise RenderFailed(
            "brand_incomplete",
            "The brand kit cannot be used for rendering. Check its colours, text and headshot.",
        ) from None


# --------------------------------------------------------------------------------------------
# Errors and findings
# --------------------------------------------------------------------------------------------


def _overflow_detail(exc: LayoutOverflowError) -> str:
    slides = sorted(
        {int(m.group(1)) for item in exc.overflows if (m := _OVERFLOW_SLIDE.match(item))}
    )
    where = f"Slide {', '.join(str(n) for n in slides)}: " if slides else ""
    return f"{where}some text does not fit its box. Shorten it."


def render_failed(exc: Exception) -> RenderFailed:
    """The :class:`RenderFailed` for an exception a render call raised (never its message)."""
    match exc:
        case RenderFailed():
            return exc
        case LayoutOverflowError():
            return RenderFailed("layout_overflow", _overflow_detail(exc))
        case DiagramLabelError():
            return RenderFailed(
                "diagram_labels",
                f"Slide {exc.slide}: the diagram labels would be too small to read. "
                "Simplify or split the diagram.",
            )
        case MermaidError():
            return RenderFailed(
                "mermaid_error", "A diagram could not be drawn. Check its Mermaid syntax."
            )
        case UnsafeSvgError():
            return RenderFailed("unsafe_svg", "A diagram produced output that is not safe to use.")
        case OutputLimitError():
            return RenderFailed("output_limit", "The render is larger than the limits allow.")
        case HeadshotError():
            return RenderFailed(
                "brand_incomplete", "The headshot cannot be used. Upload a PNG, JPEG or WebP."
            )
        case AssetIntegrityError():
            return RenderFailed("asset_integrity", "A bundled render asset failed its check.")
        case RenderTimeout():
            return RenderFailed("timeout", "The render took too long. Try again.")
        case RenderUnavailable():
            return RenderFailed("unavailable", "Rendering is not available on this server.")
        case RenderError():
            return RenderFailed("unavailable", "The render failed.")
        case _:
            # ValidationError and ValueError: text, sizes or a language the renderer refuses.
            return RenderFailed("invalid_spec", "The render input is not valid.")


@contextmanager
def _mapped() -> Generator[None]:
    """Turn render errors (and input errors) raised inside the block into ``RenderFailed``."""
    try:
        yield
    except (RenderError, ValueError) as exc:
        failed = render_failed(exc)
        logger.warning("render_adapter_failed", extra={"render_failure": failed.code})
        raise failed from None
    except RenderFailed as exc:
        logger.warning("render_adapter_failed", extra={"render_failure": exc.code})
        raise


def _finding(warning: RenderWarning, *, blocking: bool) -> Finding:
    code = warning.code if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", warning.code) else "render"
    message = (warning.message or "Render note.")[:500]
    return Finding(
        code=code,
        severity="block" if blocking else "warn",
        message=message,
        ref=f"slide-{warning.slide}" if warning.slide is not None else None,
    )


def _findings(
    warnings: Sequence[RenderWarning], blocking: frozenset[tuple[str, int | None]]
) -> tuple[Finding, ...]:
    return tuple(
        _finding(warning, blocking=(warning.code, warning.slide) in blocking)
        for warning in warnings
    )


# --------------------------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------------------------


class RenderServiceAdapter:
    """:class:`~blogwriter.domain.ports.RenderService` over a started :class:`Renderer`.

    ``blobs`` returns the blob store the headshot is read from (``app.state.blobs`` at call
    time, so the lifecycle order between storage and this adapter does not matter).
    """

    def __init__(self, renderer: Renderer, *, blobs: BlobsResolver | None = None) -> None:
        self._renderer = renderer
        self._blobs: BlobsResolver = blobs if blobs is not None else _no_blobs

    @property
    def renderer(self) -> Renderer:
        return self._renderer

    @property
    def status(self) -> RenderStatus | None:
        """The renderer's availability (``None`` until it started)."""
        return self._renderer.status

    async def _headshot(self, kit: BrandKit) -> bytes | None:
        ref = kit.headshot
        if ref is None:
            return None
        blobs = self._blobs()
        if blobs is None:
            raise RenderFailed(
                "unavailable", "Storage is not configured, so the headshot cannot be loaded."
            )
        try:
            data = await blobs.get(ref.container, ref.name)
        except BlobNotFound:
            raise RenderFailed(
                "brand_incomplete", "The headshot image is missing. Upload it again."
            ) from None
        if len(data) > MAX_HEADSHOT_BYTES:
            raise RenderFailed("brand_incomplete", "The headshot is larger than 5 MB.")
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        if digest != ref.sha256:
            raise RenderFailed(
                "asset_integrity", "The headshot does not match its checksum. Upload it again."
            )
        return data

    async def _brand(self, kit: BrandKit, disclaimer: str | None) -> RenderBrand:
        _require_author(kit)  # refuse before reading Blob storage
        return render_brand(kit, disclaimer=disclaimer, headshot=await self._headshot(kit))

    @staticmethod
    def _diagram_brand(kit: BrandKit | None) -> RenderBrand | None:
        if kit is None:
            return None
        return render_brand(kit, name_mark=kit.author_name or _DIAGRAM_NAME_MARK)

    async def render_carousel(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> RenderedCarousel:
        deck_spec = carousel_spec(spec)
        with _mapped():
            render = await self._brand(brand, disclaimer)
            deck = await self._renderer.render_carousel(deck_spec, render)
        blocking = frozenset((w.code, w.slide) for w in lint_carousel(deck_spec))
        return RenderedCarousel(
            pdf=deck.pdf,
            pages=deck.pages,
            text=deck.text,
            sha256=deck.sha256,
            warnings=_findings(deck.warnings, blocking),
        )

    async def carousel_preview_html(
        self, spec: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> str:
        deck_spec = carousel_spec(spec)
        with _mapped():
            render = await self._brand(brand, disclaimer)
            return await self._renderer.carousel_preview_html(deck_spec, render)

    async def render_image_card(
        self, slide: Mapping[str, object], brand: BrandKit, *, disclaimer: str | None = None
    ) -> bytes:
        card = slide_spec(slide)
        with _mapped():
            render = await self._brand(brand, disclaimer)
            return await self._renderer.render_image_card(card, render)

    async def render_og_image(
        self,
        *,
        title: str,
        subtitle: str | None,
        pillar_label: str | None,
        brand: BrandKit,
    ) -> bytes:
        with _mapped():
            render = await self._brand(brand, None)
            return await self._renderer.render_og_image(title, subtitle, pillar_label, render)

    async def render_mermaid_svg(self, source: str, brand: BrandKit | None = None) -> bytes:
        with _mapped():
            svg = await self._renderer.render_mermaid_svg(source, brand=self._diagram_brand(brand))
        return svg.encode("utf-8")

    async def render_mermaid_png(
        self, source: str, width: int, brand: BrandKit | None = None
    ) -> bytes:
        with _mapped():
            return await self._renderer.render_mermaid_png(
                source, width, brand=self._diagram_brand(brand)
            )

    def lint_carousel(self, spec: Mapping[str, object]) -> list[Finding]:
        """The editorial rules the deck breaks, as blocking findings (empty when ready)."""
        deck_spec = carousel_spec(spec)
        return [_finding(warning, blocking=True) for warning in lint_carousel(deck_spec)]


def _no_blobs() -> BlobStore | None:
    return None
