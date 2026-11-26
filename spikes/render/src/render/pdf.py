"""PDF finishing: exact page geometry, no active content, and controlled metadata.

Chromium writes ``/Title`` (from ``<title>``), ``/Creator`` (``Chromium``), ``/Producer``
(``Skia/PDF m159``) and creation/modification dates. The plan's confidentiality gate wants Title and
Author set and Producer/Creator stripped. Dropping the dates as well makes the output byte-for-byte
deterministic for a given spec and Chromium build, so an approval bound to the SHA-256 of the PDF
survives a re-render (e.g. staging at T-60).
"""

from __future__ import annotations

from io import BytesIO
from typing import cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject

from .errors import OutputLimitError

#: 1080x1350 CSS px slides are emitted at 1pt per CSS px (scale 4/3 on a 15in x 18.75in sheet).
SLIDE_PAGE_PT = (1080.0, 1350.0)
_FORBIDDEN_CATALOG_KEYS = ("/OpenAction", "/AA", "/AcroForm", "/Metadata", "/URI")
_FORBIDDEN_NAME_TREES = ("/JavaScript", "/EmbeddedFiles")
#: Checked against the raw bytes too, so an orphaned Info object cannot survive. Needles are long
#: enough (>= 8 bytes) that a chance match inside a compressed stream is negligible.
_FORBIDDEN_BYTES = (b"/Producer", b"/Creator", b"/CreationDate", b"/ModDate")


def _check_structure(reader: PdfReader, expected_pages: int, page_pt: tuple[float, float]) -> None:
    if len(reader.pages) != expected_pages:
        raise OutputLimitError(f"PDF has {len(reader.pages)} pages, expected {expected_pages}")
    for i, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        size = (float(box.width), float(box.height))
        if abs(size[0] - page_pt[0]) > 0.01 or abs(size[1] - page_pt[1]) > 0.01:
            raise OutputLimitError(f"page {i} is {size[0]}x{size[1]}pt, expected {page_pt[0]}x{page_pt[1]}")
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
    max_bytes: int,
    page_pt: tuple[float, float] = SLIDE_PAGE_PT,
) -> bytes:
    """Validate Chromium's PDF and rewrite its metadata to exactly ``{/Title, /Author}``."""
    reader = PdfReader(BytesIO(raw), strict=True)
    _check_structure(reader, expected_pages, page_pt)

    writer = PdfWriter(clone_from=reader)
    writer.pdf_header = reader.pdf_header  # keep Chromium's %PDF-1.4 (pypdf defaults to 1.3)
    writer.metadata = {"/Title": title, "/Author": author}
    # PdfWriter() seeds an Info dict with /Producer (pypdf) before cloning; drop it and any orphans.
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
    buf = BytesIO()
    writer.write(buf)
    out = buf.getvalue()

    for needle in _FORBIDDEN_BYTES:
        if needle in out:
            raise OutputLimitError(f"PDF still contains {needle.decode()}")
    check = PdfReader(BytesIO(out), strict=True)
    meta = check.metadata
    if meta is None or set(meta) != {"/Title", "/Author"} or meta.title != title or meta.author != author:
        raise OutputLimitError("PDF metadata was not rewritten as expected")
    _check_structure(check, expected_pages, page_pt)
    if len(out) > max_bytes:
        raise OutputLimitError(f"PDF is {len(out)} bytes; limit is {max_bytes}")
    return out
