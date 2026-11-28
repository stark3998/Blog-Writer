"""The exact bytes that ship: ``domain.compose.compose_document`` and where the body starts.

Gates never import articles: the canonical document comes from :mod:`blogwriter.domain.compose`,
the same function the publish job and the Markdown export use. When the frontmatter cannot be
composed yet (a draft without a slug, a title the dialect refuses) the body is still checked on
its own and gate 5 carries the blocking ``meta-incomplete`` / ``frontmatter-invalid`` finding,
so nothing publishes.
"""

from __future__ import annotations

from dataclasses import dataclass

from blogwriter.domain.articles import ArticleMeta
from blogwriter.domain.common import Finding
from blogwriter.domain.compose import (
    FrontmatterError,
    MetaIncomplete,
    compose_document,
    frontmatter_length,
)
from blogwriter.gates.text import TextView, markdown_view

__all__ = ["ArticleText", "prepare_text"]


@dataclass(frozen=True, slots=True)
class ArticleText:
    meta: ArticleMeta
    body: str
    document: str | None
    """``compose_document(meta, body)``, or ``None`` when the frontmatter cannot be composed."""
    body_start: int
    """Offset of the body in ``document`` (0 without a document)."""
    compose_error: Finding | None
    view: TextView
    """The body's prose view (offsets index the body)."""

    @property
    def checked(self) -> str:
        """What the byte-level checks read: the document, or the body alone."""
        return self.document if self.document is not None else self.body


def prepare_text(meta: ArticleMeta, body: str) -> ArticleText:
    """Compose the document and build the prose view (CPU-bound: call it in a thread)."""
    document: str | None = None
    body_start = 0
    error: Finding | None = None
    try:
        document = compose_document(meta, body)
        body_start = frontmatter_length(meta)
    except MetaIncomplete as exc:
        error = Finding(
            code="meta-incomplete",
            severity="block",
            message="Missing article details: " + ", ".join(exc.missing) + ".",
            overridable=False,
        )
    except FrontmatterError:
        error = Finding(
            code="frontmatter-invalid",
            severity="block",
            message="An article detail breaks the dialect (title, excerpt, tags or series).",
            overridable=False,
        )
    return ArticleText(
        meta=meta,
        body=body,
        document=document,
        body_start=body_start,
        compose_error=error,
        view=markdown_view(body),
    )
