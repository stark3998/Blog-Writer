"""Scheduler Service — APScheduler integration for periodic feed crawling."""

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _job_id(feed_source_id: str) -> str:
    return f"crawl_{feed_source_id}"


def _run_crawl(source_id: str) -> None:
    """Synchronous wrapper for crawl_feed_source, called by APScheduler."""
    from backend.services.feed_crawler import crawl_feed_source

    try:
        logger.info(f"Scheduled crawl triggered for feed source: {source_id}")
        crawl_feed_source(source_id)
    except Exception as exc:
        logger.error(f"Scheduled crawl failed for {source_id}: {exc}")


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scheduler() -> None:
    """Start the scheduler and load all enabled feed sources."""
    from backend.db.cosmos_client import list_feed_sources

    scheduler = get_scheduler()

    if scheduler.running:
        logger.info("Scheduler already running")
        return

    try:
        sources = list_feed_sources(enabled_only=True)
        for source in sources:
            schedule_feed(source)
        scheduler.start()
        logger.info(
            f"Scheduler started with {len(sources)} feed source(s) scheduled"
        )
    except Exception as exc:
        logger.error(f"Failed to start scheduler: {exc}")


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None


def schedule_feed(source: dict[str, Any]) -> None:
    """Add or update a scheduled crawl job for a feed source."""
    scheduler = get_scheduler()
    job_id = _job_id(source["id"])
    interval = max(source.get("crawlIntervalMinutes", 60), 5)  # min 5 minutes

    if not source.get("enabled", True):
        unschedule_feed(source["id"])
        return

    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.reschedule_job(
            job_id, trigger=IntervalTrigger(minutes=interval)
        )
        logger.info(f"Rescheduled feed {source['name']} every {interval}m")
    else:
        scheduler.add_job(
            _run_crawl,
            trigger=IntervalTrigger(minutes=interval),
            id=job_id,
            args=[source["id"]],
            name=f"Crawl: {source.get('name', source['id'])}",
            replace_existing=True,
        )
        logger.info(f"Scheduled feed {source['name']} every {interval}m")


def reschedule_feed(source: dict[str, Any]) -> None:
    """Reschedule a feed after settings change."""
    schedule_feed(source)


def unschedule_feed(feed_source_id: str) -> None:
    """Remove a scheduled crawl job."""
    scheduler = get_scheduler()
    job_id = _job_id(feed_source_id)
    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.remove_job(job_id)
        logger.info(f"Unscheduled feed: {feed_source_id}")
