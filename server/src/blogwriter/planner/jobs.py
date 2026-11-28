"""Job types of the planner (contract W-B4, section 7).

``planner.plan-week`` (schedule ``sched-planner-plan-week``, or the owner's "generate")
    ``collect`` (the week's plan document goes ``generating`` under this job; ids and counts
    only), ``propose`` (PLAN, anonymized and tool-free; skipped when autonomy is paused for a
    background run, when AI is not configured or not allowed, and for away weeks), ``slots``
    (deterministic: the mode's slots on free days, lane and mix rules, pillar balance, spacing
    findings), ``save`` (``generating -> proposed``), ``notify`` (push "Your plan for next week
    is ready" -> ``/calendar/plan``). It writes only its week plan: **no article, post or other
    job** (approval, an owner action, starts those).
``planner.veto-notice`` (schedule) ``notify``: :mod:`.veto`.
``planner.remind`` (delayed to ``dueAt``) ``send``: :mod:`.reminders`.
``planner.post-live`` (linkedin transport) ``reminders``: reply and snapshot reminders.
``planner.post-scheduled`` (linkedin transport) ``reminders``: the kit confirmation at T.
``planner.slot-check`` (linkedin posts, planner; delayed to slot + grace) ``check``:
    :mod:`.missed`.
``planner.scorecard`` (schedule) ``compute``: :mod:`.scorecard`.

Steps are re-entrant: every document they create has a derived id (``Conflict`` = done),
pushes are made at most once (a checkpoint before the push), and a step whose plan was taken
over by a newer generation does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final, cast

from pydantic import JsonValue, ValidationError

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, NotFound, Store
from blogwriter.domain.base import LocalDate, parse_iso, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.docstore import create_doc, read_doc, read_singleton, update_doc
from blogwriter.domain.jobflow import step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    PLANNER_PLAN_WEEK,
    PLANNER_POST_LIVE,
    PLANNER_POST_SCHEDULED,
    PLANNER_REMIND,
    PLANNER_SCORECARD,
    PLANNER_SLOT_CHECK,
    PLANNER_VETO_NOTICE,
    JobInput,
    PostLiveInput,
    PostScheduledInput,
    RemindInput,
    SlotJobInput,
    entity_changed,
)
from blogwriter.domain.planner import FlagshipPlan, PlannedSlot, WeekPlan
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import AutonomySettings, CadenceSettings
from blogwriter.domain.slots import next_article_publish, weekly_slot
from blogwriter.domain.states import PLAN_MACHINE, PlanStatus, PostStatus
from blogwriter.domain.weeks import add_days, plan_id, week_start
from blogwriter.jobs.runner import JobFailed, JobRegistry, Step, StepContext
from blogwriter.notify.payload import NotificationPayload
from blogwriter.planner.approval import plan_taken
from blogwriter.planner.links import plan_href
from blogwriter.planner.missed import check_slot
from blogwriter.planner.plan import (
    PlanRules,
    assign_slots,
    default_proposal,
    normalize,
    pillar_balance,
    plan_findings,
    slot_suffix,
)
from blogwriter.planner.proposal import (
    PlanContext,
    collect_context,
    load_material,
    load_rules,
    propose_with_ai,
    reason_text,
)
from blogwriter.planner.reminders import (
    cancel_stale_kit_reminders,
    ensure_reminders,
    natively_scheduled_at,
    reminders_for_go_live,
    schedule_kit_confirm,
    send_reminder,
)
from blogwriter.planner.scorecard import close_week
from blogwriter.planner.slots import current_week_start, free_week_slots, local_day, next_week_start
from blogwriter.planner.veto import send_veto_notices

__all__ = [
    "MAX_SERVED_BY",
    "PlanWeekInput",
    "plan_week_job_id",
    "register_jobs",
]

_logger: Final = logging.getLogger("blogwriter.planner")
register_log_fields("job_type", "plan_id", "week_start", "reason", "slots", "notified")

MAX_SERVED_BY: Final = 20
_SERVED_BY_KEY: Final = "servedBy"


class PlanWeekInput(JobInput):
    """``planner.plan-week``: ``weekStart`` (a Monday) or, from the schedule, the week after
    the one the run was scheduled in."""

    week_start: LocalDate | None = None


def plan_week_job_id(week_start_date: str) -> str:
    """The id of an owner-requested generation (``enqueue_once(..., on_conflict="rerun")``: a
    double click returns the active run)."""
    return f"planweek-{week_start_date}"


def _parse[I: JobInput](model: type[I], value: JsonValue) -> I:
    try:
        return model.from_json(value if value is not None else {})
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input", "The job input is not valid.") from None


def _skipped(output: JsonValue) -> str | None:
    if isinstance(output, dict):
        reason = output.get("skipped")
        if isinstance(reason, str):
            return reason
    return None


async def _paused(store: Store, *, now: datetime) -> bool:
    autonomy, _ = await read_singleton(store, AutonomySettings, now=to_iso(now))
    return autonomy.paused


async def _cadence(store: Store, *, now: datetime) -> CadenceSettings:
    cadence, _ = await read_singleton(store, CadenceSettings, now=to_iso(now))
    return cadence


# --------------------------------------------------------------------------------------------
# planner.plan-week
# --------------------------------------------------------------------------------------------


def _target_week(scheduled_for: str | None, now: datetime, zone: str) -> str:
    """The week after the one the run belongs to (a late run still plans the right week)."""
    base = parse_iso(scheduled_for) if scheduled_for is not None else now
    return next_week_start(base, zone)


async def _begin(
    store: Store, week: str, rules: PlanRules, *, job_id: str, now: datetime
) -> WeekPlan | None:
    """The week's plan, ``generating`` under ``job_id`` (created when missing). ``None`` when
    it is approved or closed: approved plans are never regenerated."""
    stamp = to_iso(now)
    doc_id = plan_id(week)
    try:
        await read_doc(store, WeekPlan, doc_id)
    except NotFound:
        fresh = WeekPlan(
            id=doc_id,
            created_at=stamp,
            updated_at=stamp,
            week_start=week,
            timezone=rules.cadence.timezone,
            mode=rules.cadence.default_mode,
            status=PlanStatus.GENERATING,
            status_changed_at=stamp,
            job_id=job_id,
            pillar_balance=pillar_balance([], rules),
        )
        try:
            return (await create_doc(store, fresh)).doc
        except Conflict:
            pass

    def mutate(plan: WeekPlan) -> WeekPlan | None:
        if plan.status in (PlanStatus.APPROVED, PlanStatus.CLOSED):
            return None
        if plan.status is PlanStatus.GENERATING and plan.job_id == job_id:
            return None
        changes: dict[str, object] = {"job_id": job_id, "updated_at": stamp}
        if plan.status is PlanStatus.PROPOSED:
            changes["status"] = PLAN_MACHINE.transition(plan.status, PlanStatus.GENERATING)
            changes["status_changed_at"] = stamp
        return plan.model_copy(update=changes)

    stored = await update_doc(store, WeekPlan, doc_id, mutate)
    if stored.doc.status in (PlanStatus.APPROVED, PlanStatus.CLOSED):
        return None
    return stored.doc


async def _owned(store: Store, week: str, job_id: str) -> WeekPlan | None:
    """The plan while this job still generates it (``None``: a newer run took over, or it
    left ``generating``)."""
    try:
        plan = (await read_doc(store, WeekPlan, plan_id(week))).doc
    except NotFound:
        return None
    if plan.status is not PlanStatus.GENERATING or plan.job_id != job_id:
        return None
    return plan


def _with_served_by(plan: WeekPlan, record: ServedByRecord | None) -> WeekPlan:
    """The plan with ``record`` appended to its ``servedBy`` (an extra field until the domain
    model has one; the newest :data:`MAX_SERVED_BY` are kept)."""
    if record is None:
        return plan
    body = plan.model_dump(mode="json", by_alias=True)
    raw: object = body.get(_SERVED_BY_KEY)
    records: list[JsonValue] = (
        [cast(JsonValue, item) for item in cast(list[object], raw)] if isinstance(raw, list) else []
    )
    records.append(record.model_dump(mode="json", by_alias=True, exclude_none=True))
    body[_SERVED_BY_KEY] = records[-MAX_SERVED_BY:]
    return WeekPlan.model_validate(body)


async def _write_owned(
    store: Store,
    week: str,
    job_id: str,
    change: Callable[[WeekPlan], WeekPlan],
) -> WeekPlan | None:
    """Apply ``change`` to the plan while this job generates it (CAS). ``None`` when it was
    not applied: the plan left ``generating`` or a newer run took it over."""
    applied = False

    def mutate(plan: WeekPlan) -> WeekPlan | None:
        nonlocal applied
        applied = plan.status is PlanStatus.GENERATING and plan.job_id == job_id
        return change(plan) if applied else None

    try:
        stored = await update_doc(store, WeekPlan, plan_id(week), mutate)
    except NotFound:
        return None
    return stored.doc if applied else None


def _context(ctx: StepContext) -> PlanContext:
    context = PlanContext.from_json(ctx.outputs.get("collect"))
    if context is None:
        raise JobFailed("invalid_state", "The plan job lost its collected context.")
    return context


async def _plan_collect(ctx: StepContext) -> JsonValue:
    params = _parse(PlanWeekInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    rules = await load_rules(store, now=now)
    week = params.week_start or _target_week(ctx.scheduled_for, now, rules.cadence.timezone)
    if week_start(week) != week:
        raise JobFailed("invalid_input", "weekStart must be a Monday.")
    plan = await _begin(store, week, rules, job_id=ctx.job_id, now=now)
    if plan is None:
        return {"weekStart": week, "skipped": "approved"}
    rules = rules.in_zone(plan.timezone)
    context = await collect_context(store, rules, week_start=week, mode=plan.mode, now=now)
    return {**context.to_json(), "planId": plan.id}


async def _plan_propose(ctx: StepContext) -> JsonValue:
    if (reason := _skipped(ctx.outputs.get("collect"))) is not None:
        return {"skipped": reason}
    context = _context(ctx)
    params = _parse(PlanWeekInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    plan = await _owned(store, context.week_start, ctx.job_id)
    if plan is None:
        return {"skipped": "superseded"}
    if context.mode == "away":
        return {"source": "deterministic", "reason": "away"}
    if params.origin == "background" and await _paused(store, now=now):
        return {"source": "deterministic", "reason": "paused", "skipped": "paused"}
    rules = (await load_rules(store, now=now)).in_zone(plan.timezone)
    material = await load_material(store, context)
    taken = await plan_taken(store, plan, rules)
    taken_days = {local_day(item.slot.utc, plan.timezone) for item in taken}
    free = free_week_slots(
        rules.cadence, context.week_start, context.posts, taken_days=taken_days, after=now
    )
    outcome = await propose_with_ai(
        store,
        services.ai,
        context,
        material,
        rules,
        slots=free,
        origin=params.origin,
        job_id=ctx.job_id,
        now=now,
    )
    if outcome.proposal is None:
        if outcome.served_by is not None:
            record = outcome.served_by
            await _write_owned(
                store, context.week_start, ctx.job_id, lambda p: _with_served_by(p, record)
            )
        _logger.info("planner_plan_without_ai", extra={"reason": outcome.reason})
        return {"source": "deterministic", "reason": outcome.reason}
    normalized = normalize(
        outcome.proposal, material, rules, mode=context.mode, posts=len(free), check=outcome.check
    )
    planned, short = assign_slots(
        normalized,
        rules,
        week_start=context.week_start,
        taken_days=taken_days,
        after=now,
        suffix=slot_suffix(ctx.job_id),
    )
    flagship = normalized.flagship.to_plan() if normalized.flagship is not None else None
    stamp = to_iso(now)
    served_by = outcome.served_by

    def change(current: WeekPlan) -> WeekPlan:
        updated = current.model_copy(
            update={
                "flagship": flagship,
                "slots": planned,
                "rationale": normalized.rationale,
                "updated_at": stamp,
            }
        )
        return _with_served_by(updated, served_by)

    written = await _write_owned(store, context.week_start, ctx.job_id, change)
    if written is None:
        return {"skipped": "superseded"}
    return {"source": "ai", "posts": len(planned), "short": short}


def _flagship_publish_at(flagship: FlagshipPlan, plan: WeekPlan, rules: PlanRules) -> FlagshipPlan:
    """The flagship's expected publish time: the first article slot at least the veto lead
    after the draft day (the auto path's rule)."""
    draft = weekly_slot(plan.week_start, rules.cadence.draft_day, plan.timezone)
    lead = timedelta(hours=rules.cadence.min_veto_lead_hours)
    publish = next_article_publish(rules.cadence, after=parse_iso(draft.utc), min_lead=lead)
    return flagship.model_copy(update={"publish_at": publish.utc})


async def _plan_slots(ctx: StepContext) -> JsonValue:
    if (reason := _skipped(ctx.outputs.get("collect"))) is not None:
        return {"skipped": reason}
    proposed = ctx.outputs.get("propose")
    if _skipped(proposed) == "superseded":
        return {"skipped": "superseded"}
    context = _context(ctx)
    services = step_services()
    store = services.store
    now = services.clock.now()
    plan = await _owned(store, context.week_start, ctx.job_id)
    if plan is None:
        return {"skipped": "superseded"}
    rules = (await load_rules(store, now=now)).in_zone(plan.timezone)
    source = proposed.get("source") if isinstance(proposed, dict) else None
    short_raw = proposed.get("short") if isinstance(proposed, dict) else None
    short = short_raw if isinstance(short_raw, int) and not isinstance(short_raw, bool) else 0
    taken = await plan_taken(store, plan, rules)
    flagship: FlagshipPlan | None = plan.flagship
    slots: list[PlannedSlot] = list(plan.slots)
    rationale = plan.rationale
    if source != "ai":
        reason_raw = proposed.get("reason") if isinstance(proposed, dict) else None
        reason = reason_raw if isinstance(reason_raw, str) else None
        material = await load_material(store, context)
        proposal = default_proposal(material, rules, mode=context.mode, posts=context.posts)
        normalized = normalize(
            proposal, material, rules, mode=context.mode, posts=context.posts, check=_any_title
        )
        taken_days = {local_day(item.slot.utc, plan.timezone) for item in taken}
        slots, short = assign_slots(
            normalized,
            rules,
            week_start=context.week_start,
            taken_days=taken_days,
            after=now,
            suffix=slot_suffix(ctx.job_id),
        )
        flagship = normalized.flagship.to_plan() if normalized.flagship is not None else None
        rationale = reason_text(reason) if reason not in (None, "away") else None
    if flagship is not None:
        flagship = _flagship_publish_at(flagship, plan, rules)
    stamp = to_iso(now)
    shaped = plan.model_copy(
        update={
            "flagship": flagship,
            "slots": slots,
            "rationale": rationale,
            "pillar_balance": pillar_balance(slots, rules),
            "updated_at": stamp,
        }
    )
    findings = plan_findings(shaped, rules, taken=taken, now=now, short=short)

    def change(current: WeekPlan) -> WeekPlan:
        body = shaped.model_dump(mode="json", by_alias=True)
        served = current.model_dump(mode="json", by_alias=True).get(_SERVED_BY_KEY)
        if served is not None:
            body[_SERVED_BY_KEY] = served
        body["warnings"] = [f.model_dump(mode="json", by_alias=True) for f in findings]
        body["updatedAt"] = stamp
        return WeekPlan.model_validate(body)

    written = await _write_owned(store, context.week_start, ctx.job_id, change)
    if written is None:
        return {"skipped": "superseded"}
    return {"slots": len(slots), "warnings": len(findings)}


def _any_title(_title: str) -> bool:
    """Deterministic plans use the owner's own titles (ideas, stories, POVs) as they are."""
    return True


async def _plan_save(ctx: StepContext) -> JsonValue:
    for step in ("collect", "propose", "slots"):
        reason = _skipped(ctx.outputs.get(step))
        if reason is not None and reason != "paused":
            return {"skipped": reason}
    context = _context(ctx)
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)

    def change(plan: WeekPlan) -> WeekPlan:
        status = PLAN_MACHINE.transition(plan.status, PlanStatus.PROPOSED)
        return plan.model_copy(
            update={"status": status, "status_changed_at": stamp, "updated_at": stamp}
        )

    written = await _write_owned(store, context.week_start, ctx.job_id, change)
    if written is None:
        # Already saved by an earlier attempt of this step, or taken over by a newer run.
        try:
            current = (await read_doc(store, WeekPlan, plan_id(context.week_start))).doc
        except NotFound:
            return {"skipped": "superseded"}
        if current.status is not PlanStatus.PROPOSED or current.job_id != ctx.job_id:
            return {"skipped": "superseded"}
    await ctx.emit(ENTITY_CHANGED, entity_changed("week_plan", plan_id(context.week_start)))
    _logger.info("planner_plan_proposed", extra={"plan_id": plan_id(context.week_start)})
    return {"planId": plan_id(context.week_start), "status": PlanStatus.PROPOSED.value}


async def _plan_notify(ctx: StepContext) -> JsonValue:
    saved = ctx.outputs.get("save")
    if (reason := _skipped(saved)) is not None:
        return {"skipped": reason}
    context = _context(ctx)
    proposed = ctx.outputs.get("propose")
    source = proposed.get("source") if isinstance(proposed, dict) else None
    result: dict[str, JsonValue] = {
        "planId": plan_id(context.week_start),
        "proposal": source if isinstance(source, str) else "deterministic",
        "notified": False,
    }
    if _skipped(proposed) == "paused":
        result["skipped"] = "paused"
    services = step_services()
    store = services.store
    now = services.clock.now()
    try:
        plan = (await read_doc(store, WeekPlan, plan_id(context.week_start))).doc
    except NotFound:
        return result
    if plan.status is not PlanStatus.PROPOSED or plan.job_id != ctx.job_id:
        return result
    if plan.mode == "away" and not plan.slots and plan.flagship is None:
        return result
    if isinstance(ctx.output, dict) and ctx.output.get("pushing") is True:
        result["notified"] = True  # an earlier attempt pushed it: never twice
        return result
    await ctx.checkpoint({"pushing": True})
    if context.week_start == next_week_start(now, plan.timezone):
        title = "Your plan for next week is ready"
    elif context.week_start == current_week_start(now, plan.timezone):
        title = "Your plan for this week is ready"
    else:
        title = f"Your plan for the week of {context.week_start} is ready"
    payload = NotificationPayload(
        title=title, url=plan_href(context.week_start), tag=f"plan:{context.week_start}"
    )
    try:
        outcome = await services.notifier.notify(payload)
        result["notified"] = outcome.any_delivered
    except Exception:  # the plan is ready either way; Today shows it
        _logger.warning("planner_plan_push_failed", extra={"plan_id": plan.id})
    return result


# --------------------------------------------------------------------------------------------
# The other job types
# --------------------------------------------------------------------------------------------


async def _veto_notify(ctx: StepContext) -> JsonValue:
    _parse(JobInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    if await _paused(store, now=now):
        return {"skipped": "paused"}  # auto-publish holds while paused: nothing to veto
    done: list[str] = []
    if isinstance(ctx.output, dict):
        raw = ctx.output.get("done")
        if isinstance(raw, list):
            done = [item for item in raw if isinstance(item, str)]

    async def progress(article_id: str) -> None:
        done.append(article_id)
        await ctx.checkpoint({"done": list[JsonValue](done)})
        await ctx.emit(ENTITY_CHANGED, entity_changed("article", article_id))

    report = await send_veto_notices(
        store,
        services.notifier,
        cadence=await _cadence(store, now=now),
        now=now,
        done=set(done),
        progress=progress,
    )
    return {
        "notified": len(report.notified),
        "undelivered": len(report.undelivered),
        "held": len(report.held),
    }


async def _remind_send(ctx: StepContext) -> JsonValue:
    params = _parse(RemindInput, ctx.input)
    services = step_services()
    attempted = isinstance(ctx.output, dict) and ctx.output.get("pushing") is True

    async def before_push() -> None:
        await ctx.checkpoint({"pushing": True})

    result = await send_reminder(
        services.store,
        services.notifier,
        params.reminder_id,
        now=services.clock.now(),
        resumed_after_push=attempted,
        before_push=before_push,
    )
    if result.get("status") != "missing":
        await ctx.emit(ENTITY_CHANGED, entity_changed("reminder", params.reminder_id))
    return dict(result.items())


async def _post_live(ctx: StepContext) -> JsonValue:
    params = _parse(PostLiveInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    try:
        post = (await read_doc(store, Post, params.post_id)).doc
    except NotFound:
        return {"skipped": "missing"}
    if post.status is not PostStatus.POSTED:
        return {"skipped": post.status.value}
    reminders = reminders_for_go_live(
        post, await _cadence(store, now=now), params.go_live_at, now=now
    )
    native = natively_scheduled_at(post)
    if native is not None and native != params.go_live_at:
        # Scheduled natively at T but it went out at another time: T's nudges are wrong now.
        keep = {reminder.id for reminder in reminders}
        await cancel_stale_kit_reminders(store, services.jobs, post.id, keep, now=now)
    created, existing = await ensure_reminders(
        store, services.jobs, reminders, origin=params.origin
    )
    if created and reminders:
        await ctx.emit(ENTITY_CHANGED, entity_changed("reminder", reminders[-1].id))
    return {
        "postId": post.id,
        "reminders": len(reminders),
        "created": created,
        "existing": existing,
    }


async def _post_scheduled(ctx: StepContext) -> JsonValue:
    params = _parse(PostScheduledInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    outcome = await schedule_kit_confirm(
        store,
        services.jobs,
        services.notifier,
        params.post_id,
        params.scheduled_at,
        cadence=await _cadence(store, now=now),
        origin=params.origin,
        now=now,
    )
    if outcome.skipped is not None:
        return {"skipped": outcome.skipped}
    if outcome.reminder_id is not None:
        await ctx.emit(ENTITY_CHANGED, entity_changed("reminder", outcome.reminder_id))
    return {
        "reminderId": outcome.reminder_id,
        "cancelled": outcome.cancelled,
        "spacing": list[JsonValue](outcome.spacing),
    }


async def _slot_check(ctx: StepContext) -> JsonValue:
    params = _parse(SlotJobInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    result = await check_slot(
        store,
        services.notifier,
        params.post_id,
        params.slot_utc,
        cadence=await _cadence(store, now=now),
        now=now,
    )
    if result.skipped is not None:
        return {"postId": params.post_id, "skipped": result.skipped}
    await ctx.emit(ENTITY_CHANGED, entity_changed("post", params.post_id))
    return {
        "postId": params.post_id,
        "status": result.status,
        "offer": result.offer.utc if result.offer is not None else None,
    }


async def _scorecard(ctx: StepContext) -> JsonValue:
    _parse(JobInput, ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    rules = await load_rules(store, now=now)
    zone = rules.cadence.timezone
    base = parse_iso(ctx.scheduled_for) if ctx.scheduled_for is not None else now
    week = add_days(current_week_start(base, zone), -7)
    closed = await close_week(
        store, week_start=week, zone=zone, pillar_ids=rules.pillar_ids, now=now
    )
    if closed.plan_id is not None:
        await ctx.emit(ENTITY_CHANGED, entity_changed("week_plan", closed.plan_id))
    return {
        "weekStart": week,
        "planId": closed.plan_id,
        "closed": closed.closed,
        "postsPublished": closed.scorecard.posts_published,
    }


def register_jobs(registry: JobRegistry) -> None:
    """Register every planner job type (called once by ``blogwriter.main``)."""
    registry.register(
        PLANNER_PLAN_WEEK,
        [
            Step("collect", _plan_collect),
            Step("propose", _plan_propose),
            Step("slots", _plan_slots),
            Step("save", _plan_save),
            Step("notify", _plan_notify),
        ],
    )
    registry.register(PLANNER_VETO_NOTICE, [Step("notify", _veto_notify)])
    registry.register(PLANNER_REMIND, [Step("send", _remind_send)])
    registry.register(PLANNER_POST_LIVE, [Step("reminders", _post_live)])
    registry.register(PLANNER_POST_SCHEDULED, [Step("reminders", _post_scheduled)])
    registry.register(PLANNER_SLOT_CHECK, [Step("check", _slot_check)])
    registry.register(PLANNER_SCORECARD, [Step("compute", _scorecard)])
