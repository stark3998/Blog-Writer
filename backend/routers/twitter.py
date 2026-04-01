"""Twitter/X Router — Compose and publish tweets with OAuth 2.0 PKCE."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft
from backend.models.user import UserInfo
from backend.services.twitter_service import compose_tweet
from backend.tools.twitter_publisher import (
    disconnect_session,
    get_connection_status,
    handle_oauth_callback,
    publish_tweet,
    start_oauth,
)

router = APIRouter(prefix="/api/twitter", tags=["twitter"])


class TwitterComposeRequest(BaseModel):
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    blog_url: str = ""
    additional_context: str = ""


class TwitterComposeResponse(BaseModel):
    tweet_text: str
    hashtags: list[str]
    char_count: int
    title: str
    excerpt: str


class TwitterOAuthStartResponse(BaseModel):
    session_id: str
    state: str
    auth_url: str


class TwitterStatusResponse(BaseModel):
    connected: bool
    session_id: str
    username: str = ""
    expires_at: float = 0


class TwitterPublishRequest(BaseModel):
    session_id: str
    tweet_text: str = ""
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    blog_url: str = ""


class TwitterPublishResponse(BaseModel):
    session_id: str
    tweet_id: str
    text: str
    status_code: int
    composed: bool


@router.get("/oauth/start", response_model=TwitterOAuthStartResponse)
async def oauth_start(session_id: str | None = None, user: UserInfo = Depends(get_current_user)):
    """Generate Twitter OAuth authorization URL for a session."""
    try:
        result = start_oauth(session_id)
        return TwitterOAuthStartResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """Handle Twitter OAuth redirect."""
    from fastapi.responses import HTMLResponse

    if error:
        return HTMLResponse(
            f"<html><body><script>"
            f"window.opener && window.opener.postMessage({{type:'twitter-oauth-error',error:{repr(error_description or error)}}}, '*');"
            f"window.close();"
            f"</script><p>Twitter auth failed: {error_description or error}. You can close this window.</p></body></html>"
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")
    try:
        result = handle_oauth_callback(code, state)
        session_id = result["session_id"]
        return HTMLResponse(
            f"<html><body><script>"
            f"window.opener && window.opener.postMessage({{type:'twitter-oauth-callback',session_id:'{session_id}'}}, '*');"
            f"window.close();"
            f"</script><p>Twitter connected! This window should close automatically.</p></body></html>"
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=TwitterStatusResponse)
async def oauth_status(session_id: str):
    """Return OAuth connection status for a Twitter session."""
    result = get_connection_status(session_id)
    return TwitterStatusResponse(**result)


@router.delete("/disconnect")
async def oauth_disconnect(session_id: str):
    """Disconnect a Twitter session."""
    disconnect_session(session_id)
    return {"status": "disconnected", "session_id": session_id}


@router.post("/compose", response_model=TwitterComposeResponse)
async def compose_post(request: TwitterComposeRequest):
    """Compose an optimized tweet from blog content."""
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
        raise HTTPException(status_code=400, detail="Either non-empty content or a valid draft_id is required")

    try:
        result = compose_tweet(
            blog_content=content,
            title=title,
            excerpt=excerpt,
            blog_url=request.blog_url,
            additional_context=request.additional_context,
        )
        return TwitterComposeResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Twitter compose failed: {str(exc)}")


@router.post("/publish", response_model=TwitterPublishResponse)
async def publish_post(request: TwitterPublishRequest):
    """Publish a tweet using composed text or auto-compose from content."""
    tweet_text = request.tweet_text.strip()
    composed = False

    if not tweet_text:
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
            raise HTTPException(status_code=400, detail="Provide tweet_text or content/draft_id to compose from")

        try:
            composed_payload = compose_tweet(
                blog_content=content, title=title, excerpt=excerpt, blog_url=request.blog_url,
            )
            tweet_text = composed_payload.get("tweet_text", "").strip()
            composed = True
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Twitter compose failed: {str(exc)}")

    if not tweet_text:
        raise HTTPException(status_code=400, detail="Tweet text is empty after composition")

    try:
        result = publish_tweet(session_id=request.session_id, text=tweet_text)
        return TwitterPublishResponse(**result, composed=composed, text=tweet_text)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Twitter publish failed: {str(exc)}")
