"""Embedded fonts, inlined as ``data:`` URIs.

The renderer never loads fonts from disk paths, URLs or the OS: every face is read from this
package, checked against the SHA-256 pinned in ``fonts/manifest.json`` and emitted as an
``@font-face`` rule. The CSS families use private names (``BW Text`` / ``BW Display``) so a locally
installed Inter can never mask a broken embed; the fallback stack is deliberately generic so a
failure is visible in review.

Reading the files is blocking I/O: :func:`verify_fonts` (run in a worker thread at start-up)
warms every cache, so later calls on the event loop are memory-only.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Final

from pydantic import BaseModel, ValidationError

from blogwriter.render.errors import AssetIntegrityError

__all__ = ["DISPLAY_FAMILY", "TEXT_FAMILY", "FontFace", "font_face_css", "verify_fonts"]

TEXT_FAMILY: Final = "BW Text"
DISPLAY_FAMILY: Final = "BW Display"
_PACKAGE: Final = "blogwriter.render"


@dataclass(frozen=True, slots=True)
class FontFace:
    file: str
    css_family: str
    weight: int
    sha256: str


class _ManifestFace(BaseModel):
    file: str
    css_family: str
    weight: int
    sha256: str


class _Manifest(BaseModel):
    faces: list[_ManifestFace]


@cache
def _manifest() -> tuple[FontFace, ...]:
    raw = resources.files(_PACKAGE).joinpath("fonts/manifest.json").read_text(encoding="utf-8")
    try:
        manifest = _Manifest.model_validate_json(raw)
    except ValidationError as exc:
        raise AssetIntegrityError("fonts/manifest.json is malformed") from exc
    return tuple(
        FontFace(file=f.file, css_family=f.css_family, weight=f.weight, sha256=f.sha256)
        for f in manifest.faces
    )


@cache
def _font_bytes(file: str, expected_sha256: str) -> bytes:
    try:
        blob = resources.files(_PACKAGE).joinpath(f"fonts/{file}").read_bytes()
    except FileNotFoundError as exc:  # pragma: no cover - packaging error
        raise AssetIntegrityError(f"font {file} is missing from the package") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if digest != expected_sha256:
        raise AssetIntegrityError(f"font {file} sha256 mismatch: {digest}")
    if blob[:4] != b"wOF2":
        raise AssetIntegrityError(f"font {file} is not WOFF2")
    return blob


def _face_rule(face: FontFace) -> str:
    payload = base64.b64encode(_font_bytes(face.file, face.sha256)).decode("ascii")
    return (
        "@font-face{"
        f"font-family:'{face.css_family}';"
        f"src:url(data:font/woff2;base64,{payload}) format('woff2');"
        f"font-weight:{face.weight};font-style:normal;font-display:block"
        "}"
    )


@cache
def font_face_css(families: frozenset[str] | None = None) -> str:
    """``@font-face`` rules for the requested CSS families (all of them by default)."""
    faces = [f for f in _manifest() if families is None or f.css_family in families]
    if not faces:
        raise AssetIntegrityError("no font faces matched")
    css = "".join(_face_rule(f) for f in faces)
    # The CSS is injected into <style> as trusted markup; prove it cannot terminate the element.
    if "<" in css or ">" in css:  # pragma: no cover - the base64 alphabet has neither
        raise AssetIntegrityError("font CSS contains markup characters")
    return css


def verify_fonts() -> None:
    """Fail fast (at start-up, in a worker thread) if any bundled face is missing or tampered
    with, and warm the caches the templates use."""
    for face in _manifest():
        _font_bytes(face.file, face.sha256)
    font_face_css()
    font_face_css(frozenset({TEXT_FAMILY}))
