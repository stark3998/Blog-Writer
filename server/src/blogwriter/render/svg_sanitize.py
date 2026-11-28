"""Allowlist sanitiser for Mermaid SVG output.

Mermaid already runs with ``securityLevel: 'strict'``; this is the independent second layer the
plan asks for. The output is safe to embed as ``<img src="data:image/svg+xml;...">`` in a slide,
to publish to the blog as a file, and to show in the Studio preview:

* parsed by lxml with DTDs, entities and network access disabled (no XXE, no entity bombs);
* only presentational SVG elements survive; ``script``, ``foreignObject``, animation elements
  (which can rewrite ``href``), ``image`` and anything unknown are removed with their subtree;
* ``<a>`` is unwrapped into ``<g>`` (links are not allowed in carousels or diagrams);
* ``on*`` event attributes are dropped, as is any attribute whose value is a ``javascript:``,
  ``vbscript:`` or ``data:`` URL once whitespace and control characters are ignored;
* ``href``/``xlink:href`` may only point at a local fragment (``#id``);
* CSS (``<style>``, ``style=""`` and presentation attributes such as ``fill``/``filter``/``mask``)
  loses ``@import``, escapes, ``expression()``, string-URL functions (``image-set("...")``) and
  every ``url()`` that is not a local fragment. ``<style>`` is reduced to plain text first,
  because text after a child element is still part of the sheet.
* The output must pass :func:`assert_svg_safe`, the independent checker, or nothing is returned.

Both functions are CPU-bound: call them through ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from blogwriter.render._xml import XmlElement, XmlSyntaxError, parse_xml, split_name, to_bytes
from blogwriter.render.errors import UnsafeSvgError

__all__ = [
    "ALLOWED_ELEMENTS",
    "DANGEROUS_ELEMENTS",
    "MAX_SVG_BYTES",
    "SVG_NS",
    "XLINK_NS",
    "SanitizeReport",
    "assert_svg_safe",
    "css_is_safe",
    "sanitize_svg",
]

SVG_NS: Final = "http://www.w3.org/2000/svg"
XLINK_NS: Final = "http://www.w3.org/1999/xlink"
MAX_SVG_BYTES: Final = 2 * 1024 * 1024

ALLOWED_ELEMENTS: Final = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "title", "desc", "style", "switch",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "textPath",
        "marker", "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask",
        "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
        "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur",
        "feMerge", "feMergeNode", "feMorphology", "feOffset",
    }
)  # fmt: skip
#: Removed with their subtree. Anything not in ALLOWED_ELEMENTS is removed the same way.
DANGEROUS_ELEMENTS: Final = frozenset(
    {
        "script", "foreignObject", "iframe", "object", "embed", "image", "feImage", "animate",
        "animateMotion", "animateTransform", "set", "discard", "handler", "listener", "audio",
        "video", "canvas", "font", "font-face", "font-face-uri", "cursor", "view",
    }
)  # fmt: skip
_HREF_ATTRS: Final = frozenset({"href", f"{{{XLINK_NS}}}href"})
_URL_SCHEME: Final = re.compile(r"^(javascript|vbscript|data|file|https?|ftp|blob):", re.IGNORECASE)
_INVISIBLE: Final = re.compile(r"[\x00-\x20\x7f-\x9f\u200b-\u200f\u2028-\u202f\ufeff]+")
_CSS_URL: Final = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_CSS_IMPORT: Final = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_CSS_BAD: Final = re.compile(
    r"\\|expression\s*\(|behavior\s*:|-moz-binding|@font-face|@namespace", re.IGNORECASE
)
#: CSS functions that take a URL as a plain string, so they never show up as ``url(``.
_CSS_FETCHING: Final = re.compile(r"image-set\s*\(|(?<![\w-])(?:image|src)\s*\(", re.IGNORECASE)


@dataclass(slots=True)
class SanitizeReport:
    removed_elements: list[str] = field(default_factory=list[str])
    removed_attributes: list[str] = field(default_factory=list[str])
    unwrapped_links: int = 0
    css_rewrites: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.removed_elements
            or self.removed_attributes
            or self.unwrapped_links
            or self.css_rewrites
        )


def _qname(tag: object) -> tuple[str | None, str]:
    """``(namespace, localname)``; ``(None, "")`` for comments and processing instructions."""
    if not isinstance(tag, str):
        return None, ""
    return split_name(tag)


def _squash(value: str) -> str:
    return _INVISIBLE.sub("", value)


def css_is_safe(css: str, *, allow_font_data: bool = False) -> bool:
    """True when nothing in ``css`` can fetch or hide: every ``url()`` is a complete local
    reference (or, in our own ``@font-face``, embedded WOFF2), and there is no ``@import``,
    escape or string-URL function. Shared by the sanitiser and the checker so they cannot drift
    apart."""
    if "\\" in css or "@import" in css.lower() or _CSS_FETCHING.search(css):
        return False
    local = 0
    for m in _CSS_URL.finditer(css):
        target = _squash(m.group(2))
        allowed_font = allow_font_data and target.startswith("data:font/woff2;base64,")
        if not (target.startswith("#") or allowed_font):
            return False
        local += 1
    # Every url( must have been a complete, local match: an unterminated url( runs to the end of
    # the sheet in CSS and still fetches.
    return _squash(css).lower().count("url(") == local


def _clean_css(css: str, report: SanitizeReport) -> str:
    original = css
    # Escapes first: deleting a backslash can complete a token (ur\l( -> url( ), so it must
    # happen before the url() rewrite, never after. Escapes can spell anything.
    css = _CSS_BAD.sub("", css)
    css = _CSS_IMPORT.sub("", css)

    def _url(m: re.Match[str]) -> str:
        target = _squash(m.group(2))
        return f"url({target})" if target.startswith("#") else "none"

    css = _CSS_URL.sub(_url, css)
    if not css_is_safe(css):
        # Fail closed: an unterminated url( at the end of the sheet, or image-set("https://...").
        # Mermaid never emits either, so the sheet is hostile; an unstyled diagram is the result.
        css = ""
    if css != original:
        report.css_rewrites += 1
    return css


def _clean_style_attr(value: str, report: SanitizeReport) -> str:
    kept: list[str] = []
    for decl in value.split(";"):
        if not decl.strip():
            continue
        if not css_is_safe(decl) or _CSS_BAD.search(decl):
            report.css_rewrites += 1
            continue
        kept.append(decl.strip())
    return ";".join(kept)


def _attr_allowed(name: str, value: str) -> bool:
    _, local = split_name(name)
    if local.lower().startswith("on"):
        return False
    squashed = _squash(value)
    if name in _HREF_ATTRS:
        return squashed.startswith("#")
    if _URL_SCHEME.match(squashed):
        return False
    # Presentation attributes are CSS values too (fill="url(https://...)", filter, mask,
    # clip-path, marker-*). ``style`` is cleaned declaration by declaration instead.
    return local == "style" or css_is_safe(value)


def sanitize_svg(svg: str | bytes) -> tuple[bytes, SanitizeReport]:
    """Return a sanitised, UTF-8 SVG document (no XML declaration) and what was changed."""
    raw = svg.encode("utf-8") if isinstance(svg, str) else svg
    if len(raw) > MAX_SVG_BYTES:
        raise UnsafeSvgError(f"SVG larger than {MAX_SVG_BYTES} bytes")
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in raw.lower():
        raise UnsafeSvgError("SVG must not contain a DTD or entity declarations")
    try:
        root = parse_xml(raw, strip_comments_and_pis=True)
    except XmlSyntaxError as exc:
        raise UnsafeSvgError(f"SVG is not well-formed XML: {exc.msg}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise UnsafeSvgError("root element is not <svg> in the SVG namespace")

    report = SanitizeReport()
    # Materialise the walk first: we mutate the tree while visiting.
    for el in list(root.iter()):
        if not _still_attached(el, root):
            continue  # removed together with an ancestor
        ns, local = _qname(el.tag)
        if el is not root and (
            ns != SVG_NS or local in DANGEROUS_ELEMENTS or local not in ALLOWED_ELEMENTS
        ):
            if local == "a" and ns == SVG_NS:
                _unwrap_link(el)
                report.unwrapped_links += 1
                local = "g"
            else:
                report.removed_elements.append(local or str(el.tag))
                _remove_keep_tail(el)
                continue
        for name, value in list(el.attrib.items()):
            if not _attr_allowed(name, value):
                report.removed_attributes.append(f"{local}@{split_name(name)[1]}")
                del el.attrib[name]
            elif split_name(name)[1] == "style":
                cleaned = _clean_style_attr(value, report)
                if cleaned:
                    el.attrib[name] = cleaned
                else:
                    del el.attrib[name]
        if local == "style":
            # Browsers build the sheet from every text child, including text *after* a child
            # element (<style>a<g/>b</style> applies "b" too). Reduce the element to plain text,
            # then clean it.
            sheet = "".join(el.itertext())
            for child in list(el):
                report.removed_elements.append(_qname(child.tag)[1] or "node")
                el.remove(child)
            el.text = _clean_css(sheet, report) if sheet else None

    out = to_bytes(root)
    assert_svg_safe(out)  # the independent checker must agree, or nothing is returned
    return out, report


def _still_attached(el: XmlElement, root: XmlElement) -> bool:
    node: XmlElement | None = el
    while node is not None:
        if node is root:
            return True
        node = node.getparent()
    return False


def _remove_keep_tail(el: XmlElement) -> None:
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _unwrap_link(el: XmlElement) -> None:
    """Turn ``<a>`` into a plain ``<g>``: same children and transform, no link semantics."""
    el.tag = f"{{{SVG_NS}}}g"
    for name in list(el.attrib):
        if split_name(name)[1] not in {"transform", "class", "id"}:
            del el.attrib[name]


def assert_svg_safe(svg: bytes | str, *, allow_font_data: bool = True) -> None:
    """Independent structural re-check (sanitiser output, finished diagrams, pre-publish).

    Raises :class:`UnsafeSvgError`. Text content is not inspected: a label that *says*
    ``javascript:`` is inert text. ``allow_font_data`` permits embedded WOFF2 ``@font-face``
    sources inside ``<style>`` (our own fonts).
    """
    raw = svg.encode("utf-8") if isinstance(svg, str) else svg
    if b"<!doctype" in raw[:4096].lower() or b"<!entity" in raw.lower():
        raise UnsafeSvgError("DTD or entity declaration present")
    try:
        root = parse_xml(raw)
    except XmlSyntaxError as exc:
        raise UnsafeSvgError(f"SVG is not well-formed XML: {exc.msg}") from exc
    for el in root.iter():
        if not isinstance(el.tag, str):
            raise UnsafeSvgError("comment or processing instruction present")
        ns, local = split_name(el.tag)
        if ns != SVG_NS or local not in ALLOWED_ELEMENTS:
            raise UnsafeSvgError(f"element not allowed: {local}")
        for name, value in el.attrib.items():
            attr = split_name(name)[1]
            squashed = _squash(value)
            if attr.lower().startswith("on"):
                raise UnsafeSvgError(f"event handler attribute: {attr}")
            if name in _HREF_ATTRS and not squashed.startswith("#"):
                raise UnsafeSvgError("non-local href")
            if _URL_SCHEME.match(squashed):
                raise UnsafeSvgError(f"URL-valued attribute: {attr}")
            if not css_is_safe(value):  # style="" and presentation attributes (fill, mask...)
                raise UnsafeSvgError(f"unsafe CSS value in attribute: {attr}")
        if local == "style":
            if len(el):
                raise UnsafeSvgError("style element has child elements (text after them is CSS)")
            if el.text and not css_is_safe(el.text, allow_font_data=allow_font_data):
                raise UnsafeSvgError("unsafe CSS in style element (import, escape or remote URL)")
