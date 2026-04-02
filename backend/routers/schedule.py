"""Schedule Router — Create, list, and cancel scheduled publishes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    create_scheduled_publish,
    get_draft,
    get_scheduled_publish,
    list_scheduled_publishes,
    cancel_scheduled_publish,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


class CreateScheduleRequest(BaseModel):
    draft_id: str
    scheduled_at: str  # ISO 8601 datetime string
    platforms: list[str]  # e.g. ["blog", "linkedin", "twitter", "medium"]


class ScheduleResponse(BaseModel):
    id: str
    draftId: str
    scheduledAt: str
    platforms: list[str]
    status: str
    createdAt: str
    completedAt: str = ""
    error: str = ""


VALID_PLATFORMS = {"blog", "linkedin", "twitter", "medium"}


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    request: CreateScheduleRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Schedule a draft for future publishing on one or more platforms."""
    # Validate platforms
    invalid = set(request.platforms) - VALID_PLATFORMS
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platforms: {', '.join(invalid)}. Valid: {', '.join(sorted(VALID_PLATFORMS))}",
        )
    if not request.platforms:
        raise HTTPException(status_code=400, detail="At least one platform is required")

    # Validate draft exists
    draft = get_draft(request.draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Validate scheduled_at is a valid ISO datetime in the future
    try:
        scheduled_dt = datetime.fromisoformat(request.scheduled_at)
        # Ensure timezone-aware; assume UTC if naive
        if scheduled_dt.tzinfo is None:
            scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="scheduled_at must be a valid ISO 8601 datetime string",
        )

    if scheduled_dt <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400, detail="scheduled_at must be in the future"
        )

    try:
        record = create_scheduled_publish(
            draft_id=request.draft_id,
            scheduled_at=scheduled_dt.isoformat(),
            platforms=request.platforms,
            user_id=user.user_id,
        )
        return record
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create schedule: {str(e)}"
        )


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    status: str | None = Query(None, description="Filter by status (pending, completed, failed, cancelled)"),
    limit: int = Query(50, ge=1, le=200),
    user: UserInfo = Depends(get_current_user),
):
    """List all scheduled publishes, optionally filtered by status."""
    try:
        items = list_scheduled_publishes(status=status, limit=limit)
        return items
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list schedules: {str(e)}"
        )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Get a specific scheduled publish by ID."""
    record = get_scheduled_publish(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return record


@router.delete("/{schedule_id}", response_model=ScheduleResponse)
async def cancel_schedule(
    schedule_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Cancel a pending scheduled publish."""
    existing = get_scheduled_publish(schedule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if existing.get("status") != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel schedule with status '{existing.get('status')}'. Only pending schedules can be cancelled.",
        )

    result = cancel_scheduled_publish(schedule_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to cancel schedule")
    return result
