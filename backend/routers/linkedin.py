"""LinkedIn Router — Compose and publish LinkedIn content with OAuth."""

import asyncio
import json as _json
import logging
import os
import re
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft, get_published_blog, get_user_profile, publish_blog, update_draft
from backend.models.user import UserInfo
from backend.services.hashtag_agent import generate_hashtags
from backend.services.linkedin_service import compose_linkedin_post
from backend.services.config import get_blog_base_url
from backend.services.export_service import _convert_to_html, _strip_frontmatter
from backend.tools.portfolio_deployer import trigger_deploy as trigger_portfolio_deploy
from backend.tools.linkedin_publisher import (
    disconnect_session,
    get_connection_status,
    handle_oauth_callback,
    publish_member_post,
    start_oauth,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


def _ensure_blog_published(draft: dict, slug: str, blog_base: str) -> str:
    """Publish the blog to Cosmos DB if not already published and trigger a portfolio deploy.

    Returns the full blog URL. Does not verify URL reachability — the GitHub
    Pages static build takes minutes, but the URL will be live by the time
    anyone clicks it in the LinkedIn post.
    """
    blog_url = f"{blog_base}/blog/{slug}"

    # Check if blog is already published in Cosmos
    existing = get_published_blog(slug)
    if not existing:
        logger.info(f"Blog not published yet for slug '{slug}', auto-publishing before LinkedIn post")
        content = draft.get("content", "")
        if not content:
            raise HTTPException(status_code=400, detail="Draft has no content — cannot publish blog")

        html_content = _convert_to_html(content)
        metadata, _ = _strip_frontmatter(content)
        title = draft.get("title", "") or metadata.get("title", "Untitled")
        excerpt = draft.get("excerpt", "") or metadata.get("excerpt", "")
        source_url = draft.get("sourceUrl", "") or metadata.get("source_url", "")
        source_type = draft.get("sourceType", "") or metadata.get("source_type", "")
        tags = draft.get("tags", [])
        if not tags and "tags" in metadata:
            tags = re.findall(r'"([^"]+)"', str(metadata.get("tags", "")))
        date = metadata.get("date", "")

        result = publish_blog(
            slug=slug,
            title=title,
            excerpt=excerpt,
            html_content=html_content,
            mdx_content=content,
            source_url=source_url,
            source_type=source_type,
            tags=tags,
            date=date,
        )

        # Update the draft record so the editor knows it's published
        draft_id = draft.get("id", "")
        if draft_id:
            update_draft(draft_id, {
                "publishedSlug": result["slug"],
                "publishedAt": result.get("publishedAt", ""),
                "publishedUrl": f"/blog/{result['slug']}",
            })

        logger.info(f"Blog auto-published for LinkedIn: {slug}")

        # Trigger portfolio GitHub Pages rebuild so the static site picks up the new post
        try:
            deploy_result = trigger_portfolio_deploy()
            logger.info(f"Portfolio deploy triggered: {deploy_result}")
        except Exception as deploy_exc:
            logger.warning(f"Portfolio deploy trigger failed (non-blocking): {deploy_exc}")

    return blog_url


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
    # If blog is not published, publish it first so the URL is active
    blog_url = request.blog_url
    if not blog_url:
        blog_base = get_blog_base_url()
        if blog_base:
            slug = ""
            if request.draft_id and draft:
                slug = draft.get("slug", "")
            if not slug and content:
                m = re.search(r'^slug:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                if m:
                    slug = m.group(1).strip()
            if slug:
                if draft:
                    blog_url = _ensure_blog_published(draft, slug, blog_base)
                else:
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


@router.post("/compose/stream")
async def compose_post_stream(request: LinkedInComposeRequest, user: UserInfo = Depends(get_current_user)):
    """SSE streaming compose — sends progress events for each workflow step."""
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

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _run() -> None:
        def _produce() -> None:
            _emit = lambda evt, data: queue._loop.call_soon_threadsafe(  # type: ignore[attr-defined]
                queue.put_nowait, {"type": evt, "data": data}
            )

            blog_url = request.blog_url
            try:
                # --- Step 1: Resolve blog URL & publish if needed ---
                slug = ""
                if request.draft_id and draft:
                    slug = draft.get("slug", "")
                if not slug and content:
                    m = re.search(r'^slug:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
                    if m:
                        slug = m.group(1).strip()

                if not blog_url and slug:
                    blog_base = get_blog_base_url()
                    if blog_base:
                        existing = get_published_blog(slug)
                        if existing:
                            _emit("step", {"step": "blog_check", "status": "complete", "message": "Blog already published"})
                            blog_url = f"{blog_base}/blog/{slug}"
                        elif draft:
                            _emit("step", {"step": "blog_publish", "status": "running", "message": "Publishing blog..."})
                            try:
                                blog_url = _ensure_blog_published(draft, slug, blog_base)
                                _emit("step", {"step": "blog_publish", "status": "complete", "message": "Blog published"})
                            except Exception as exc:
                                _emit("step", {"step": "blog_publish", "status": "error", "message": f"Blog publish failed: {str(exc)[:200]}"})
                                blog_url = f"{blog_base}/blog/{slug}"
                        else:
                            blog_url = f"{blog_base}/blog/{slug}"
                            _emit("step", {"step": "blog_check", "status": "complete", "message": "Blog URL resolved"})
                else:
                    _emit("step", {"step": "blog_check", "status": "complete", "message": "Blog URL provided" if blog_url else "No blog URL"})

                # --- Step 2: Compose LinkedIn post via AI ---
                _emit("step", {"step": "compose", "status": "running", "message": "AI is crafting your post..."})
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
                _emit("step", {"step": "compose", "status": "complete", "message": "Post composed"})

                # --- Step 3: Done ---
                _emit("complete", result)

            except Exception as exc:
                _emit("error", {"message": str(exc)[:500]})

            queue._loop.call_soon_threadsafe(queue.put_nowait, None)  # type: ignore[attr-defined]

        await asyncio.to_thread(_produce)

    async def event_generator() -> AsyncGenerator[dict, None]:
        task = asyncio.create_task(_run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {
                    "event": event["type"],
                    "data": _json.dumps(event["data"]),
                }
        finally:
            task.cancel()

    return EventSourceResponse(event_generator())


@router.post("/publish", response_model=LinkedInPublishResponse)
async def publish_post(request: LinkedInPublishRequest):
    """Publish to LinkedIn using composed content or provided post_text.

    If a draft_id is provided and the blog is not yet published,
    auto-publishes the blog first so the blog URL is active.
    """
    publish_text = request.post_text.strip()
    composed = False
    draft = None

    # Ensure blog is published before posting to LinkedIn
    if request.draft_id:
        draft = get_draft(request.draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")
        slug = draft.get("slug", "")
        blog_base = get_blog_base_url()
        if slug and blog_base:
            _ensure_blog_published(draft, slug, blog_base)

    if not publish_text:
        content = request.content
        title = request.title
        excerpt = request.excerpt

        if draft:
            content = draft.get("content", "")
            title = title or draft.get("title", "")
            excerpt = excerpt or draft.get("excerpt", "")

        if not content or not content.strip():
            raise HTTPException(
                status_code=400,
                detail="Provide post_text, or provide content/draft_id to compose from",
            )

        # Build blog_url for composition
        blog_url = ""
        if draft:
            slug = draft.get("slug", "")
            blog_base = get_blog_base_url()
            if slug and blog_base:
                blog_url = f"{blog_base}/blog/{slug}"

        try:
            composed_payload = compose_linkedin_post(
                blog_content=content,
                title=title,
                excerpt=excerpt,
                post_format=request.post_format,
                additional_context=request.additional_context,
                blog_url=blog_url,
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
