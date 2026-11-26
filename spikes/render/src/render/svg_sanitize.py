"""Allowlist sanitiser for Mermaid SVG output.

Mermaid already runs with ``securityLevel: 'strict'``; this is the independent second layer the plan
asks for. The output is safe to embed as ``<img src="data:image/svg+xml;...">`` in a slide, to publish
to the blog as a file, and to show in the Studio preview:

* parsed by lxml with DTDs, entities and network access disabled (no XXE, no entity bombs);
* only presentational SVG elements survive; ``script``, ``foreignObject``, animation elements (which
  can rewrite ``href``), ``image`` and anything unknown are removed with their subtree;
* ``<a>`` is unwrapped into ``<g>`` (links are not allowed in carousels or diagrams);
* ``on*`` event attributes are dropped, as is any attribute whose value is a ``javascript:``,
  ``vbscript:`` or ``data:`` URL once whitespace and control characters are ignored;
* ``href``/``xlink:href`` may only point at a local fragment (``#id``);
* CSS (``<style>``, ``style=""`` and presentation attributes such as ``fill``/``filter``/``mask``)
  loses ``@import``, escapes, ``expression()``, string-URL functions (``image-set("...")``) and every
  ``url()`` that is not a local fragment. ``<style>`` is reduced to plain text first, because text
  after a child element is still part of the sheet.
* The output must pass :func:`assert_svg_safe`, the independent checker, or nothing is returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from .errors import UnsafeSvgError

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MAX_SVG_BYTES = 2 * 1024 * 1024

ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "title", "desc", "style", "switch",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "textPath",
        "marker", "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask",
        "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite", "feDropShadow",
        "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feMerge",
        "feMergeNode", "feMorphology", "feOffset",
    }
)  # fmt: skip
#: Removed with their subtree. Anything not in ALLOWED_ELEMENTS is removed the same way.
DANGEROUS_ELEMENTS = frozenset(
    {
        "script", "foreignObject", "iframe", "object", "embed", "image", "feImage", "animate",
        "animateMotion", "animateTransform", "set", "discard", "handler", "listener", "audio",
        "video", "canvas", "font", "font-face", "font-face-uri", "cursor", "view",
    }
)  # fmt: skip
_HREF_ATTRS = frozenset({"href", f"{{{XLINK_NS}}}href"})
_URL_SCHEME = re.compile(r"^(javascript|vbscript|data|file|https?|ftp|blob):", re.IGNORECASE)
_INVISIBLE = re.compile(r"[\x00-\x20\x7f-\x9f\u200b-\u200f\u2028-\u202f\ufeff]+")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)
_CSS_BAD = re.compile(r"\\|expression\s*\(|behavior\s*:|-moz-binding|@font-face|@namespace", re.IGNORECASE)
#: CSS functions that take a URL as a plain string, so they never show up as ``url(``.
_CSS_FETCHING = re.compile(r"image-set\s*\(|(?<![\w-])(?:image|src)\s*\(", re.IGNORECASE)


@dataclass(slots=True)
class SanitizeReport:
    removed_elements: list[str] = field(default_factory=list)
    removed_attributes: list[str] = field(default_factory=list)
    unwrapped_links: int = 0
    css_rewrites: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.removed_elements or self.removed_attributes or self.unwrapped_links or self.css_rewrites
        )


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""  # comments / processing instructions (already removed by the parser)
    return etree.QName(tag).localname


def _squash(value: str) -> str:
    return _INVISIBLE.sub("", value)


def css_is_safe(css: str, *, allow_font_data: bool = False) -> bool:
    """True when nothing in ``css`` can fetch or hide: every ``url()`` is a complete local reference
    (or, in our own ``@font-face``, embedded WOFF2), and there is no ``@import``, escape or
    string-URL function. Shared by the sanitiser and the checker so they cannot drift apart."""
    if "\\" in css or "@import" in css.lower() or _CSS_FETCHING.search(css):
        return False
    local = 0
    for m in _CSS_URL.finditer(css):
        target = _squash(m.group(2))
        if not (target.startswith("#") or (allow_font_data and target.startswith("data:font/woff2;base64,"))):
            return False
        local += 1
    # Every url( must have been a complete, local match: an unterminated url( runs to the end of the
    # sheet in CSS and still fetches.
    return _squash(css).lower().count("url(") == local


def _clean_css(css: str, report: SanitizeReport) -> str:
    original = css
    # Escapes first: deleting a backslash can complete a token (ur\l( -> url( ), so it must happen
    # before the url() rewrite, never after. Escapes can spell anything; we do not reason about them.
    css = _CSS_BAD.sub("", css)
    css = _CSS_IMPORT.sub("", css)

    def _url(m: re.Match[str]) -> str:
        target = _squash(m.group(2))
        return f"url({target})" if target.startswith("#") else "none"

    css = _CSS_URL.sub(_url, css)
    if not css_is_safe(css):
        # Fail closed: an unterminated url( at the end of the sheet, or image-set("https://...").
        # Mermaid never emits either, so the sheet is hostile; an unstyled diagram is the safe result.
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
    local = etree.QName(name).localname if name.startswith("{") else name
    if local.lower().startswith("on"):
        return False
    squashed = _squash(value)
    if name in _HREF_ATTRS:
        return squashed.startswith("#")
    if _URL_SCHEME.match(squashed):
        return False
    # Presentation attributes are CSS values too (fill="url(https://...)", filter, mask, clip-path,
    # marker-*). ``style`` is cleaned declaration by declaration instead of being dropped whole.
    return local == "style" or css_is_safe(value)


def sanitize_svg(svg: str | bytes) -> tuple[bytes, SanitizeReport]:
    """Return a sanitised, UTF-8 SVG document (no XML declaration) and what was changed."""
    raw = svg.encode("utf-8") if isinstance(svg, str) else svg
    if len(raw) > MAX_SVG_BYTES:
        raise UnsafeSvgError(f"SVG larger than {MAX_SVG_BYTES} bytes")
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in raw.lower():
        raise UnsafeSvgError("SVG must not contain a DTD or entity declarations")

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
        strip_cdata=True,
    )
    try:
        root = etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise UnsafeSvgError(f"SVG is not well-formed XML: {exc.msg}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise UnsafeSvgError("root element is not <svg> in the SVG namespace")

    report = SanitizeReport()
    # Materialise the walk first: we mutate the tree while visiting.
    for el in list(root.iter()):
        if not _still_attached(el, root):
            continue  # removed together with an ancestor
        local = _local(el.tag)
        ns = etree.QName(el.tag).namespace if isinstance(el.tag, str) else None
        if el is not root and (ns != SVG_NS or local in DANGEROUS_ELEMENTS or local not in ALLOWED_ELEMENTS):
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
                report.removed_attributes.append(f"{local}@{etree.QName(name).localname}")
                del el.attrib[name]
            elif etree.QName(name).localname == "style":
                cleaned = _clean_style_attr(value, report)
                if cleaned:
                    el.attrib[name] = cleaned
                else:
                    del el.attrib[name]
        if local == "style":
            # Browsers build the sheet from every text child, including text *after* a child element
            # (<style>a<g/>b</style> applies "b" too). Reduce the element to plain text, then clean it.
            sheet = "".join(el.itertext())
            for child in list(el):
                report.removed_elements.append(_local(child.tag) or "node")
                el.remove(child)
            el.text = _clean_css(sheet, report) if sheet else None

    out = etree.tostring(root, encoding="utf-8", xml_declaration=False)
    assert_svg_safe(out)  # the independent checker must agree, or nothing is returned
    return out, report


def _still_attached(el: etree._Element, root: etree._Element) -> bool:
    node: etree._Element | None = el
    while node is not None:
        if node is root:
            return True
        node = node.getparent()
    return False


def _remove_keep_tail(el: etree._Element) -> None:
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


def _unwrap_link(el: etree._Element) -> None:
    """Turn ``<a>`` into a plain ``<g>``: same children and transform, no link semantics."""
    el.tag = f"{{{SVG_NS}}}g"
    for name in list(el.attrib):
        if etree.QName(name).localname not in {"transform", "class", "id"}:
            del el.attrib[name]


def assert_svg_safe(svg: bytes | str) -> None:
    """Independent structural re-check (tests, pre-publish). Raises :class:`UnsafeSvgError`.

    Text content is not inspected: a label that *says* ``javascript:`` is inert text.
    """
    raw = svg.encode("utf-8") if isinstance(svg, str) else svg
    if b"<!doctype" in raw[:4096].lower() or b"<!entity" in raw.lower():
        raise UnsafeSvgError("DTD or entity declaration present")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(raw, parser=parser)
    for el in root.iter():
        if not isinstance(el.tag, str):
            raise UnsafeSvgError("comment or processing instruction present")
        qname = etree.QName(el.tag)
        if qname.namespace != SVG_NS or qname.localname not in ALLOWED_ELEMENTS:
            raise UnsafeSvgError(f"element not allowed: {qname.localname}")
        for name, value in el.attrib.items():
            local = etree.QName(name).localname
            squashed = _squash(value)
            if local.lower().startswith("on"):
                raise UnsafeSvgError(f"event handler attribute: {local}")
            if name in _HREF_ATTRS and not squashed.startswith("#"):
                raise UnsafeSvgError("non-local href")
            if _URL_SCHEME.match(squashed):
                raise UnsafeSvgError(f"URL-valued attribute: {local}")
            if not css_is_safe(value):  # style="" and presentation attributes (fill, filter, mask...)
                raise UnsafeSvgError(f"unsafe CSS value in attribute: {local}")
        if qname.localname == "style":
            if len(el):
                raise UnsafeSvgError("style element has child elements (text after them is CSS too)")
            if el.text and not css_is_safe(el.text, allow_font_data=True):
                raise UnsafeSvgError("unsafe CSS in style element (import, escape or non-local URL)")
