"""News Hub Router — Public endpoint for auto-published Azure news posts (no auth required)."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.cosmos_client import list_news_posts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["news"])


class NewsPost(BaseModel):
    slug: str
    title: str
    excerpt: str
    source_url: str
    published_at: str
    updated_at: str
    tags: list[str]


class NewsListResponse(BaseModel):
    posts: list[NewsPost]
    count: int
    limit: int
    offset: int


@router.get("", response_model=NewsListResponse)
async def list_news(limit: int = 20, offset: int = 0, tag: str | None = None):
    """List auto-published Azure news posts for the public news hub.

    No authentication required — fully public and indexable by search engines.
    """
    limit = min(limit, 50)
    try:
        posts = list_news_posts(limit=limit, offset=offset, tag=tag)
        return NewsListResponse(
            posts=[
                NewsPost(
                    slug=p.get("slug", p.get("id", "")),
                    title=p.get("title", ""),
                    excerpt=p.get("excerpt", ""),
                    source_url=p.get("sourceUrl", ""),
                    published_at=p.get("publishedAt", ""),
                    updated_at=p.get("updatedAt", ""),
                    tags=p.get("tags", []),
                )
                for p in posts
            ],
            count=len(posts),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error(f"News list failed: {exc}")
        return NewsListResponse(posts=[], count=0, limit=limit, offset=offset)
