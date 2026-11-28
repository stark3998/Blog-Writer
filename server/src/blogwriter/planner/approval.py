"""Owner actions on week plans: approve, edit (drag and drop) and the availability mode.

**Approve** (``proposed -> approved``, an owner action, so everything it starts runs as
``origin: owner``). Refused while a spacing rule blocks (409 ``slot_conflict``: two posts on one
local day, also against posts outside the plan) or a mix rule blocks (409 ``mix_rules``). Then:

- the flagship article is created ``planned`` (id :func:`flagship_article_id`: a double approval
  finds it) and its ``pipeline.article`` is enqueued for ``cadence.research_start`` of the
  plan's week (``pipeline_job_id(articleId, 1)``; ``set_links(pipelineJobId, pipelineRuns=1)``);
  its idea becomes ``used``;
- each native slot gets its post (``Post.id_for_plan_slot``, ``generating``, with the slot,
  the plan, and the slot's stories and POVs) and a ``posts.generate`` job;
- quick-take slots generate nothing: they are "What's your take?" Today items until the owner's
  take starts the post; derivative slots wait for the flagship (``posts.derive`` is the
  owner's action);
- an **away** week generates nothing at all.

Every create is retry-safe (derived ids, ``enqueue_once`` reuse), so approving again after an
edit only adds what is new. **Edit** moves the posts already in the plan (their slot follows,
and approved posts get new slot jobs); an edit that changes what approval generates (mode,
flagship, slots without posts) sends an approved plan back to ``proposed``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from blogwriter.articles import ArticleLinks, create_article, owner_save, set_links, transition
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, Filter, NotFound, PreconditionFailed, Store
from blogwriter.domain.articles import Article, ArticleMeta
from blogwriter.domain.base import derived_id, parse_iso, to_iso
from blogwriter.domain.common import Finding
from blogwriter.domain.deps import etag_mismatch
from blogwriter.domain.docstore import Stored, create_doc, find_docs, read_doc, replace_doc
from blogwriter.domain.golive import cancel_slot_jobs, default_go_live_mode
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import (
    PIPELINE_ARTICLE,
    POSTS_GENERATE,
    PipelineInput,
    PostJobInput,
    pipeline_job_id,
)
from blogwriter.domain.planner import FlagshipPlan, PlannedSlot, WeekPlan
from blogwriter.domain.ports import JobQueue
from blogwriter.domain.posts import Post, into_kit
from blogwriter.domain.settings import WeekMode
from blogwriter.domain.slots import weekly_slot
from blogwriter.domain.states import (
    PLAN_MACHINE,
    ArticleStatus,
    PlanStatus,
    PostStatus,
    TransitionError,
    WriterConflict,
)
from blogwriter.domain.weeks import add_days, plan_id
from blogwriter.planner.models import FlagshipIn, PlanEditIn
from blogwriter.planner.plan import (
    MAX_TITLE,
    MIX_BLOCKING,
    SPACING_BLOCKING,
    PlanRules,
    SlotRefs,
    clean_title,
    pillar_balance,
    plan_findings,
    slot_refs,
    with_refs,
)
from blogwriter.planner.proposal import load_rules
from blogwriter.planner.slots import (
    SCHEDULED_STATUSES,
    TakenSlot,
    check_free,
    check_move,
    local_day,
    move_post,
    slot_at,
    taken_slots,
    week_bounds,
)
from blogwriter.signals import mark_idea_used

__all__ = [
    "Approved",
    "approve_plan",
    "edit_plan",
    "flagship_article_id",
    "plan_taken",
    "post_generate_job_id",
    "set_mode",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("plan_id", "article_id", "post_id", "job_type", "posts")

_GONE: Final = frozenset({PostStatus.ARCHIVED, PostStatus.EXPIRED, PostStatus.DELETED})


def flagship_article_id(week_plan_id: str) -> str:
    """The flagship article a plan's approval creates (retry-safe; a domain helper
    ``Article.id_for_plan`` is requested)."""
    return derived_id("art", "plan", week_plan_id)


def post_generate_job_id(post_id: str) -> str:
    """The ``posts.generate`` job plan approval enqueues for a native slot's post."""
    return f"plangen-{post_id}"


def _plan_post_ids(plan: WeekPlan) -> set[str]:
    ids = {slot.post_id for slot in plan.slots if slot.post_id is not None}
    ids.update(Post.id_for_plan_slot(plan.id, slot.id) for slot in plan.slots)
    return ids


async def plan_taken(
    store: Store, plan: WeekPlan, rules: PlanRules, *, exclude: Sequence[str] = ()
) -> list[TakenSlot]:
    """Go-live times other posts hold around the plan's week (a day either side, for the
    under-24-hours warning); the plan's own posts are left out."""
    zone = plan.timezone
    start, _end = week_bounds(add_days(plan.week_start, -1), zone)
    _start, end = week_bounds(add_days(plan.week_start, 1), zone)
    since = parse_iso(start)
    taken = await taken_slots(
        store, rules.cadence, since=since, exclude={*_plan_post_ids(plan), *exclude}
    )
    return [item for item in taken if start <= item.slot.utc < end]


def _findings_json(findings: Sequence[Finding]) -> list[JsonValue]:
    return [
        finding.model_dump(mode="json", by_alias=True, exclude_none=True) for finding in findings
    ]


async def _current_plan(store: Store, week_start: str, if_match: str) -> Stored[WeekPlan]:
    current = await read_doc(store, WeekPlan, plan_id(week_start))
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


async def _replace_plan(store: Store, changed: WeekPlan, if_match: str) -> Stored[WeekPlan]:
    try:
        return await replace_doc(store, changed, if_match=if_match)
    except PreconditionFailed:
        raise etag_mismatch(await read_doc(store, WeekPlan, changed.id)) from None


# --------------------------------------------------------------------------------------------
# Approve
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Approved:
    plan: Stored[WeekPlan]
    article_id: str | None
    job_ids: list[str]
    post_ids: list[str]
    """The posts this approval started (native slots, ``generating``)."""


async def _sync_meta(
    store: Store, stored: Stored[Article], meta: ArticleMeta, now: datetime
) -> None:
    """A planned flagship follows a changed plan flagship (title, pillar, format)."""
    article = stored.doc
    wanted = (meta.title, meta.pillar, meta.format)
    if article.status is not ArticleStatus.PLANNED:
        return
    if (article.meta.title, article.meta.pillar, article.meta.format) == wanted:
        return
    merged = article.meta.model_copy(
        update={"title": meta.title, "pillar": meta.pillar, "format": meta.format}
    )
    try:
        await owner_save(
            store,
            article.id,
            if_match=stored.etag,
            base_version=article.version,
            meta=merged,
            markdown=article.markdown,
            now=now,
        )
    except (PreconditionFailed, WriterConflict):
        _logger.info("planner_flagship_meta_not_synced", extra={"article_id": article.id})


async def _start_flagship(
    store: Store, jobs: JobQueue, plan: WeekPlan, rules: PlanRules, *, now: datetime
) -> tuple[str, list[str]]:
    flagship = plan.flagship
    if flagship is None:  # pragma: no cover - guarded by the caller
        raise ValueError("no flagship")
    article_id = flagship_article_id(plan.id)
    meta = ArticleMeta(
        title=clean_title(flagship.title, limit=MAX_TITLE),
        pillar=flagship.pillar,
        format=flagship.format,
    )
    try:
        stored = await create_article(
            store,
            now=now,
            meta=meta,
            idea_id=flagship.idea_id,
            week_plan_id=plan.id,
            article_id=article_id,
        )
    except Conflict:
        stored = await read_doc(store, Article, article_id)
        if stored.doc.status is ArticleStatus.ARCHIVED:
            # Retired by an earlier edit (or by the owner) and planned again: bring it back.
            stored = await transition(
                store, article_id, ArticleStatus.PLANNED, actor="system", now=now
            )
        await _sync_meta(store, stored, meta, now)
        stored = await read_doc(store, Article, article_id)
    if flagship.idea_id is not None:
        try:
            await mark_idea_used(store, flagship.idea_id, now=now, article_id=article_id)
        except (NotFound, TransitionError):
            _logger.info("planner_idea_not_marked", extra={"article_id": article_id})
    article = stored.doc
    if article.status is not ArticleStatus.PLANNED or article.pipeline_runs > 1:
        return article_id, []  # the owner already started (or restarted) its pipeline
    research = weekly_slot(plan.week_start, rules.cadence.research_start, plan.timezone)
    payload = PipelineInput(article_id=article_id, origin="owner").to_json()
    try:
        job = await enqueue_once(
            jobs,
            PIPELINE_ARTICLE,
            payload,
            job_id=pipeline_job_id(article_id, 1),
            run_after=parse_iso(research.utc),
            on_conflict="rerun",  # a queued start is kept; a cancelled one starts again
        )
    except ValueError:
        _logger.warning("job_hook_unregistered", extra={"job_type": PIPELINE_ARTICLE})
        return article_id, []
    await set_links(
        store, article_id, ArticleLinks(pipeline_job_id=job.id, pipeline_runs=1), now=now
    )
    return article_id, [job.id]


async def _retire_flagship(store: Store, jobs: JobQueue, plan: WeekPlan, *, now: datetime) -> None:
    """A flagship removed after an earlier approval: its article, still ``planned``, is
    archived and the queued pipeline start cancelled. Anything further along stays."""
    article_id = flagship_article_id(plan.id)
    try:
        article = (await read_doc(store, Article, article_id)).doc
    except NotFound:
        return
    if article.status is not ArticleStatus.PLANNED:
        return
    if article.pipeline_job_id is not None:
        with contextlib.suppress(NotFound):
            await jobs.cancel(article.pipeline_job_id)
    await transition(store, article_id, ArticleStatus.ARCHIVED, actor="system", now=now)


async def _start_native(
    store: Store, jobs: JobQueue, plan: WeekPlan, slot: PlannedSlot, *, now: datetime
) -> tuple[str, str | None]:
    post_id = Post.id_for_plan_slot(plan.id, slot.id)
    refs = slot_refs(slot)
    stamp = to_iso(now)
    post = Post(
        id=post_id,
        created_at=stamp,
        updated_at=stamp,
        status=PostStatus.GENERATING,
        status_changed_at=stamp,
        lane="native",
        pillar=slot.pillar or "",
        format=slot.format or "text",
        week_plan_id=plan.id,
        plan_slot_id=slot.id,
        slot=slot.slot,
        story_ids=refs.story_ids,
        pov_ids=refs.pov_ids,
        idea_id=refs.idea_id,
        go_live_mode=await default_go_live_mode(store, now=now),
    )
    with contextlib.suppress(Conflict):
        await create_doc(store, post)
    payload = PostJobInput(post_id=post_id, origin="owner").to_json()
    try:
        job = await enqueue_once(
            jobs, POSTS_GENERATE, payload, job_id=post_generate_job_id(post_id)
        )
    except ValueError:
        _logger.warning("job_hook_unregistered", extra={"job_type": POSTS_GENERATE})
        return post_id, None
    return post_id, job.id


async def approve_plan(
    store: Store, jobs: JobQueue, *, week_start: str, if_match: str, now: datetime
) -> Approved:
    """Approve a proposed plan and start what it generates (module docs)."""
    current = await _current_plan(store, week_start, if_match)
    plan = current.doc
    PLAN_MACHINE.transition(plan.status, PlanStatus.APPROVED)
    rules = (await load_rules(store, now=now)).in_zone(plan.timezone)
    findings = plan_findings(plan, rules, taken=await plan_taken(store, plan, rules), now=now)
    spacing = [f for f in findings if f.severity == "block" and f.code in SPACING_BLOCKING]
    if spacing:
        raise ProblemError(
            409,
            code="slot_conflict",
            detail="Two posts would go out on the same day. Move one first.",
            extensions={"findings": _findings_json(spacing)},
        )
    mix = [f for f in findings if f.severity == "block" and f.code in MIX_BLOCKING]
    if mix:
        raise ProblemError(
            409,
            code="mix_rules",
            detail="The plan breaks a lane or mix rule. Fix the marked slots first.",
            extensions={"findings": _findings_json(mix)},
        )
    article_id: str | None = None
    job_ids: list[str] = []
    post_ids: list[str] = []
    flagship = plan.flagship
    slots = list(plan.slots)
    if plan.mode != "away":
        if flagship is not None:
            article_id, started = await _start_flagship(store, jobs, plan, rules, now=now)
            job_ids.extend(started)
            flagship = flagship.model_copy(update={"article_id": article_id})
        else:
            await _retire_flagship(store, jobs, plan, now=now)
        for index, slot in enumerate(slots):
            due = parse_iso(slot.slot.utc) > now
            if slot.lane != "native" or slot.post_id is not None or not due:
                continue
            if slot.pillar not in rules.pillar_ids:
                continue
            post_id, job_id = await _start_native(store, jobs, plan, slot, now=now)
            slots[index] = slot.model_copy(update={"post_id": post_id})
            post_ids.append(post_id)
            if job_id is not None:
                job_ids.append(job_id)
    stamp = to_iso(now)
    changed = plan.model_copy(
        update={
            "status": PlanStatus.APPROVED,
            "status_changed_at": stamp,
            "approved_at": stamp,
            "updated_at": stamp,
            "flagship": flagship,
            "slots": slots,
            "warnings": findings,
        }
    )
    stored = await _replace_plan(store, changed, if_match)
    _logger.info(
        "planner_plan_approved",
        extra={"plan_id": plan.id, "article_id": article_id, "posts": len(job_ids)},
    )
    return Approved(stored, article_id, job_ids, post_ids)


# --------------------------------------------------------------------------------------------
# Edit (drag and drop) and mode
# --------------------------------------------------------------------------------------------


def _unprocessable(code: str, detail: str) -> ProblemError:
    return ProblemError(422, code=code, detail=detail)


def _signature(
    mode: WeekMode, flagship: FlagshipPlan | None, slots: Sequence[PlannedSlot]
) -> tuple[object, ...]:
    """What approval generates from: a change sends an approved plan back to proposed."""
    head = (
        (flagship.title, flagship.pillar, flagship.format, flagship.idea_id)
        if flagship is not None
        else None
    )
    open_slots = sorted(
        (slot.id, slot.lane or "", slot.pillar or "", slot.format or "")
        for slot in slots
        if slot.post_id is None
    )
    return (mode, head, tuple(open_slots))


def _flagship_from(
    body: FlagshipIn, rules: PlanRules, previous: FlagshipPlan | None
) -> FlagshipPlan:
    if body.pillar not in rules.pillar_ids:
        raise _unprocessable("unknown_pillar", "Pick one of your pillars.")
    if rules.packs.get(body.format) is None:
        raise _unprocessable("unknown_format", "Pick one of your article formats.")
    return FlagshipPlan(
        title=clean_title(body.title, limit=MAX_TITLE) or body.title,
        pillar=body.pillar,
        format=body.format,
        idea_id=body.idea_id,
        optional=body.optional,
        article_id=previous.article_id if previous is not None else None,
        publish_at=previous.publish_at if previous is not None else None,
    )


async def _check_editable(store: Store, jobs: JobQueue, plan: WeekPlan) -> None:
    if plan.status is PlanStatus.CLOSED:
        raise ProblemError(409, code="plan_closed", detail="This week is closed.")
    if plan.status is PlanStatus.GENERATING and plan.job_id is not None:
        try:
            job = await jobs.get(plan.job_id)
        except NotFound:
            return
        if job.is_active:
            raise ProblemError(
                409, code="plan_generating", detail="The plan is being generated. Try again soon."
            )


async def _apply_moves(
    store: Store,
    jobs: JobQueue,
    plan: WeekPlan,
    moves: Sequence[tuple[str, PlannedSlot]],
    rules: PlanRules,
    *,
    now: datetime,
) -> None:
    """Move the plan's posts to their new slots, all checked before any is moved: a swap of
    two days works, while two scheduled posts on one day (or one on a day a post outside the
    plan holds) is 409 ``slot_conflict``: posts are never stacked."""
    cadence = rules.cadence
    pending: list[tuple[Post, PlannedSlot]] = []
    scheduled: dict[str, PlannedSlot] = {}
    in_plan: set[str] = set()
    for post_id, planned in moves:
        post = (await read_doc(store, Post, post_id)).doc
        if post.status in _GONE:
            continue
        in_plan.add(post_id)
        if post.status in SCHEDULED_STATUSES:
            scheduled[post_id] = planned
        if post.slot == planned.slot and post.week_plan_id == plan.id:
            continue
        check_move(post, planned.slot, now=now)
        pending.append((post, planned))
    days: dict[str, str] = {}
    for post_id, planned in scheduled.items():
        day = local_day(planned.slot.utc, cadence.timezone)
        if day in days:
            raise ProblemError(
                409,
                code="slot_conflict",
                detail="Two scheduled posts would go out on the same day. Posts are never stacked.",
                extensions={"conflicts": [days[day], post_id]},
            )
        days[day] = post_id
    for post, planned in pending:
        if post.status in SCHEDULED_STATUSES:
            await check_free(store, planned.slot, cadence=cadence, now=now, exclude=in_plan)
    for post, planned in pending:
        await move_post(
            store,
            jobs,
            post.id,
            planned.slot,
            cadence=cadence,
            now=now,
            week_plan_id=plan.id,
            check_conflicts=False,
        )


async def edit_plan(
    store: Store,
    jobs: JobQueue,
    *,
    week_start: str,
    body: PlanEditIn,
    if_match: str,
    now: datetime,
) -> Stored[WeekPlan]:
    """Apply a plan edit: slot times (UTC recomputed in the plan's zone), slot contents, the
    flagship and the mode; posts already in the plan move with their slots (module docs). A
    slot field left out of the request keeps its value (drag and drop sends only the id, date
    and time); ``null`` clears it. A slot with a post mirrors the post's lane, pillar and
    format.

    422: ``duplicate_slot``, ``too_many_slots``, ``slot_outside_week``, ``unknown_pillar``,
    ``unknown_format``, ``unknown_post``. 409: ``plan_closed``, ``plan_generating``,
    ``slot_has_post`` (removing a slot whose post is still live), and the re-slot codes of
    :func:`~blogwriter.planner.slots.move_post`.
    """
    current = await _current_plan(store, week_start, if_match)
    plan = current.doc
    await _check_editable(store, jobs, plan)
    rules = (await load_rules(store, now=now)).in_zone(plan.timezone)
    ids = [item.id for item in body.slots]
    if len(ids) != len(set(ids)):
        raise _unprocessable("duplicate_slot", "Each slot needs its own id.")
    if len(ids) > rules.cadence.posts_per_week.max:
        raise _unprocessable(
            "too_many_slots", f"At most {rules.cadence.posts_per_week.max} posts in a week."
        )
    week_days = {add_days(plan.week_start, offset) for offset in range(7)}
    existing = {slot.id: slot for slot in plan.slots}
    for slot in plan.slots:
        if slot.id in ids or slot.post_id is None:
            continue
        try:
            linked = (await read_doc(store, Post, slot.post_id)).doc
        except NotFound:
            continue
        if linked.status not in _GONE:
            raise ProblemError(
                409,
                code="slot_has_post",
                detail="A post fills this slot. Move or archive the post first.",
                extensions={"slotId": slot.id},
            )
    new_slots: list[PlannedSlot] = []
    moves: list[tuple[str, PlannedSlot]] = []
    for item in body.slots:
        if item.local_date not in week_days:
            raise _unprocessable("slot_outside_week", "Every slot must be in this week.")
        if item.pillar is not None and item.pillar not in rules.pillar_ids:
            raise _unprocessable("unknown_pillar", "Pick one of your pillars.")
        old = existing.get(item.id)
        refs = slot_refs(old) if old is not None else SlotRefs()
        post_id = item.post_id or (old.post_id if old is not None else None)
        given = item.model_fields_set
        lane = item.lane if "lane" in given or old is None else old.lane
        pillar = item.pillar if "pillar" in given or old is None else old.pillar
        fmt = item.format if "format" in given or old is None else old.format
        slot_time = slot_at(rules.cadence, item.local_date, item.local_time)
        if post_id is not None:
            try:
                post = (await read_doc(store, Post, post_id)).doc
            except NotFound:
                raise _unprocessable("unknown_post", "That post does not exist.") from None
            if post.status in _GONE and post_id == item.post_id:
                raise _unprocessable("unknown_post", "That post is archived or expired.")
            lane, pillar, fmt = post.lane, post.pillar, post.format
        if "title" in given or old is None:
            title = clean_title(item.title, limit=300) if item.title else None
        else:
            title = old.title
        planned = PlannedSlot(
            id=item.id,
            slot=slot_time,
            post_id=post_id,
            lane=lane,
            pillar=pillar,
            format=fmt,
            title=title or None,
        )
        new_slots.append(with_refs(planned, refs))
        if post_id is not None:
            moves.append((post_id, planned))
    flagship = plan.flagship
    if "flagship" in body.model_fields_set:
        flagship = (
            _flagship_from(body.flagship, rules, plan.flagship)
            if body.flagship is not None
            else None
        )
    mode = body.mode or plan.mode
    await _apply_moves(store, jobs, plan, moves, rules, now=now)
    status = plan.status
    regenerate = _signature(mode, flagship, new_slots) != _signature(
        plan.mode, plan.flagship, plan.slots
    )
    if status is PlanStatus.GENERATING or (status is PlanStatus.APPROVED and regenerate):
        status = PLAN_MACHINE.transition(status, PlanStatus.PROPOSED)
    stamp = to_iso(now)
    changed = plan.model_copy(
        update={
            "mode": mode,
            "flagship": flagship,
            "slots": sorted(new_slots, key=lambda s: s.slot.utc),
            "status": status,
            "status_changed_at": stamp if status is not plan.status else plan.status_changed_at,
            "updated_at": stamp,
            "pillar_balance": pillar_balance(new_slots, rules),
        }
    )
    findings = plan_findings(changed, rules, taken=await plan_taken(store, changed, rules), now=now)
    changed = changed.model_copy(update={"warnings": findings})
    stored = await _replace_plan(store, changed, if_match)
    _logger.info("planner_plan_edited", extra={"plan_id": plan.id})
    return stored


async def _away_to_kit(
    store: Store, jobs: JobQueue, week_start: str, zone: str, *, now: datetime
) -> list[str]:
    """An away week: its approved and staged one-click posts move to the kit (``prepared``,
    ``domain.posts.into_kit``: the rule of the transport's mode switch) and their staging and
    slot checks are cancelled, so nobody is
    asked to tap at a slot while away; the owner schedules them natively from the kit (plan:
    "Away: already-approved posts are batch-scheduled natively through the kit"). Returns the
    moved post ids."""
    start, end = week_bounds(week_start, zone)
    found = await find_docs(
        store,
        Post,
        [
            Filter("status", "in", [PostStatus.APPROVED.value, PostStatus.STAGED.value]),
            Filter("slot.utc", ">=", start),
            Filter("slot.utc", "<", end),
        ],
    )
    stamp = to_iso(now)
    moved: list[str] = []
    for stored in found:
        post = stored.doc
        if post.go_live_mode != "one_click" or post.slot is None or not post.approval_current:
            continue
        try:
            await replace_doc(store, into_kit(post, stamp=stamp), if_match=stored.etag)
        except (PreconditionFailed, TransitionError):
            continue  # changed meanwhile: its own state wins
        await cancel_slot_jobs(jobs, post.id, post.slot.utc)
        moved.append(post.id)
    if moved:
        _logger.info("planner_away_to_kit", extra={"count": len(moved)})
    return moved


async def set_mode(
    store: Store,
    *,
    week_start: str,
    mode: WeekMode,
    if_match: str | None,
    now: datetime,
    jobs: JobQueue | None = None,
) -> Stored[WeekPlan]:
    """The availability mode of a week. ``if_match`` ``None`` (``If-None-Match: *``) creates
    the week's plan holding only its mode (status ``generating``, no job: the plan job fills it
    in); losing that race is a 412. A proposed or approved plan keeps its slots and gets the
    mode findings; a closed week answers 409 ``plan_closed``. Switching a week to ``away``
    moves its approved one-click posts to the kit (:func:`_away_to_kit`, with ``jobs``)."""
    rules = await load_rules(store, now=now)
    stamp = to_iso(now)
    if if_match is None:
        plan = WeekPlan(
            id=plan_id(week_start),
            created_at=stamp,
            updated_at=stamp,
            week_start=week_start,
            timezone=rules.cadence.timezone,
            mode=mode,
            status=PlanStatus.GENERATING,
            status_changed_at=stamp,
            pillar_balance=pillar_balance([], rules),
        )
        try:
            return await create_doc(store, plan)
        except Conflict:
            raise etag_mismatch(await read_doc(store, WeekPlan, plan.id)) from None
    current = await _current_plan(store, week_start, if_match)
    plan = current.doc
    if plan.status is PlanStatus.CLOSED:
        raise ProblemError(409, code="plan_closed", detail="This week is closed.")
    if plan.mode == mode:
        return current
    rules = rules.in_zone(plan.timezone)
    changed = plan.model_copy(update={"mode": mode, "updated_at": stamp})
    if plan.status is not PlanStatus.GENERATING:
        findings = plan_findings(
            changed, rules, taken=await plan_taken(store, changed, rules), now=now
        )
        changed = changed.model_copy(update={"warnings": findings})
    stored = await _replace_plan(store, changed, if_match)
    if mode == "away" and jobs is not None:
        await _away_to_kit(store, jobs, plan.week_start, plan.timezone, now=now)
    return stored
