"""Same-origin paths the planner links to (Today items, push notifications, reminders).

Routes follow the web map (contract section 10): a piece is an article (``/studio/art-...``) or a
standalone post (``/studio/post-...``, native and quick-take lanes); a derivative post opens
under its article. Paths carry ids only, never content.
"""

from __future__ import annotations

from typing import Literal

from blogwriter.domain.posts import Post

__all__ = [
    "article_href",
    "engage_href",
    "plan_href",
    "post_href",
    "snapshot_href",
]

type StudioTab = Literal["brief", "interview", "draft", "posts", "go-live"]


def article_href(article_id: str, tab: StudioTab = "draft") -> str:
    return f"/studio/{article_id}/{tab}"


def post_href(post: Post, tab: StudioTab = "go-live") -> str:
    """The post's page: under its article for derivatives, else its own piece."""
    piece = post.article_id if post.lane == "derivative" and post.article_id else post.id
    return f"/studio/{piece}/{tab}?post={post.id}"


def plan_href(week_start: str) -> str:
    return f"/calendar/plan?week={week_start}"


def engage_href(post_id: str) -> str:
    """The Engage panel in Today, for replies on one post."""
    return f"/today?engage={post_id}"


def snapshot_href(post_id: str) -> str:
    """The snapshot form in Today, for one post."""
    return f"/today?snapshot={post_id}"
