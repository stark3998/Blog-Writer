"""Small helpers on a post shared by the checks, the generator, staging and the routes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from blogwriter.domain.common import MAX_SERVED_BY, Finding, ServedByRecord
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Pillar

__all__ = [
    "OWNER_TAKE_FIELD",
    "add_served_by",
    "approved_subset",
    "finding_field",
    "first_line",
    "go_live_path",
    "located",
    "merge_reasons",
    "owner_take",
    "piece_id",
    "with_extra",
]

OWNER_TAKE_FIELD: Final = "ownerTake"
"""Quick takes: the owner's confirmed take typed with ``POST /api/posts`` (``takeText``).

Stored as an extra field of the post (domain models keep unknown fields) until the domain
model gains ``owner_take`` (domain change request in the W-B3 report)."""


def owner_take(post: Post) -> str | None:
    """The owner's confirmed take on a quick take, if one was typed."""
    value: object = (post.model_extra or {}).get(OWNER_TAKE_FIELD)
    return value if isinstance(value, str) and value.strip() else None


def with_extra(post: Post, **fields: object) -> Post:
    """A validated copy of ``post`` with extra (camelCase) fields set; ``None`` removes one."""
    body: dict[str, object] = dict(post.model_dump(by_alias=True))
    for name, value in fields.items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    return Post.model_validate(body)


def located(finding: Finding, field: str | None) -> Finding:
    """``finding`` with the post field it points into (``displayText``, ``slides[2]``...):
    ``Finding.field`` (the web highlights by field and span, like the ``confidential_text``
    problem's findings)."""
    if field is None:
        return finding
    body: dict[str, object] = dict(finding.model_dump(by_alias=True))
    body["field"] = field
    return Finding.model_validate(body)


def finding_field(finding: Finding) -> str | None:
    """The post field ``finding`` points into, if it says."""
    return finding.field


def first_line(text: str, *, limit: int = 140) -> str:
    """The hook as a list shows it: the first non-empty line, at most ``limit`` characters."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= limit else stripped[: limit - 1] + "…"
    return ""


def piece_id(post: Post) -> str:
    """The Studio piece a post belongs to: its article (derivatives) or the post itself."""
    return post.article_id if post.article_id is not None else post.id


def go_live_path(post: Post) -> str:
    """The go-live page of a post (push notifications open it)."""
    return f"/studio/{piece_id(post)}/go-live?post={post.id}"


def approved_subset(wanted: Iterable[str], pillar: Pillar | None) -> list[str]:
    """The hashtags of ``wanted`` that are on the pillar's approved list, in the pillar's
    spelling, without duplicates, at most three (leading ``#`` ignored, case-insensitive)."""
    if pillar is None:
        return []
    approved = {tag.casefold(): tag for tag in pillar.hashtags}
    out: list[str] = []
    for tag in wanted:
        canonical = approved.get(tag.strip().removeprefix("#").casefold())
        if canonical is not None and canonical not in out:
            out.append(canonical)
    return out[:3]


def add_served_by(
    existing: Sequence[ServedByRecord], record: ServedByRecord
) -> list[ServedByRecord]:
    """``existing`` plus ``record``, keeping the newest :data:`MAX_SERVED_BY`."""
    return [*existing, record][-MAX_SERVED_BY:]


def merge_reasons(existing: Sequence[str], new: Iterable[str]) -> list[str]:
    """Review-only reason codes, de-duplicated in order (at most 20)."""
    out = list(dict.fromkeys([*existing, *new]))
    return out[:20]
