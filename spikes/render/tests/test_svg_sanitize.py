"""SVG sanitiser unit tests with hand-made attack SVGs (no browser needed)."""

from __future__ import annotations

import pytest

from render.errors import UnsafeSvgError
from render.svg_sanitize import assert_svg_safe, sanitize_svg

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def svg(body: str) -> str:
    return f'<svg {NS} viewBox="0 0 10 10">{body}</svg>'


@pytest.mark.parametrize(
    ("body", "gone"),
    [
        ("<script>alert(1)</script>", "script"),
        ('<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>', "foreignObject"),
        ('<image href="https://evil.example/x.png" width="1" height="1"/>', "image"),
        ('<animate attributeName="href" to="javascript:alert(1)"/>', "animate"),
        ('<set attributeName="onload" to="alert(1)"/>', "set"),
        ('<iframe src="https://evil.example"/>', "iframe"),
        ('<feImage href="https://evil.example/x.png"/>', "feImage"),
        ('<unknownThing><rect width="1" height="1"/></unknownThing>', "unknownThing"),
    ],
)
def test_dangerous_elements_are_removed_with_their_subtree(body: str, gone: str) -> None:
    out, report = sanitize_svg(svg(body + '<rect id="keep" width="1" height="1"/>'))
    assert gone.encode() not in out
    assert b'id="keep"' in out
    assert gone in report.removed_elements
    assert_svg_safe(out)


def test_event_handlers_and_script_urls_are_dropped() -> None:
    out, report = sanitize_svg(
        svg(
            '<rect onclick="alert(1)" ONLOAD="alert(2)" width="1" height="1"/>'
            '<use href="https://evil.example/s.svg#x"/><use xlink:href=" java&#9;script:alert(3)"/>'
            '<use href="#local"/><rect fill="url(#grad)" width="1" height="1"/>'
        )
    )
    text = out.decode()
    assert "onclick" not in text.lower() and "onload" not in text.lower()
    assert "evil.example" not in text and "javascript" not in text
    assert 'href="#local"' in text and 'fill="url(#grad)"' in text
    assert len(report.removed_attributes) == 4
    assert_svg_safe(out)


def test_links_are_unwrapped_into_groups() -> None:
    out, report = sanitize_svg(
        svg('<a href="https://evil.example" transform="translate(1,1)"><text>t</text></a>')
    )
    assert b"<a" not in out and b"evil" not in out
    assert b'<g transform="translate(1,1)"><text>t</text></g>' in out
    assert report.unwrapped_links == 1


def test_css_is_restricted_to_local_references() -> None:
    out, report = sanitize_svg(
        svg(
            "<style>@import url(https://evil.example/a.css);"
            ".a{fill:url(https://evil.example/p.png)} .b{fill:url(#ok)} .c{background:u\\72l(x)}</style>"
            '<rect style="fill:url(https://evil.example/x);stroke:#000" width="1" height="1"/>'
        )
    )
    text = out.decode()
    assert "evil.example" not in text and "@import" not in text and "\\" not in text
    assert "url(#ok)" in text and "stroke:#000" in text
    assert report.css_rewrites >= 2
    assert_svg_safe(out)


@pytest.mark.parametrize(
    "doc",
    [
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "boom">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        '<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg xmlns="http://www.w3.org/2000/svg"/>',
        '<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
        "<svg><rect></svg>",
        "<svg>" + "<g>" * 10 + "</svg>",
    ],
)
def test_unsafe_or_malformed_documents_are_rejected(doc: str) -> None:
    with pytest.raises(UnsafeSvgError):
        sanitize_svg(doc)


def test_oversized_input_is_rejected() -> None:
    with pytest.raises(UnsafeSvgError, match="larger than"):
        sanitize_svg(svg("<g/>" * 600_000))


def test_checker_flags_what_the_sanitiser_removes() -> None:
    with pytest.raises(UnsafeSvgError):
        assert_svg_safe(svg("<script>x</script>"))
    with pytest.raises(UnsafeSvgError):
        assert_svg_safe(svg('<rect onload="x"/>'))
    with pytest.raises(UnsafeSvgError):
        assert_svg_safe(svg('<use href="https://evil.example#a"/>'))
    assert_svg_safe(svg("<text>javascript: is just text here</text>"))


# --- Reviewer additions: remote references hidden in CSS forms the first version missed. ---------------
# Each body below got "evil.example" through the sanitiser *and* past assert_svg_safe before the fix.
CSS_BYPASSES = {
    "presentation attributes": (
        '<path d="M0 0L1 1" fill="url(https://evil.example/p.svg#g)" filter="url(https://evil.example/f#x)"'
        ' mask="url(https://evil.example/m#m)" clip-path="url(https://evil.example/c#c)"'
        ' marker-end="url(https://evil.example/k#k)"/>'
    ),
    "local then remote url in one declaration": (
        '<rect style="filter:url(#ok) url(https://evil.example/f.svg#x)" width="1" height="1"/>'
    ),
    "string URL in image-set (style attribute)": (
        '<rect style="background-image:-webkit-image-set(\'https://evil.example/i.png\' 1x)" width="1"/>'
    ),
    "string URL in image-set (style element)": (
        '<style>.a{background-image:image-set("https://evil.example/i.png" 1x)}</style>'
    ),
    "unterminated url( at the end of the sheet": (
        "<style>.a{fill:red} .b{background:url(https://evil.example/u.png</style>"
    ),
    "escape that completes url( once removed": "<style>.c{background:ur\\l(https://evil.example/e.png)}</style>",
    "CSS after a child element inside style": (
        "<style>.a{fill:red}<g/>.b{background:url(https://evil.example/tail.png)}</style>"
    ),
}


@pytest.mark.parametrize("body", CSS_BYPASSES.values(), ids=CSS_BYPASSES.keys())
def test_remote_references_in_every_css_form_are_removed(body: str) -> None:
    out, report = sanitize_svg(svg(body + '<rect id="keep" width="1" height="1"/>'))
    assert b"evil.example" not in out
    assert b'id="keep"' in out
    assert report.changed
    assert_svg_safe(out)


@pytest.mark.parametrize("body", CSS_BYPASSES.values(), ids=CSS_BYPASSES.keys())
def test_checker_alone_rejects_every_css_bypass(body: str) -> None:
    """The checker is an independent layer: it must catch each raw form without the sanitiser."""
    with pytest.raises(UnsafeSvgError):
        assert_svg_safe(svg(body))


def test_local_references_in_css_values_are_kept() -> None:
    body = (
        '<defs><linearGradient id="g"/><marker id="m"/><filter id="f"/></defs>'
        '<path d="M0 0L1 1" fill="url(#g)" marker-end="url(#m)" style="filter:url(#f);stroke:#000"/>'
    )
    out, report = sanitize_svg(svg(body))
    text = out.decode()
    assert 'fill="url(#g)"' in text and 'marker-end="url(#m)"' in text
    assert 'style="filter:url(#f);stroke:#000"' in text
    assert not report.changed
