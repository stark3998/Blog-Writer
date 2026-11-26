"""Embedded fonts, inlined as ``data:`` URIs.

The renderer never loads fonts from disk, URLs or the OS: every face is read from this package, checked
against the SHA-256 pinned in ``fonts/manifest.json`` and emitted as an ``@font-face`` rule. The CSS
families use private names (``BW Text`` / ``BW Display``) so a locally installed Inter can never mask a
broken embed; the fallback stack is deliberately generic so a failure is visible in review.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

from .errors import AssetIntegrityError

TEXT_FAMILY = "BW Text"
DISPLAY_FAMILY = "BW Display"


@dataclass(frozen=True, slots=True)
class FontFace:
    file: str
    css_family: str
    weight: int
    sha256: str


@cache
def _manifest() -> tuple[FontFace, ...]:
    raw = resources.files("render").joinpath("fonts/manifest.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    return tuple(
        FontFace(file=f["file"], css_family=f["css_family"], weight=int(f["weight"]), sha256=f["sha256"])
        for f in data["faces"]
    )


@cache
def _font_bytes(file: str, expected_sha256: str) -> bytes:
    try:
        blob = resources.files("render").joinpath(f"fonts/{file}").read_bytes()
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
    if "<" in css or ">" in css:  # pragma: no cover - base64 alphabet has neither
        raise AssetIntegrityError("font CSS contains markup characters")
    return css


def verify_fonts() -> None:
    """Fail fast (e.g. at app start-up) if any bundled face is missing or tampered with."""
    for face in _manifest():
        _font_bytes(face.file, face.sha256)
