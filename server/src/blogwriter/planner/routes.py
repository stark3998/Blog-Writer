"""HTTP routes of the planner (contract W-B4).

Every route is owner-only and every non-GET passes CSRF (core). Single documents are
``Versioned[T]`` with the ``ETag`` header; lists are ``Page[T]``. Mutations of an existing
document require ``If-Match`` (428 without it, 412 with a stale one, with ``current``); the
mode route also creates a week's plan with ``If-None-Match: *``. Dismissing a reminder is a
one-tap phone action and needs no ``If-Match``.

Routes:

- ``GET /api/today``: the action inbox (:mod:`.today`).
- ``GET /api/plan/weeks?from=&limit=&cursor=``: plans from a week on (default: this week).
- ``GET /api/plan/weeks/{weekStart}``: one plan (404 for a missing plan or a non-Monday).
- ``POST /api/plan/weeks/{weekStart}/generate``: 202, ``planner.plan-week`` (origin owner);
  409 ``plan_approved``, ``week_past``.
- ``PUT /api/plan/weeks/{weekStart}``: drag and drop (:func:`~.approval.edit_plan`).
- ``PUT /api/plan/weeks/{weekStart}/mode``: availability (:func:`~.approval.set_mode`).
- ``POST /api/plan/weeks/{weekStart}/approve``: :func:`~.approval.approve_plan`; 409
  ``slot_conflict``, ``mix_rules``, ``invalid_transition``.
- ``POST /api/posts/{id}/reslot``: snooze 30 min, the next free slot, or a set slot
  (:func:`~.slots.move_post`).
- ``GET /api/reminders?status=pending``: reminders by due time.
- ``POST /api/reminders/{id}/dismiss``: cancel (pending) or hide (sent) a reminder.
- ``GET`` and ``POST /api/articles/{id}/veto``: the veto notice state, the veto (:mod:`.veto`).
- ``GET /api/plan/scorecard?weeks=8``: the last weeks' scorecards, newest first.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from blogwriter.articles import article_problems
from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, NotFound, OrderBy
from blogwriter.domain.api import (
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.articles import Article
from blogwriter.domain.base import Document, parse_iso, to_iso
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    JobsDep,
    SingletonPrecondition,
    StoreDep,
    problems,
    versioned,
)
from blogwriter.domain.docstore import Stored, find_docs, read_doc, read_singleton
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import PLANNER_PLAN_WEEK
from blogwriter.domain.planner import Reminder, WeekPlan
from blogwriter.domain.posts import Post, Slot
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import PlanStatus, ReminderStatus
from blogwriter.domain.weeks import add_days, plan_id, slot_utc
from blogwriter.domain.weeks import week_start as monday_of
from blogwriter.planner.approval import approve_plan, edit_plan, set_mode
from blogwriter.planner.jobs import PlanWeekInput, plan_week_job_id
from blogwriter.planner.models import (
    ApproveOut,
    ModeIn,
    PlanEditIn,
    ReslotIn,
    Today,
    VetoView,
    WeekScorecard,
)
from blogwriter.planner.proposal import load_rules
from blogwriter.planner.reminders import dismiss_reminder
from blogwriter.planner.scorecard import compute_scorecard
from blogwriter.planner.slots import (
    current_week_start,
    local_day,
    move_post,
    next_offer,
    slot_at,
    snooze_slot,
    taken_slots,
)
from blogwriter.planner.today import build_today
from blogwriter.planner.veto import veto_article, veto_view

__all__ = ["MAX_SCORECARD_WEEKS", "get_router"]

MAX_SCORECARD_WEEKS: Final = 52


def _monday(value: str) -> str:
    """``value`` when it is a Monday (``YYYY-MM-DD``); else 404 (no plan can have that id)."""
    try:
        if monday_of(value) == value and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ProblemError(404, code="not_found")


def _page[D: Document](docs: list[Stored[D]], size: int, key: str) -> Page[D]:
    items = [versioned(stored) for stored in docs[:size]]
    last = docs[size - 1].doc if len(docs) > size else None
    next_cursor = encode_cursor(str(getattr(last, key))) if last is not None else None
    return Page[D](items=items, next_cursor=next_cursor)


async def _reslot_target(
    body: ReslotIn, post: Post, cadence: CadenceSettings, store: StoreDep, now_iso: str
) -> Slot:
    now = parse_iso(now_iso)
    match body.action:
        case "snooze":
            return snooze_slot(post.slot, cadence, now=now)
        case "set":
            if body.slot is None:
                raise ProblemError(422, code="slot_required", detail="Pick a date and time.")
            try:
                return slot_at(cadence, body.slot.local_date, body.slot.local_time)
            except ValueError:
                raise ProblemError(
                    422, code="invalid_slot", detail="That time is not valid."
                ) from None
        case "next_slot":
            after = max(now, parse_iso(post.slot.utc)) if post.slot is not None else now
            midnight = slot_utc(local_day(now_iso, cadence.timezone), "00:00", cadence.timezone)
            taken = await taken_slots(store, cadence, since=parse_iso(midnight), exclude={post.id})
            offer = next_offer(post, [item.slot for item in taken], cadence, after=after)
            if offer is None:
                code = "expires_first" if post.time_sensitive else "no_free_slot"
                raise ProblemError(
                    409, code=code, detail="There is no free slot this post can still use."
                )
            return offer


def get_router() -> APIRouter:
    """Routes under ``/api/today``, ``/api/plan``, ``/api/reminders``, ``/api/posts/{id}/reslot``
    and ``/api/articles/{id}/veto``."""
    router = APIRouter(prefix="/api", tags=["planner"])

    @router.get("/today")
    async def get_today(store: StoreDep, clock: ClockDep) -> Today:
        with problems():
            return await build_today(store, now=clock.now())

    # ---- week plans ----------------------------------------------------------------------

    @router.get("/plan/weeks")
    async def list_weeks(
        store: StoreDep,
        clock: ClockDep,
        from_week: Annotated[str | None, Query(alias="from")] = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[WeekPlan]:
        """Plans from the week containing ``from`` (default: this week), oldest first."""
        size = clamp_limit(limit)
        now = clock.now()
        with problems():
            if from_week is None:
                cadence, _ = await read_singleton(store, CadenceSettings, now=to_iso(now))
                start = current_week_start(now, cadence.timezone)
            else:
                try:
                    start = monday_of(from_week)
                except ValueError:
                    raise ProblemError(
                        422, code="invalid_week", detail="from must be a date (YYYY-MM-DD)."
                    ) from None
            filters = [Filter("weekStart", ">=", start)]
            if cursor is not None:
                filters.append(Filter("weekStart", ">", decode_cursor(cursor)))
            docs = await find_docs(
                store, WeekPlan, filters, order_by=OrderBy("weekStart"), limit=size + 1
            )
        return _page(docs, size, "week_start")

    @router.get("/plan/weeks/{week_start}")
    async def get_week(week_start: str, store: StoreDep, response: Response) -> Versioned[WeekPlan]:
        monday = _monday(week_start)
        with problems():
            stored = await read_doc(store, WeekPlan, plan_id(monday))
        return versioned(stored, response)

    @router.post("/plan/weeks/{week_start}/generate", status_code=202)
    async def generate_week(
        week_start: str, store: StoreDep, jobs: JobsDep, clock: ClockDep
    ) -> JobAccepted:
        """Regenerate a week's plan (``planner.plan-week``, origin owner). A run already in
        progress is returned; an approved or closed plan is 409 ``plan_approved``."""
        monday = _monday(week_start)
        now = clock.now()
        with problems():
            rules = await load_rules(store, now=now)
            if monday < current_week_start(now, rules.cadence.timezone):
                raise ProblemError(409, code="week_past", detail="That week is over.")
            try:
                plan = (await read_doc(store, WeekPlan, plan_id(monday))).doc
            except NotFound:
                plan = None
            if plan is not None and plan.status in (PlanStatus.APPROVED, PlanStatus.CLOSED):
                raise ProblemError(
                    409,
                    code="plan_approved",
                    detail="This plan is approved. Edit it instead of regenerating it.",
                )
            payload = PlanWeekInput(week_start=monday, origin="owner").to_json()
            job = await enqueue_once(
                jobs,
                PLANNER_PLAN_WEEK,
                payload,
                job_id=plan_week_job_id(monday),
                on_conflict="rerun",
            )
        return JobAccepted(job_id=job.id, status=job.status)

    @router.put("/plan/weeks/{week_start}")
    async def put_week(
        week_start: str,
        body: PlanEditIn,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[WeekPlan]:
        monday = _monday(week_start)
        with problems():
            stored = await edit_plan(
                store, jobs, week_start=monday, body=body, if_match=if_match, now=clock.now()
            )
        return versioned(stored, response)

    @router.put("/plan/weeks/{week_start}/mode")
    async def put_mode(
        week_start: str,
        body: ModeIn,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        precondition: SingletonPrecondition,
        response: Response,
    ) -> Versioned[WeekPlan]:
        monday = _monday(week_start)
        with problems():
            stored = await set_mode(
                store,
                week_start=monday,
                mode=body.mode,
                if_match=precondition,
                now=clock.now(),
                jobs=jobs,
            )
        return versioned(stored, response)

    @router.post("/plan/weeks/{week_start}/approve")
    async def approve_week(
        week_start: str,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> ApproveOut:
        monday = _monday(week_start)
        with problems():
            approved = await approve_plan(
                store, jobs, week_start=monday, if_match=if_match, now=clock.now()
            )
        return ApproveOut(
            plan=versioned(approved.plan, response),
            article_id=approved.article_id,
            job_ids=approved.job_ids,
            post_ids=approved.post_ids,
        )

    @router.get("/plan/scorecard")
    async def scorecards(store: StoreDep, clock: ClockDep, weeks: int = 8) -> list[WeekScorecard]:
        """The scorecards of the last ``weeks`` complete weeks, newest first (stored on the
        week's plan, else computed from the documents)."""
        count = max(1, min(weeks, MAX_SCORECARD_WEEKS))
        now = clock.now()
        out: list[WeekScorecard] = []
        with problems():
            rules = await load_rules(store, now=now)
            zone = rules.cadence.timezone
            current = current_week_start(now, zone)
            for back in range(1, count + 1):
                monday = add_days(current, -7 * back)
                try:
                    plan = (await read_doc(store, WeekPlan, plan_id(monday))).doc
                except NotFound:
                    plan = None
                card = plan.scorecard if plan is not None else None
                if card is None:
                    card = await compute_scorecard(
                        store, week_start=monday, zone=zone, pillar_ids=rules.pillar_ids, now=now
                    )
                out.append(WeekScorecard(week_start=monday, scorecard=card))
        return out

    # ---- posts and reminders ---------------------------------------------------------------

    @router.post("/posts/{post_id}/reslot")
    async def reslot(
        post_id: str,
        body: ReslotIn,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Post]:
        """Snooze 30 minutes, take the next free slot or set one (staged and missed posts go
        back to approved; new ``posts.stage`` and ``planner.slot-check`` jobs)."""
        now = clock.now()
        with problems():
            cadence, _ = await read_singleton(store, CadenceSettings, now=to_iso(now))
            post = (await read_doc(store, Post, post_id)).doc
            target = await _reslot_target(body, post, cadence, store, to_iso(now))
            moved = await move_post(
                store, jobs, post_id, target, cadence=cadence, now=now, if_match=if_match
            )
        return versioned(moved.stored, response)

    @router.get("/reminders")
    async def list_reminders(
        store: StoreDep,
        status: ReminderStatus = ReminderStatus.PENDING,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Reminder]:
        """Reminders of one status by due time (soonest first)."""
        size = clamp_limit(limit)
        with problems():
            filters = [Filter("status", "==", status.value)]
            if cursor is not None:
                filters.append(Filter("dueAt", ">", decode_cursor(cursor)))
            docs = await find_docs(
                store, Reminder, filters, order_by=OrderBy("dueAt"), limit=size + 1
            )
        return _page(docs, size, "due_at")

    @router.post("/reminders/{reminder_id}/dismiss")
    async def dismiss(
        reminder_id: str, store: StoreDep, jobs: JobsDep, clock: ClockDep, response: Response
    ) -> Versioned[Reminder]:
        with problems():
            stored = await dismiss_reminder(store, jobs, reminder_id, now=clock.now())
        return versioned(stored, response)

    # ---- the veto ----------------------------------------------------------------------------

    @router.get("/articles/{article_id}/veto")
    async def get_veto(article_id: str, store: StoreDep) -> VetoView:
        with problems():
            stored = await read_doc(store, Article, article_id)
        return veto_view(stored.doc)

    @router.post("/articles/{article_id}/veto")
    async def post_veto(
        article_id: str,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        """Stop an approved article from publishing: back to review, the publish job cancelled,
        the calibration streak restarted."""
        with article_problems():
            stored = await veto_article(store, jobs, article_id, if_match=if_match, now=clock.now())
        return versioned(stored, response)

    return router
