"""Templates Router — Manage reusable blog post content templates."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    create_template,
    list_templates,
    get_template,
    update_template,
    delete_template,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/templates", tags=["templates"])


# ---------- Models ----------


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "general"
    content: str = ""
    tags: list[str] = []


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    content: str
    tags: list[str]
    isBuiltIn: bool
    userId: str | None = None
    createdAt: str
    updatedAt: str


# ---------- Endpoints ----------


@router.post("", response_model=TemplateResponse)
async def create_new_template(
    request: TemplateCreateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new content template."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Template name is required")

    template = create_template(
        name=request.name.strip(),
        description=request.description,
        category=request.category,
        content=request.content,
        tags=request.tags,
        user_id=user.user_id,
    )
    return TemplateResponse(**template)


@router.get("", response_model=list[TemplateResponse])
async def list_all_templates(
    category: str | None = None,
    include_builtin: bool = True,
    user: UserInfo = Depends(get_current_user),
):
    """List templates, optionally filtered by category."""
    templates = list_templates(category=category, user_id=user.user_id)

    if not include_builtin:
        templates = [t for t in templates if not t.get("isBuiltIn", False)]

    return [TemplateResponse(**t) for t in templates]


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_single_template(
    template_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Get a template by ID."""
    template = get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Allow access to built-in templates or user's own templates
    if not template.get("isBuiltIn", False) and template.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return TemplateResponse(**template)


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_existing_template(
    template_id: str,
    request: TemplateUpdateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Update a template."""
    template = get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.get("isBuiltIn", False):
        raise HTTPException(status_code=403, detail="Cannot modify built-in templates")
    if template.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    updates = {}
    if request.name is not None:
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Template name cannot be empty")
        updates["name"] = request.name.strip()
    if request.description is not None:
        updates["description"] = request.description
    if request.category is not None:
        updates["category"] = request.category
    if request.content is not None:
        updates["content"] = request.content
    if request.tags is not None:
        updates["tags"] = request.tags

    if not updates:
        return TemplateResponse(**template)

    updated = update_template(template_id, updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update template")
    return TemplateResponse(**updated)


@router.delete("/{template_id}")
async def delete_existing_template(
    template_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a template. Built-in templates cannot be deleted."""
    template = get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.get("isBuiltIn", False):
        raise HTTPException(status_code=403, detail="Cannot delete built-in templates")
    if template.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    deleted = delete_template(template_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete template")
    return {"status": "deleted", "id": template_id}
