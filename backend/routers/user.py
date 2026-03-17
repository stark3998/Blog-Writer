"""User Router — Profile management and authentication status."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_or_create_user_profile, get_user_profile
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/user", tags=["user"])


class UserProfileResponse(BaseModel):
    id: str
    name: str
    email: str
    linkedinSessionId: str = ""
    createdAt: str = ""
    lastLoginAt: str = ""


@router.get("/me", response_model=UserProfileResponse)
async def get_me(user: UserInfo = Depends(get_current_user)):
    """Get or create the current user's profile."""
    profile = get_or_create_user_profile(
        user_id=user.user_id,
        name=user.name,
        email=user.email,
    )
    return UserProfileResponse(
        id=profile["id"],
        name=profile.get("name", ""),
        email=profile.get("email", ""),
        linkedinSessionId=profile.get("linkedinSessionId", ""),
        createdAt=profile.get("createdAt", ""),
        lastLoginAt=profile.get("lastLoginAt", ""),
    )
