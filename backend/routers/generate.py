"""Generate Router — Blog generation from URL with SSE streaming."""

import json
import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from sse_starlette.sse import EventSourceResponse

from backend.services.blog_service import (
    analyze_source,
    generate_blog_post,
    generate_blog_post_stream,
    _parse_blog_response,
)

router = APIRouter(prefix="/api", tags=["generate"])


class GenerateRequest(BaseModel):
    url: str


class GenerateResponse(BaseModel):
    mdx_content: str
    slug: str
    title: str
    excerpt: str
    source_url: str
    source_type: str


@router.post("/generate", response_model=GenerateResponse)
async def generate_blog(request: GenerateRequest):
    """Generate a blog post from a URL (non-streaming)."""
    try:
        analysis = await asyncio.to_thread(analyze_source, request.url)
        source_type = analysis.get("_source_type", "webpage")

        blog_data = await asyncio.to_thread(generate_blog_post, analysis)

        return GenerateResponse(
            mdx_content=blog_data["mdx_content"],
            slug=blog_data["slug"],
            title=blog_data["title"],
            excerpt=blog_data["excerpt"],
            source_url=request.url,
            source_type=source_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@router.post("/generate/stream")
async def generate_blog_stream(request: GenerateRequest):
    """Generate a blog post from a URL with SSE streaming.

    Events:
    - type: "analyzing" — source analysis started
    - type: "generating" — blog generation started, data contains chunks
    - type: "chunk" — content chunk
    - type: "complete" — generation complete, data contains full parsed result
    - type: "error" — an error occurred
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            # Phase 1: Analyze source
            yield {"event": "analyzing", "data": json.dumps({"status": "Analyzing source URL..."})}

            analysis = await asyncio.to_thread(analyze_source, request.url)
            source_type = analysis.get("_source_type", "webpage")

            yield {
                "event": "analyzed",
                "data": json.dumps({
                    "status": "Source analyzed successfully",
                    "source_type": source_type,
                }),
            }

            # Phase 2: Stream blog generation
            yield {"event": "generating", "data": json.dumps({"status": "Generating blog post..."})}

            full_content = ""
            async for chunk in generate_blog_post_stream(analysis):
                full_content += chunk
                yield {"event": "chunk", "data": json.dumps({"content": chunk})}

            # Phase 3: Parse and return final result
            blog_data = _parse_blog_response(full_content)

            yield {
                "event": "complete",
                "data": json.dumps({
                    "mdx_content": blog_data["mdx_content"],
                    "slug": blog_data["slug"],
                    "title": blog_data["title"],
                    "excerpt": blog_data["excerpt"],
                    "source_url": request.url,
                    "source_type": source_type,
                }),
            }

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())
