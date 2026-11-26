"""The shared sanitizer profile, implemented with nh3 (ammonia).

Explicit allowlist: tags, attributes per tag, and a value rule for every
attribute that carries a URL, an id, a class or a style. ``sanitizer_profile()``
exports the same rules as data so the portfolio's ``rehype-sanitize`` schema
(``clobberPrefix: ''``) can be generated from, and tested against, this one.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

import nh3

PYGMENTS_CLASS_RE: Final = re.compile(r"^[a-z]{1,3}[0-9]?$")
"""Pygments short token classes (``k``, ``nf``, ``s2``, ...)."""
HEADING_ID_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_HTTPS_RE: Final = re.compile(r"^https://[^\s\\]+$")
_MAILTO_RE: Final = re.compile(r"^mailto:[^\s\\]+$")
_FRAGMENT_RE: Final = re.compile(r"^#[A-Za-z0-9._:-]+$")
# A single leading slash: "//host" and "/\host" are protocol-relative in browsers.
_SITE_RELATIVE_RE: Final = re.compile(r"^/(?![/\\])[^\s\\]*$")
_DIMENSION_RE: Final = re.compile(r"^[1-9][0-9]{0,4}$")
_START_RE: Final = re.compile(r"^[0-9]{1,9}$")
_DIAGRAM_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_TEXT_ALIGN: Final = frozenset({"text-align:left", "text-align:center", "text-align:right"})

ALLOWED_TAGS: Final = frozenset(
    {
        "a", "aside", "blockquote", "br", "code", "em", "figure", "h2", "h3", "h4", "hr", "img",
        "input", "li", "ol", "p", "pre", "section", "span", "strong", "sup", "table", "tbody",
        "td", "th", "thead", "tr", "ul",
    }
)  # fmt: skip

ALLOWED_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href", "title", "class", "id"}),
    "aside": frozenset({"class"}),
    "code": frozenset({"class"}),
    "figure": frozenset({"class", "data-diagram"}),
    "h2": frozenset({"id"}),
    "h3": frozenset({"id"}),
    "hr": frozenset({"class"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
    "input": frozenset({"type", "checked", "disabled", "class"}),
    "li": frozenset({"id", "class"}),
    "ol": frozenset({"start", "class"}),
    "pre": frozenset({"class"}),
    "section": frozenset({"class"}),
    "span": frozenset({"class"}),
    "sup": frozenset({"class"}),
    "td": frozenset({"style"}),
    "th": frozenset({"style"}),
    "ul": frozenset({"class"}),
}

CLASS_RULES: Final[dict[str, re.Pattern[str]]] = {
    "a": re.compile(r"^footnote-backref$"),
    "aside": re.compile(r"^callout(?:-(?:note|tip|warning))?$"),
    "code": re.compile(r"^language-[a-z0-9][a-z0-9+#._-]{0,31}$"),
    "figure": re.compile(r"^mermaid$"),
    "hr": re.compile(r"^footnotes-sep$"),
    "input": re.compile(r"^task-list-item-checkbox$"),
    "li": re.compile(r"^(?:task-list-item|footnote-item)$"),
    "ol": re.compile(r"^footnotes-list$"),
    "pre": re.compile(r"^(?:highlight|mermaid-source)$"),
    "section": re.compile(r"^footnotes$"),
    "span": PYGMENTS_CLASS_RE,
    "sup": re.compile(r"^footnote-ref$"),
    "ul": re.compile(r"^contains-task-list$"),
}

ID_RULES: Final[dict[str, re.Pattern[str]]] = {
    "h2": HEADING_ID_RE,
    "h3": HEADING_ID_RE,
    "li": re.compile(r"^fn[0-9]+$"),
    "a": re.compile(r"^fnref[0-9]+(?::[0-9]+)?$"),
}

URL_SCHEMES: Final = frozenset({"https", "mailto"})


def _keep_classes(tag: str, value: str) -> str | None:
    rule = CLASS_RULES.get(tag)
    if rule is None:
        return None
    kept = [name for name in value.split() if rule.fullmatch(name)]
    return " ".join(kept) if kept else None


def _allowed_href(value: str) -> bool:
    return any(
        pattern.fullmatch(value)
        for pattern in (_HTTPS_RE, _MAILTO_RE, _FRAGMENT_RE, _SITE_RELATIVE_RE)
    )


def _allowed_src(value: str) -> bool:
    return bool(_HTTPS_RE.fullmatch(value) or _SITE_RELATIVE_RE.fullmatch(value))


def attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """Value rules for the allowlisted attributes. ``None`` drops the attribute."""
    match attribute:
        case "class":
            return _keep_classes(tag, value)
        case "id":
            rule = ID_RULES.get(tag)
            return value if rule is not None and rule.fullmatch(value) else None
        case "href":
            return value if _allowed_href(value) else None
        case "src":
            return value if _allowed_src(value) else None
        case "width" | "height":
            return value if _DIMENSION_RE.fullmatch(value) else None
        case "start":
            return value if _START_RE.fullmatch(value) else None
        case "style":
            compact = value.replace(" ", "").rstrip(";").lower()
            return compact if compact in _TEXT_ALIGN else None
        case "type":
            return value if value == "checkbox" else None
        case "checked" | "disabled":
            return attribute
        case "data-diagram":
            return value if _DIAGRAM_RE.fullmatch(value) else None
        case "rel":
            # Added by nh3 itself (link_rel); never taken from the input.
            return value if tag == "a" else None
        case "title" | "alt":
            return value
        case _:
            return None


@lru_cache(maxsize=1)
def _cleaner() -> nh3.Cleaner:
    return nh3.Cleaner(
        tags=set(ALLOWED_TAGS),
        clean_content_tags={"script", "style", "template", "noscript", "iframe", "object"},
        attributes={tag: set(names) for tag, names in ALLOWED_ATTRIBUTES.items()},
        attribute_filter=attribute_filter,
        strip_comments=True,
        link_rel="noopener noreferrer",
        url_schemes=set(URL_SCHEMES),
        set_tag_attribute_values={"input": {"disabled": "disabled", "type": "checkbox"}},
    )


def sanitize_html(html: str) -> str:
    """Sanitize rendered article HTML with the shared profile."""
    return _cleaner().clean(html)


def sanitizer_profile() -> dict[str, object]:
    """The profile as JSON-serializable data (for the rehype-sanitize twin and review)."""
    return {
        "tags": sorted(ALLOWED_TAGS),
        "attributes": {tag: sorted(names) for tag, names in sorted(ALLOWED_ATTRIBUTES.items())},
        "classPatterns": {tag: rule.pattern for tag, rule in sorted(CLASS_RULES.items())},
        "idPatterns": {tag: rule.pattern for tag, rule in sorted(ID_RULES.items())},
        "urlPatterns": {
            "href": [
                _HTTPS_RE.pattern,
                _MAILTO_RE.pattern,
                _FRAGMENT_RE.pattern,
                _SITE_RELATIVE_RE.pattern,
            ],
            "src": [_HTTPS_RE.pattern, _SITE_RELATIVE_RE.pattern],
        },
        "valuePatterns": {
            "width": _DIMENSION_RE.pattern,
            "height": _DIMENSION_RE.pattern,
            "start": _START_RE.pattern,
            "data-diagram": _DIAGRAM_RE.pattern,
            "type": "^checkbox$",
        },
        "styleValues": sorted(_TEXT_ALIGN),
        "forcedAttributes": {"input": {"disabled": "disabled", "type": "checkbox"}},
        "linkRel": "noopener noreferrer",
        "dropWithContent": ["iframe", "noscript", "object", "script", "style", "template"],
        "clobberPrefix": "",
    }
