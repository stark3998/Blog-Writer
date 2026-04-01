"""Dashboard Router — Aggregated stats, article browser, and one-click actions."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.cosmos_client import (
    get_crawled_article,
    get_feed_source,
    list_crawl_jobs,
    list_crawled_articles,
    list_feed_sources,
    upsert_crawled_article,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ---------- Response Models ----------


class PipelineStatsResponse(BaseModel):
    total_articles: int
    relevant_articles: int
    irrelevant_articles: int
    drafted: int
    published: int
    errors: int
    skipped_rank: int
    linkedin_posts: int
    relevance_rate: float
    success_rate: float
    feeds_active: int
    feeds_total: int
    crawl_jobs_total: int
    crawl_jobs_failed: int
    avg_relevance_score: float
    top_topics: list[dict[str, Any]]
    daily_activity: list[dict[str, Any]]


class DashboardArticle(BaseModel):
    id: str
    feed_source_id: str
    feed_name: str
    article_url: str
    title: str
    is_relevant: bool
    relevance_score: float
    matched_topics: list[str]
    matched_keywords: list[str]
    draft_id: str
    linkedin_post_id: str
    status: str
    crawled_at: str
    hero_image_url: str = ""
    retry_count: int = 0
    last_error: str = ""


class ArticleActionResponse(BaseModel):
    article_id: str
    status: str
    message: str
    draft_id: str = ""
    linkedin_post_id: str = ""


# ---------- Helpers ----------


def _get_articles_in_range(days: int) -> list[dict[str, Any]]:
    """Get all crawled articles within the given day range."""
    all_articles = list_crawled_articles(limit=2000)
    if days <= 0:
        return all_articles

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [a for a in all_articles if a.get("crawledAt", "") >= cutoff]


def _build_feed_name_map() -> dict[str, str]:
    """Map feed source IDs to names."""
    sources = list_feed_sources()
    return {s["id"]: s.get("name", s["id"]) for s in sources}


# ---------- Endpoints ----------


@router.get("/stats", response_model=PipelineStatsResponse)
async def get_pipeline_stats(days: int = 7):
    """Get aggregated pipeline statistics for the dashboard."""
    articles = await asyncio.to_thread(_get_articles_in_range, days)
    sources = list_feed_sources()
    jobs = list_crawl_jobs(limit=500)

    # Filter jobs by time range
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        jobs = [j for j in jobs if j.get("startedAt", "") >= cutoff]

    # Count statuses
    total = len(articles)
    relevant = sum(1 for a in articles if a.get("isRelevant"))
    irrelevant = total - relevant
    drafted = sum(1 for a in articles if a.get("status") == "drafted")
    published = sum(1 for a in articles if a.get("status") == "published")
    errors = sum(1 for a in articles if a.get("status") == "error")
    skipped_rank = sum(1 for a in articles if a.get("status") == "skipped_rank")
    linkedin_posts = sum(1 for a in articles if a.get("linkedinPostId"))

    # Scores
    relevant_scores = [a.get("relevanceScore", 0) for a in articles if a.get("isRelevant")]
    avg_score = sum(relevant_scores) / len(relevant_scores) if relevant_scores else 0

    # Topic frequency
    topic_counts: dict[str, int] = {}
    for a in articles:
        for t in a.get("matchedTopics", []):
            topic_counts[t] = topic_counts.get(t, 0) + 1
    top_topics = sorted(
        [{"topic": t, "count": c} for t, c in topic_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    # Daily activity (articles crawled per day)
    daily: dict[str, dict[str, int]] = {}
    for a in articles:
        crawled = a.get("crawledAt", "")
        if crawled:
            day = crawled[:10]  # YYYY-MM-DD
            if day not in daily:
                daily[day] = {"total": 0, "relevant": 0, "processed": 0}
            daily[day]["total"] += 1
            if a.get("isRelevant"):
                daily[day]["relevant"] += 1
            if a.get("status") in ("drafted", "published"):
                daily[day]["processed"] += 1

    daily_activity = sorted(
        [{"date": d, **counts} for d, counts in daily.items()],
        key=lambda x: x["date"],
    )

    feeds_active = sum(1 for s in sources if s.get("enabled", True))
    jobs_failed = sum(1 for j in jobs if j.get("status") == "failed")

    return PipelineStatsResponse(
        total_articles=total,
        relevant_articles=relevant,
        irrelevant_articles=irrelevant,
        drafted=drafted,
        published=published,
        errors=errors,
        skipped_rank=skipped_rank,
        linkedin_posts=linkedin_posts,
        relevance_rate=round(relevant / total, 3) if total else 0,
        success_rate=round((drafted + published) / relevant, 3) if relevant else 0,
        feeds_active=feeds_active,
        feeds_total=len(sources),
        crawl_jobs_total=len(jobs),
        crawl_jobs_failed=jobs_failed,
        avg_relevance_score=round(avg_score, 3),
        top_topics=top_topics,
        daily_activity=daily_activity,
    )


@router.get("/articles", response_model=list[DashboardArticle])
async def get_dashboard_articles(
    days: int = 7,
    status: str = "",
    feed_id: str = "",
    relevant_only: bool = False,
    limit: int = 200,
):
    """Get crawled articles for the dashboard table with feed names."""
    articles = await asyncio.to_thread(_get_articles_in_range, days)
    feed_map = _build_feed_name_map()

    # Apply filters
    if status:
        articles = [a for a in articles if a.get("status") == status]
    if feed_id:
        articles = [a for a in articles if a.get("feedSourceId") == feed_id]
    if relevant_only:
        articles = [a for a in articles if a.get("isRelevant")]

    # Sort by relevance score descending
    articles.sort(key=lambda a: a.get("relevanceScore", 0), reverse=True)

    # Limit
    articles = articles[:limit]

    return [
        DashboardArticle(
            id=a["id"],
            feed_source_id=a.get("feedSourceId", ""),
            feed_name=feed_map.get(a.get("feedSourceId", ""), "Unknown"),
            article_url=a.get("articleUrl", ""),
            title=a.get("title", ""),
            is_relevant=a.get("isRelevant", False),
            relevance_score=a.get("relevanceScore", 0),
            matched_topics=a.get("matchedTopics", []),
            matched_keywords=a.get("matchedKeywords", []),
            draft_id=a.get("draftId", ""),
            linkedin_post_id=a.get("linkedinPostId", ""),
            status=a.get("status", ""),
            crawled_at=a.get("crawledAt", ""),
            hero_image_url=a.get("heroImageUrl", ""),
            retry_count=a.get("retryCount", 0),
            last_error=a.get("lastError", ""),
        )
        for a in articles
    ]


@router.post("/articles/{article_id}/regenerate", response_model=ArticleActionResponse)
async def regenerate_article(article_id: str):
    """Re-generate a blog from a crawled article (works on skipped/failed articles)."""
    record = get_crawled_article(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="Article not found")

    source = get_feed_source(record.get("feedSourceId", ""))
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    article = {
        "url": record.get("articleUrl", ""),
        "title": record.get("title", ""),
        "summary": "",
        "published": "",
    }

    try:
        from backend.services.auto_publisher import process_relevant_article

        result = await asyncio.to_thread(process_relevant_article, article, source)
        record["draftId"] = result.get("draft_id", "")
        record["status"] = result.get("status", "drafted")
        record["lastError"] = ""
        upsert_crawled_article(record)

        return ArticleActionResponse(
            article_id=article_id,
            status=record["status"],
            message=f"Blog generated: {result.get('draft_id', '')}",
            draft_id=result.get("draft_id", ""),
        )
    except Exception as exc:
        record["status"] = "error"
        record["retryCount"] = record.get("retryCount", 0) + 1
        record["lastError"] = str(exc)[:500]
        upsert_crawled_article(record)
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")


@router.post("/articles/{article_id}/linkedin", response_model=ArticleActionResponse)
async def promote_to_linkedin(article_id: str):
    """Manually publish a LinkedIn post for a crawled article."""
    record = get_crawled_article(article_id)
    if not record:
        raise HTTPException(status_code=404, detail="Article not found")

    draft_id = record.get("draftId", "")
    if not draft_id:
        raise HTTPException(
            status_code=400,
            detail="No draft exists for this article. Generate a blog first.",
        )

    source = get_feed_source(record.get("feedSourceId", ""))
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")

    try:
        from backend.services.auto_publisher import (
            _get_active_linkedin_session_id,
            _retry_with_backoff,
        )
        from backend.services.linkedin_service import compose_linkedin_post
        from backend.tools.linkedin_publisher import publish_member_post
        from backend.services.export_service import _strip_frontmatter
        import os

        session_id = _get_active_linkedin_session_id()
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="No active LinkedIn session. Connect LinkedIn in Settings first.",
            )

        # We need blog content from the draft
        from backend.db.cosmos_client import get_draft

        draft = get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        blog_base = os.environ.get("BLOG_BASE_URL", "").rstrip("/")
        blog_url = f"{blog_base}/blog/{draft.get('slug', '')}" if blog_base else ""

        linkedin_data = await asyncio.to_thread(
            compose_linkedin_post,
            blog_content=draft.get("content", ""),
            title=draft.get("title", ""),
            excerpt=draft.get("excerpt", ""),
            blog_url=blog_url,
            source_url=record.get("articleUrl", ""),
        )

        li_result = await asyncio.to_thread(
            _retry_with_backoff,
            publish_member_post,
            session_id=session_id,
            post_text=linkedin_data.get("post_text", ""),
            visibility="PUBLIC",
            image_url=linkedin_data.get("image_url", ""),
        )

        post_id = li_result.get("post_id", "")
        record["linkedinPostId"] = post_id
        record["status"] = "published"
        upsert_crawled_article(record)

        return ArticleActionResponse(
            article_id=article_id,
            status="published",
            message=f"LinkedIn post published: {post_id}",
            linkedin_post_id=post_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LinkedIn publish failed: {exc}")
