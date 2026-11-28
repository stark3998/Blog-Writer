"""Small rules shared by the publish routes and jobs: correction notes, push notices, paths.

Correction notes. A correction of a live article is approved with a note; the note is stored
on the article at approval (``Article.corrections``) and ships with the next publish. A note is
**pending** until a publish ships it: it was created after the article's last publication
(``publish.lastPublishedAt``). A re-approval replaces pending notes (a pull back then a new
approval never ships two notes for one correction), and the publish job dates pending notes
with the UTC day it publishes them, so every published note lies between ``publishedAt`` and
``updatedAt`` as the contract requires.

Push notices carry a title of at most 80 characters and a same-origin path, nothing else
(security rule 6); they are best effort (a failed push is logged, never fatal).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError

from blogwriter.core.logging import register_log_fields
from blogwriter.domain.articles import Article, Correction
from blogwriter.domain.ports import Notifier
from blogwriter.notify.payload import MAX_TITLE_CHARS, NotificationPayload
from blogwriter.publish.document import plain_line

__all__ = [
    "dated_corrections",
    "is_pending",
    "push_notice",
    "shipped_corrections",
    "studio_path",
]

_logger: Final = logging.getLogger("blogwriter.publish.notices")
register_log_fields("article_id")


def is_pending(correction: Correction, article: Article) -> bool:
    """Whether ``correction`` has not shipped yet (created after the last publication)."""
    last = article.publish.last_published_at
    return last is None or correction.created_at > last


def shipped_corrections(article: Article) -> list[Correction]:
    """The notes that already went out with a publish."""
    return [item for item in article.corrections if not is_pending(item, article)]


def dated_corrections(article: Article, now: datetime) -> list[Correction]:
    """All notes, the pending ones dated with ``now``'s UTC day (the day they ship)."""
    day = now.astimezone(UTC).date().isoformat()
    out: list[Correction] = []
    for item in article.corrections:
        if is_pending(item, article) and item.date != day:
            out.append(item.model_copy(update={"date": day}))
        else:
            out.append(item)
    return out


def studio_path(article_id: str, tab: str = "draft") -> str:
    """The article's Studio page (a same-origin path)."""
    return f"/studio/{article_id}/{tab}"


def _title(prefix: str, title: str | None) -> str:
    text = plain_line(title or "your article", MAX_TITLE_CHARS - len(prefix))
    return f"{prefix}{text}"


async def push_notice(
    notifier: Notifier, prefix: str, title: str | None, *, url: str, tag: str, article_id: str
) -> bool:
    """Push ``<prefix><title>`` (cut to 80 characters) with a same-origin link. Returns
    whether a push service accepted it; failures are logged (ids only), never raised."""
    try:
        payload = NotificationPayload(title=_title(prefix, title), url=url, tag=tag)
    except ValidationError:
        _logger.warning("publish_notice_invalid", extra={"article_id": article_id})
        return False
    try:
        result = await notifier.notify(payload)
    except Exception:  # best effort: a broken push channel never fails a publish
        _logger.warning("publish_notice_failed", extra={"article_id": article_id})
        return False
    return result.any_delivered
