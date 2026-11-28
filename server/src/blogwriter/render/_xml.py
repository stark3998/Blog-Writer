"""A typed facade over the few lxml operations the render package uses.

lxml ships no type information (``types-lxml`` is not a dependency), so strict pyright sees every
lxml call as unknown. This module is the one place that touches the untyped API; everything else
works with :class:`XmlElement`. Parsing is always done with DTDs, entity resolution and network
access off.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator, MutableMapping
from typing import Any, Final, Protocol, cast

__all__ = [
    "XmlElement",
    "XmlSyntaxError",
    "new_element",
    "parse_xml",
    "split_name",
    "to_bytes",
]

_etree: Final[Any] = importlib.import_module("lxml.etree")
_LXML_SYNTAX_ERROR: Final[type[Exception]] = _etree.XMLSyntaxError


class XmlSyntaxError(ValueError):
    """The document is not well-formed XML. ``msg`` is lxml's short description."""

    def __init__(self, msg: str) -> None:
        self.msg = msg
        super().__init__(msg)


class XmlElement(Protocol):
    """The subset of ``lxml.etree._Element`` used here."""

    tag: object
    """Clark-notation name (``{ns}local``) for elements; not a ``str`` for comments and PIs."""
    text: str | None
    tail: str | None

    @property
    def attrib(self) -> MutableMapping[str, str]: ...

    def get(self, key: str, default: str | None = None, /) -> str | None: ...

    def set(self, key: str, value: str, /) -> None: ...

    def iter(self, tag: str | None = None, /) -> Iterator[XmlElement]: ...

    def itertext(self) -> Iterator[str]: ...

    def getparent(self) -> XmlElement | None: ...

    def getprevious(self) -> XmlElement | None: ...

    def remove(self, element: XmlElement, /) -> None: ...

    def insert(self, index: int, element: XmlElement, /) -> None: ...

    def __iter__(self) -> Iterator[XmlElement]: ...

    def __len__(self) -> int: ...


def parse_xml(raw: bytes, *, strip_comments_and_pis: bool = False) -> XmlElement:
    """Parse ``raw`` with DTDs, entities and network access disabled (CDATA becomes text).

    ``strip_comments_and_pis`` drops comments and processing instructions (the sanitiser wants
    them gone; the checker wants to see them). Raises :class:`XmlSyntaxError`.
    """
    parser = _etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=strip_comments_and_pis,
        remove_pis=strip_comments_and_pis,
        strip_cdata=True,
    )
    try:
        root = _etree.fromstring(raw, parser=parser)
    except _LXML_SYNTAX_ERROR as exc:
        raise XmlSyntaxError(str(getattr(exc, "msg", "") or "syntax error")) from exc
    return cast(XmlElement, root)


def to_bytes(root: XmlElement) -> bytes:
    """Serialise as UTF-8 without an XML declaration."""
    out: object = _etree.tostring(root, encoding="utf-8", xml_declaration=False)
    if not isinstance(out, bytes):  # pragma: no cover - lxml always returns bytes here
        raise TypeError("lxml returned a non-bytes serialisation")
    return out


def new_element(tag: str) -> XmlElement:
    """A new, detached element (``tag`` in Clark notation)."""
    return cast(XmlElement, _etree.Element(tag))


def split_name(name: str) -> tuple[str | None, str]:
    """``{ns}local`` -> ``(ns, local)``; a name without a namespace -> ``(None, name)``."""
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        return namespace, local
    return None, name
