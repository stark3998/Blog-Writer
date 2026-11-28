"""The article dialect's markdown-it-py configuration, shared by the renderer, linter and
normalizer.

``MarkdownIt("commonmark", {"html": False})`` plus GFM tables, task lists
(``mdit_py_plugins.tasklists``), citations as footnotes (``mdit_py_plugins.footnote``, no inline
``^[...]`` footnotes) and strict ``:::note|tip|warning`` callouts (``mdit_py_plugins.container``).
Raw HTML, autolinking of bare URLs, typographic replacements, strikethrough and everything else
outside ``docs/v2/article-dialect.md`` stay literal text.

Instances are reentrant (state lives in ``env`` and the token list of each call); the cached ones
from :func:`dialect_parser` must never be mutated.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import override

from markdown_it import MarkdownIt
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from .dialect import CALLOUT_TYPES

__all__ = ["dialect_parser", "new_dialect_markdown"]


class _PermissiveMarkdownIt(MarkdownIt):
    """Keeps every link and image, with the destination exactly as decoded from the source.

    markdown-it drops ``javascript:``, ``vbscript:``, ``file:`` and most ``data:`` targets before
    any rule sees them (the construct renders as literal text) and percent-encodes the rest. The
    linter needs the refused links to report them, and the normalizer needs the destination as
    written to keep it.
    """

    @override
    def validateLink(self, url: str) -> bool:
        return True

    @override
    def normalizeLink(self, url: str) -> str:
        return url

    @override
    def normalizeLinkText(self, link: str) -> str:
        return link


def _exact_type(kind: str) -> Callable[[str, str], bool]:
    """Callout validation: the type and nothing else (``:::note Title`` is not a callout)."""

    def validate(params: str, markup: str) -> bool:
        return params.strip() == kind

    return validate


def new_dialect_markdown(
    *, join_text: bool = True, footnotes_to_end: bool = True, permissive_links: bool = False
) -> MarkdownIt:
    """A fresh dialect parser.

    - ``join_text=False`` keeps backslash escapes and entities as separate ``text_special``
      tokens, so callers can tell ``\\[^x\\]`` (escaped by the author) from a raw ``[^x]``.
    - ``footnotes_to_end=False`` leaves footnote definitions where they are written
      (``footnote_reference_open``/``_close``) instead of moving the referenced ones to a
      ``footnote_block`` at the end and dropping the rest.
    - ``permissive_links=True``: see :class:`_PermissiveMarkdownIt`.
    """
    options = {"html": False, "linkify": False, "typographer": False}
    md = (
        _PermissiveMarkdownIt("commonmark", options)
        if permissive_links
        else MarkdownIt("commonmark", options)
    )
    if not join_text:
        md.core.ruler.disable("text_join")
    md.enable("table")
    md.use(tasklists_plugin)
    md.use(footnote_plugin, inline=False, move_to_end=footnotes_to_end)
    for kind in CALLOUT_TYPES:
        md.use(container_plugin, name=kind, validate=_exact_type(kind))
    return md


@cache
def dialect_parser(
    *, join_text: bool = True, footnotes_to_end: bool = True, permissive_links: bool = False
) -> MarkdownIt:
    """A shared parser for :func:`new_dialect_markdown`'s options. Parse with it; never mutate
    it."""
    return new_dialect_markdown(
        join_text=join_text, footnotes_to_end=footnotes_to_end, permissive_links=permissive_links
    )
