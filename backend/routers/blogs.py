"""Blogs Router — CRUD endpoints for blog drafts in Cosmos DB."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from backend.db.cosmos_client import (
    list_drafts,
    get_draft,
    create_draft,
    update_draft,
    delete_draft,
)

router = APIRouter(prefix="/api/blogs", tags=["blogs"])


class CreateDraftRequest(BaseModel):
    title: str
    slug: str
    excerpt: str = ""
    content: str
    source_url: str = ""
    source_type: str = "unknown"
    origin: str = "user"
    tags: list[str] = []


class UpdateDraftRequest(BaseModel):
    title: str | None = None
    slug: str | None = None
    excerpt: str | None = None
    content: str | None = None


class DraftSummary(BaseModel):
    id: str
    title: str
    slug: str
    excerpt: str
    sourceUrl: str
    sourceType: str
    origin: str = "user"
    tags: list[str] = []
    createdAt: str
    updatedAt: str


class DraftFull(DraftSummary):
    content: str


@router.get("", response_model=list[DraftSummary])
async def list_all_drafts(limit: int = 50):
    """List all blog drafts (without full content)."""
    try:
        drafts = list_drafts(limit=limit)
        return drafts
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list drafts: {str(e)}")


@router.get("/{draft_id}", response_model=DraftFull)
async def get_single_draft(draft_id: str):
    """Get a single draft by ID (includes full content)."""
    try:
        draft = get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        return draft
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get draft: {str(e)}")


@router.post("", response_model=DraftFull, status_code=201)
async def create_new_draft(request: CreateDraftRequest):
    """Create a new blog draft."""
    try:
        draft = create_draft(
            title=request.title,
            slug=request.slug,
            excerpt=request.excerpt,
            content=request.content,
            source_url=request.source_url,
            source_type=request.source_type,
            origin=request.origin,
            tags=request.tags,
        )
        return draft
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create draft: {str(e)}")


@router.put("/{draft_id}", response_model=DraftFull)
async def update_existing_draft(draft_id: str, request: UpdateDraftRequest):
    """Update an existing draft."""
    try:
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        draft = update_draft(draft_id, updates)
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        return draft
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update draft: {str(e)}")


@router.delete("/{draft_id}", status_code=204)
async def delete_existing_draft(draft_id: str):
    """Delete a draft by ID."""
    try:
        deleted = delete_draft(draft_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Draft not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete draft: {str(e)}")
