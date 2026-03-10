"""LinkedIn Router — Compose and publish LinkedIn content with OAuth."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.cosmos_client import get_draft
from backend.services.linkedin_service import compose_linkedin_post
from backend.tools.linkedin_publisher import (
    disconnect_session,
    get_connection_status,
    handle_oauth_callback,
    publish_member_post,
    start_oauth,
)

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


class LinkedInComposeRequest(BaseModel):
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    post_format: Literal["feed_post", "long_form"] = "feed_post"
    additional_context: str = ""


class LinkedInComposeResponse(BaseModel):
    format: str
    title: str
    excerpt: str
    summary: str
    insights: list[str]
    my_2_cents: str
    hashtags: list[str]
    post_text: str
    word_count: int


class LinkedInOAuthStartResponse(BaseModel):
    session_id: str
    state: str
    auth_url: str


class LinkedInOAuthCallbackRequest(BaseModel):
    code: str
    state: str


class LinkedInOAuthCallbackResponse(BaseModel):
    session_id: str
    person_urn: str
    expires_at: float


class LinkedInStatusResponse(BaseModel):
    connected: bool
    session_id: str
    person_urn: str = ""
    expires_at: float = 0


class LinkedInPublishRequest(BaseModel):
    session_id: str
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    post_format: Literal["feed_post", "long_form"] = "feed_post"
    additional_context: str = ""
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    post_text: str = ""


class LinkedInPublishResponse(BaseModel):
    session_id: str
    post_id: str
    visibility: str
    status_code: int
    composed: bool
    post_text: str


@router.get("/oauth/start", response_model=LinkedInOAuthStartResponse)
async def oauth_start(session_id: str | None = None):
    """Generate LinkedIn OAuth authorization URL for a session."""
    try:
        result = start_oauth(session_id)
        return LinkedInOAuthStartResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/oauth/callback", response_model=LinkedInOAuthCallbackResponse)
async def oauth_callback(request: LinkedInOAuthCallbackRequest):
    """Exchange authorization code and store linked session token."""
    try:
        result = handle_oauth_callback(request.code, request.state)
        return LinkedInOAuthCallbackResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=LinkedInStatusResponse)
async def oauth_status(session_id: str):
    """Return OAuth connection status for a LinkedIn session."""
    result = get_connection_status(session_id)
    return LinkedInStatusResponse(**result)


@router.delete("/disconnect")
async def oauth_disconnect(session_id: str):
    """Disconnect a LinkedIn session and remove in-memory token."""
    disconnect_session(session_id)
    return {"status": "disconnected", "session_id": session_id}


@router.post("/compose", response_model=LinkedInComposeResponse)
async def compose_post(request: LinkedInComposeRequest):
    """Compose an optimized LinkedIn post from blog content or a saved draft."""
    content = request.content
    title = request.title
    excerpt = request.excerpt

    if request.draft_id:
        draft = get_draft(request.draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")
        content = draft.get("content", "")
        title = title or draft.get("title", "")
        excerpt = excerpt or draft.get("excerpt", "")

    if not content or not content.strip():
        raise HTTPException(
            status_code=400,
            detail="Either non-empty content or a valid draft_id is required",
        )

    try:
        result = compose_linkedin_post(
            blog_content=content,
            title=title,
            excerpt=excerpt,
            post_format=request.post_format,
            additional_context=request.additional_context,
        )
        return LinkedInComposeResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LinkedIn compose failed: {str(exc)}")


@router.post("/publish", response_model=LinkedInPublishResponse)
async def publish_post(request: LinkedInPublishRequest):
    """Publish to LinkedIn using composed content or provided post_text."""
    publish_text = request.post_text.strip()
    composed = False

    if not publish_text:
        content = request.content
        title = request.title
        excerpt = request.excerpt

        if request.draft_id:
            draft = get_draft(request.draft_id)
            if not draft:
                raise HTTPException(status_code=404, detail="Draft not found")
            content = draft.get("content", "")
            title = title or draft.get("title", "")
            excerpt = excerpt or draft.get("excerpt", "")

        if not content or not content.strip():
            raise HTTPException(
                status_code=400,
                detail="Provide post_text, or provide content/draft_id to compose from",
            )

        try:
            composed_payload = compose_linkedin_post(
                blog_content=content,
                title=title,
                excerpt=excerpt,
                post_format=request.post_format,
                additional_context=request.additional_context,
            )
            publish_text = composed_payload.get("post_text", "").strip()
            composed = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"LinkedIn compose failed: {str(exc)}")

    if not publish_text:
        raise HTTPException(status_code=400, detail="Post text is empty after composition")

    try:
        result = publish_member_post(
            session_id=request.session_id,
            post_text=publish_text,
            visibility=request.visibility,
        )
        return LinkedInPublishResponse(
            **result,
            composed=composed,
            post_text=publish_text,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LinkedIn publish failed: {str(exc)}")
