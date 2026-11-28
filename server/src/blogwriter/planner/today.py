"""Today: the owner's action inbox, computed from documents on every request.

Nothing here is stored: each item is derived from the document that needs the owner (a
proposed plan, a pending interview, an article in review, a post due within two hours, a due
reminder...). Only ``reply``, ``snapshot`` and ``kit_confirm`` come from stored reminders. Item
ids are stable (``<kind>:<document id>``), titles are at most 80 characters, links are
same-origin paths, and items are sorted by priority, then due time.

Reads are summaries over bounded queries; a document that cannot be read is skipped (Today is
informational: the actions it links to re-check everything).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final

from pydantic import ValidationError

from blogwriter.backups import restore_drill_due
from blogwriter.db import Filter, NotFound, OrderBy, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.backups import BackupStatus
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import InvalidStoredDocument, find_docs, read_doc, read_singleton
from blogwriter.domain.golive import GONE_POSTS, slot_post_gone
from blogwriter.domain.planner import Reminder, WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import LinkedInConnection
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.states import (
    ArticleStatus,
    ConnectionStatus,
    InterviewStatus,
    KitState,
    PlanStatus,
    PostStatus,
    ReminderStatus,
)
from blogwriter.domain.stories import Interview
from blogwriter.domain.weeks import add_days, plan_id
from blogwriter.jobs.models import Job
from blogwriter.jobs.scheduler import ScheduleItem
from blogwriter.kb import owner_inputs
from blogwriter.planner.links import article_href, plan_href, post_href
from blogwriter.planner.missed import missed_offer
from blogwriter.planner.models import (
    MAX_TODAY_TITLE,
    PRIORITY_RANK,
    Priority,
    Today,
    TodayItem,
    TodayKind,
    TodayRefs,
    TodayWeek,
)
from blogwriter.planner.plan import PlanRules, clean_title, slot_refs
from blogwriter.planner.proposal import load_rules
from blogwriter.planner.reminders import dismissed_at, snapshot_logged
from blogwriter.planner.scorecard import compute_scorecard
from blogwriter.planner.slots import current_week_start, local_label, week_bounds

__all__ = ["PUBLISH_SOON", "build_today"]

PUBLISH_SOON: Final = timedelta(hours=2)
"""A one-click post whose slot is this close shows "tap to publish"."""
VETO_SOON: Final = timedelta(hours=48)
STUCK_JOBS: Final = timedelta(days=14)
_LIMIT: Final = 50
_LANE_LABELS: Final = {
    "derivative": "derivative",
    "native": "native",
    "quick_take": "quick-take",
    "repurpose": "repurposed",
}
_KIT_HANDLED: Final = frozenset({KitState.NATIVELY_SCHEDULED, KitState.POSTED})


def _title(prefix: str, name: str | None = None) -> str:
    text = f"{prefix}: {name}" if name else prefix
    return clean_title(text, limit=MAX_TODAY_TITLE) or prefix[:MAX_TODAY_TITLE]


def _item(
    kind: TodayKind,
    doc_id: str,
    title: str,
    href: str,
    *,
    priority: Priority = "normal",
    due_at: str | None = None,
    refs: TodayRefs | None = None,
) -> TodayItem:
    return TodayItem(
        id=f"{kind}:{doc_id}",
        kind=kind,
        title=_title(title),
        href=href,
        due_at=due_at,
        priority=priority,
        refs=refs or TodayRefs(),
    )


def _week_label(week_start: str) -> str:
    day = date.fromisoformat(week_start)
    return f"{day:%b} {day.day}"


# --------------------------------------------------------------------------------------------
# Plans and quick takes
# --------------------------------------------------------------------------------------------


async def _plan_items(store: Store, rules: PlanRules, *, now: datetime) -> list[TodayItem]:
    zone = rules.cadence.timezone
    week = current_week_start(now, zone)
    following = add_days(week, 7)
    plans = await find_docs(
        store,
        WeekPlan,
        [Filter("weekStart", ">=", week)],
        order_by=OrderBy("weekStart"),
        limit=8,
    )
    items: list[TodayItem] = []
    approved: list[WeekPlan] = []
    for stored in plans:
        plan = stored.doc
        if plan.status is PlanStatus.APPROVED and plan.week_start in (week, following):
            approved.append(plan)
        if plan.status is not PlanStatus.PROPOSED:
            continue
        if plan.mode == "away" and not plan.slots and plan.flagship is None:
            continue
        if plan.week_start == following:
            title = "Approve next week's plan"
        elif plan.week_start == week:
            title = "Approve this week's plan"
        else:
            title = f"Approve the plan for the week of {_week_label(plan.week_start)}"
        research = weekly_slot(plan.week_start, rules.cadence.research_start, plan.timezone)
        items.append(
            _item(
                "approve_plan",
                plan.id,
                title,
                plan_href(plan.week_start),
                priority="high",
                due_at=research.utc,
                refs=TodayRefs(week_start=plan.week_start),
            )
        )
    if approved:
        items.extend(await _take_items(store, approved, rules, now=now))
    return items


async def _take_items(
    store: Store, plans: Sequence[WeekPlan], rules: PlanRules, *, now: datetime
) -> list[TodayItem]:
    """Quick-take slots still waiting for the owner's take ("What's your take?"). A slot whose
    take was archived, expired or deleted waits again (``domain.golive.slot_post_gone``): the
    owner takes it anew without a plan edit."""
    grace = timedelta(minutes=rules.cadence.missed_slot_grace_minutes)
    zone = rules.cadence.timezone
    since = to_iso(now - timedelta(days=21))
    found = await find_docs(
        store,
        Post,
        [Filter("lane", "==", "quick_take"), Filter("createdAt", ">=", since)],
        limit=_LIMIT,
    )
    statuses = {stored.doc.id: stored.doc.status for stored in found}
    takes = [stored.doc for stored in found if stored.doc.status not in GONE_POSTS]
    linked = {(post.week_plan_id, post.plan_slot_id) for post in takes if post.plan_slot_id}
    unlinked_ideas = {post.idea_id for post in takes if post.plan_slot_id is None and post.idea_id}
    items: list[TodayItem] = []
    for plan in plans:
        for slot in plan.slots:
            if slot.lane != "quick_take":
                continue
            if parse_iso(slot.slot.utc) + grace <= now:
                continue
            if slot.post_id is not None and not await _take_gone(store, slot.post_id, statuses):
                continue
            refs = slot_refs(slot)
            # Filled: a take written for this slot (the post names it; a crash between the
            # post and the plan write leaves only that side), or one written from the slot's
            # idea before posts named their slot. Never "a take on the same day".
            if (plan.id, slot.id) in linked or (
                refs.idea_id is not None and refs.idea_id in unlinked_ideas
            ):
                continue
            query = f"take={slot.id}&week={plan.week_start}"
            href = (
                f"/calendar/ideas?idea={refs.idea_id}&{query}"
                if refs.idea_id is not None
                else f"/calendar/ideas?{query}"
            )
            items.append(
                _item(
                    "take_needed",
                    f"{plan.id}:{slot.id}",
                    f"What's your take? Quick-take slot {local_label(slot.slot.utc, zone)}",
                    href,
                    priority="high",
                    due_at=slot.slot.utc,
                    refs=TodayRefs(idea_id=refs.idea_id, week_start=plan.week_start),
                )
            )
    return items


async def _take_gone(store: Store, post_id: str, known: Mapping[str, PostStatus]) -> bool:
    """The post a planned slot names is archived, expired or deleted: the takes query's status
    when it saw the post, else a read. A post that cannot be read keeps its slot."""
    status = known.get(post_id)
    if status is not None:
        return status in GONE_POSTS
    try:
        return await slot_post_gone(store, post_id)
    except InvalidStoredDocument:
        return False


async def _away_kit_items(store: Store, rules: PlanRules, *, now: datetime) -> list[TodayItem]:
    """Away weeks: the kit posts still to schedule in LinkedIn, one item per week.

    Switching a week to ``away`` moves its approved one-click posts to the ready-to-post kit
    (``planner.approval``), so nobody is asked to tap at a slot while away: the owner schedules
    them natively in LinkedIn beforehand. The item names how many are left and opens the first
    one's kit; it leaves Today once every post of the week is scheduled (or posted)."""
    zone = rules.cadence.timezone
    week = current_week_start(now, zone)
    plans = await find_docs(
        store,
        WeekPlan,
        [Filter("weekStart", ">=", week)],
        order_by=OrderBy("weekStart"),
        limit=8,
    )
    items: list[TodayItem] = []
    for stored in plans:
        plan = stored.doc
        if plan.mode != "away" or plan.status is PlanStatus.CLOSED:
            continue
        start, end = week_bounds(plan.week_start, plan.timezone)
        found = await find_docs(
            store,
            Post,
            [
                Filter("status", "==", PostStatus.KIT.value),
                Filter("slot.utc", ">=", max(start, to_iso(now))),
                Filter("slot.utc", "<", end),
            ],
            limit=_LIMIT,
        )
        waiting = sorted(
            (
                post.doc
                for post in found
                if post.doc.slot is not None
                and (post.doc.kit is None or post.doc.kit.state not in _KIT_HANDLED)
            ),
            key=lambda post: (post.slot.utc if post.slot is not None else "", post.id),
        )
        first = waiting[0] if waiting else None
        if first is None or first.slot is None:
            continue
        count = len(waiting)
        noun = "post" if count == 1 else "posts"
        items.append(
            _item(
                "schedule_kit",
                plan.id,
                f"Away week of {_week_label(plan.week_start)}: schedule {count} {noun} in LinkedIn",
                post_href(first),
                priority="high",
                due_at=first.slot.utc,
                refs=TodayRefs(post_id=first.id, week_start=plan.week_start),
            )
        )
    return items


# --------------------------------------------------------------------------------------------
# Interviews and articles
# --------------------------------------------------------------------------------------------


async def _interview_items(store: Store) -> list[TodayItem]:
    found = await find_docs(
        store,
        Interview,
        [Filter("status", "in", [InterviewStatus.PENDING.value, InterviewStatus.EXPIRED.value])],
        limit=_LIMIT,
    )
    items: list[TodayItem] = []
    for stored in found:
        interview = stored.doc
        open_questions = max(1, len(interview.questions) - len(interview.answers))
        noun = "question" if open_questions == 1 else "questions"
        piece = interview.article_id or interview.post_id
        href = f"/studio/{piece}/interview" if piece is not None else "/studio"
        items.append(
            _item(
                "answer_interview",
                interview.id,
                f"Answer {open_questions} quick {noun}",
                href,
                priority="high" if interview.status is InterviewStatus.PENDING else "normal",
                due_at=interview.due_at,
                refs=TodayRefs(
                    interview_id=interview.id,
                    article_id=interview.article_id,
                    post_id=interview.post_id,
                ),
            )
        )
    return items


async def _article_items(store: Store, *, now: datetime, zone: str) -> list[TodayItem]:
    stamp = to_iso(now)
    attention = await find_docs(
        store,
        Article,
        [
            Filter(
                "status",
                "in",
                [ArticleStatus.IN_REVIEW.value, ArticleStatus.NEEDS_ATTENTION.value],
            )
        ],
        limit=_LIMIT,
    )
    pending = await find_docs(
        store,
        Article,
        [
            Filter("status", "==", ArticleStatus.APPROVED.value),
            Filter("publish.auto", "==", True),
            Filter("publish.publishAt", ">", stamp),
        ],
        limit=_LIMIT,
    )
    items: list[TodayItem] = []
    for stored in attention:
        article = stored.doc
        if article.purpose != "flagship":
            continue
        name = article.meta.title or "your article"
        refs = TodayRefs(article_id=article.id)
        if article.status is ArticleStatus.NEEDS_ATTENTION:
            items.append(
                _item(
                    "needs_attention",
                    article.id,
                    _title("Needs you", name),
                    article_href(article.id),
                    priority="high",
                    refs=refs,
                )
            )
        elif article.last_error == "publish_late":
            items.append(
                _item(
                    "review_article",
                    article.id,
                    "Publish time passed: approve again",
                    article_href(article.id),
                    priority="high",
                    refs=refs,
                )
            )
        else:
            items.append(
                _item(
                    "review_article",
                    article.id,
                    _title("Review", name),
                    article_href(article.id),
                    refs=refs,
                )
            )
    for stored in pending:
        article = stored.doc
        publish_at = article.publish.publish_at
        if publish_at is None or article.purpose != "flagship":
            continue
        soon = parse_iso(publish_at) - now < VETO_SOON
        items.append(
            _item(
                "veto_window",
                article.id,
                f"Auto-publishes {local_label(publish_at, zone)}: review or veto",
                article_href(article.id),
                priority="high" if soon else "normal",
                due_at=publish_at,
                refs=TodayRefs(article_id=article.id),
            )
        )
    return items


# --------------------------------------------------------------------------------------------
# Posts
# --------------------------------------------------------------------------------------------


async def _post_items(store: Store, rules: PlanRules, *, now: datetime) -> list[TodayItem]:
    cadence = rules.cadence
    zone = cadence.timezone
    grace = timedelta(minutes=cadence.missed_slot_grace_minutes)
    found = await find_docs(
        store,
        Post,
        [
            Filter(
                "status",
                "in",
                [
                    PostStatus.DRAFT.value,
                    PostStatus.APPROVED.value,
                    PostStatus.STAGED.value,
                    PostStatus.MISSED.value,
                    PostStatus.NEEDS_ATTENTION.value,
                    PostStatus.KIT.value,
                ],
            )
        ],
        limit=_LIMIT * 2,
    )
    items: list[TodayItem] = []
    for stored in found:
        post = stored.doc
        lane = _LANE_LABELS.get(post.lane, post.lane)
        refs = TodayRefs(post_id=post.id, article_id=post.article_id)
        slot = post.slot
        when = local_label(slot.utc, zone) if slot is not None else None
        match post.status:
            case PostStatus.DRAFT:
                soon = slot is not None and parse_iso(slot.utc) - now < timedelta(hours=24)
                title = f"Approve the {lane} post" + (f" for {when}" if when else "")
                items.append(
                    _item(
                        "approve_post",
                        post.id,
                        title,
                        post_href(post, "posts"),
                        priority="high" if soon else "normal",
                        due_at=slot.utc if slot is not None else None,
                        refs=refs,
                    )
                )
            case PostStatus.APPROVED | PostStatus.STAGED | PostStatus.KIT:
                if slot is None:
                    continue
                kit = post.status is PostStatus.KIT
                if kit and post.kit is not None and post.kit.state in _KIT_HANDLED:
                    continue  # scheduled in LinkedIn (LinkedIn posts it) or already posted
                if not kit and post.go_live_mode != "one_click":
                    continue
                moment = parse_iso(slot.utc)
                if not (moment - PUBLISH_SOON <= now < moment + grace):
                    continue
                label = local_label(slot.utc, zone)
                action = "post it from the kit" if kit else "tap to publish"
                title = (
                    f"Post at {label}: {action}"
                    if now < moment
                    else f"Your {label} slot is open: {action}"
                )
                items.append(
                    _item(
                        "publish_post",
                        post.id,
                        title,
                        post_href(post),
                        priority="urgent",
                        due_at=slot.utc,
                        refs=refs,
                    )
                )
            case PostStatus.MISSED:
                offer = await missed_offer(store, post, cadence, now=now)
                label = when or "slot"
                if offer is not None:
                    title = f"Missed {label}. Next free slot: {local_label(offer.utc, zone)}"
                elif post.time_sensitive:
                    title = f"Missed {label}. It expires before the next free slot"
                else:
                    title = f"Missed {label}. Pick a new time"
                items.append(
                    _item(
                        "missed_slot",
                        post.id,
                        title,
                        post_href(post),
                        priority="high",
                        due_at=offer.utc if offer is not None else None,
                        refs=refs,
                    )
                )
            case PostStatus.NEEDS_ATTENTION:
                attempt = post.intent is not None and post.intent.status == "unknown"
                # Only a publish attempt whose answer never came asks about LinkedIn; anything
                # else (a failed generation) is fixed on the post itself.
                items.append(
                    _item(
                        "needs_attention",
                        post.id,
                        "Check LinkedIn: did your post go out?"
                        if attempt
                        else "Post generation failed: regenerate or archive it",
                        post_href(post) if attempt else post_href(post, "posts"),
                        priority="high",
                        refs=refs,
                    )
                )
            case _:
                continue
    return items


# --------------------------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------------------------


async def _reminder_items(store: Store, *, now: datetime) -> list[TodayItem]:
    stamp = to_iso(now)
    found = await find_docs(
        store,
        Reminder,
        [
            Filter("status", "in", [ReminderStatus.PENDING.value, ReminderStatus.SENT.value]),
            Filter("dueAt", "<=", stamp),
            Filter("expiresAt", ">", stamp),
        ],
        limit=_LIMIT,
    )
    reminders = [stored.doc for stored in found if dismissed_at(stored.doc) is None]
    post_ids = sorted({r.target_id for r in reminders if r.target_kind == "post" and r.target_id})
    posts: dict[str, Post] = {}
    if post_ids:
        found_posts = await find_docs(store, Post, [Filter("id", "in", post_ids)])
        posts = {stored.doc.id: stored.doc for stored in found_posts}
    items: list[TodayItem] = []
    for reminder in reminders:
        post = posts.get(reminder.target_id or "")
        if post is None or post.status in (PostStatus.ARCHIVED, PostStatus.DELETED):
            continue
        if reminder.reminder_kind == "snapshot" and snapshot_logged(post, reminder):
            continue
        if reminder.reminder_kind == "kit_confirm" and (
            post.status is not PostStatus.KIT
            or (post.kit is not None and post.kit.state is KitState.POSTED)
        ):
            continue
        priority: Priority = "normal" if reminder.reminder_kind == "snapshot" else "high"
        items.append(
            TodayItem(
                id=f"{reminder.reminder_kind}:{reminder.id}",
                kind=reminder.reminder_kind,
                title=reminder.title,
                href=reminder.url,
                due_at=reminder.due_at,
                priority=priority,
                refs=TodayRefs(reminder_id=reminder.id, post_id=post.id),
            )
        )
    return items


# --------------------------------------------------------------------------------------------
# System: stuck jobs, schedules waiting, LinkedIn, owner inputs, restore drill
# --------------------------------------------------------------------------------------------


async def _job_items(store: Store, *, now: datetime) -> list[TodayItem]:
    since = to_iso(now - STUCK_JOBS)
    raws = await store.find(
        "jobs",
        [
            Filter("kind", "==", "job"),
            Filter("status", "==", "quarantined"),
            Filter("updatedAt", ">=", since),
        ],
        limit=20,
    )
    items: list[TodayItem] = []
    for raw in raws:
        try:
            job = Job.from_body(raw.body)
        except ValidationError:
            continue
        items.append(
            _item(
                "needs_attention",
                job.id,
                f"A {job.type} job is stuck: retry it",
                f"/settings/system?job={job.id}",
                priority="high",
                refs=TodayRefs(job_id=job.id),
            )
        )
    return items


async def _schedule_items(store: Store) -> list[TodayItem]:
    raws = await store.find(
        "jobs",
        [Filter("kind", "==", "schedule"), Filter("status", "==", "needs_confirmation")],
        limit=20,
    )
    items: list[TodayItem] = []
    for raw in raws:
        try:
            item = ScheduleItem.from_body(raw.body)
        except ValidationError:
            continue
        items.append(
            _item(
                "schedule_confirm",
                item.id,
                _title("Confirm a missed run", item.name),
                f"/settings/slots?schedule={item.id}",
                priority="high",
                due_at=item.pending_run_at,
                refs=TodayRefs(schedule_id=item.id),
            )
        )
    return items


async def _connection_items(store: Store, *, now: datetime) -> list[TodayItem]:
    connection, _ = await read_singleton(store, LinkedInConnection, now=to_iso(now))
    if connection.status is ConnectionStatus.NEEDS_RECONNECT:
        title, priority = "Reconnect LinkedIn: one-click publishing is off", "urgent"
    elif connection.status is ConnectionStatus.CONNECTED and connection.banner_due(now):
        title = f"LinkedIn token day {connection.token_day(now)}: reconnect before day 60"
        priority = "high"
    else:
        return []
    return [
        _item(
            "reconnect_linkedin",
            connection.id,
            title,
            "/settings/integrations",
            priority=priority,
        )
    ]


async def _owner_input_items(store: Store, *, now: datetime) -> list[TodayItem]:
    inputs = await owner_inputs(store, now=now)
    return [
        _item("owner_input", item.code, item.label, item.href, priority="low")
        for item in inputs.items
        if not item.done
    ]


async def _restore_items(store: Store, *, now: datetime) -> list[TodayItem]:
    status, _ = await read_singleton(store, BackupStatus, now=to_iso(now))
    if not restore_drill_due(status, now=now):
        return []
    return [
        _item(
            "restore_drill",
            status.id,
            "Run the monthly restore drill",
            "/settings/system?backups=restore-drill",
            priority="low",
        )
    ]


# --------------------------------------------------------------------------------------------
# Today
# --------------------------------------------------------------------------------------------


async def build_today(store: Store, *, now: datetime) -> Today:
    """``GET /api/today``: the week (mode, plan status, this week's scorecard so far) and the
    items, most urgent first."""
    rules = await load_rules(store, now=now)
    cadence = rules.cadence
    zone = cadence.timezone
    week = current_week_start(now, zone)
    try:
        plan: WeekPlan | None = (await read_doc(store, WeekPlan, plan_id(week))).doc
    except NotFound:
        plan = None
    groups = await asyncio.gather(
        _plan_items(store, rules, now=now),
        _interview_items(store),
        _article_items(store, now=now, zone=zone),
        _post_items(store, rules, now=now),
        _away_kit_items(store, rules, now=now),
        _reminder_items(store, now=now),
        _job_items(store, now=now),
        _schedule_items(store),
        _connection_items(store, now=now),
        _owner_input_items(store, now=now),
        _restore_items(store, now=now),
    )
    items = [item for group in groups for item in group]
    items.sort(key=lambda item: (PRIORITY_RANK[item.priority], item.due_at or "~", item.id))
    scorecard = await compute_scorecard(
        store, week_start=week, zone=zone, pillar_ids=rules.pillar_ids, now=now
    )
    return Today(
        generated_at=to_iso(now),
        week=TodayWeek(
            week_start=week,
            mode=plan.mode if plan is not None else cadence.default_mode,
            plan_status=plan.status if plan is not None else None,
            scorecard=scorecard,
        ),
        items=items,
    )
