"""Feeds Router — CRUD for feed sources and crawl management."""

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.db.cosmos_client import (
    create_feed_source,
    delete_feed_source,
    get_feed_source,
    list_crawl_jobs,
    list_crawled_articles,
    list_feed_sources,
    update_feed_source,
)
from backend.services.feed_crawler import crawl_feed_source, crawl_feed_source_stream, discover_feed

router = APIRouter(prefix="/api/feeds", tags=["feeds"])


# ---------- Request / Response Models ----------


class FeedSourceCreateRequest(BaseModel):
    base_url: str
    name: str = ""
    topics: list[str] = ["cloud security", "azure", "ai"]
    crawl_interval_minutes: int = 60
    auto_publish_blog: bool = False
    auto_publish_linkedin: bool = False


class FeedSourceUpdateRequest(BaseModel):
    name: str | None = None
    topics: list[str] | None = None
    crawl_interval_minutes: int | None = None
    auto_publish_blog: bool | None = None
    auto_publish_linkedin: bool | None = None
    enabled: bool | None = None


class FeedSourceResponse(BaseModel):
    id: str
    name: str
    base_url: str
    feed_url: str
    feed_type: str
    topics: list[str]
    auto_publish_blog: bool
    auto_publish_linkedin: bool
    crawl_interval_minutes: int
    enabled: bool
    last_crawled_at: str
    created_at: str
    updated_at: str


class FeedDiscoverResponse(BaseModel):
    feed_url: str
    feed_type: str
    site_name: str


class CrawlResultResponse(BaseModel):
    job_id: str
    feed_source_id: str
    articles_found: int
    new_articles: int
    articles_relevant: int
    articles_processed: int
    status: str


class CrawledArticleResponse(BaseModel):
    id: str
    feed_source_id: str
    article_url: str
    title: str
    is_relevant: bool
    relevance_score: float
    matched_topics: list[str]
    draft_id: str
    status: str
    crawled_at: str


class CrawlJobResponse(BaseModel):
    id: str
    feed_source_id: str
    started_at: str
    completed_at: str
    articles_found: int
    articles_relevant: int
    articles_processed: int
    status: str
    error: str


# ---------- Helpers ----------


def _to_feed_response(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "base_url": item.get("baseUrl", ""),
        "feed_url": item.get("feedUrl", ""),
        "feed_type": item.get("feedType", "rss"),
        "topics": item.get("topics", []),
        "auto_publish_blog": item.get("autoPublishBlog", False),
        "auto_publish_linkedin": item.get("autoPublishLinkedIn", False),
        "crawl_interval_minutes": item.get("crawlIntervalMinutes", 60),
        "enabled": item.get("enabled", True),
        "last_crawled_at": item.get("lastCrawledAt", ""),
        "created_at": item.get("createdAt", ""),
        "updated_at": item.get("updatedAt", ""),
    }


def _to_article_response(item: dict) -> dict:
    return {
        "id": item["id"],
        "feed_source_id": item.get("feedSourceId", ""),
        "article_url": item.get("articleUrl", ""),
        "title": item.get("title", ""),
        "is_relevant": item.get("isRelevant", False),
        "relevance_score": item.get("relevanceScore", 0),
        "matched_topics": item.get("matchedTopics", []),
        "draft_id": item.get("draftId", ""),
        "status": item.get("status", ""),
        "crawled_at": item.get("crawledAt", ""),
    }


def _to_job_response(item: dict) -> dict:
    return {
        "id": item["id"],
        "feed_source_id": item.get("feedSourceId", ""),
        "started_at": item.get("startedAt", ""),
        "completed_at": item.get("completedAt", ""),
        "articles_found": item.get("articlesFound", 0),
        "articles_relevant": item.get("articlesRelevant", 0),
        "articles_processed": item.get("articlesProcessed", 0),
        "status": item.get("status", ""),
        "error": item.get("error", ""),
    }


# ---------- Endpoints ----------


@router.get("", response_model=list[FeedSourceResponse])
async def list_feeds():
    """List all configured feed sources."""
    sources = list_feed_sources()
    return [_to_feed_response(s) for s in sources]


@router.post("", response_model=FeedSourceResponse, status_code=201)
async def create_feed(request: FeedSourceCreateRequest):
    """Add a new feed source. Auto-discovers RSS feed from the URL."""
    base_url = request.base_url.strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    try:
        discovery = await asyncio.to_thread(discover_feed, base_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Feed discovery failed: {exc}")

    name = request.name.strip() or discovery.get("siteName", "") or base_url
    source = create_feed_source(
        name=name,
        base_url=base_url,
        feed_url=discovery.get("feedUrl", ""),
        feed_type=discovery.get("feedType", "html"),
        topics=request.topics,
        auto_publish_blog=request.auto_publish_blog,
        auto_publish_linkedin=request.auto_publish_linkedin,
        crawl_interval_minutes=request.crawl_interval_minutes,
    )

    # Schedule the new feed in APScheduler
    try:
        from backend.services.scheduler import schedule_feed
        schedule_feed(source)
    except Exception:
        pass  # Scheduler may not be initialized yet

    return _to_feed_response(source)


@router.get("/discover", response_model=FeedDiscoverResponse)
async def discover_feed_endpoint(url: str):
    """Discover RSS/Atom feed for a given blog URL (preview before adding)."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="url query param is required")
    try:
        result = await asyncio.to_thread(discover_feed, url.strip())
        return FeedDiscoverResponse(
            feed_url=result.get("feedUrl", ""),
            feed_type=result.get("feedType", "html"),
            site_name=result.get("siteName", ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Discovery failed: {exc}")


@router.get("/crawl-log", response_model=list[CrawlJobResponse])
async def get_crawl_log(limit: int = 50):
    """Get recent crawl jobs across all feed sources."""
    jobs = list_crawl_jobs(limit=limit)
    return [_to_job_response(j) for j in jobs]


@router.get("/{feed_id}", response_model=FeedSourceResponse)
async def get_feed(feed_id: str):
    """Get a single feed source by ID."""
    source = get_feed_source(feed_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")
    return _to_feed_response(source)


@router.put("/{feed_id}", response_model=FeedSourceResponse)
async def update_feed(feed_id: str, request: FeedSourceUpdateRequest):
    """Update a feed source's settings."""
    updates: dict = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.topics is not None:
        updates["topics"] = request.topics
    if request.crawl_interval_minutes is not None:
        updates["crawlIntervalMinutes"] = request.crawl_interval_minutes
    if request.auto_publish_blog is not None:
        updates["autoPublishBlog"] = request.auto_publish_blog
    if request.auto_publish_linkedin is not None:
        updates["autoPublishLinkedIn"] = request.auto_publish_linkedin
    if request.enabled is not None:
        updates["enabled"] = request.enabled

    result = update_feed_source(feed_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="Feed source not found")

    # Reschedule in APScheduler
    try:
        from backend.services.scheduler import reschedule_feed
        reschedule_feed(result)
    except Exception:
        pass

    return _to_feed_response(result)


@router.delete("/{feed_id}")
async def delete_feed(feed_id: str):
    """Delete a feed source."""
    deleted = delete_feed_source(feed_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Feed source not found")

    # Remove from APScheduler
    try:
        from backend.services.scheduler import unschedule_feed
        unschedule_feed(feed_id)
    except Exception:
        pass

    return {"status": "deleted", "id": feed_id}


@router.post("/{feed_id}/crawl", response_model=CrawlResultResponse)
async def trigger_crawl(feed_id: str):
    """Trigger an immediate crawl for a feed source."""
    source = get_feed_source(feed_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    try:
        result = await asyncio.to_thread(crawl_feed_source, feed_id)
        return CrawlResultResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Crawl failed: {exc}")


@router.get("/{feed_id}/articles", response_model=list[CrawledArticleResponse])
async def get_feed_articles(feed_id: str, limit: int = 50):
    """List crawled articles for a specific feed source."""
    source = get_feed_source(feed_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    articles = list_crawled_articles(feed_source_id=feed_id, limit=limit)
    return [_to_article_response(a) for a in articles]


@router.post("/{feed_id}/crawl/stream")
async def trigger_crawl_stream(feed_id: str):
    """Trigger an immediate crawl with SSE streaming progress events.

    Events: crawl_started, fetching_articles, articles_fetched, classifying,
    classified, generating, generated, generate_error, complete, error.
    """
    source = get_feed_source(feed_id)
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _run_sync_generator() -> None:
        """Run the sync generator in a thread, pushing events to the queue."""
        def _produce() -> None:
            for event in crawl_feed_source_stream(feed_id):
                queue._loop.call_soon_threadsafe(queue.put_nowait, event)  # type: ignore[attr-defined]
            queue._loop.call_soon_threadsafe(queue.put_nowait, None)  # type: ignore[attr-defined]

        await asyncio.to_thread(_produce)

    async def event_generator() -> AsyncGenerator[dict, None]:
        task = asyncio.create_task(_run_sync_generator())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {
                    "event": event["type"],
                    "data": json.dumps(event["data"]),
                }
        finally:
            task.cancel()

    return EventSourceResponse(event_generator())
