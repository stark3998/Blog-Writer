"""Article content rules: the 512 KiB cap, server-side normalization, word counts, frontmatter
checks on draft metadata and the clearance rule.

``Article.markdown`` is the article-dialect **body**; the frontmatter lives in
``Article.meta``. What ships is ``domain.compose.compose_document(meta, body)``: frontmatter, one
blank line, the body. The body is therefore normalized the way it ships, behind a frontmatter
head (:func:`prepare_body`), so a body that opens with a thematic break stays a thematic break
instead of reading as frontmatter. The canonical normalizer treats the head and the body
independently, so the head used here (a fixed, valid placeholder: drafts rarely have complete
metadata) does not change the body's canonical bytes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import MAX_ARTICLE_MARKDOWN_BYTES, ArticleMeta, OwnerSignalRef
from blogwriter.domain.common import MAX_FINDING_MESSAGE, Finding, from_publish_finding
from blogwriter.domain.compose import to_frontmatter, word_stats
from blogwriter.domain.docstore import InvalidStoredDocument, read_doc
from blogwriter.domain.hashing import article_sha256
from blogwriter.domain.stories import StoryEntry
from blogwriter.publish.dialect import SourceTooLargeError
from blogwriter.publish.frontmatter import Frontmatter, FrontmatterError, dump_frontmatter
from blogwriter.publish.normalize import normalize_markdown

__all__ = [
    "CASE_STUDY_FORMAT",
    "PLACEHOLDER_HEAD",
    "MarkdownTooLarge",
    "PreparedBody",
    "check_markdown_size",
    "clearance_required",
    "content_sha256",
    "meta_findings",
    "prepare_body",
]

CASE_STUDY_FORMAT: Final = "case-study"
"""The format pack whose articles always need the owner's clearance attestation."""

_PLACEHOLDERS: Final[dict[str, str]] = {
    "title": "Draft",
    "slug": "draft",
    "excerpt": "Draft",
    "pillar": "draft",
    "format": "draft",
}
PLACEHOLDER_HEAD: Final = dump_frontmatter(
    Frontmatter(
        title=_PLACEHOLDERS["title"],
        slug=_PLACEHOLDERS["slug"],
        excerpt=_PLACEHOLDERS["excerpt"],
        pillar=_PLACEHOLDERS["pillar"],
        format=_PLACEHOLDERS["format"],
        tags=(),
    )
)
"""A valid canonical frontmatter block the body is normalized behind (see the module doc)."""


class MarkdownTooLarge(ValueError):
    """The body is larger than 512 KiB of UTF-8 (413 ``markdown_too_large``)."""

    code: Final = "markdown_too_large"

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(
            f"the article body is {size} bytes; the limit is {MAX_ARTICLE_MARKDOWN_BYTES}"
        )


def check_markdown_size(markdown: str) -> None:
    """Raise :class:`MarkdownTooLarge` above :data:`MAX_ARTICLE_MARKDOWN_BYTES`."""
    size = len(markdown.encode("utf-8", errors="surrogatepass"))
    if size > MAX_ARTICLE_MARKDOWN_BYTES:
        raise MarkdownTooLarge(size)


@dataclass(frozen=True, slots=True)
class PreparedBody:
    """A body as the server stores it, with what the normalizer found."""

    text: str
    """The canonical body (unchanged when the normalizer fails closed)."""
    findings: tuple[Finding, ...]
    """The normalizer's errors (lint findings the canonical text can no longer show), with spans
    into the body the owner sent."""
    word_count: int
    reading_time_minutes: int


def prepare_body(markdown: str) -> PreparedBody:
    """Normalize ``markdown`` (a body) and count its words. Sync and CPU-bound: call it through
    ``asyncio.to_thread``. The caller checked the size (:func:`check_markdown_size`)."""
    composed = f"{PLACEHOLDER_HEAD}\n{markdown}"
    try:
        result = normalize_markdown(composed)
    except SourceTooLargeError:
        # The body fits the cap but not behind the head: normalize it alone.
        alone = normalize_markdown(markdown)
        text = alone.text
        findings = [from_publish_finding(error, markdown) for error in alone.errors]
    else:
        text = _body_of(result.text, composed=composed, original=markdown)
        start = len(PLACEHOLDER_HEAD) + 1
        findings = [
            from_publish_finding(error, composed, body_start=start) for error in result.errors
        ]
    words, minutes = word_stats(text)
    return PreparedBody(
        text=text, findings=tuple(findings), word_count=words, reading_time_minutes=minutes
    )


def _body_of(normalized: str, *, composed: str, original: str) -> str:
    """The body part of the normalizer's output for ``composed`` (head + blank line + body)."""
    if normalized == composed:
        return original  # already canonical, or the normalizer failed closed
    if not normalized.startswith(PLACEHOLDER_HEAD):  # pragma: no cover - the head is canonical
        raise RuntimeError("the normalizer rewrote the placeholder frontmatter")
    rest = normalized[len(PLACEHOLDER_HEAD) :]
    return rest.removeprefix("\n")


def meta_findings(meta: ArticleMeta) -> list[Finding]:
    """Frontmatter rules the set metadata fields break (``<`` or line breaks in a title...).

    Unset required fields are not findings (drafts are incomplete); the export, the gates and
    the publish job refuse incomplete metadata with ``meta_incomplete``.
    """
    missing = meta.missing_for_publish()
    filled = meta.model_copy(update={name: _PLACEHOLDERS[name] for name in missing})
    try:
        to_frontmatter(filled)
    except FrontmatterError as exc:
        return [
            Finding(
                code="frontmatter-invalid",
                severity="block",
                message=f"{issue.path}: {issue.message}"[:MAX_FINDING_MESSAGE],
            )
            for issue in exc.issues
        ]
    return []


def content_sha256(meta: ArticleMeta, markdown: str) -> str:
    """``Article.compute_sha256`` for a meta and body that are not on an article yet."""
    return article_sha256(meta.model_dump(mode="json", by_alias=True), markdown)


async def clearance_required(
    store: Store, meta: ArticleMeta, owner_signals: Iterable[OwnerSignalRef]
) -> bool:
    """``clearance.required``: a case study, or any owner-signal story that is anonymized.

    Fails closed: a story that cannot be read (deleted, unreadable) counts as anonymized.
    """
    if meta.format == CASE_STUDY_FORMAT:
        return True
    story_ids = list(dict.fromkeys(ref.id for ref in owner_signals if ref.kind == "story"))
    for story_id in story_ids:
        try:
            story = await read_doc(store, StoryEntry, story_id)
        except (NotFound, InvalidStoredDocument):
            return True
        if story.doc.confidentiality == "anonymized":
            return True
    return False
