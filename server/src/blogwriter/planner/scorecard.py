"""The weekly quality scorecard (it replaces daily streaks; plan: "Tracking").

For one week (Monday to Sunday in the owner's zone): posts that went out, those carrying owner
signal (a story or POV entry, a quick take with the owner's own take, or a derivative of a
flagship that drew on the owner's material), the owner's replies and substantive comments
(engagement records) and the share of posts on a pillar. ``planner.scorecard`` (Mondays 07:00)
stores last week's on its plan and closes an approved plan; Today shows the current week live.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Filter, NotFound, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import find_docs, read_doc, update_doc
from blogwriter.domain.engagement import EngagementRecord
from blogwriter.domain.planner import Scorecard, WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.states import PLAN_MACHINE, PlanStatus
from blogwriter.domain.weeks import plan_id
from blogwriter.planner.slots import week_bounds

__all__ = ["ClosedWeek", "close_week", "compute_scorecard"]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("plan_id", "week_start", "posts", "closed")

_LIMIT: Final = 200


def _has_owner_signal(post: Post, articles: dict[str, Article]) -> bool:
    if post.story_ids or post.pov_ids or post.lane == "quick_take":
        return True
    if post.lane == "derivative" and post.article_id in articles:
        return bool(articles[post.article_id].owner_signals)
    return False


async def compute_scorecard(
    store: Store,
    *,
    week_start: str,
    zone: str,
    pillar_ids: Collection[str],
    now: datetime,
) -> Scorecard:
    """The scorecard of the week starting on Monday ``week_start`` (so far, for this week)."""
    start, end = week_bounds(week_start, zone)
    posted = await find_docs(
        store,
        Post,
        [Filter("postedAt", ">=", start), Filter("postedAt", "<", end)],
        limit=_LIMIT,
    )
    posts = [stored.doc for stored in posted]
    article_ids = sorted({p.article_id for p in posts if p.lane == "derivative" and p.article_id})
    articles: dict[str, Article] = {}
    if article_ids:
        found = await find_docs(store, Article, [Filter("id", "in", article_ids)])
        articles = {stored.doc.id: stored.doc for stored in found}
    records = await find_docs(
        store,
        EngagementRecord,
        [Filter("postedAt", ">=", start), Filter("postedAt", "<", end)],
        limit=_LIMIT,
    )
    replies = sum(record.doc.item_kind == "reply" for record in records)
    comments = sum(
        record.doc.item_kind == "comment" and record.doc.substantive for record in records
    )
    on_pillar = sum(post.pillar in pillar_ids for post in posts)
    return Scorecard(
        posts_published=len(posts),
        posts_with_owner_signal=sum(_has_owner_signal(post, articles) for post in posts),
        replies=replies,
        substantive_comments=comments,
        on_pillar_share=round(on_pillar / len(posts), 3) if posts else None,
        computed_at=to_iso(now),
    )


@dataclass(frozen=True, slots=True)
class ClosedWeek:
    week_start: str
    plan_id: str | None
    closed: bool
    scorecard: Scorecard


async def close_week(
    store: Store,
    *,
    week_start: str,
    zone: str,
    pillar_ids: Collection[str],
    now: datetime,
) -> ClosedWeek:
    """Store the week's scorecard on its plan and close an approved plan (``approved ->
    closed``). A week without a plan keeps no scorecard document (the scorecard view computes
    it); a plan that was never approved keeps its status. Running it again recomputes."""
    scorecard = await compute_scorecard(
        store, week_start=week_start, zone=zone, pillar_ids=pillar_ids, now=now
    )
    plan_doc_id = plan_id(week_start)
    stamp = to_iso(now)

    def mutate(plan: WeekPlan) -> WeekPlan:
        changes: dict[str, object] = {"scorecard": scorecard, "updated_at": stamp}
        if plan.status is PlanStatus.APPROVED:
            changes["status"] = PLAN_MACHINE.transition(plan.status, PlanStatus.CLOSED)
            changes["status_changed_at"] = stamp
        return plan.model_copy(update=changes)

    try:
        await read_doc(store, WeekPlan, plan_doc_id)
    except NotFound:
        return ClosedWeek(week_start, None, False, scorecard)
    stored = await update_doc(store, WeekPlan, plan_doc_id, mutate)
    closed = stored.doc.status is PlanStatus.CLOSED
    _logger.info(
        "planner_week_scored",
        extra={"plan_id": plan_doc_id, "posts": scorecard.posts_published, "closed": closed},
    )
    return ClosedWeek(week_start, plan_doc_id, closed, scorecard)
