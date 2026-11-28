"""The Markdown helpers behind inserting an illustration: headings (fenced code skipped), the
image as its own paragraph after a heading, alt text kept plain (dialect escapes), and the alt
text of an image already in the body replaced in place."""

from __future__ import annotations

import pytest

from blogwriter.articles import HeadingNotFound, InvalidAltText, image_markdown, markdown_headings
from blogwriter.articles.content import prepare_body
from blogwriter.articles.illustrations import (
    _replace_alt,  # pyright: ignore[reportPrivateUsage]
    clean_alt_text,
    insert_after_heading,
)

NAME = "articles/art-1/" + "a" * 64 + ".png"
SRC = f"/api/media/{NAME}"
BODY = (
    "Intro.\n\n"
    "## Why *scope* per call\n\n"
    "Text.\n\n"
    "```markdown\n## Not a heading\n```\n\n"
    "### The `gateway` pattern ###\n\n"
    "More.\n\n"
    "#### A [linked](https://example.com/x) detail\n\n"
    "End.\n"
)


def test_headings_are_h2_to_h4_outside_code() -> None:
    assert markdown_headings(BODY) == [
        "Why *scope* per call",
        "The `gateway` pattern",
        "A [linked](https://example.com/x) detail",
    ]
    assert markdown_headings("# Title\n\n##### Deep\n\nText.\n") == []
    fenced = "~~~~\n## inside\n~~~\n## still inside\n~~~~\n\n## Outside\n"
    assert markdown_headings(fenced) == ["Outside"]


@pytest.mark.parametrize(
    "heading",
    ["Why scope per call", "why SCOPE per call", "  Why *scope*   per call "],
)
def test_an_image_goes_after_the_heading_as_its_own_paragraph(heading: str) -> None:
    block = image_markdown("Glass keys", SRC)
    inserted = prepare_body(insert_after_heading(BODY, heading, block))
    assert inserted.findings == ()
    assert f"## Why *scope* per call\n\n{block}\n\nText." in inserted.text


def test_markup_in_headings_is_ignored_when_matching() -> None:
    block = image_markdown("Keys", SRC)
    assert block in insert_after_heading(BODY, "The gateway pattern", block)
    assert block in insert_after_heading(BODY, "A linked detail", block)
    with pytest.raises(HeadingNotFound):
        insert_after_heading(BODY, "Not a heading", block)  # only inside a code block
    with pytest.raises(HeadingNotFound):
        insert_after_heading(BODY, "   ", block)


def test_alt_text_stays_plain_text_in_the_dialect() -> None:
    block = image_markdown("A gate [open] *now* & <b>\nnext", SRC)
    assert block == f"![A gate &#91;open&#93; \\*now\\* \\& \\<b\\> next]({SRC})"
    normalized = prepare_body(f"Intro.\n\n{block}\n").text
    assert "&#91;open&#93; \\*now\\*" in normalized
    assert clean_alt_text("  two\u2028lines\t here ") == "two lines here"
    assert len(clean_alt_text("x" * 400)) == 300
    with pytest.raises(InvalidAltText):
        clean_alt_text(" \n\x00 ")


def test_the_alt_text_of_an_image_in_the_body_is_replaced_in_place() -> None:
    body = (
        f"Intro.\n\n![Old [alt]]({SRC})\n\nText.\n\n"
        f'![Old again]({SRC} "A title")\n\n![Other](/api/media/uploads/2026-11/x.png)\n'
    )
    replaced = _replace_alt(body, NAME, "New alt")
    assert f"![New alt]({SRC})" in replaced
    assert f'![New alt]({SRC} "A title")' in replaced
    assert "![Other](/api/media/uploads/2026-11/x.png)" in replaced
    assert "Old" not in replaced


def test_escaped_markup_in_a_heading_matches_what_the_reader_sees() -> None:
    body = "Intro.\n\n## Why \\*this\\* matters\n\nText.\n"
    block = image_markdown("Keys", SRC)
    assert f"## Why \\*this\\* matters\n\n{block}" in insert_after_heading(
        body, "Why *this* matters", block
    )
