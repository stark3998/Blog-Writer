"""Edit Router — AI-powered blog content editing with SSE streaming."""

import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.services.ai_editor import edit_blog_content, edit_blog_content_stream

router = APIRouter(prefix="/api", tags=["edit"])


class EditRequest(BaseModel):
    content: str
    prompt: str
    blog_id: str | None = None


class EditResponse(BaseModel):
    content: str


@router.post("/edit", response_model=EditResponse)
async def edit_blog(request: EditRequest):
    """Edit blog content using AI (non-streaming)."""
    try:
        updated = edit_blog_content(request.content, request.prompt)
        return EditResponse(content=updated)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Edit failed: {str(e)}")


@router.post("/edit/stream")
async def edit_blog_stream(request: EditRequest):
    """Edit blog content using AI with SSE streaming.

    Events:
    - type: "chunk" — content chunk
    - type: "complete" — editing complete, data contains full content
    - type: "error" — an error occurred
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        try:
            full_content = ""
            async for chunk in edit_blog_content_stream(request.content, request.prompt):
                full_content += chunk
                yield {"event": "chunk", "data": json.dumps({"content": chunk})}

            yield {
                "event": "complete",
                "data": json.dumps({"content": full_content}),
            }

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())
