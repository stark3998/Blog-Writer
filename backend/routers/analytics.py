"""Analytics Router — Post performance tracking and engagement metrics."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    get_post_analytics,
    get_analytics_overview,
    record_post_event,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class RecordEventRequest(BaseModel):
    slug: str
    event_type: str
    platform: str = "blog"
    metadata: dict[str, Any] = {}


class PostAnalyticsResponse(BaseModel):
    slug: str
    days: int
    events: dict[str, int]


class AnalyticsOverviewItem(BaseModel):
    slug: str
    events: dict[str, int]


@router.post("/event")
async def track_event(request: RecordEventRequest):
    """Record an analytics event (page view, share, click, etc.)."""
    record_post_event(
        slug=request.slug,
        event_type=request.event_type,
        platform=request.platform,
        metadata=request.metadata,
    )
    return {"status": "recorded"}


@router.get("/post/{slug}", response_model=PostAnalyticsResponse)
async def get_post_stats(slug: str, days: int = 30):
    """Get analytics for a specific published post."""
    return get_post_analytics(slug, days)


@router.get("/overview", response_model=list[AnalyticsOverviewItem])
async def get_overview(days: int = 30, user: UserInfo = Depends(get_current_user)):
    """Get analytics overview across all posts."""
    return get_analytics_overview(days)
