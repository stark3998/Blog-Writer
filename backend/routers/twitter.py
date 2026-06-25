"""Twitter/X Router — Compose and publish tweets with OAuth 2.0 PKCE."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft
from backend.models.user import UserInfo
from backend.services.twitter_service import compose_tweet, compose_thread
from backend.tools.twitter_publisher import (
    disconnect_session,
    get_connection_status,
    handle_oauth_callback,
    publish_tweet,
    publish_thread,
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


class TwitterThreadTweet(BaseModel):
    position: int
    tweet: str


class TwitterComposeThreadRequest(BaseModel):
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    blog_url: str = ""
    source_url: str = ""
    additional_context: str = ""


class TwitterComposeThreadResponse(BaseModel):
    tweets: list[TwitterThreadTweet]
    thread_length: int
    title: str
    excerpt: str


class TwitterPublishThreadRequest(BaseModel):
    session_id: str
    tweets: list[str] | None = None
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    blog_url: str = ""
    source_url: str = ""


class TwitterPublishThreadResponse(BaseModel):
    session_id: str
    thread_id: str
    tweet_ids: list[str]
    tweet_count: int
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


@router.post("/compose/thread", response_model=TwitterComposeThreadResponse)
async def compose_thread_post(request: TwitterComposeThreadRequest):
    """Compose a 4-6 tweet thread from blog content."""
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
        result = compose_thread(
            blog_content=content,
            title=title,
            excerpt=excerpt,
            blog_url=request.blog_url,
            source_url=request.source_url,
            additional_context=request.additional_context,
        )
        return TwitterComposeThreadResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Thread compose failed: {str(exc)}")


@router.post("/publish/thread", response_model=TwitterPublishThreadResponse)
async def publish_thread_post(request: TwitterPublishThreadRequest):
    """Publish a Twitter thread — optionally compose from content first."""
    tweet_texts = request.tweets
    composed = False

    if not tweet_texts:
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
            raise HTTPException(status_code=400, detail="Provide tweets list or content/draft_id to compose from")

        try:
            thread_data = compose_thread(
                blog_content=content,
                title=title,
                excerpt=excerpt,
                blog_url=request.blog_url,
                source_url=request.source_url,
            )
            tweet_texts = [t["tweet"] for t in thread_data.get("tweets", [])]
            composed = True
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Thread compose failed: {str(exc)}")

    if not tweet_texts:
        raise HTTPException(status_code=400, detail="No tweets to publish")

    try:
        result = publish_thread(session_id=request.session_id, tweets=tweet_texts)
        return TwitterPublishThreadResponse(**result, composed=composed)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Thread publish failed: {str(exc)}")
