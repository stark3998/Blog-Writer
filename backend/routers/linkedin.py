"""LinkedIn Router — Compose and publish LinkedIn content with OAuth."""

import os
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft, get_user_profile
from backend.models.user import UserInfo
from backend.services.hashtag_agent import generate_hashtags
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
    blog_url: str = ""
    source_url: str = ""


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
    image_url: str = ""
    validation: dict | None = None


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
    image_url: str = ""


class LinkedInPublishResponse(BaseModel):
    session_id: str
    post_id: str
    visibility: str
    status_code: int
    composed: bool
    post_text: str
    image_included: bool = False
    image_failed: bool = False


class GenerateImageRequest(BaseModel):
    title: str = ""
    excerpt: str = ""
    topics: list[str] = []


class GenerateImageResponse(BaseModel):
    image_url: str


class HashtagRequest(BaseModel):
    content: str = ""
    title: str = ""
    excerpt: str = ""


class HashtagResponse(BaseModel):
    topics: list[str]
    hashtags: list[dict]
    final_tags: list[str]


@router.post("/generate-image", response_model=GenerateImageResponse)
async def generate_image(request: GenerateImageRequest, user: UserInfo = Depends(get_current_user)):
    """Generate a hero image for a LinkedIn post using AI."""
    if not request.title.strip():
        raise HTTPException(status_code=400, detail="Title is required to generate an image")
    try:
        from backend.services.image_generator import generate_hero_image
        image_url = generate_hero_image(
            title=request.title,
            excerpt=request.excerpt,
            topics=request.topics or ["technology"],
        )
        return GenerateImageResponse(image_url=image_url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Image generation failed: {str(exc)}")


@router.post("/hashtags", response_model=HashtagResponse)
async def regenerate_hashtags(request: HashtagRequest):
    """Generate trend-optimized hashtags for the given content."""
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content is required")
    try:
        result = generate_hashtags(
            content=request.content,
            title=request.title,
            excerpt=request.excerpt,
            platform="linkedin",
        )
        return HashtagResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Hashtag generation failed: {str(exc)}")


@router.get("/oauth/start", response_model=LinkedInOAuthStartResponse)
async def oauth_start(session_id: str | None = None, user: UserInfo = Depends(get_current_user)):
    """Generate LinkedIn OAuth authorization URL for a session."""
    try:
        result = start_oauth(session_id)
        return LinkedInOAuthStartResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """Handle LinkedIn OAuth redirect — exchange code, notify opener popup, and close."""
    from fastapi.responses import HTMLResponse

    if error:
        return HTMLResponse(
            f"<html><body><script>"
            f"window.opener && window.opener.postMessage({{type:'linkedin-oauth-error',error:{repr(error_description or error)}}}, '*');"
            f"window.close();"
            f"</script><p>LinkedIn auth failed: {error_description or error}. You can close this window.</p></body></html>"
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")
    try:
        result = handle_oauth_callback(code, state)
        session_id = result["session_id"]
        return HTMLResponse(
            f"<html><body><script>"
            f"window.opener && window.opener.postMessage({{type:'linkedin-oauth-callback',session_id:'{session_id}'}}, '*');"
            f"window.close();"
            f"</script><p>LinkedIn connected! This window should close automatically.</p></body></html>"
        )
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
async def compose_post(request: LinkedInComposeRequest, user: UserInfo = Depends(get_current_user)):
    """Compose an optimized LinkedIn post from blog content or a saved draft."""
    # Get user's image handling preference
    profile = get_user_profile(user.user_id) if user.user_id != "local-dev" else None
    image_handling = (profile or {}).get("settings", {}).get("image_handling", "regenerate_on_share")

    content = request.content
    title = request.title
    excerpt = request.excerpt
    draft = None

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

    # Auto-populate blog_url from BLOG_BASE_URL + slug if not provided
    blog_url = request.blog_url
    if not blog_url:
        blog_base = os.environ.get("BLOG_BASE_URL", "").rstrip("/")
        if blog_base:
            slug = ""
            if request.draft_id and draft:
                slug = draft.get("slug", "")
            if not slug and content:
                m = re.search(r'^slug:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                if m:
                    slug = m.group(1).strip()
            if slug:
                blog_url = f"{blog_base}/blog/{slug}"

    try:
        result = compose_linkedin_post(
            blog_content=content,
            title=title,
            excerpt=excerpt,
            post_format=request.post_format,
            additional_context=request.additional_context,
            blog_url=blog_url,
            source_url=request.source_url,
            image_handling=image_handling,
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
            image_url=request.image_url,
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
