"""PDF finishing: exact page geometry, no active content, controlled metadata, a clean text layer.

Chromium writes ``/Title`` (from ``<title>``), ``/Creator`` (``Chromium``), ``/Producer``
(``Skia/PDF m159``) and creation/modification dates. The plan's confidentiality gate wants Title
and Author set and Producer/Creator stripped. Dropping the dates as well makes the output
byte-for-byte deterministic for a given spec and Chromium build, so an approval bound to the
SHA-256 of the PDF survives a re-render (e.g. staging at T-60).

The text layer is what the confidentiality gate scans. Inter's contextual alternates and tabular
figures reach it as Private Use Area code points (the templates switch them off); any PUA code
point that still appears fails the render, because it would hide text from the gate.

Everything here is CPU-bound: run :func:`finish_pdf` through ``asyncio.to_thread``.
"""

from __future__ import annotations

from io import BytesIO
from typing import Final, cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject

from blogwriter.render.errors import OutputLimitError

__all__ = [
    "MAX_PDF_BYTES",
    "PUA_RANGES",
    "SLIDE_PAGE_PT",
    "extract_text_layer",
    "finalize_pdf",
    "finish_pdf",
    "private_use_chars",
]

#: 1080x1350 CSS px slides are emitted at 1pt per CSS px (scale 4/3 on a 15in x 18.75in sheet).
SLIDE_PAGE_PT: Final = (1080.0, 1350.0)
#: The plan's carousel limit (LinkedIn allows 100 MB).
MAX_PDF_BYTES: Final = 10 * 1024 * 1024
PUA_RANGES: Final = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
_FORBIDDEN_CATALOG_KEYS: Final = ("/OpenAction", "/AA", "/AcroForm", "/Metadata", "/URI")
_FORBIDDEN_NAME_TREES: Final = ("/JavaScript", "/EmbeddedFiles")
#: Checked against the raw bytes too, so an orphaned Info object cannot survive. Needles are long
#: enough (>= 8 bytes) that a chance match inside a compressed stream is negligible.
_FORBIDDEN_BYTES: Final = (b"/Producer", b"/Creator", b"/CreationDate", b"/ModDate")


def _check_structure(reader: PdfReader, expected_pages: int, page_pt: tuple[float, float]) -> None:
    if len(reader.pages) != expected_pages:
        raise OutputLimitError(f"PDF has {len(reader.pages)} pages, expected {expected_pages}")
    for i, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        size = (float(box.width), float(box.height))
        if abs(size[0] - page_pt[0]) > 0.01 or abs(size[1] - page_pt[1]) > 0.01:
            raise OutputLimitError(
                f"page {i} is {size[0]}x{size[1]}pt, expected {page_pt[0]}x{page_pt[1]}"
            )
        if "/Annots" in page or "/AA" in page:
            raise OutputLimitError(f"page {i} has annotations or actions (links are not allowed)")
    root = cast(DictionaryObject, reader.trailer["/Root"].get_object())
    for key in _FORBIDDEN_CATALOG_KEYS:
        if key in root:
            raise OutputLimitError(f"PDF catalog has {key}")
    names = root.get("/Names")
    names = names.get_object() if names is not None else None
    if isinstance(names, DictionaryObject):
        for key in _FORBIDDEN_NAME_TREES:
            if key in names:
                raise OutputLimitError(f"PDF name tree has {key}")


def finalize_pdf(
    raw: bytes,
    *,
    title: str,
    author: str,
    expected_pages: int,
    max_bytes: int = MAX_PDF_BYTES,
    page_pt: tuple[float, float] = SLIDE_PAGE_PT,
) -> bytes:
    """Validate Chromium's PDF and rewrite its metadata to exactly ``{/Title, /Author}``."""
    reader = PdfReader(BytesIO(raw), strict=True)
    _check_structure(reader, expected_pages, page_pt)

    writer = PdfWriter(clone_from=reader)
    writer.pdf_header = reader.pdf_header  # keep Chromium's %PDF-1.4 (pypdf defaults to 1.3)
    writer.metadata = {"/Title": title, "/Author": author}
    # PdfWriter() seeds an Info dict with /Producer (pypdf) before cloning; drop it and orphans.
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
    buf = BytesIO()
    writer.write(buf)
    out = buf.getvalue()

    for needle in _FORBIDDEN_BYTES:
        if needle in out:
            raise OutputLimitError(f"PDF still contains {needle.decode()}")
    check = PdfReader(BytesIO(out), strict=True)
    meta = check.metadata
    if (
        meta is None
        or set(meta) != {"/Title", "/Author"}
        or meta.title != title
        or meta.author != author
    ):
        raise OutputLimitError("PDF metadata was not rewritten as expected")
    _check_structure(check, expected_pages, page_pt)
    if len(out) > max_bytes:
        raise OutputLimitError(f"PDF is {len(out)} bytes; limit is {max_bytes}")
    return out


def private_use_chars(text: str) -> list[str]:
    """Every Private Use Area code point in ``text``."""
    return [ch for ch in text if any(lo <= ord(ch) <= hi for lo, hi in PUA_RANGES)]


def extract_text_layer(pdf: bytes) -> tuple[str, ...]:
    """The text layer of every page, in order (what the confidentiality gate scans)."""
    return tuple(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)


def finish_pdf(
    raw: bytes, *, title: str, author: str, expected_pages: int, max_bytes: int = MAX_PDF_BYTES
) -> tuple[bytes, tuple[str, ...]]:
    """:func:`finalize_pdf`, then the text layer; fails if it holds Private Use Area code points."""
    pdf = finalize_pdf(
        raw, title=title, author=author, expected_pages=expected_pages, max_bytes=max_bytes
    )
    text = extract_text_layer(pdf)
    for n, page_text in enumerate(text, start=1):
        if private_use_chars(page_text):
            raise OutputLimitError(
                f"page {n}: the PDF text layer holds Private Use Area code points "
                "(an OpenType feature such as calt or tnum was re-enabled)"
            )
    return pdf, text
