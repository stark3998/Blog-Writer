"""Job types of the signals package: ``signals.crawl`` and ``signals.scout``.

Both write only ``sources``, ``signals`` and ``ideas`` and never enqueue another job: feed items
never generate content without an owner action.

``signals.crawl`` (schedule every ``crawl_interval_hours``; owner "crawl now")
    ``fetch``: housekeeping (expire time-sensitive ideas, wake snoozed ones), then the
    preflight (AI configured, blocklist saved, prompt settings clear of it), then every due feed
    source (:mod:`blogwriter.signals.crawl`). ``triage``: :mod:`blogwriter.signals.triage`
    with origin ``feed``.
``signals.scout`` (weekly)
    ``search``: :mod:`blogwriter.signals.scout`. ``triage``: origin ``scout``.

**Pause.** When autonomy is paused, a background run (the schedule) does its housekeeping and
finishes with ``{"skipped": "paused"}`` before it crawls or searches anything: no signal is
recorded, so nothing seen during a pause is lost. A pause that lands between the two steps
skips the triage step the same way; its candidates stay untriaged and are queued again the
next time a crawl sees them. Owner-started runs (``origin: owner``) still run.

Background AI calls run inside ``defer_on_brake``. Steps are re-entrant: each checkpoints its
progress (candidates, batch index) and resumes from it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import JsonValue, ValidationError

from blogwriter.db import Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.jobflow import require_service, step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    SIGNALS_CRAWL,
    SIGNALS_SCOUT,
    JobInput,
    entity_changed,
)
from blogwriter.domain.settings import AutonomySettings, Blocklist
from blogwriter.jobs.runner import JobFailed, JobRegistry, Step, StepContext
from blogwriter.signals.candidates import candidates_from_json
from blogwriter.signals.crawl import CrawlProgress, crawl_feeds
from blogwriter.signals.ideas import expire_ideas, wake_snoozed_ideas
from blogwriter.signals.scout import ScoutProgress, scout_searches
from blogwriter.signals.settings import SignalsSettings, get_settings
from blogwriter.signals.triage import TriageProgress, load_triage_context, triage_candidates

__all__ = ["PAUSED", "SignalsJobInput", "register_jobs"]

PAUSED: Final = "paused"


class SignalsJobInput(JobInput):
    """Input of ``signals.crawl`` and ``signals.scout``: only the origin (``{}`` from the
    schedule means ``background``)."""


def job_input(value: JsonValue) -> SignalsJobInput:
    try:
        return SignalsJobInput.from_json(value if value is not None else {})
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None


async def autonomy_paused(store: Store, *, now: datetime) -> bool:
    autonomy, _ = await read_singleton(store, AutonomySettings, now=to_iso(now))
    return autonomy.paused


async def _blocklist(store: Store, *, now: datetime) -> Blocklist:
    blocklist, _ = await read_singleton(store, Blocklist, now=to_iso(now))
    if not blocklist.configured:
        raise JobFailed("blocklist_not_configured", "Save the blocklist once in Settings > Policy.")
    return blocklist


def _skipped(output: JsonValue) -> JsonValue:
    if isinstance(output, dict):
        reason = output.get("skipped")
        if isinstance(reason, str):
            return reason
    return None


class _Steps:
    """The step functions, bound to the package settings."""

    def __init__(self, settings: SignalsSettings | None) -> None:
        self._settings = settings

    @property
    def settings(self) -> SignalsSettings:
        return self._settings if self._settings is not None else get_settings()

    async def crawl_fetch(self, ctx: StepContext) -> JsonValue:
        params = job_input(ctx.input)
        services = step_services()
        store = services.store
        now = services.clock.now()
        expired = await expire_ideas(store, now=now)
        woken = await wake_snoozed_ideas(store, now=now)
        if params.origin == "background" and await autonomy_paused(store, now=now):
            return {"skipped": PAUSED, "expired": expired, "woken": woken}
        require_service(services.ai, "ai")
        blocklist = await _blocklist(store, now=now)
        await load_triage_context(store, now=now)  # fails the job before anything is crawled

        async def save(progress: CrawlProgress) -> None:
            await ctx.checkpoint(progress.to_json())

        progress = await crawl_feeds(
            store,
            services.egress,
            settings=self.settings,
            now=now,
            progress=CrawlProgress.from_json(ctx.output),
            save=save,
            blocklist=blocklist,
        )
        if progress.done:  # the sources' health changed: one event for the Sources page
            await ctx.emit(ENTITY_CHANGED, entity_changed("source", progress.done[-1]))
        return {**progress.to_json(), "expired": expired, "woken": woken}

    async def crawl_triage(self, ctx: StepContext) -> JsonValue:
        return await self._triage(ctx, previous="fetch", origin="feed")

    async def scout_search(self, ctx: StepContext) -> JsonValue:
        params = job_input(ctx.input)
        services = step_services()
        store = services.store
        now = services.clock.now()
        if params.origin == "background" and await autonomy_paused(store, now=now):
            return {"skipped": PAUSED}
        ai = require_service(services.ai, "ai")
        blocklist = await _blocklist(store, now=now)
        await load_triage_context(store, now=now)

        async def save(progress: ScoutProgress) -> None:
            await ctx.checkpoint(progress.to_json())

        progress = await scout_searches(
            services,
            ai,
            blocklist=blocklist,
            job_origin=params.origin,
            job_id=ctx.job_id,
            progress=ScoutProgress.from_json(ctx.output),
            save=save,
        )
        touched = [
            key.removeprefix("source:") for key in progress.done if key.startswith("source:")
        ]
        if touched:
            await ctx.emit(ENTITY_CHANGED, entity_changed("source", touched[-1]))
        return progress.to_json()

    async def scout_triage(self, ctx: StepContext) -> JsonValue:
        return await self._triage(ctx, previous="search", origin="scout")

    async def _triage(self, ctx: StepContext, *, previous: str, origin: str) -> JsonValue:
        params = job_input(ctx.input)
        found = ctx.outputs.get(previous)
        skipped = _skipped(found)
        if skipped is not None:
            return {"skipped": skipped}
        services = step_services()
        if params.origin == "background" and await autonomy_paused(
            services.store, now=services.clock.now()
        ):
            return {"skipped": PAUSED}
        try:
            candidates = candidates_from_json(
                found.get("items") if isinstance(found, dict) else None
            )
        except ValueError:
            raise JobFailed("invalid_checkpoint") from None
        progress = TriageProgress.from_json(ctx.output)
        if not candidates:
            return progress.to_json()
        ai = require_service(services.ai, "ai")

        async def save(state: TriageProgress) -> None:
            await ctx.checkpoint(state.to_json())

        progress = await triage_candidates(
            services,
            ai,
            candidates,
            origin="scout" if origin == "scout" else "feed",
            job_origin=params.origin,
            job_id=ctx.job_id,
            settings=self.settings,
            progress=progress,
            save=save,
            emit=ctx.emit,
        )
        return progress.to_json()


def register_jobs(registry: JobRegistry, *, settings: SignalsSettings | None = None) -> None:
    """Register ``signals.crawl`` and ``signals.scout`` (called once by ``blogwriter.main``).

    ``settings`` defaults to :func:`blogwriter.signals.settings.get_settings` at run time.
    """
    steps = _Steps(settings)
    registry.register(
        SIGNALS_CRAWL, [Step("fetch", steps.crawl_fetch), Step("triage", steps.crawl_triage)]
    )
    registry.register(
        SIGNALS_SCOUT, [Step("search", steps.scout_search), Step("triage", steps.scout_triage)]
    )
