"""Medium Router — Connect and publish articles to Medium."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft
from backend.models.user import UserInfo
from backend.services.medium_service import prepare_medium_article
from backend.tools.medium_publisher import (
    connect_with_token,
    disconnect_session,
    get_connection_status,
    publish_article,
)

router = APIRouter(prefix="/api/medium", tags=["medium"])


class MediumConnectRequest(BaseModel):
    integration_token: str
    session_id: str | None = None


class MediumConnectResponse(BaseModel):
    session_id: str
    author_id: str
    username: str
    name: str


class MediumStatusResponse(BaseModel):
    connected: bool
    session_id: str
    username: str = ""
    author_id: str = ""


class MediumPublishRequest(BaseModel):
    session_id: str
    content: str | None = None
    draft_id: str | None = None
    title: str = ""
    excerpt: str = ""
    tags: list[str] = []
    blog_url: str = ""
    publish_status: str = "draft"  # "public", "draft", or "unlisted"


class MediumPublishResponse(BaseModel):
    session_id: str
    post_id: str
    url: str
    title: str
    publish_status: str
    status_code: int


@router.post("/connect", response_model=MediumConnectResponse)
async def connect(request: MediumConnectRequest, user: UserInfo = Depends(get_current_user)):
    """Connect to Medium using an integration token."""
    session_id = request.session_id or str(uuid.uuid4())
    try:
        result = connect_with_token(session_id, request.integration_token)
        return MediumConnectResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=MediumStatusResponse)
async def status(session_id: str):
    """Return connection status for a Medium session."""
    result = get_connection_status(session_id)
    return MediumStatusResponse(**result)


@router.delete("/disconnect")
async def disconnect(session_id: str):
    """Disconnect a Medium session."""
    disconnect_session(session_id)
    return {"status": "disconnected", "session_id": session_id}


@router.post("/publish", response_model=MediumPublishResponse)
async def publish_post(request: MediumPublishRequest):
    """Publish an article to Medium from blog content or a draft."""
    content = request.content
    title = request.title
    excerpt = request.excerpt
    tags = request.tags

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
        prepared = prepare_medium_article(
            blog_content=content,
            title=title,
            excerpt=excerpt,
            blog_url=request.blog_url,
        )

        final_title = prepared["title"]
        html_content = prepared["html_content"]
        final_tags = tags if tags else prepared.get("tags", [])

        result = publish_article(
            session_id=request.session_id,
            title=final_title,
            content_html=html_content,
            tags=final_tags,
            canonical_url=request.blog_url,
            publish_status=request.publish_status,
        )
        return MediumPublishResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Medium publish failed: {str(exc)}")
