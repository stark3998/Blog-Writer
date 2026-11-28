"""AI illustration candidates on an article (``Article.illustrations``) and the owner's actions.

The illustrations job (``illustrations.generate``, package ``blogwriter.illustrations``) only
**attaches** candidates (:func:`attach_illustrations`): it never touches the body, the metadata,
``version`` or the hero, so a candidate is not content and the article may be in any state but
archived. Everything that makes a candidate part of what is published is an owner action with
a strict If-Match (a stale screen is refused, never merged), in owner states only (planned,
in_review, needs_attention; ``WriterConflict`` otherwise):

- :func:`choose_hero`: the candidate becomes the article's ``hero_image`` media, which the
  publish job copies to ``published/<slug>/hero-<hash>.<ext>`` for ``heroImage`` (replacing the
  typographic card; ``og:image`` stays the 1200x630 card). :func:`clear_hero` goes back to the
  card. Approval binds this: the approve route takes the article's etag, and nobody writes an
  approved article.
- :func:`insert_illustration`: a normal Markdown image ``![alt](/api/media/<name>)`` after a
  heading, saved through :func:`~blogwriter.articles.service.owner_save` (normalized, a new
  ``version``, so the content hash the gates and the approval use covers it). Publishing copies
  it to ``published/<slug>/img-<hash>.<ext>`` and rewrites it to ``/blog-media/<slug>/...``.
- :func:`edit_illustration_alt`: the candidate's alt text, and the same alt text wherever the
  image is used (the hero media, the body's image: a body change is a new version).
- :func:`discard_illustration`: removes an unused candidate (one that is the hero or in the
  body is refused: ``illustration_in_use``). Blobs are never deleted here: names are
  content-addressed and an older version may show the image (media garbage collection is a
  deferred item).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Final

from blogwriter.articles.service import SavedArticle, StaleArticle, owner_save
from blogwriter.core.logging import register_log_fields
from blogwriter.db import NotFound, PreconditionFailed, Store
from blogwriter.domain.articles import (
    MAX_ILLUSTRATION_ALT,
    MAX_ILLUSTRATIONS,
    Article,
    Illustration,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import DEFAULT_CAS_ATTEMPTS, Stored, read_doc, replace_doc
from blogwriter.domain.states import ArticleStatus, WriterConflict, check_article_writer

__all__ = [
    "MAX_MEDIA",
    "HeadingNotFound",
    "IllustrationInUse",
    "IllustrationInserted",
    "IllustrationNotFound",
    "InvalidAltText",
    "MediaFull",
    "attach_illustrations",
    "choose_hero",
    "clean_alt_text",
    "clear_hero",
    "discard_illustration",
    "edit_illustration_alt",
    "image_markdown",
    "insert_after_heading",
    "insert_illustration",
    "link_illustration_job",
    "markdown_headings",
]

MAX_MEDIA: Final = 50
"""``Article.media`` holds at most 50 references."""

_logger: Final = logging.getLogger("blogwriter.articles.illustrations")
register_log_fields("article_id", "illustration_id", "attached", "dropped")

_FENCE_RE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE: Final = re.compile(r"^ {0,3}(#{2,4})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_LINK_RE: Final = re.compile(r"!?\[((?:\\.|[^\]\\])*)\]\([^)]*\)")
_MARKUP_RE: Final = re.compile(r"[`*_~]")
_ESCAPE_RE: Final = re.compile(r"\\(.)")
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_ALT_SPECIAL_RE: Final = re.compile(r"([\\`*_<>&~!|])")


# --------------------------------------------------------------------------------------------
# Errors (the illustrations routes map them to problems)
# --------------------------------------------------------------------------------------------


class IllustrationNotFound(NotFound):
    """The article has no candidate with that id (404 ``not_found``)."""


class HeadingNotFound(ValueError):
    """The body has no h2-h4 heading with that text (422)."""

    code: ClassVar[str] = "heading_not_found"


class IllustrationInUse(ValueError):
    """The candidate is the hero or in the body: clear or remove it first (409)."""

    code: ClassVar[str] = "illustration_in_use"


class IllustrationInserted(ValueError):
    """The body already shows this image (409)."""

    code: ClassVar[str] = "illustration_inserted"


class MediaFull(ValueError):
    """The article already holds the most media references it may (409)."""

    code: ClassVar[str] = "too_many_media"


class InvalidAltText(ValueError):
    """Alt text that is empty once cleaned (422)."""

    code: ClassVar[str] = "invalid_alt_text"


# --------------------------------------------------------------------------------------------
# Markdown helpers (pure)
# --------------------------------------------------------------------------------------------


def clean_alt_text(text: str) -> str:
    """Alt text as one plain line: control characters and line breaks become spaces, runs of
    whitespace collapse, at most :data:`~blogwriter.domain.articles.MAX_ILLUSTRATION_ALT`
    characters. Raises :class:`InvalidAltText` when nothing is left."""
    flat = " ".join(_CONTROL_RE.sub(" ", text).split())
    if len(flat) > MAX_ILLUSTRATION_ALT:
        flat = flat[: MAX_ILLUSTRATION_ALT - 1].rstrip() + "…"
    if not flat:
        raise InvalidAltText("alt text is empty")
    return flat


def _plain(text: str) -> str:
    """A heading as comparable text: links become their text, escapes are undone, then markup
    characters and case are ignored (so ``Why \\*this\\*`` matches ``Why *this*``)."""
    text = _LINK_RE.sub(r"\1", text)
    text = _ESCAPE_RE.sub(r"\1", text)
    text = _MARKUP_RE.sub("", text)
    return " ".join(text.split()).casefold()


def _body_lines(markdown: str) -> list[tuple[int, str, bool]]:
    """``(index, line, in_code)`` for every line (fenced code blocks marked)."""
    out: list[tuple[int, str, bool]] = []
    fence: str | None = None
    for index, line in enumerate(markdown.split("\n")):
        opened = _FENCE_RE.match(line)
        if fence is not None:
            out.append((index, line, True))
            marker = opened.group(1) if opened is not None else ""
            if (
                marker
                and marker[0] == fence[0]
                and len(marker) >= len(fence)
                and not line.strip()[len(marker) :]
            ):
                fence = None
            continue
        if opened is not None:
            fence = opened.group(1)
            out.append((index, line, True))
            continue
        out.append((index, line, False))
    return out


def markdown_headings(markdown: str) -> list[str]:
    """The text of every h2-h4 heading of the body, in order (fenced code skipped)."""
    headings: list[str] = []
    for _, line, in_code in _body_lines(markdown):
        if in_code:
            continue
        found = _HEADING_RE.match(line)
        if found is not None:
            headings.append(found.group(2).strip())
    return headings


def image_markdown(alt_text: str, src: str) -> str:
    """``![alt](src)`` with the alt text escaped for the article dialect (brackets as
    ``&#91;``/``&#93;``, markup characters backslash-escaped), so it stays plain text."""
    escaped = _ALT_SPECIAL_RE.sub(r"\\\1", clean_alt_text(alt_text))
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    return f"![{escaped}]({src})"


def insert_after_heading(markdown: str, heading: str, block: str) -> str:
    """``block`` as its own paragraph right after the first h2-h4 heading whose plain text is
    ``heading`` (case and markup ignored). Raises :class:`HeadingNotFound`."""
    wanted = _plain(heading)
    lines = markdown.split("\n")
    for index, line, in_code in _body_lines(markdown):
        if in_code:
            continue
        found = _HEADING_RE.match(line)
        if found is not None and wanted and _plain(found.group(2)) == wanted:
            return "\n".join([*lines[: index + 1], "", block, "", *lines[index + 1 :]])
    raise HeadingNotFound("heading not found")


def _image_pattern(name: str) -> re.Pattern[str]:
    """``![alt](/api/media/<name> "title"?)``; the alt may hold escapes and one level of
    balanced brackets (the normalizer writes brackets as entities, but be lenient)."""
    src = re.escape(f"{MediaRef.URL_PREFIX}{name}")
    plain = r"\\.|[^\[\]\\]"
    alt = rf"(?:{plain}|\[(?:{plain})*\])*"
    return re.compile(rf'!\[({alt})\]\({src}((?:\s+"[^"]*")?)\)')


def _replace_alt(markdown: str, name: str, alt_text: str) -> str:
    """The body with the alt text of every ``/api/media/<name>`` image replaced."""
    pattern = _image_pattern(name)
    replacement = image_markdown(alt_text, f"{MediaRef.URL_PREFIX}{name}")

    def swap(match: re.Match[str]) -> str:
        title = match.group(2)
        return replacement[:-1] + title + ")" if title else replacement

    return pattern.sub(swap, markdown)


# --------------------------------------------------------------------------------------------
# Reads and writes
# --------------------------------------------------------------------------------------------


async def _owner_current(store: Store, article_id: str, if_match: str) -> Stored[Article]:
    """The article for an owner action: strict If-Match, owner states only."""
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    check_article_writer(current.doc.status, "owner")
    return current


def _candidate(article: Article, illustration_id: str) -> Illustration:
    found = article.illustration(illustration_id)
    if found is None:
        raise IllustrationNotFound(f"articles/{article.id}: no illustration {illustration_id}")
    return found


async def _replace(store: Store, changed: Article, if_match: str) -> Stored[Article]:
    try:
        return await replace_doc(store, changed, if_match=if_match)
    except PreconditionFailed:
        raise StaleArticle(await read_doc(store, Article, changed.id)) from None


async def link_illustration_job(
    store: Store, article_id: str, job_id: str, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Name the illustrations run that owns new candidates (``illustrationJobId``), with the
    owner's If-Match, before the run is enqueued. Not content: no new version."""
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    changed = current.doc.model_copy(
        update={"illustration_job_id": job_id, "updated_at": to_iso(now)}
    )
    return await _replace(store, changed, if_match)


def _trimmed(doc: Article, candidates: list[Illustration]) -> list[Illustration]:
    """At most ``MAX_ILLUSTRATIONS``: the oldest unused candidates go first."""
    kept = list(candidates)
    for item in list(kept):
        if len(kept) <= MAX_ILLUSTRATIONS:
            break
        if not doc.references_media(item.media.name):
            kept.remove(item)
    return kept[-MAX_ILLUSTRATIONS:] if len(kept) > MAX_ILLUSTRATIONS else kept


async def attach_illustrations(
    store: Store,
    article_id: str,
    candidates: Sequence[Illustration],
    *,
    now: datetime,
) -> Stored[Article]:
    """Add the job's checked candidates (CAS loop; any state but archived, which raises
    ``WriterConflict``). Idempotent: a candidate already on the article (same id) is kept as
    it is, so a resumed step attaches nothing twice. Never changes the body, the metadata,
    ``version`` or the hero."""
    stamp = to_iso(now)
    for _ in range(DEFAULT_CAS_ATTEMPTS):
        current = await read_doc(store, Article, article_id)
        doc = current.doc
        if doc.status is ArticleStatus.ARCHIVED:
            raise WriterConflict(ArticleStatus.ARCHIVED, "pipeline")
        known = {item.id for item in doc.illustrations}
        new = [item.model_copy(deep=True) for item in candidates if item.id not in known]
        if not new:
            return current
        merged = _trimmed(doc, [*doc.illustrations, *new])
        changed = doc.model_copy(deep=True, update={"illustrations": merged, "updated_at": stamp})
        try:
            stored = await replace_doc(store, changed, if_match=current.etag)
        except PreconditionFailed:
            continue
        _logger.info(
            "article_illustrations_attached",
            extra={"article_id": article_id, "attached": len(new)},
        )
        return stored
    raise PreconditionFailed(f"update articles/{article_id}: lost {DEFAULT_CAS_ATTEMPTS} CAS")


def _og_hashes(article: Article) -> set[str]:
    return {item.sha256 for item in article.media if item.role == "og_image"}


async def choose_hero(
    store: Store, article_id: str, illustration_id: str, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Make the candidate the hero image (the article's only ``hero_image`` media, with the
    candidate's alt text); the publish job uses it for ``heroImage``."""
    current = await _owner_current(store, article_id, if_match)
    doc = current.doc
    candidate = _candidate(doc, illustration_id)
    hero = candidate.media.model_copy(update={"role": "hero_image", "alt_text": candidate.alt_text})
    existing = doc.hero_media()
    if (
        existing is not None
        and existing.name == hero.name
        and existing.alt_text == hero.alt_text
        and sum(item.role == "hero_image" for item in doc.media) == 1
    ):
        return current
    media = [item for item in doc.media if item.role != "hero_image"]
    if len(media) >= MAX_MEDIA:
        raise MediaFull("too many media references")
    changed = doc.model_copy(
        deep=True,
        update={"media": [*media, hero], "updated_at": to_iso(now), "updated_by": "owner"},
    )
    stored = await _replace(store, changed, if_match)
    _logger.info(
        "article_hero_chosen", extra={"article_id": article_id, "illustration_id": candidate.id}
    )
    return stored


async def clear_hero(
    store: Store, article_id: str, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Back to the typographic card: drop every hero image that is not the pipeline's copy of
    the Open Graph card (the publish job then uses the card)."""
    current = await _owner_current(store, article_id, if_match)
    doc = current.doc
    cards = _og_hashes(doc)
    media = [item for item in doc.media if item.role != "hero_image" or item.sha256 in cards]
    if len(media) == len(doc.media):
        return current
    changed = doc.model_copy(
        deep=True, update={"media": media, "updated_at": to_iso(now), "updated_by": "owner"}
    )
    return await _replace(store, changed, if_match)


async def insert_illustration(
    store: Store,
    article_id: str,
    illustration_id: str,
    *,
    heading: str,
    if_match: str,
    now: datetime,
) -> SavedArticle:
    """Insert the candidate after ``heading`` as ``![alt](/api/media/<name>)`` (its own
    paragraph), through :func:`owner_save`: normalized, a new version, so the content hash
    (gates, approval) covers it. :class:`IllustrationInserted` when the body shows it already,
    :class:`HeadingNotFound` when no h2-h4 heading has that text."""
    current = await _owner_current(store, article_id, if_match)
    doc = current.doc
    candidate = _candidate(doc, illustration_id)
    src = candidate.media.url
    if src in doc.markdown:
        raise IllustrationInserted("the body already shows this image")
    block = image_markdown(candidate.alt_text, src)
    markdown = insert_after_heading(doc.markdown, heading, block)
    saved = await owner_save(
        store,
        article_id,
        if_match=current.etag,
        base_version=doc.version,
        meta=doc.meta,
        markdown=markdown,
        now=now,
    )
    _logger.info(
        "article_illustration_inserted",
        extra={"article_id": article_id, "illustration_id": candidate.id},
    )
    return saved


async def edit_illustration_alt(
    store: Store,
    article_id: str,
    illustration_id: str,
    alt_text: str,
    *,
    if_match: str,
    now: datetime,
) -> SavedArticle:
    """Set the candidate's alt text, and the same text on its hero media and on its image in
    the body (one write; a body change is a new version)."""
    current = await _owner_current(store, article_id, if_match)
    doc = current.doc
    candidate = _candidate(doc, illustration_id)
    alt = clean_alt_text(alt_text)
    name = candidate.media.name

    def update(article: Article) -> Article:
        illustrations = [
            item.model_copy(update={"alt_text": alt}) if item.id == candidate.id else item
            for item in article.illustrations
        ]
        media = [
            item.model_copy(update={"alt_text": alt})
            if item.role == "hero_image" and item.container == "media" and item.name == name
            else item
            for item in article.media
        ]
        return article.model_copy(update={"illustrations": illustrations, "media": media})

    markdown = _replace_alt(doc.markdown, name, alt)
    return await owner_save(
        store,
        article_id,
        if_match=current.etag,
        base_version=doc.version,
        meta=doc.meta,
        markdown=markdown,
        now=now,
        update=update,
    )


async def discard_illustration(
    store: Store, article_id: str, illustration_id: str, *, if_match: str, now: datetime
) -> Stored[Article]:
    """Remove an unused candidate (any state but archived). The hero or an image in the body
    raises :class:`IllustrationInUse`. The blob stays (see the module docstring)."""
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    doc = current.doc
    if doc.status is ArticleStatus.ARCHIVED:
        raise WriterConflict(ArticleStatus.ARCHIVED, "owner")
    candidate = _candidate(doc, illustration_id)
    if doc.references_media(candidate.media.name):
        raise IllustrationInUse("the illustration is the hero or in the body")
    remaining = [item for item in doc.illustrations if item.id != candidate.id]
    changed = doc.model_copy(
        deep=True,
        update={"illustrations": remaining, "updated_at": to_iso(now), "updated_by": "owner"},
    )
    stored = await _replace(store, changed, if_match)
    _logger.info(
        "article_illustration_discarded",
        extra={"article_id": article_id, "illustration_id": candidate.id},
    )
    return stored
