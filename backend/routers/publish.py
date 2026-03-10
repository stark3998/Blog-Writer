"""Publish Router — Publish blog post as a GitHub PR."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.tools.blog_publisher import publish_blog_post

router = APIRouter(prefix="/api", tags=["publish"])


class PublishRequest(BaseModel):
    content: str
    slug: str
    title: str
    excerpt: str = ""


class PublishResponse(BaseModel):
    pr_url: str
    branch: str
    file_path: str


@router.post("/publish", response_model=PublishResponse)
async def publish_blog(request: PublishRequest):
    """Publish a blog post by creating a GitHub PR."""
    try:
        result = publish_blog_post(
            mdx_content=request.content,
            slug=request.slug,
            title=request.title,
            excerpt=request.excerpt,
        )
        return PublishResponse(**result)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Publish failed: {str(e)}")
