"""The render service: the public async API, one isolated job per call.

Every call that needs Chromium is one *job*: it waits for a slot of the process-wide limiter
(``BW_RENDER_CONCURRENCY``, default 1), starts its own Playwright driver and Chromium (minimal
environment, throwaway ``HOME``), renders, tears everything down and logs one ``render_job`` line
(ids, durations and memory figures only). The job deadline (``BW_RENDER_JOB_TIMEOUT_S``) covers
driver start, browser launch and every page, and is lifted before teardown, so teardown always
runs and the slot is always released.

:class:`Renderer` holds the verified assets and the availability decided at :meth:`Renderer.start`
(the render lifecycle calls it at app start-up). The module-level functions in
:mod:`blogwriter.render` use the process-wide default renderer.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from playwright.async_api import Page, Playwright

from blogwriter.core.logging import describe_exception
from blogwriter.render._logging import logger
from blogwriter.render.brand import BrandColors, RenderBrand
from blogwriter.render.browser import (
    RenderLimiter,
    RenderSession,
    default_limiter,
    find_overflows,
    install_driver_env_guard,
    sweep_stale_homes,
    wait_for_fonts,
)
from blogwriter.render.chromium import locate_headless_shell
from blogwriter.render.errors import (
    DiagramLabelError,
    LayoutOverflowError,
    MermaidError,
    RenderError,
    RenderTimeout,
    RenderUnavailable,
)
from blogwriter.render.fonts import verify_fonts
from blogwriter.render.headshot import headshot_data_uri
from blogwriter.render.html import (
    OG_H,
    OG_W,
    SLIDE_H,
    SLIDE_W,
    deck_html,
    image_card_html,
    og_html,
    warm_templates,
)
from blogwriter.render.lint import RenderWarning, lint_carousel
from blogwriter.render.mermaid import (
    ARTICLE_FONT_SIZE,
    MAX_RASTER_SIDE,
    SLIDE_FONT_SIZE,
    MermaidBundle,
    MermaidRenderer,
    MermaidResult,
    load_bundle,
    measure_label_px,
    rasterise_svg,
    unsupported_reason,
)
from blogwriter.render.pdf import finish_pdf
from blogwriter.render.settings import MERMAID_TINY_SHA256, RenderSettings
from blogwriter.render.spec import (
    MAX_MERMAID_SOURCE,
    CarouselSpec,
    DiagramSlide,
    OgImageSpec,
    Slide,
)
from blogwriter.render.telemetry import MemorySampler, StageTimer

__all__ = [
    "LABEL_BLOCK_PX",
    "LABEL_WARN_PX",
    "PDF_HEIGHT",
    "PDF_SCALE",
    "PDF_WIDTH",
    "MermaidDiagram",
    "PreviewScale",
    "RenderKind",
    "RenderStatus",
    "RenderedDeck",
    "Renderer",
    "carousel_preview_html",
    "get_default_renderer",
    "render_carousel",
    "render_image_card",
    "render_mermaid",
    "render_mermaid_png",
    "render_mermaid_svg",
    "render_og_image",
    "set_default_renderer",
    "uninstall_default_renderer",
]

#: Chromium sizes PDF pages in inches (96 CSS px = 72 pt). Printing the 1080px layout on a
#: 15in x 18.75in sheet at scale 4/3 yields pages of exactly 1080 x 1350 pt, with no extra pages.
PDF_WIDTH: Final = "15in"
PDF_HEIGHT: Final = "18.75in"
PDF_SCALE: Final = 4 / 3

#: Effective diagram label size on a slide (labels are set at 26 px, then the diagram is scaled to
#: fit its frame): a warning below 22 px, a :class:`DiagramLabelError` below 18 px.
LABEL_WARN_PX: Final = 22.0
LABEL_BLOCK_PX: Final = 18.0

#: The slide page renders at 2x (the PDF and 2x previews come from the same layout).
_SLIDE_PAGE_SCALE: Final = 2
_LANG: Final = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")

type PreviewScale = Literal[1, 2]
type RenderKind = Literal["carousel", "preview", "image_card", "og_image", "mermaid"]

_DIAGRAM_BOXES_JS: Final = """() => [...document.querySelectorAll('img[data-diagram]')].map(
  (img) => ({slide: Number(img.closest('[data-slide]').dataset.slide),
             w: img.clientWidth, h: img.clientHeight}))"""
_SCROLL_JS: Final = "(y) => window.scrollTo(0, y)"


@dataclass(frozen=True, slots=True)
class RenderedDeck:
    """A rendered carousel."""

    pdf: bytes
    """The LinkedIn document: one 1080x1350 pt page per slide, metadata exactly Title/Author."""
    pages: tuple[bytes, ...]
    """PNG preview of every page (1080x1350 at ``preview_scale`` 1, 2160x2700 at 2)."""
    text: tuple[str, ...]
    """The PDF text layer per page (what the confidentiality gate scans)."""
    sha256: str
    """Hex SHA-256 of ``pdf`` (bind approvals to it; re-renders are byte-identical)."""
    warnings: tuple[RenderWarning, ...]
    """Lint findings, diagram source changes and small diagram labels. Rendering went ahead."""


@dataclass(frozen=True, slots=True)
class MermaidDiagram:
    """A standalone diagram (articles, the editor's live preview)."""

    svg: str
    """Sanitised SVG with the text font embedded and an explicit width and height."""
    width: int
    height: int
    warnings: tuple[str, ...]
    """What neutralisation removed from the source."""
    png: bytes | None = None


@dataclass(frozen=True, slots=True)
class RenderStatus:
    available: bool
    reason: str | None
    """Why rendering is unavailable (for people), or ``None``."""
    reason_code: str | None
    """``chromium_missing`` or ``driver_env_unconfined``, or ``None``."""
    chromium: Path | None
    browsers_path: Path | None
    mermaid_sha256: str


@dataclass(slots=True)
class _Job:
    id: str
    kind: RenderKind
    session: RenderSession
    slides: int | None = None
    output_bytes: int | None = None
    warnings: int = 0


@dataclass(frozen=True, slots=True)
class _Diagrams:
    results: dict[int, MermaidResult]
    """Rendered diagrams by 0-based slide index."""
    label_px: dict[int, float]
    """Smallest rendered text size per diagram at intrinsic size (measured; may be partial)."""
    warnings: list[RenderWarning]

    def uris(self) -> dict[int, str]:
        return {i: r.data_uri for i, r in self.results.items()}


def _check_lang(lang: str) -> str:
    if not _LANG.fullmatch(lang):
        raise ValueError("lang must look like 'en' or 'en-GB'")
    return lang


def _diagram_slides(slides: Sequence[Slide]) -> list[tuple[int, DiagramSlide]]:
    """The diagram slides with their 0-based index; refuses (before any browser work) a diagram
    type the shipped Mermaid build cannot draw."""
    found = [(i, s) for i, s in enumerate(slides) if isinstance(s, DiagramSlide)]
    for i, slide in found:
        reason = unsupported_reason(slide.mermaid)
        if reason is not None:
            raise MermaidError(f"slide {i + 1}: {reason}")
    return found


def _warm_static_assets() -> None:
    verify_fonts()
    warm_templates()


class Renderer:
    """The render service for one process. Create one, :meth:`start` it, share it.

    ``limiter`` overrides the process-wide job limiter (tests). ``playwright`` reuses an already
    started driver for every job instead of one per job (tests and benchmarks only; the
    Chromium lookup is then left to that driver).
    """

    def __init__(
        self,
        settings: RenderSettings | None = None,
        *,
        limiter: RenderLimiter | None = None,
        playwright: Playwright | None = None,
    ) -> None:
        self.settings = settings if settings is not None else RenderSettings()
        self._limiter = limiter
        self._playwright = playwright
        self._status: RenderStatus | None = None
        self._bundle: MermaidBundle | None = None

    @property
    def status(self) -> RenderStatus | None:
        """``None`` until :meth:`start` has run."""
        return self._status

    async def start(self) -> RenderStatus:
        """Verify the fonts, templates and the pinned Mermaid bundle (raises
        :class:`~blogwriter.render.errors.AssetIntegrityError`), sweep per-job homes left by a
        crash, and decide availability: without Chromium headless shell, rendering is off (a
        warning is logged and render calls raise :class:`RenderUnavailable`)."""
        await asyncio.to_thread(_warm_static_assets)
        bundle = await asyncio.to_thread(load_bundle, self.settings.mermaid_js, MERMAID_TINY_SHA256)
        removed = await asyncio.to_thread(sweep_stale_homes, self.settings.stale_home_max_age_s)
        if self._playwright is not None:
            status = RenderStatus(True, None, None, None, None, bundle.sha256)
        else:
            location = await asyncio.to_thread(locate_headless_shell)
            if location.executable is None:
                where = location.install_dir or location.browsers_path
                status = RenderStatus(
                    available=False,
                    reason=(
                        f"Chromium headless shell is not installed (looked in {where}); run "
                        "`playwright install --only-shell chromium` or set "
                        "PLAYWRIGHT_BROWSERS_PATH"
                    ),
                    reason_code="chromium_missing",
                    chromium=None,
                    browsers_path=location.browsers_path,
                    mermaid_sha256=bundle.sha256,
                )
            elif not install_driver_env_guard():  # pragma: no cover - Playwright internals
                status = RenderStatus(
                    available=False,
                    reason="the Playwright driver's environment cannot be confined",
                    reason_code="driver_env_unconfined",
                    chromium=location.executable,
                    browsers_path=location.browsers_path,
                    mermaid_sha256=bundle.sha256,
                )
            else:
                status = RenderStatus(
                    True, None, None, location.executable, location.browsers_path, bundle.sha256
                )
        self._bundle = bundle
        self._status = status
        if status.available:
            logger.info(
                "render_ready",
                extra={
                    "concurrency": self.settings.concurrency,
                    "sandbox": self.settings.chromium_sandbox,
                    "removed_homes": removed,
                },
            )
        else:
            logger.warning(
                "render_unavailable",
                extra={"reason": status.reason_code, "removed_homes": removed},
            )
        return status

    async def _ensure_started(self) -> RenderStatus:
        return self._status if self._status is not None else await self.start()

    async def _ready(self) -> RenderStatus:
        status = await self._ensure_started()
        if not status.available:
            raise RenderUnavailable(status.reason or "rendering is unavailable")
        return status

    def _require_bundle(self) -> MermaidBundle:
        if self._bundle is None:  # pragma: no cover - start() sets it before any job
            raise RenderError("the render service has not started")
        return self._bundle

    async def _headshot(self, brand: RenderBrand) -> str | None:
        if brand.headshot is None:
            return None
        return await asyncio.to_thread(headshot_data_uri, brand.headshot)

    @contextlib.asynccontextmanager
    async def _job(self, kind: RenderKind, *, slides: int | None = None) -> AsyncGenerator[_Job]:
        status = await self._ready()
        limiter = self._limiter if self._limiter is not None else default_limiter(self.settings)
        timer = StageTimer()
        session = RenderSession(
            self.settings,
            playwright=self._playwright,
            browsers_path=status.browsers_path,
            timer=timer,
        )
        job = _Job(id=uuid.uuid4().hex, kind=kind, session=session, slides=slides)
        async with limiter.slot():
            sampler = MemorySampler(self.settings.memory_sample_interval_s)
            sampler.start()
            started = time.perf_counter()
            outcome = "error"
            failure: BaseException | None = None
            deadline = asyncio.timeout(self.settings.job_timeout_s)
            try:
                try:
                    async with deadline:
                        await session.open()
                        try:
                            yield job
                        finally:
                            # Lift the deadline before teardown, on success and on error alike.
                            if not deadline.expired():
                                deadline.reschedule(None)
                except TimeoutError as exc:
                    if not deadline.expired():
                        raise
                    raise RenderTimeout(
                        f"render job exceeded {self.settings.job_timeout_s:g}s"
                    ) from exc
                outcome = "ok"
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except BaseException as exc:
                failure = exc
                outcome = "timeout" if isinstance(exc, RenderTimeout) else "error"
                raise
            finally:
                with timer.stage("teardown"):
                    await session.close()
                peaks = await sampler.stop()
                fields: dict[str, object] = {
                    "render_job": job.id,
                    "render_kind": kind,
                    "outcome": outcome,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "slides": job.slides,
                    "output_bytes": job.output_bytes,
                    "warnings": job.warnings,
                    "blocked_requests": len(session.guard.blocked),
                    "stages": timer.as_log_list(),
                    **peaks.log_fields(),
                }
                if failure is not None:
                    fields.update(describe_exception(failure))
                logger.info("render_job", extra=fields)

    async def _render_diagrams(
        self,
        session: RenderSession,
        slides: Sequence[tuple[int, DiagramSlide]],
        colors: BrandColors,
        *,
        measure: bool,
    ) -> _Diagrams:
        results: dict[int, MermaidResult] = {}
        warnings: list[RenderWarning] = []
        if not slides:
            return _Diagrams(results, {}, warnings)
        renderer = MermaidRenderer(session, self._require_bundle())
        with session.timer.stage("mermaid_context"):
            await renderer.open()
        try:
            with session.timer.stage("mermaid_render"):
                for index, slide in slides:
                    try:
                        result = await renderer.render(
                            slide.mermaid, colors, font_size=SLIDE_FONT_SIZE
                        )
                    except MermaidError as exc:
                        raise MermaidError(f"slide {index + 1}: {exc}") from exc
                    results[index] = result
                    warnings.extend(
                        RenderWarning("diagram_source", index + 1, w) for w in result.warnings
                    )
        finally:
            await renderer.close()
        if renderer.dialogs or renderer.page_errors:
            # Something ran in the Mermaid page beyond Mermaid itself. Output is still sanitised.
            logger.error(
                "render_isolation_event",
                extra={"count": len(renderer.dialogs) + len(renderer.page_errors)},
            )
        label_px: dict[int, float] = {}
        if measure:
            with session.timer.stage("label_measure"):
                label_px = await measure_label_px(session, results)
        return _Diagrams(results, label_px, warnings)

    async def _load_static_page(self, page: Page, html: str, timer: StageTimer) -> None:
        with timer.stage("set_content"):
            await page.set_content(html, wait_until="load")
            await wait_for_fonts(page)
        with timer.stage("fit_check"):
            overflows = await find_overflows(page)
        if overflows:
            raise LayoutOverflowError(overflows)

    async def _check_labels(self, page: Page, diagrams: _Diagrams) -> list[RenderWarning]:
        """Effective label size of every diagram as laid out (its smallest rendered text size
        times the scale that fits it into its frame): warn below 22 px, raise below 18."""
        if not diagrams.results:
            return []
        raw: object = await page.evaluate(_DIAGRAM_BOXES_JS)
        warnings: list[RenderWarning] = []
        for box in cast(list[dict[str, float]], raw):
            slide_no = int(box["slide"])
            index = slide_no - 1
            result = diagrams.results.get(index)
            if result is None:  # pragma: no cover - every diagram slide has a result
                raise RenderError(f"slide {slide_no}: diagram was not rendered")
            scale = min(box["w"] / result.width, box["h"] / result.height)
            effective = diagrams.label_px.get(index, float(result.font_size)) * scale
            if effective < LABEL_BLOCK_PX:
                raise DiagramLabelError(slide_no, effective)
            if effective < LABEL_WARN_PX:
                warnings.append(
                    RenderWarning(
                        "diagram_label_size",
                        slide_no,
                        f"diagram labels render at {effective:.1f}px (below {LABEL_WARN_PX:g}px "
                        "is hard to read on a phone); simplify or split the diagram",
                    )
                )
        return warnings

    async def render_carousel(
        self, spec: CarouselSpec, brand: RenderBrand, *, preview_scale: PreviewScale = 1
    ) -> RenderedDeck:
        """The carousel PDF, a PNG preview per page, the text layer, its SHA-256 and warnings.

        Raises :class:`LayoutOverflowError` instead of clipping, :class:`DiagramLabelError` for
        illegible diagrams, :class:`~blogwriter.render.errors.MermaidError` for invalid ones.
        """
        diagram_slides = _diagram_slides(spec.slides)
        headshot = await self._headshot(brand)
        warnings = lint_carousel(spec)
        n = len(spec.slides)
        async with self._job("carousel", slides=n) as job:
            session = job.session
            diagrams = await self._render_diagrams(
                session, diagram_slides, brand.colors, measure=True
            )
            warnings.extend(diagrams.warnings)
            with session.timer.stage("html"):
                html = deck_html(spec, brand, diagram_uris=diagrams.uris(), headshot_uri=headshot)
            async with session.static_page(
                width=SLIDE_W, height=SLIDE_H, scale=_SLIDE_PAGE_SCALE
            ) as page:
                await self._load_static_page(page, html, session.timer)
                warnings.extend(await self._check_labels(page, diagrams))
                with session.timer.stage("previews"):
                    pages = await _page_previews(page, n, preview_scale)
                with session.timer.stage("pdf"):
                    raw_pdf = await page.pdf(
                        width=PDF_WIDTH,
                        height=PDF_HEIGHT,
                        scale=PDF_SCALE,
                        print_background=True,
                        prefer_css_page_size=False,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    )
            with session.timer.stage("pdf_finalize"):
                pdf, text = await asyncio.to_thread(
                    finish_pdf,
                    raw_pdf,
                    title=spec.title,
                    author=brand.name_mark,
                    expected_pages=n,
                    max_bytes=self.settings.max_pdf_bytes,
                )
            job.output_bytes = len(pdf)
            job.warnings = len(warnings)
            return RenderedDeck(
                pdf=pdf,
                pages=pages,
                text=text,
                sha256=hashlib.sha256(pdf).hexdigest(),
                warnings=tuple(warnings),
            )

    async def carousel_preview_html(self, spec: CarouselSpec, brand: RenderBrand) -> str:
        """The self-contained HTML :meth:`render_carousel` prints, for a sandboxed iframe.

        It carries its own CSP (no script, ``data:`` images and fonts only). Chromium is needed
        only to draw diagram slides; a deck without diagrams renders even when Chromium is absent.
        """
        diagram_slides = _diagram_slides(spec.slides)
        await self._ensure_started()
        headshot = await self._headshot(brand)
        uris: dict[int, str] = {}
        if diagram_slides:
            async with self._job("preview", slides=len(spec.slides)) as job:
                diagrams = await self._render_diagrams(
                    job.session, diagram_slides, brand.colors, measure=False
                )
                uris = diagrams.uris()
        return deck_html(spec, brand, diagram_uris=uris, headshot_uri=headshot)

    async def render_image_card(
        self, slide: Slide, brand: RenderBrand, *, lang: str = "en"
    ) -> bytes:
        """One slide from the template kit as a 1080x1350 PNG (single-image post)."""
        _check_lang(lang)
        diagram_slides = _diagram_slides([slide])
        headshot = await self._headshot(brand)
        async with self._job("image_card", slides=1) as job:
            session = job.session
            diagrams = await self._render_diagrams(
                session, diagram_slides, brand.colors, measure=True
            )
            html = image_card_html(
                slide,
                brand,
                lang=lang,
                diagram_uri=diagrams.uris().get(0),
                headshot_uri=headshot,
            )
            async with session.static_page(width=SLIDE_W, height=SLIDE_H) as page:
                await self._load_static_page(page, html, session.timer)
                await self._check_labels(page, diagrams)
                with session.timer.stage("png"):
                    png = await page.screenshot(
                        type="png",
                        clip={"x": 0, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                        animations="disabled",
                        caret="hide",
                    )
            job.output_bytes = len(png)
            return png

    async def render_og_image(
        self,
        title: str,
        subtitle: str | None,
        pillar_label: str | None,
        brand: RenderBrand,
        *,
        lang: str = "en",
    ) -> bytes:
        """A 1200x630 Open Graph PNG. Invalid text raises ``pydantic.ValidationError``."""
        spec = OgImageSpec(title=title, subtitle=subtitle, pillar_label=pillar_label, lang=lang)
        headshot = await self._headshot(brand)
        async with self._job("og_image") as job:
            session = job.session
            html = og_html(spec, brand, headshot_uri=headshot)
            async with session.static_page(width=OG_W, height=OG_H) as page:
                await self._load_static_page(page, html, session.timer)
                with session.timer.stage("png"):
                    png = await page.screenshot(type="png", animations="disabled", caret="hide")
            job.output_bytes = len(png)
            return png

    async def render_mermaid(
        self,
        source: str,
        *,
        brand: RenderBrand | None = None,
        font_size: int = ARTICLE_FONT_SIZE,
        png_width: int | None = None,
    ) -> MermaidDiagram:
        """One diagram: sanitised SVG (font embedded) and, with ``png_width``, a PNG that wide.

        The PNG must stay within 12 MP and 16,384 px per side
        (:class:`~blogwriter.render.errors.OutputLimitError` otherwise, before rasterising).
        """
        if not source.strip() or len(source) > MAX_MERMAID_SOURCE or "\x00" in source:
            raise MermaidError(
                f"diagram source must be 1-{MAX_MERMAID_SOURCE} characters without NUL"
            )
        if not 12 <= font_size <= 40:
            raise ValueError("font_size must be between 12 and 40 px")
        if png_width is not None and not 1 <= png_width <= MAX_RASTER_SIDE:
            raise ValueError(f"png width must be between 1 and {MAX_RASTER_SIDE} px")
        reason = unsupported_reason(source)
        if reason is not None:
            raise MermaidError(reason)
        colors = brand.colors if brand is not None else BrandColors()
        async with self._job("mermaid") as job:
            session = job.session
            async with MermaidRenderer(session, self._require_bundle()) as renderer:
                with session.timer.stage("mermaid_render"):
                    result = await renderer.render(source, colors, font_size=font_size)
            png: bytes | None = None
            if png_width is not None:
                with session.timer.stage("png"):
                    png = await rasterise_svg(
                        session,
                        result.svg,
                        result.width,
                        result.height,
                        colors.paper,
                        out_width=png_width,
                    )
            job.output_bytes = len(result.svg) + (len(png) if png is not None else 0)
            job.warnings = len(result.warnings)
            return MermaidDiagram(
                svg=result.svg.decode("utf-8"),
                width=result.width,
                height=result.height,
                warnings=result.warnings,
                png=png,
            )

    async def render_mermaid_svg(
        self, source: str, *, brand: RenderBrand | None = None, font_size: int = ARTICLE_FONT_SIZE
    ) -> str:
        """The sanitised SVG of one diagram (see :meth:`render_mermaid`)."""
        return (await self.render_mermaid(source, brand=brand, font_size=font_size)).svg

    async def render_mermaid_png(
        self,
        source: str,
        width: int,
        *,
        brand: RenderBrand | None = None,
        font_size: int = ARTICLE_FONT_SIZE,
    ) -> bytes:
        """A PNG of one diagram, ``width`` pixels wide (see :meth:`render_mermaid`)."""
        diagram = await self.render_mermaid(
            source, brand=brand, font_size=font_size, png_width=width
        )
        if diagram.png is None:  # pragma: no cover - png_width was given
            raise RenderError("diagram was not rasterised")
        return diagram.png


async def _page_previews(page: Page, count: int, scale: PreviewScale) -> tuple[bytes, ...]:
    """One PNG per slide: scroll the slide into the viewport and capture exactly the viewport
    (never a capture beyond it, which would make Chromium allocate the whole deck at once)."""
    out: list[bytes] = []
    for i in range(count):
        await page.evaluate(_SCROLL_JS, i * SLIDE_H)
        out.append(
            await page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                animations="disabled",
                caret="hide",
                scale="css" if scale == 1 else "device",
            )
        )
    await page.evaluate(_SCROLL_JS, 0)  # print from the top, exactly as without previews
    return tuple(out)


_default_renderer: Renderer | None = None


def get_default_renderer() -> Renderer:
    """The process-wide renderer: the one the render lifecycle installed, or a lazily created one
    (settings from the environment; it starts itself on first use)."""
    global _default_renderer
    if _default_renderer is None:
        _default_renderer = Renderer()
    return _default_renderer


def set_default_renderer(renderer: Renderer | None) -> None:
    """Install (or clear) the process-wide renderer. Called by the render lifecycle."""
    global _default_renderer
    _default_renderer = renderer


def uninstall_default_renderer(renderer: Renderer) -> None:
    """Clear the process-wide renderer if it is ``renderer`` (never creates one)."""
    global _default_renderer
    if _default_renderer is renderer:
        _default_renderer = None


async def render_carousel(
    spec: CarouselSpec, brand: RenderBrand, *, preview_scale: PreviewScale = 1
) -> RenderedDeck:
    """See :meth:`Renderer.render_carousel`."""
    return await get_default_renderer().render_carousel(spec, brand, preview_scale=preview_scale)


async def carousel_preview_html(spec: CarouselSpec, brand: RenderBrand) -> str:
    """See :meth:`Renderer.carousel_preview_html`."""
    return await get_default_renderer().carousel_preview_html(spec, brand)


async def render_image_card(slide: Slide, brand: RenderBrand, *, lang: str = "en") -> bytes:
    """See :meth:`Renderer.render_image_card`."""
    return await get_default_renderer().render_image_card(slide, brand, lang=lang)


async def render_og_image(
    title: str,
    subtitle: str | None,
    pillar_label: str | None,
    brand: RenderBrand,
    *,
    lang: str = "en",
) -> bytes:
    """See :meth:`Renderer.render_og_image`."""
    return await get_default_renderer().render_og_image(
        title, subtitle, pillar_label, brand, lang=lang
    )


async def render_mermaid(
    source: str,
    *,
    brand: RenderBrand | None = None,
    font_size: int = ARTICLE_FONT_SIZE,
    png_width: int | None = None,
) -> MermaidDiagram:
    """See :meth:`Renderer.render_mermaid`."""
    return await get_default_renderer().render_mermaid(
        source, brand=brand, font_size=font_size, png_width=png_width
    )


async def render_mermaid_svg(
    source: str, *, brand: RenderBrand | None = None, font_size: int = ARTICLE_FONT_SIZE
) -> str:
    """See :meth:`Renderer.render_mermaid_svg`."""
    return await get_default_renderer().render_mermaid_svg(source, brand=brand, font_size=font_size)


async def render_mermaid_png(
    source: str,
    width: int,
    *,
    brand: RenderBrand | None = None,
    font_size: int = ARTICLE_FONT_SIZE,
) -> bytes:
    """See :meth:`Renderer.render_mermaid_png`."""
    return await get_default_renderer().render_mermaid_png(
        source, width, brand=brand, font_size=font_size
    )
