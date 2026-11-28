"""The ``RenderService`` adapter over ``blogwriter.render`` (contract I-3).

Plan: "LinkedIn Studio" > Carousels (brand kit: author name required, colours, headshot; the
employer disclaimer on every slide) and "Security model" > Render isolation (render errors never
carry slide text into logs or problems). Most tests replace the Chromium work with a recording
``Renderer`` subclass; the last ones use the real renderer where the bundle is installed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Final, override

import pytest
from pydantic.alias_generators import to_camel

from blogwriter.domain.common import MediaRef
from blogwriter.domain.ports import RenderedCarousel, RenderFailed, RenderService
from blogwriter.domain.settings import BrandColors as KitColors
from blogwriter.domain.settings import BrandKit
from blogwriter.domain.testing import MemoryBlobStore, png_bytes, sample_document
from blogwriter.render import (
    AssetIntegrityError,
    CarouselSpec,
    DiagramLabelError,
    HeadshotError,
    LayoutOverflowError,
    MermaidError,
    OgImageSpec,
    OutputLimitError,
    RenderBrand,
    RenderedDeck,
    Renderer,
    RenderError,
    RenderStatus,
    RenderTimeout,
    RenderUnavailable,
    RenderWarning,
    Slide,
    UnsafeSvgError,
)
from blogwriter.render.service import PreviewScale
from blogwriter.render.testing import mermaid_bundle_installed
from blogwriter.services.render import RenderServiceAdapter, render_brand

SPEC: Final[dict[str, object]] = {
    "title": "Give every AI agent its own identity",
    "slides": [
        {"type": "cover", "hook": "Your MCP server trusts every agent"},
        {"type": "statement", "statement": "A shared key is an anonymous insider."},
        {"type": "recap", "title": "Do this Monday", "items": ["Inventory keys", "Scope tools"]},
    ],
}
SLIDE: Final[dict[str, object]] = {"type": "statement", "statement": "Scope every tool call."}
MERMAID: Final = "flowchart LR\n  A[Agent] --> B[Tool]"


class RecordingRenderer(Renderer):
    """Records the brand and spec of every call; answers with tiny real-looking outputs."""

    def __init__(
        self,
        *,
        fail: Exception | None = None,
        warnings: tuple[RenderWarning, ...] = (),
        status: RenderStatus | None = None,
    ) -> None:
        super().__init__()
        self.fail = fail
        self.warnings = warnings
        self.fake_status = status
        self.brands: list[RenderBrand | None] = []
        self.calls: list[str] = []

    @property
    @override
    def status(self) -> RenderStatus | None:
        return self.fake_status

    def _enter(self, name: str, brand: RenderBrand | None) -> None:
        self.calls.append(name)
        self.brands.append(brand)
        if self.fail is not None:
            raise self.fail

    @override
    async def render_carousel(
        self, spec: CarouselSpec, brand: RenderBrand, *, preview_scale: PreviewScale = 1
    ) -> RenderedDeck:
        self._enter("render_carousel", brand)
        pdf = b"%PDF-1.7 fake"
        return RenderedDeck(
            pdf=pdf,
            pages=tuple(png_bytes(108, 135) for _ in spec.slides),
            text=tuple("" for _ in spec.slides),
            sha256=hashlib.sha256(pdf).hexdigest(),
            warnings=self.warnings,
        )

    @override
    async def carousel_preview_html(self, spec: CarouselSpec, brand: RenderBrand) -> str:
        self._enter("carousel_preview_html", brand)
        return f"<!doctype html><title>{spec.title}</title><p>{brand.name_mark}</p>"

    @override
    async def render_image_card(
        self, slide: Slide, brand: RenderBrand, *, lang: str = "en"
    ) -> bytes:
        self._enter("render_image_card", brand)
        return png_bytes(108, 135)

    @override
    async def render_og_image(
        self,
        title: str,
        subtitle: str | None,
        pillar_label: str | None,
        brand: RenderBrand,
        *,
        lang: str = "en",
    ) -> bytes:
        self._enter("render_og_image", brand)
        OgImageSpec(title=title, subtitle=subtitle, pillar_label=pillar_label, lang=lang)
        return png_bytes(120, 63)

    @override
    async def render_mermaid_svg(
        self, source: str, *, brand: RenderBrand | None = None, font_size: int = 16
    ) -> str:
        self._enter("render_mermaid_svg", brand)
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>é</text></svg>'

    @override
    async def render_mermaid_png(
        self, source: str, width: int, *, brand: RenderBrand | None = None, font_size: int = 16
    ) -> bytes:
        self._enter("render_mermaid_png", brand)
        return png_bytes(width, 4)


def kit(**fields: object) -> BrandKit:
    """A brand kit (python field names; ``sample_document`` merges singletons by alias)."""
    values: dict[str, object] = {
        "author_name": "Sam Rivera",
        "author_headline": "Identity and AI security",
        "site_name": "samrivera.dev",
    }
    values.update(fields)
    return sample_document(BrandKit, **{to_camel(name): value for name, value in values.items()})


async def headshot_kit(blobs: MemoryBlobStore, data: bytes, *, sha: str | None = None) -> BrandKit:
    digest = hashlib.sha256(data).hexdigest()
    name = f"brand/headshot/{digest}.png"
    await blobs.put("media", name, data, content_type="image/png")
    ref = MediaRef(
        role="headshot",
        name=name,
        sha256=sha or digest,
        content_type="image/png",
        size_bytes=len(data),
    )
    return kit(headshot=ref)


# --------------------------------------------------------------------------------------------
# Brand
# --------------------------------------------------------------------------------------------


async def test_the_brand_kit_maps_onto_the_render_brand() -> None:
    blobs = MemoryBlobStore()
    face = png_bytes(32, 32, "#A8C1B8")
    brand_kit = await headshot_kit(blobs, face)
    renderer = RecordingRenderer()
    adapter: RenderService = RenderServiceAdapter(renderer, blobs=lambda: blobs)
    result = await adapter.render_carousel(SPEC, brand_kit, disclaimer="Views are my own.")
    assert isinstance(result, RenderedCarousel)
    assert result.page_count == 3
    (brand,) = renderer.brands
    assert brand is not None
    assert brand.name_mark == "Sam Rivera"
    assert brand.headline == "Identity and AI security"
    assert brand.site_name == "samrivera.dev"
    assert brand.disclaimer == "Views are my own."
    assert brand.headshot == face
    assert brand.fonts == "inter"
    assert brand.colors.ink == brand_kit.colors.ink.upper()


def test_render_brand_copies_every_colour() -> None:
    palette = KitColors(ink="#101820", paper="#FAFAF7")
    brand = render_brand(kit(colors=palette))
    assert (brand.colors.ink, brand.colors.paper) == ("#101820", "#FAFAF7")
    assert brand.colors.signal == palette.signal.upper()


async def test_an_incomplete_kit_is_refused_before_any_render_or_storage_read() -> None:
    class CountingBlobs(MemoryBlobStore):
        reads = 0

        @override
        async def get(self, container: str, name: str) -> bytes:  # pyright: ignore[reportIncompatibleMethodOverride]
            self.reads += 1
            return await super().get("media", name)

    blobs = CountingBlobs()
    renderer = RecordingRenderer()
    adapter = RenderServiceAdapter(renderer, blobs=lambda: blobs)
    face = png_bytes(8, 8)
    incomplete = (await headshot_kit(blobs, face)).model_copy(update={"author_name": None})
    for call in (
        adapter.render_carousel(SPEC, incomplete),
        adapter.carousel_preview_html(SPEC, incomplete),
        adapter.render_image_card(SLIDE, incomplete),
        adapter.render_og_image(title="T", subtitle=None, pillar_label=None, brand=incomplete),
    ):
        with pytest.raises(RenderFailed) as caught:
            await call
        assert caught.value.code == "brand_incomplete"
    assert renderer.calls == []
    assert blobs.reads == 0


async def test_colours_below_wcag_aa_are_a_brand_problem() -> None:
    grey = KitColors(ink="#777777", paper="#888888")
    adapter = RenderServiceAdapter(RecordingRenderer())
    with pytest.raises(RenderFailed) as caught:
        await adapter.render_carousel(SPEC, kit(colors=grey))
    assert caught.value.code == "brand_incomplete"


async def test_a_headshot_that_does_not_match_its_checksum_is_refused() -> None:
    blobs = MemoryBlobStore()
    tampered = await headshot_kit(blobs, png_bytes(8, 8), sha="0" * 64)
    adapter = RenderServiceAdapter(RecordingRenderer(), blobs=lambda: blobs)
    with pytest.raises(RenderFailed) as caught:
        await adapter.render_carousel(SPEC, tampered)
    assert caught.value.code == "asset_integrity"


async def test_a_missing_headshot_or_missing_storage_fails_closed() -> None:
    blobs = MemoryBlobStore()
    brand_kit = await headshot_kit(blobs, png_bytes(8, 8))
    renderer = RecordingRenderer()
    empty = MemoryBlobStore()
    with pytest.raises(RenderFailed) as missing:
        await RenderServiceAdapter(renderer, blobs=lambda: empty).render_carousel(SPEC, brand_kit)
    assert missing.value.code == "brand_incomplete"
    with pytest.raises(RenderFailed) as off:
        await RenderServiceAdapter(renderer).render_carousel(SPEC, brand_kit)
    assert off.value.code == "unavailable"
    assert renderer.calls == []


# --------------------------------------------------------------------------------------------
# Specs, errors and findings
# --------------------------------------------------------------------------------------------


async def test_invalid_specs_and_slides_are_invalid_spec() -> None:
    renderer = RecordingRenderer()
    adapter = RenderServiceAdapter(renderer)
    bad_spec: Mapping[str, object] = {"title": "T", "slides": [{"type": "nope"}]}
    for call in (
        adapter.render_carousel(bad_spec, kit()),
        adapter.carousel_preview_html({"title": "T"}, kit()),
        adapter.render_image_card({"type": "statement"}, kit()),
    ):
        with pytest.raises(RenderFailed) as caught:
            await call
        assert caught.value.code == "invalid_spec"
    with pytest.raises(RenderFailed) as linted:
        adapter.lint_carousel(bad_spec)
    assert linted.value.code == "invalid_spec"
    with pytest.raises(RenderFailed) as og:
        await adapter.render_og_image(
            title="x" * 300, subtitle=None, pillar_label=None, brand=kit()
        )
    assert og.value.code == "invalid_spec"
    assert renderer.calls == ["render_og_image"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RenderUnavailable("no chromium at /opt/x"), "unavailable"),
        (RenderTimeout("job deadline"), "timeout"),
        (AssetIntegrityError("font sha mismatch"), "asset_integrity"),
        (DiagramLabelError(4, 15.5), "diagram_labels"),
        (MermaidError("Parse error near 'Contoso Payroll'"), "mermaid_error"),
        (OutputLimitError("pdf too large"), "output_limit"),
        (UnsafeSvgError("entity in svg"), "unsafe_svg"),
        (HeadshotError("not an image"), "brand_incomplete"),
        (RenderError("other"), "unavailable"),
    ],
)
async def test_render_errors_map_to_codes_without_their_messages(
    error: Exception, code: str
) -> None:
    adapter = RenderServiceAdapter(RecordingRenderer(fail=error))
    with pytest.raises(RenderFailed) as caught:
        await adapter.render_carousel(SPEC, kit())
    assert caught.value.code == code
    detail = caught.value.detail or ""
    assert detail
    assert str(error) not in detail
    assert "Contoso" not in detail
    if isinstance(error, DiagramLabelError):
        assert detail.startswith("Slide 4:")


async def test_overflow_names_the_slides_only() -> None:
    overflow = LayoutOverflowError(
        ["slide 3 body (+12px tall, +0px wide)", "slide 5 title (+4px tall, +0px wide)"]
    )
    adapter = RenderServiceAdapter(RecordingRenderer(fail=overflow))
    with pytest.raises(RenderFailed) as caught:
        await adapter.render_image_card(SLIDE, kit())
    assert caught.value.code == "layout_overflow"
    assert caught.value.detail == "Slide 3, 5: some text does not fit its box. Shorten it."


async def test_render_warnings_become_findings_and_lint_rules_block() -> None:
    warnings = (
        RenderWarning("slide_count", None, "3 slides; carousels use 8-12"),
        RenderWarning("diagram_label_size", 2, "diagram labels render at 20.0px"),
    )
    adapter = RenderServiceAdapter(RecordingRenderer(warnings=warnings))
    deck = await adapter.render_carousel(SPEC, kit())
    by_code = {finding.code: finding for finding in deck.warnings}
    assert by_code["slide_count"].severity == "block"  # an editorial rule the deck breaks
    assert by_code["slide_count"].ref is None
    assert by_code["diagram_label_size"].severity == "warn"
    assert by_code["diagram_label_size"].ref == "slide-2"


def test_lint_carousel_returns_blocking_findings_per_rule() -> None:
    findings = RenderServiceAdapter(RecordingRenderer()).lint_carousel(SPEC)
    codes = {finding.code for finding in findings}
    assert "slide_count" in codes
    assert all(finding.severity == "block" for finding in findings)
    assert all("Give every" not in finding.message for finding in findings)


# --------------------------------------------------------------------------------------------
# Diagrams and status
# --------------------------------------------------------------------------------------------


async def test_mermaid_svg_is_utf8_bytes_and_diagrams_need_no_author_name() -> None:
    renderer = RecordingRenderer()
    adapter = RenderServiceAdapter(renderer)
    anonymous = kit(author_name=None, colors=KitColors(ink="#101820"))
    svg = await adapter.render_mermaid_svg(MERMAID, anonymous)
    assert svg.decode("utf-8").startswith("<svg")
    assert "é".encode() in svg
    png = await adapter.render_mermaid_png(MERMAID, 640)
    assert png.startswith(b"\x89PNG")
    first, second = renderer.brands
    assert first is not None
    assert first.colors.ink == "#101820"
    assert second is None


def test_the_adapter_reports_the_renderer_status() -> None:
    missing = RenderStatus(False, "no chromium", "chromium_missing", None, None, "sha")
    adapter = RenderServiceAdapter(RecordingRenderer(status=missing))
    assert adapter.status == missing
    assert isinstance(adapter.renderer, Renderer)


# --------------------------------------------------------------------------------------------
# The real renderer (no Chromium needed for a deck without diagrams)
# --------------------------------------------------------------------------------------------

needs_bundle = pytest.mark.skipif(
    not mermaid_bundle_installed(Renderer().settings.mermaid_js),
    reason="the pinned Mermaid bundle is missing (`npm ci --ignore-scripts` in server/vendor)",
)


@needs_bundle
async def test_the_real_preview_carries_the_brand_and_the_disclaimer() -> None:
    renderer = Renderer()
    status = await renderer.start()
    adapter = RenderServiceAdapter(renderer)
    html = await adapter.carousel_preview_html(SPEC, kit(), disclaimer="Views are my own.")
    assert "Sam Rivera" in html
    assert "Views are my own." in html
    assert "<script" not in html.lower()
    if not status.available:
        with pytest.raises(RenderFailed) as caught:
            await adapter.render_carousel(SPEC, kit())
        assert caught.value.code == "unavailable"
