"""blogwriter.analytics: insights (contract W-B6; plan "Planner, engagement, insights").

- **Snapshots**: the 30-second form at +48 h and +7 d (``POST /api/posts/{id}/snapshots``) and the
  **beta** LinkedIn export import (``BW_ANALYTICS_IMPORT_BETA``; assumed format documented in
  :mod:`blogwriter.analytics.imports`), matched by date, then URN.
- **Rates** per 1K impressions vs the owner's trailing median, by pillar, format, hook type,
  lane, go-live mode and owner signal (:mod:`blogwriter.analytics.rates`).
- **Experiments**: pre-registered, one running at a time, at least 8 measured posts per arm.
- **Suggestions only** (``analytics.suggest``, weekly): the owner accepts or dismisses; nothing
  self-adjusts.
- **Spend**: the ``usage`` ledger by day, task and model, the brake's threshold, the fallback
  and review-only rates.
- **Evals**: 12 topics (4 formats x 3 pillars) drafted by the real pipeline, judged by gate 1
  alone, labelled as-is / needs edits / reject by the owner; the labels calibrate
  ``autonomy.judge`` (judge-pass must mean "as-is" at least 90 % of the time).

Integration (``blogwriter.main``): :func:`get_router`, :func:`register_jobs`
(``analytics.suggest``, ``analytics.eval``), :func:`schedule_entries`
(``sched-analytics-suggest``). No lifecycle. Settings: :class:`AnalyticsSettings`
(``BW_ANALYTICS_*``); the spend view also reads ``AISettings`` (``BW_AI_BRAKE_*``, routes).
"""

from blogwriter.analytics.evals import JudgeStats, judge_stats, recompute_judge, with_judge
from blogwriter.analytics.jobs import register_jobs
from blogwriter.analytics.rates import InsightsOverview, MetricRates, build_overview
from blogwriter.analytics.routes import get_router
from blogwriter.analytics.schedule import schedule_entries
from blogwriter.analytics.settings import AnalyticsSettings, analytics_settings
from blogwriter.analytics.spend import SpendView, build_spend

__all__ = [
    "AnalyticsSettings",
    "InsightsOverview",
    "JudgeStats",
    "MetricRates",
    "SpendView",
    "analytics_settings",
    "build_overview",
    "build_spend",
    "get_router",
    "judge_stats",
    "recompute_judge",
    "register_jobs",
    "schedule_entries",
    "with_judge",
]
