"""Repurposing a post that went out (plan: "Post lanes", repurposing).

Two kinds of ``origin: repurpose`` idea name the post in ``repurposeOf``:

- ``flagship``: a post that beat the owner's trailing median becomes a **flagship candidate**
  (``suggestedLane: flagship``: the idea of an article);
- ``recut``: an evergreen post 8-12 weeks old (``LaneSettings.repurpose``) may be **re-cut with
  a new angle** (``suggestedLane: repurpose``, the post's format). The novelty check runs when
  the new post is linted (the near-duplicate check against the last 90 days of posts).

``analytics.suggest`` proposes both after its weekly numbers (:func:`propose_repurpose`); the
owner can also repurpose any posted post (``POST /api/ideas/repurpose``). Ids are derived from
the post and the kind, so proposing again returns the idea already there (whatever the owner did
with it: a dismissed suggestion is not proposed again). The title quotes the post's first line
only when the current blocklist clears it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from blogwriter.db import Conflict, Store
from blogwriter.domain.base import derived_id, to_iso
from blogwriter.domain.confidentiality import blocklist_hit
from blogwriter.domain.docstore import Stored, create_doc, read_doc, read_singleton
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, CadenceSettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import IdeaStatus, PostStatus
from blogwriter.domain.weeks import local_date_time

__all__ = ["RepurposeKind", "Repurposed", "propose_repurpose", "repurpose_idea_id"]

type RepurposeKind = Literal["flagship", "recut"]
_TITLE_CHARS: Final = 120


@dataclass(frozen=True, slots=True)
class Repurposed:
    stored: Stored[Idea]
    created: bool
    """False: the idea was there already (proposed before)."""


def repurpose_idea_id(post_id: str, kind: RepurposeKind) -> str:
    """The one idea of each kind a post gets."""
    return derived_id("idea", "repurpose", kind, post_id)


def _first_line(post: Post) -> str:
    text = post.final_text or post.display_text
    for line in text.splitlines():
        words = " ".join(line.split())
        if words:
            return words if len(words) <= _TITLE_CHARS else words[: _TITLE_CHARS - 1] + "…"
    return ""


def _title(post: Post, kind: RepurposeKind, blocklist: Blocklist, day: str) -> str:
    line = _first_line(post)
    if line and not blocklist_hit([line], blocklist):
        return f"Flagship candidate: {line}" if kind == "flagship" else f"Re-cut: {line}"
    if kind == "flagship":
        return f"Flagship candidate: your post of {day} beat your median"
    return f"Re-cut your post of {day} with a new angle"


async def propose_repurpose(
    store: Store, post: Post, *, kind: RepurposeKind, now: datetime
) -> Repurposed:
    """The ``kind`` repurpose idea of a posted ``post`` (created once; ``ValueError`` when the
    post has not gone out)."""
    if post.status is not PostStatus.POSTED:
        raise ValueError("only a post that went out can be repurposed")
    idea_id = repurpose_idea_id(post.id, kind)
    stamp = to_iso(now)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    went_live = post.posted_at or post.created_at
    day = local_date_time(went_live, cadence.timezone)[0]
    idea = Idea(
        id=idea_id,
        created_at=stamp,
        updated_at=stamp,
        status=IdeaStatus.NEW,
        status_changed_at=stamp,
        origin="repurpose",
        title=_title(post, kind, blocklist, day),
        pillar=post.pillar or None,
        suggested_lane="flagship" if kind == "flagship" else "repurpose",
        suggested_format=None if kind == "flagship" else post.format,
        repurpose_of=post.id,
    )
    try:
        return Repurposed(await create_doc(store, idea), created=True)
    except Conflict:
        return Repurposed(await read_doc(store, Idea, idea_id), created=False)
