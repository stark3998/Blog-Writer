"""The canonical article document: one function for every package that needs the exact bytes.

``Article.markdown`` is the article-dialect body and ``Article.meta`` holds the frontmatter
fields. Gate 3 (blocklist on the bytes that ship), gate 5 (dialect lint), the publish job
(render, upsert) and the Markdown export all call :func:`compose_document`, so the gates check
exactly what the publish job ships. Word counts come from :func:`word_stats`, which counts the
way the publish renderer does (the published document's ``wordCount`` and
``readingTimeMinutes``).

Import this module explicitly (it is not re-exported from :mod:`blogwriter.domain`): it pulls in
the publish package's YAML and markdown-it configuration. Both functions are CPU-bound for long
bodies; call them through ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from markdown_it.token import Token

from blogwriter.domain.articles import ArticleMeta, SeriesRef
from blogwriter.publish.frontmatter import (
    Frontmatter,
    FrontmatterError,
    Series,
    dump_frontmatter,
    validate_frontmatter,
)
from blogwriter.publish.parser import dialect_parser

__all__ = [
    "WORDS_PER_MINUTE",
    "FrontmatterError",
    "MetaIncomplete",
    "compose_document",
    "frontmatter_length",
    "meta_from_frontmatter",
    "to_frontmatter",
    "word_stats",
]


WORDS_PER_MINUTE: Final = 230
"""Reading speed (``blogwriter.publish.renderer.WORDS_PER_MINUTE``; a test keeps them equal)."""


class MetaIncomplete(ValueError):
    """The article lacks frontmatter fields a document needs (409 ``meta_incomplete``)."""

    code: Final = "meta_incomplete"

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing: tuple[str, ...] = tuple(missing)
        super().__init__("missing frontmatter fields: " + ", ".join(self.missing))


def to_frontmatter(meta: ArticleMeta) -> Frontmatter:
    """``meta`` as the dialect's frontmatter, checked by the dialect's own schema.

    Raises :class:`MetaIncomplete` when a required field is unset and
    :class:`~blogwriter.publish.frontmatter.FrontmatterError` when a value breaks the dialect
    (for example ``<`` or a line break in the title).
    """
    missing = meta.missing_for_publish()
    if missing:
        raise MetaIncomplete(missing)
    data: dict[str, object] = {
        "title": meta.title,
        "slug": meta.slug,
        "excerpt": meta.excerpt,
        "pillar": meta.pillar,
        "format": meta.format,
        "tags": list(meta.tags),
    }
    if meta.series is not None:
        series: dict[str, object] = {
            "id": meta.series.id,
            "title": meta.series.title,
            "part": str(meta.series.part),
        }
        if meta.series.total is not None:
            series["total"] = str(meta.series.total)
        data["series"] = series
    # The dialect's validator reads YAML scalars as strings (the failsafe schema), hence str().
    return validate_frontmatter(data)


def meta_from_frontmatter(frontmatter: Frontmatter) -> ArticleMeta:
    """The :class:`ArticleMeta` of parsed frontmatter (imports, tests)."""
    series = frontmatter.series
    return ArticleMeta(
        title=frontmatter.title,
        slug=frontmatter.slug,
        excerpt=frontmatter.excerpt,
        pillar=frontmatter.pillar,
        format=frontmatter.format,
        tags=list(frontmatter.tags),
        series=None if series is None else _series_ref(series),
    )


def _series_ref(series: Series) -> SeriesRef:
    return SeriesRef(id=series.id, title=series.title, part=series.part, total=series.total)


def compose_document(meta: ArticleMeta, body: str) -> str:
    """The canonical article: canonical frontmatter, one blank line, the body.

    The same join as ``blogwriter.publish.normalize.normalize_markdown``: composing the meta and
    the body of a normalized document gives back its exact bytes. Raises like
    :func:`to_frontmatter`.
    """
    head = dump_frontmatter(to_frontmatter(meta))
    return head if body == "" else f"{head}\n{body}"


def frontmatter_length(meta: ArticleMeta) -> int:
    """Where the body starts in :func:`compose_document` (for
    :func:`~blogwriter.domain.common.from_publish_finding`'s ``body_start``)."""
    return len(dump_frontmatter(to_frontmatter(meta))) + 1


def _inline_text(children: Sequence[Token] | None) -> str:
    parts: list[str] = []
    for child in children or ():
        if child.type in ("text", "code_inline", "text_special"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "image":
            parts.append(_inline_text(child.children))
    return "".join(parts)


def word_stats(body: str) -> tuple[int, int]:
    """``(words, reading minutes)`` of an article body, as the published document counts them.

    Words of prose: inline text outside code blocks and the footnotes section (the publish
    renderer's rule), 230 words per minute rounded up, at least 1 when there are words. A test
    pins it to ``render_article``'s ``word_count`` and ``reading_time_minutes``.
    """
    tokens = dialect_parser().parse(body, {})
    words = 0
    in_footnotes = False
    for token in tokens:
        if token.type == "footnote_block_open":
            in_footnotes = True
        elif token.type == "footnote_block_close":
            in_footnotes = False
        elif token.type == "inline" and not in_footnotes:
            words += len(_inline_text(token.children).split())
    minutes = max(1, math.ceil(words / WORDS_PER_MINUTE)) if words else 0
    return words, minutes
