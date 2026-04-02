"""User Router — Profile management and authentication status."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_or_create_user_profile, get_user_profile, update_user_profile
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/user", tags=["user"])

# Default settings for new users
DEFAULT_SETTINGS = {
    "image_handling": "regenerate_on_share",  # "store_image" | "regenerate_on_share"
    "blog_base_url": "",  # Override for BLOG_BASE_URL env var; empty = use env var
}


class UserProfileResponse(BaseModel):
    id: str
    name: str
    email: str
    linkedinSessionId: str = ""
    settings: dict = {}
    createdAt: str = ""
    lastLoginAt: str = ""


class UserSettingsUpdate(BaseModel):
    image_handling: str | None = None
    blog_base_url: str | None = None


@router.get("/me", response_model=UserProfileResponse)
async def get_me(user: UserInfo = Depends(get_current_user)):
    """Get or create the current user's profile."""
    profile = get_or_create_user_profile(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
    )
    # Merge defaults with stored settings
    settings = {**DEFAULT_SETTINGS, **profile.get("settings", {})}
    return UserProfileResponse(
        id=profile["id"],
        name=profile.get("name", ""),
        email=profile.get("email", ""),
        linkedinSessionId=profile.get("linkedinSessionId", ""),
        settings=settings,
        createdAt=profile.get("createdAt", ""),
        lastLoginAt=profile.get("lastLoginAt", ""),
    )


@router.get("/settings")
async def get_settings(user: UserInfo = Depends(get_current_user)):
    """Get current user's settings."""
    profile = get_user_profile(user.user_id)
    settings = {**DEFAULT_SETTINGS, **(profile.get("settings", {}) if profile else {})}
    return settings


@router.put("/settings")
async def update_settings(request: UserSettingsUpdate, user: UserInfo = Depends(get_current_user)):
    """Update current user's settings."""
    profile = get_user_profile(user.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found")

    current_settings = {**DEFAULT_SETTINGS, **profile.get("settings", {})}
    updates = request.model_dump(exclude_none=True)

    # Validate image_handling value
    if "image_handling" in updates and updates["image_handling"] not in ("store_image", "regenerate_on_share"):
        raise HTTPException(status_code=400, detail="image_handling must be 'store_image' or 'regenerate_on_share'")

    # Normalize blog_base_url — strip trailing slash
    if "blog_base_url" in updates:
        updates["blog_base_url"] = (updates["blog_base_url"] or "").strip().rstrip("/")

    current_settings.update(updates)
    updated = update_user_profile(user.user_id, {"settings": current_settings})
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update settings")

    return current_settings
