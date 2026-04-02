"""Comments Router — Collaborative editing with inline comments and threads."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    create_comment,
    delete_comment,
    list_comments,
    update_comment,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/comments", tags=["comments"])


class CreateCommentRequest(BaseModel):
    draft_id: str
    content: str
    line_number: int | None = None
    parent_id: str | None = None


class UpdateCommentRequest(BaseModel):
    content: str | None = None
    resolved: bool | None = None


class CommentResponse(BaseModel):
    id: str
    draftId: str
    userId: str
    userName: str
    content: str
    lineNumber: int | None = None
    parentId: str = ""
    resolved: bool = False
    createdAt: str = ""
    updatedAt: str = ""


@router.post("/", response_model=CommentResponse)
async def add_comment(request: CreateCommentRequest, user: UserInfo = Depends(get_current_user)):
    """Add a comment to a draft."""
    comment = create_comment(
        draft_id=request.draft_id,
        user_id=user.user_id,
        user_name=user.name,
        content=request.content,
        line_number=request.line_number,
        parent_id=request.parent_id,
    )
    return CommentResponse(**comment)


@router.get("/{draft_id}", response_model=list[CommentResponse])
async def get_comments(draft_id: str, user: UserInfo = Depends(get_current_user)):
    """List all comments for a draft."""
    comments = list_comments(draft_id)
    return [CommentResponse(**c) for c in comments]


@router.put("/{comment_id}")
async def edit_comment(
    comment_id: str,
    request: UpdateCommentRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Update a comment (edit content or resolve)."""
    updates = request.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    updated = update_comment(comment_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Comment not found")
    return updated


@router.delete("/{comment_id}")
async def remove_comment(comment_id: str, user: UserInfo = Depends(get_current_user)):
    """Delete a comment."""
    if not delete_comment(comment_id):
        raise HTTPException(status_code=404, detail="Comment not found")
    return {"status": "deleted", "id": comment_id}
