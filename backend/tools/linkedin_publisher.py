"""LinkedIn Publisher Tool — OAuth and publish transport for LinkedIn member posts."""

import os
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests

from backend.db.cosmos_client import (
    consume_linkedin_oauth_state,
    delete_linkedin_session,
    get_linkedin_session,
    store_linkedin_oauth_state,
    upsert_linkedin_session,
)

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/v2/ugcPosts"

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _scopes() -> str:
    return os.environ.get("LINKEDIN_SCOPES", "openid profile w_member_social")


def _token_for_session(session_id: str) -> dict[str, Any]:
    session_record = get_linkedin_session(session_id)
    token_data = (
        {
            "access_token": session_record.get("accessToken", "") if session_record else "",
            "expires_at": session_record.get("expiresAt", 0) if session_record else 0,
            "person_urn": session_record.get("personUrn", "") if session_record else "",
            "member_id": session_record.get("memberId", "") if session_record else "",
        }
        if session_record
        else None
    )
    if not token_data:
        raise RuntimeError("LinkedIn session is not connected. Complete OAuth first.")

    expires_at = float(token_data.get("expires_at", 0))
    if expires_at and time.time() >= expires_at:
        delete_linkedin_session(session_id)
        raise RuntimeError("LinkedIn access token expired. Reconnect OAuth.")

    return token_data


def _linkedin_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def start_oauth(session_id: str | None = None) -> dict[str, str]:
    """Create LinkedIn OAuth URL and state for a session."""
    client_id = _require_env("LINKEDIN_CLIENT_ID")
    redirect_uri = _require_env("LINKEDIN_REDIRECT_URI")

    current_session = session_id.strip() if session_id else str(uuid.uuid4())
    state = secrets.token_urlsafe(24)
    state_ttl = int(os.environ.get("LINKEDIN_OAUTH_STATE_TTL_SECONDS", "900") or 900)
    store_linkedin_oauth_state(
        state=state,
        session_id=current_session,
        expires_at=time.time() + max(state_ttl, 60),
    )

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": _scopes(),
        }
    )

    return {
        "session_id": current_session,
        "state": state,
        "auth_url": f"{LINKEDIN_AUTH_URL}?{query}",
    }


def handle_oauth_callback(code: str, state: str) -> dict[str, Any]:
    """Exchange OAuth code for token and store token for session."""
    client_id = _require_env("LINKEDIN_CLIENT_ID")
    client_secret = _require_env("LINKEDIN_CLIENT_SECRET")
    redirect_uri = _require_env("LINKEDIN_REDIRECT_URI")

    session_id = consume_linkedin_oauth_state(state)
    if not session_id:
        raise RuntimeError("Invalid or expired OAuth state")

    token_response = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )

    if token_response.status_code >= 400:
        raise RuntimeError(f"LinkedIn token exchange failed: {token_response.text}")

    token_data = token_response.json()
    access_token = token_data.get("access_token", "")
    expires_in = int(token_data.get("expires_in", 0) or 0)
    if not access_token:
        raise RuntimeError("LinkedIn did not return access_token")

    me_response = requests.get(
        LINKEDIN_USERINFO_URL,
        headers=_linkedin_headers(access_token),
        timeout=30,
    )
    if me_response.status_code >= 400:
        raise RuntimeError(f"LinkedIn profile fetch failed: {me_response.text}")

    me = me_response.json()
    member_id = me.get("sub", "")
    if not member_id:
        raise RuntimeError("LinkedIn profile does not include member id")

    person_urn = f"urn:li:person:{member_id}"
    expires_at = time.time() + expires_in - 30 if expires_in else 0

    upsert_linkedin_session(
        session_id,
        {
            "access_token": access_token,
            "expires_at": expires_at,
            "person_urn": person_urn,
            "member_id": member_id,
        },
    )

    return {
        "session_id": session_id,
        "person_urn": person_urn,
        "expires_at": expires_at,
    }


def get_connection_status(session_id: str) -> dict[str, Any]:
    """Return whether OAuth session is connected and non-expired."""
    session_record = get_linkedin_session(session_id)
    if not session_record:
        return {"connected": False, "session_id": session_id}

    expires_at = float(session_record.get("expiresAt", 0) or 0)
    if expires_at and time.time() >= expires_at:
        delete_linkedin_session(session_id)
        return {"connected": False, "session_id": session_id}

    return {
        "connected": True,
        "session_id": session_id,
        "person_urn": session_record.get("personUrn", ""),
        "expires_at": session_record.get("expiresAt", 0),
    }


def disconnect_session(session_id: str) -> None:
    """Forget OAuth session tokens in memory."""
    delete_linkedin_session(session_id)


def _upload_image_to_linkedin(access_token: str, person_urn: str, image_url: str) -> str | None:
    """Download an image from a URL and upload it to LinkedIn. Returns the asset URN or None."""
    try:
        # Download the image
        img_response = requests.get(image_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        img_response.raise_for_status()
        image_bytes = img_response.content
        content_type = img_response.headers.get("Content-Type", "image/jpeg")

        # Register the upload with LinkedIn
        register_payload = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": person_urn,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }

        headers = _linkedin_headers(access_token)
        register_response = requests.post(
            "https://api.linkedin.com/v2/assets?action=registerUpload",
            json=register_payload,
            headers=headers,
            timeout=30,
        )
        if register_response.status_code >= 400:
            return None

        register_data = register_response.json()
        upload_url = (
            register_data.get("value", {})
            .get("uploadMechanism", {})
            .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
            .get("uploadUrl", "")
        )
        asset = register_data.get("value", {}).get("asset", "")

        if not upload_url or not asset:
            return None

        # Upload the image binary
        upload_response = requests.put(
            upload_url,
            data=image_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": content_type,
            },
            timeout=60,
        )
        if upload_response.status_code >= 400:
            return None

        return asset
    except Exception:
        return None


def publish_member_post(
    session_id: str,
    post_text: str,
    visibility: str = "PUBLIC",
    image_url: str = "",
) -> dict[str, Any]:
    """Publish a LinkedIn member feed post using UGC API, optionally with an image."""
    if not post_text.strip():
        raise RuntimeError("post_text cannot be empty")

    token_data = _token_for_session(session_id)
    access_token = str(token_data["access_token"])
    person_urn = str(token_data["person_urn"])

    # Try to upload image if provided
    asset_urn = None
    if image_url.strip():
        asset_urn = _upload_image_to_linkedin(access_token, person_urn, image_url)

    if asset_urn:
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": "IMAGE",
            "media": [
                {
                    "status": "READY",
                    "media": asset_urn,
                }
            ],
        }
    else:
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": "NONE",
        }

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content,
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": visibility,
        },
    }

    response = requests.post(
        LINKEDIN_POST_URL,
        json=payload,
        headers=_linkedin_headers(access_token),
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"LinkedIn publish failed: {response.text}")

    data = response.json() if response.text else {}
    return {
        "session_id": session_id,
        "post_id": data.get("id", ""),
        "visibility": visibility,
        "status_code": response.status_code,
    }
