"""PDF finishing on synthetic documents (no browser): geometry, active content, metadata, size."""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from blogwriter.render import OutputLimitError
from blogwriter.render.pdf import finalize_pdf, finish_pdf, private_use_chars


def make_pdf(pages: int = 2, size: tuple[float, float] = (1080, 1350), **extra: object) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=size[0], height=size[1])
    writer.add_metadata({"/Producer": "Skia/PDF m159", "/Creator": "Chromium", "/Title": "x"})
    if extra.get("open_action"):
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject()  # pyright: ignore[reportPrivateUsage]
    if extra.get("annots"):
        writer.pages[0][NameObject("/Annots")] = DictionaryObject()
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_metadata_becomes_exactly_title_and_author() -> None:
    out = finalize_pdf(make_pdf(), title="Deck title", author="Avery Quinn", expected_pages=2)
    meta = PdfReader(BytesIO(out)).metadata
    assert meta is not None
    assert dict(meta) == {"/Title": "Deck title", "/Author": "Avery Quinn"}
    for needle in (b"/Producer", b"/Creator", b"Skia", b"Chromium", b"pypdf", b"/CreationDate"):
        assert needle not in out, needle


def test_page_count_and_size_are_enforced() -> None:
    with pytest.raises(OutputLimitError, match="expected 3"):
        finalize_pdf(make_pdf(2), title="t", author="a", expected_pages=3)
    with pytest.raises(OutputLimitError, match="expected 1080"):
        finalize_pdf(make_pdf(2, size=(810, 1013.04)), title="t", author="a", expected_pages=2)


def test_active_content_is_refused() -> None:
    with pytest.raises(OutputLimitError, match="/OpenAction"):
        finalize_pdf(make_pdf(open_action=True), title="t", author="a", expected_pages=2)
    with pytest.raises(OutputLimitError, match="annotations"):
        finalize_pdf(make_pdf(annots=True), title="t", author="a", expected_pages=2)


def test_size_limit() -> None:
    with pytest.raises(OutputLimitError, match="limit is 100"):
        finalize_pdf(make_pdf(), title="t", author="a", expected_pages=2, max_bytes=100)


def test_finish_returns_the_text_layer_per_page() -> None:
    pdf, text = finish_pdf(make_pdf(3), title="t", author="a", expected_pages=3)
    assert pdf.startswith(b"%PDF-")
    assert text == ("", "", "")


def test_title_that_names_a_metadata_key_is_not_a_false_positive() -> None:
    """The raw-bytes /Producer check must not trip on a title that mentions it."""
    title = "Why /Producer and /Creator matter"
    out = finalize_pdf(make_pdf(), title=title, author="a", expected_pages=2)
    meta = PdfReader(BytesIO(out)).metadata
    assert meta is not None
    assert meta.title == title
    assert isinstance(meta["/Title"], TextStringObject)


def test_private_use_detection() -> None:
    assert private_use_chars("12 / 12 ->") == []
    assert private_use_chars("a\ue000b\U000f0000") == ["\ue000", "\U000f0000"]
