"""Voice Profiles Router — Manage AI tone/voice profiles for content generation."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import (
    create_voice_profile,
    list_voice_profiles,
    get_voice_profile,
    update_voice_profile,
    delete_voice_profile,
    get_default_voice_profile,
)
from backend.models.user import UserInfo

router = APIRouter(prefix="/api/voice-profiles", tags=["voice-profiles"])


# ---------- Models ----------


class VoiceProfileCreateRequest(BaseModel):
    name: str
    description: str = ""
    tone: str = "professional"
    style_notes: str = ""
    sample_text: str = ""
    is_default: bool = False


class VoiceProfileUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tone: str | None = None
    style_notes: str | None = None
    sample_text: str | None = None
    is_default: bool | None = None


class VoiceProfileResponse(BaseModel):
    id: str
    userId: str
    name: str
    description: str
    tone: str
    styleNotes: str
    sampleText: str
    isDefault: bool
    createdAt: str
    updatedAt: str


# ---------- Endpoints ----------


@router.post("", response_model=VoiceProfileResponse)
async def create_profile(
    request: VoiceProfileCreateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Create a new voice profile."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Profile name is required")

    # If setting as default, unset any existing default first
    if request.is_default:
        existing_default = get_default_voice_profile(user.user_id)
        if existing_default:
            update_voice_profile(existing_default["id"], {"isDefault": False})

    profile = create_voice_profile(
        user_id=user.user_id,
        name=request.name.strip(),
        description=request.description,
        tone=request.tone,
        style_notes=request.style_notes,
        sample_text=request.sample_text,
        is_default=request.is_default,
    )
    return VoiceProfileResponse(**profile)


@router.get("", response_model=list[VoiceProfileResponse])
async def list_profiles(user: UserInfo = Depends(get_current_user)):
    """List all voice profiles for the current user."""
    profiles = list_voice_profiles(user.user_id)
    return [VoiceProfileResponse(**p) for p in profiles]


@router.get("/{profile_id}", response_model=VoiceProfileResponse)
async def get_profile(
    profile_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Get a voice profile by ID."""
    profile = get_voice_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    if profile.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return VoiceProfileResponse(**profile)


@router.put("/{profile_id}", response_model=VoiceProfileResponse)
async def update_profile(
    profile_id: str,
    request: VoiceProfileUpdateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Update a voice profile."""
    profile = get_voice_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    if profile.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    updates = {}
    if request.name is not None:
        if not request.name.strip():
            raise HTTPException(status_code=400, detail="Profile name cannot be empty")
        updates["name"] = request.name.strip()
    if request.description is not None:
        updates["description"] = request.description
    if request.tone is not None:
        updates["tone"] = request.tone
    if request.style_notes is not None:
        updates["styleNotes"] = request.style_notes
    if request.sample_text is not None:
        updates["sampleText"] = request.sample_text
    if request.is_default is not None:
        updates["isDefault"] = request.is_default

    if not updates:
        return VoiceProfileResponse(**profile)

    updated = update_voice_profile(profile_id, updates)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update voice profile")
    return VoiceProfileResponse(**updated)


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a voice profile."""
    profile = get_voice_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    if profile.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    delete_voice_profile(profile_id)
    return {"status": "deleted", "id": profile_id}


@router.post("/{profile_id}/default", response_model=VoiceProfileResponse)
async def set_default_profile(
    profile_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Set a voice profile as the default, unsetting any previous default."""
    profile = get_voice_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    if profile.get("userId") != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Unset existing default
    existing_default = get_default_voice_profile(user.user_id)
    if existing_default and existing_default["id"] != profile_id:
        update_voice_profile(existing_default["id"], {"isDefault": False})

    updated = update_voice_profile(profile_id, {"isDefault": True})
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to set default profile")
    return VoiceProfileResponse(**updated)
