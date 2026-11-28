"""The blocklist again, at the moment an article leaves the app (security rule 1.9.5).

Gate 3 checked the article against the blocklist **as it was when the gates ran**; the owner may
add a term later. A gate report is keyed on the content only, so approving, publishing and
previewing re-check the current blocklist on what actually ships. It is deterministic and local
(``domain.confidentiality``): the terms never leave the process, findings name them by id.

- :func:`article_texts`: what an article sends out, by field: the composed document
  (``compose_document``: title, excerpt, slug, tags, series and the body), the media alt texts and
  names, the correction notes and the footer disclaimer. The approve route and the ``check``
  step of ``publish.article`` screen it (``check_texts``: ``BlocklistNotConfigured`` when the
  owner never saved the list, ``ConfidentialText`` on a hit).
- :func:`document_texts`: every text value of a built published-blogs document (the HTML as
  text), screened right before the ``upsert`` writes it (the brief's citation titles and
  publishers ship there too).
- :func:`cleared_title`: an article title for a push notice only when the current blocklist
  clears it (else the notice names no title).
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Final, cast

from blogwriter.domain.articles import Article, Correction
from blogwriter.domain.compose import FrontmatterError, MetaIncomplete, compose_document
from blogwriter.domain.confidentiality import blocklist_hit
from blogwriter.domain.settings import Blocklist

__all__ = ["article_texts", "cleared_title", "document_texts"]

_TAG_RE: Final = re.compile(r"<[^>]*>")
_SKIPPED_KEYS: Final = frozenset({"htmlContent"})


def _details(article: Article) -> dict[str, str]:
    meta = article.meta
    details: dict[str, str] = {}
    for name, value in (
        ("meta.title", meta.title),
        ("meta.excerpt", meta.excerpt),
        ("meta.slug", meta.slug),
        ("meta.tags", " ".join(meta.tags)),
    ):
        if value:
            details[name] = value
    if meta.series is not None:
        details["meta.series"] = f"{meta.series.id} {meta.series.title}"
    return details


def article_texts(
    article: Article,
    *,
    corrections: Sequence[Correction] | None = None,
    disclaimer: str | None = None,
    extra: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Every text ``article`` publishes, by field (CPU-bound on long bodies: use a thread).

    ``corrections`` defaults to the article's notes; ``extra`` adds texts about to be stored
    with it (a new correction note)."""
    texts: dict[str, str] = {}
    try:
        texts["document"] = compose_document(article.meta, article.markdown)
    except (MetaIncomplete, FrontmatterError):
        texts.update(_details(article))
        texts["markdown"] = article.markdown
    for index, media in enumerate(article.media):
        for name, value in (
            ("altText", media.alt_text),
            ("filename", media.filename),
            ("name", media.name),
        ):
            if value:
                texts[f"media[{index}].{name}"] = value
    notes = article.corrections if corrections is None else corrections
    for index, correction in enumerate(notes):
        texts[f"corrections[{index}]"] = correction.note
    if disclaimer:
        texts["disclaimer"] = disclaimer
    for name, value in (extra or {}).items():
        if value:
            texts[name] = value
    return texts


def _html_text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", fragment))


def _collect(value: object, path: str, out: dict[str, str]) -> None:
    if isinstance(value, str):
        if value:
            out[path] = value
    elif isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            _collect(item, f"{path}.{key}", out)
    elif isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            _collect(item, f"{path}[{index}]", out)


def document_texts(doc: Mapping[str, object]) -> dict[str, str]:
    """Every text value of a published-blogs document by JSON path, ``htmlContent`` as plain
    text (tags dropped, entities decoded), so markup never reads as a term."""
    out: dict[str, str] = {}
    for key, value in doc.items():
        if key in _SKIPPED_KEYS:
            continue
        _collect(value, key, out)
    content = doc.get("htmlContent")
    if isinstance(content, str) and content:
        out["htmlContent"] = _html_text(content)
    return out


def cleared_title(title: str | None, blocklist: Blocklist) -> str | None:
    """``title`` when the current blocklist clears it, else ``None`` (an unconfigured list
    clears nothing)."""
    if not title or blocklist_hit([title], blocklist):
        return None
    return title
