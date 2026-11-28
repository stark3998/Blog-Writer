"""blogwriter.planner: the weekly production calendar (contract W-B4).

Week plans and their approval, slots and DST math (on top of ``domain.slots``), availability
modes (normal: 3 posts plus the flagship; light: 2 posts, flagship optional; away: nothing
generated), lane and mix rules (derivatives at most 2, native at least 1 on another pillar,
at least 80% on-pillar, pillar balance across all posts), spacing (two posts on one day block,
under 24 hours warns), the missed-slot policy (grace until slot + 2 h, then the next free slot,
never stacked; time-sensitive posts are not offered a slot after they expire), reminders (+15,
+60, +180 minutes and +48 h, +7 d after go-live; the kit confirmation at T + 30 min), the veto
notice, the weekly scorecard and the Today inbox. All times are stored in UTC next to their
IANA zone (``America/New_York`` by default).

Integration (``blogwriter.main``):

- :func:`get_router`: ``/api/today``, ``/api/plan/*``, ``/api/reminders*``,
  ``/api/posts/{id}/reslot`` and ``/api/articles/{id}/veto``.
- :func:`register_jobs`: ``planner.plan-week``, ``planner.veto-notice``, ``planner.remind``,
  ``planner.post-live``, ``planner.post-scheduled``, ``planner.slot-check``,
  ``planner.scorecard``.
- :func:`schedule_entries`: ``sched-planner-plan-week``, ``sched-planner-veto-notice``,
  ``sched-planner-scorecard``.
- No lifecycle and no settings class: everything owner-editable is in the settings documents.
  Routes and steps use ``store``, ``jobs``, ``clock``, ``notifier`` and (PLAN) ``ai``.

Public Python API: :func:`reminders_for_go_live` (and :func:`build_today`,
:func:`flagship_article_id`, :func:`plan_week_job_id`).
"""

from blogwriter.planner.approval import (
    Approved,
    approve_plan,
    edit_plan,
    flagship_article_id,
    post_generate_job_id,
    set_mode,
)
from blogwriter.planner.jobs import PlanWeekInput, plan_week_job_id, register_jobs
from blogwriter.planner.reminders import reminders_for_go_live
from blogwriter.planner.routes import get_router
from blogwriter.planner.schedule import schedule_entries
from blogwriter.planner.today import build_today

__all__ = [
    "Approved",
    "PlanWeekInput",
    "approve_plan",
    "build_today",
    "edit_plan",
    "flagship_article_id",
    "get_router",
    "plan_week_job_id",
    "post_generate_job_id",
    "register_jobs",
    "reminders_for_go_live",
    "schedule_entries",
    "set_mode",
]
