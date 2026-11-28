"""The brand (WCAG AA, CSS-safe colours, headshot) and the embedded fonts (no browser)."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from blogwriter.render import BrandColors, CarouselSpec, RenderBrand, contrast_ratio
from blogwriter.render.errors import HeadshotError
from blogwriter.render.fonts import DISPLAY_FAMILY, TEXT_FAMILY, font_face_css, verify_fonts
from blogwriter.render.headshot import HEADSHOT_PX, headshot_data_uri, prepare_headshot
from blogwriter.render.testing import make_image, png_chunks, png_size, sample_brand


def test_default_colours_meet_wcag_aa_on_every_text_pair() -> None:
    colors = BrandColors()
    for name, (fg, bg) in colors.text_pairs().items():
        assert contrast_ratio(fg, bg) >= 4.5, name


def test_contrast_math_matches_wcag_reference_values() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)
    assert contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)


def test_low_contrast_colours_are_rejected() -> None:
    with pytest.raises(ValidationError, match="WCAG AA"):
        BrandColors(muted="#9AA5A0")  # grey on paper: about 2.4:1


@pytest.mark.parametrize(
    "color",
    [
        "red",
        "#fff",
        "#0E2A24;background:url(https://evil.example)",
        "#0E2A24</style>",
        "rgb(0,0,0)",
    ],
)
def test_brand_colours_cannot_inject_css(color: str) -> None:
    with pytest.raises(ValidationError):
        RenderBrand.model_validate({"name_mark": "A", "colors": {"ink": color}})


def test_brand_text_is_plain_text_with_limits() -> None:
    assert sample_brand(name_mark=" Avery\nQuinn ").name_mark == "Avery Quinn"
    with pytest.raises(ValidationError):
        sample_brand(name_mark="x" * 49)
    with pytest.raises(ValidationError):
        sample_brand(disclaimer="x" * 41)
    with pytest.raises(ValidationError):
        sample_brand(name_mark="")
    with pytest.raises(ValidationError):
        sample_brand(fonts="comic-sans")
    with pytest.raises(ValidationError):
        RenderBrand.model_validate({"name_mark": "A", "author_name": "legacy field"})


def test_brand_is_separate_from_the_spec() -> None:
    with pytest.raises(ValidationError):
        CarouselSpec.model_validate(
            {"title": "t", "brand": {}, "slides": [{"type": "statement", "statement": "s"}]}
        )


def test_headshot_must_be_an_image_and_round_trips_as_base64_json() -> None:
    with pytest.raises(ValidationError, match="PNG, JPEG or WebP"):
        sample_brand(headshot=b"GIF89a....")
    with pytest.raises(ValidationError):
        sample_brand(headshot=b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024))
    jpeg = make_image("JPEG")
    brand = sample_brand(headshot=jpeg)
    data = brand.model_dump_json()
    assert base64.urlsafe_b64encode(jpeg).decode() in data  # pydantic writes URL-safe base64
    assert RenderBrand.model_validate_json(data) == brand
    standard = data.replace(
        base64.urlsafe_b64encode(jpeg).decode(), base64.b64encode(jpeg).decode()
    )
    assert RenderBrand.model_validate_json(standard) == brand  # and reads the standard alphabet


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_headshot_is_reencoded_square_and_metadata_free(fmt: str) -> None:
    source = make_image(fmt, size=(640, 480), exif_orientation=6, comment=b"client name")
    out = prepare_headshot(source)
    assert png_size(out) == (HEADSHOT_PX, HEADSHOT_PX)
    assert set(png_chunks(out)) == {"IHDR", "IDAT", "IEND"}
    assert b"client name" not in out
    assert prepare_headshot(source) == out  # deterministic: same input, same bytes


def test_headshot_follows_the_exif_orientation() -> None:
    # A wide image tagged "rotate 90" is upright portrait; both become square, but the crop
    # differs, so the output must differ from the untagged image.
    plain = Image.new("RGB", (600, 300), (0, 0, 0))
    plain.paste((255, 255, 255), (0, 0, 300, 300))  # left half white
    tagged = BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    plain.save(tagged, format="JPEG", exif=exif.tobytes())
    untagged = BytesIO()
    plain.save(untagged, format="JPEG")
    assert prepare_headshot(tagged.getvalue()) != prepare_headshot(untagged.getvalue())


def test_broken_or_oversized_headshots_raise_a_headshot_error() -> None:
    with pytest.raises(HeadshotError):
        prepare_headshot(b"\xff\xd8\xff" + b"not really a jpeg")
    with pytest.raises(HeadshotError, match="25 MP"):
        prepare_headshot(make_image("PNG", size=(6000, 5000)))
    with pytest.raises(HeadshotError):
        prepare_headshot(b"plain text")


def test_headshot_uri_is_a_png_data_uri() -> None:
    uri = headshot_data_uri(make_image("JPEG"))
    assert uri.startswith("data:image/png;base64,")
    assert png_size(base64.b64decode(uri.split(",", 1)[1])) == (HEADSHOT_PX, HEADSHOT_PX)


def test_fonts_are_verified_and_inlined_under_private_names() -> None:
    verify_fonts()
    css = font_face_css()
    assert css.count("@font-face") == 4
    assert f"font-family:'{TEXT_FAMILY}'" in css
    assert f"font-family:'{DISPLAY_FAMILY}'" in css
    assert "data:font/woff2;base64," in css
    assert "<" not in css
    assert font_face_css(frozenset({TEXT_FAMILY})).count("@font-face") == 2
