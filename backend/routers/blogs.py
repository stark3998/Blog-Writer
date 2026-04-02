"""Blogs Router — CRUD endpoints for blog drafts in Cosmos DB."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    list_drafts,
    get_draft,
    create_draft,
    update_draft,
    delete_draft,
    delete_all_drafts,
    save_draft_version,
    list_draft_versions,
    get_draft_version,
    list_published_blogs,
)
from backend.models.user import UserInfo

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
    publishedSlug: str | None = None
    publishedAt: str | None = None
    publishedUrl: str | None = None


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
    publishedSlug: str | None = None
    publishedAt: str | None = None
    publishedUrl: str | None = None


@router.get("", response_model=list[DraftSummary])
async def list_all_drafts(limit: int = 50, user: UserInfo = Depends(get_current_user)):
    """List all blog drafts (without full content)."""
    try:
        drafts = list_drafts(limit=limit, user_id=user.user_id)
        return drafts
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list drafts: {str(e)}")


@router.get("/published")
async def list_published(limit: int = 50):
    """List all published blogs (metadata only)."""
    return list_published_blogs(limit=limit)


@router.delete("/all")
async def delete_all_drafts_endpoint():
    """Delete all blog drafts."""
    try:
        count = delete_all_drafts()
        return {"status": "deleted", "count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete drafts: {str(e)}")


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
async def create_new_draft(request: CreateDraftRequest, user: UserInfo = Depends(get_current_user)):
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
            user_id=user.user_id,
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


# ---------- Raw Cosmos Data ----------

COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


@router.get("/{draft_id}/raw")
async def get_draft_raw(draft_id: str):
    """Return the full raw Cosmos DB document for a draft."""
    try:
        doc = get_draft(draft_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        return doc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get raw draft: {str(e)}")


@router.put("/{draft_id}/raw")
async def update_draft_raw(draft_id: str, body: dict[str, Any]):
    """Replace the Cosmos DB document with the provided JSON.

    The id and partition key are preserved; Cosmos system fields (_rid, _etag, etc.) are stripped.
    """
    from backend.db.cosmos_client import _get_container

    try:
        container = _get_container()

        # Read existing to verify it exists
        try:
            existing = container.read_item(item=draft_id, partition_key=draft_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Draft not found")

        # Strip system keys from incoming body
        cleaned = {k: v for k, v in body.items() if k not in COSMOS_SYSTEM_KEYS}

        # Force id to stay consistent
        cleaned["id"] = draft_id

        result = container.replace_item(item=draft_id, body=cleaned)
        return dict(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update raw draft: {str(e)}")


# ---------- Version History ----------


class VersionSummary(BaseModel):
    id: str
    draftId: str
    title: str = ""
    contentLength: int = 0
    trigger: str = ""
    createdAt: str = ""


class VersionFull(VersionSummary):
    content: str = ""


@router.get("/{draft_id}/versions", response_model=list[VersionSummary])
async def list_versions(draft_id: str, limit: int = 20):
    """List version history for a draft."""
    try:
        versions = list_draft_versions(draft_id, limit=limit)
        return versions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list versions: {str(e)}")


@router.get("/{draft_id}/versions/{version_id}", response_model=VersionFull)
async def get_version(draft_id: str, version_id: str):
    """Get a specific version with full content."""
    try:
        version = get_draft_version(version_id)
        if version is None or version.get("draftId") != draft_id:
            raise HTTPException(status_code=404, detail="Version not found")
        return version
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get version: {str(e)}")


@router.post("/{draft_id}/versions", response_model=VersionSummary, status_code=201)
async def create_version(draft_id: str):
    """Manually save a version snapshot of the current draft state."""
    draft = get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        version = save_draft_version(
            draft_id=draft_id,
            content=draft.get("content", ""),
            title=draft.get("title", ""),
            trigger="manual",
        )
        return version
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save version: {str(e)}")


@router.post("/{draft_id}/versions/{version_id}/restore", response_model=DraftFull)
async def restore_version(draft_id: str, version_id: str):
    """Restore a draft to a previous version."""
    version = get_draft_version(version_id)
    if version is None or version.get("draftId") != draft_id:
        raise HTTPException(status_code=404, detail="Version not found")

    # Save current state as a version first
    draft = get_draft(draft_id)
    if draft:
        try:
            save_draft_version(
                draft_id=draft_id,
                content=draft.get("content", ""),
                title=draft.get("title", ""),
                trigger="pre_restore",
            )
        except Exception:
            pass

    updated = update_draft(draft_id, {"content": version.get("content", "")})
    if updated is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return updated


class TestReadinessRequest(BaseModel):
    topics: list[str] = ["cloud security", "azure", "ai"]


class RelevanceResult(BaseModel):
    is_relevant: bool
    relevance_score: float
    matched_topics: list[str]
    matched_keywords: list[str]
    method: str
    reasoning: str


class LinkedInPreview(BaseModel):
    post_text: str
    hashtags: list[str]
    word_count: int
    image_url: str


class TestReadinessResponse(BaseModel):
    relevance: RelevanceResult
    linkedin_preview: LinkedInPreview | None = None


@router.post("/{draft_id}/test-readiness", response_model=TestReadinessResponse)
async def test_draft_readiness(draft_id: str, request: TestReadinessRequest):
    """Test a draft's technical relevance score and preview LinkedIn post.

    Runs the same two-stage relevance classifier used during RSS crawls
    and generates a LinkedIn post preview without publishing.
    """
    draft = get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    title = draft.get("title", "")
    excerpt = draft.get("excerpt", "")
    content = draft.get("content", "")

    # Run relevance classification in a thread (calls LLM)
    from backend.services.relevance_classifier import classify_article

    try:
        classification = await asyncio.to_thread(
            classify_article,
            title=title,
            summary=excerpt,
            content=content[:3000],
            topics=request.topics,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relevance check failed: {str(e)}")

    relevance = RelevanceResult(
        is_relevant=classification.get("is_relevant", False),
        relevance_score=classification.get("relevance_score", 0),
        matched_topics=classification.get("matched_topics", []),
        matched_keywords=classification.get("matched_keywords", []),
        method=classification.get("method", ""),
        reasoning=classification.get("reasoning", ""),
    )

    # Compose LinkedIn post preview
    linkedin_preview = None
    try:
        from backend.services.linkedin_service import compose_linkedin_post

        li_result = await asyncio.to_thread(
            compose_linkedin_post,
            blog_content=content,
            title=title,
            excerpt=excerpt,
            source_url=draft.get("sourceUrl", ""),
        )
        linkedin_preview = LinkedInPreview(
            post_text=li_result.get("post_text", ""),
            hashtags=li_result.get("hashtags", []),
            word_count=li_result.get("word_count", 0),
            image_url=li_result.get("image_url", ""),
        )
    except Exception:
        pass  # LinkedIn preview is optional

    return TestReadinessResponse(
        relevance=relevance,
        linkedin_preview=linkedin_preview,
    )
