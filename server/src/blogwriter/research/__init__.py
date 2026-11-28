"""blogwriter.research: research runs, quick search, ``fetch_page`` and the blocklist audit.

Contract work order W-A5. **No passages are ever persisted**: fetched pages live in memory inside
one fetch-and-extract step; documents, job checkpoints, events and logs hold claims (with any
run shared with the passage cut), quotes of at most 200 characters per source, URLs, titles,
publishers and dates.

Integration (``blogwriter.main``):

- ``get_router()``: ``/api/research/runs``, ``/runs/{id}``, ``/runs/{id}/cancel``,
  ``/briefs/{id}``, ``/quick``, ``/quick/{id}``.
- ``register_jobs(registry)``: ``research.run``.
- ``schedule_entries(cadence)``: none.
- ``ResearchLifecycle``: ``app.state.fetcher`` (Web IQ Browse with the egress fallback, or egress
  only), closed on shutdown.

Other packages use :func:`create_run` and :func:`start_run` (pipeline, evals),
:func:`quick_search`, :func:`audit_texts` and ``services.fetcher`` (the port).
"""

from blogwriter.research.audit import audit_texts
from blogwriter.research.fetch import (
    EgressFetcher,
    FallbackFetcher,
    ResearchFetcher,
    WebIqFetcher,
    fetch_page,
)
from blogwriter.research.jobs import register_jobs, schedule_entries
from blogwriter.research.lifecycle import ResearchLifecycle, webiq_state
from blogwriter.research.quick import QuickSearchTimeout, quick_search
from blogwriter.research.routes import get_router
from blogwriter.research.run import create_run, start_run
from blogwriter.research.settings import ResearchSettings, research_settings

__all__ = [
    "EgressFetcher",
    "FallbackFetcher",
    "QuickSearchTimeout",
    "ResearchFetcher",
    "ResearchLifecycle",
    "ResearchSettings",
    "WebIqFetcher",
    "audit_texts",
    "create_run",
    "fetch_page",
    "get_router",
    "quick_search",
    "register_jobs",
    "research_settings",
    "schedule_entries",
    "start_run",
    "webiq_state",
]
