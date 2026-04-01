"""Twitter/X Publisher Tool — OAuth 2.0 PKCE and tweet publishing via X API v2."""

import hashlib
import os
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from typing import Any
from urllib.parse import urlencode

import requests

from backend.db.cosmos_client import (
    consume_twitter_oauth_state,
    delete_twitter_session,
    get_twitter_session,
    store_twitter_oauth_state,
    upsert_twitter_session,
)

TWITTER_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWITTER_ME_URL = "https://api.twitter.com/2/users/me"
TWITTER_TWEET_URL = "https://api.twitter.com/2/tweets"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _scopes() -> str:
    return os.environ.get("TWITTER_SCOPES", "tweet.read tweet.write users.read offline.access")


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _token_for_session(session_id: str) -> dict[str, Any]:
    session_record = get_twitter_session(session_id)
    if not session_record:
        raise RuntimeError("Twitter session is not connected. Complete OAuth first.")

    token_data = {
        "access_token": session_record.get("accessToken", ""),
        "refresh_token": session_record.get("refreshToken", ""),
        "expires_at": session_record.get("expiresAt", 0),
        "username": session_record.get("username", ""),
        "user_id": session_record.get("twitterUserId", ""),
    }

    expires_at = float(token_data.get("expires_at", 0))
    if expires_at and time.time() >= expires_at:
        # Try refresh
        refresh_token = token_data.get("refresh_token", "")
        if refresh_token:
            try:
                return _refresh_access_token(session_id, refresh_token)
            except Exception:
                pass
        delete_twitter_session(session_id)
        raise RuntimeError("Twitter access token expired. Reconnect OAuth.")

    return token_data


def _refresh_access_token(session_id: str, refresh_token: str) -> dict[str, Any]:
    """Refresh an expired Twitter access token."""
    client_id = _require_env("TWITTER_CLIENT_ID")

    response = requests.post(
        TWITTER_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Twitter token refresh failed: {response.text}")

    data = response.json()
    access_token = data.get("access_token", "")
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = int(data.get("expires_in", 7200) or 7200)

    session_record = get_twitter_session(session_id) or {}
    expires_at = time.time() + expires_in - 30

    upsert_twitter_session(session_id, {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "expires_at": expires_at,
        "username": session_record.get("username", ""),
        "user_id": session_record.get("twitterUserId", ""),
    })

    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "expires_at": expires_at,
        "username": session_record.get("username", ""),
        "user_id": session_record.get("twitterUserId", ""),
    }


def start_oauth(session_id: str | None = None) -> dict[str, str]:
    """Create Twitter OAuth 2.0 PKCE URL and state for a session."""
    client_id = _require_env("TWITTER_CLIENT_ID")
    redirect_uri = _require_env("TWITTER_REDIRECT_URI")

    current_session = session_id.strip() if session_id else str(uuid.uuid4())
    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _generate_pkce()

    state_ttl = int(os.environ.get("TWITTER_OAUTH_STATE_TTL_SECONDS", "900") or 900)
    store_twitter_oauth_state(
        state=state,
        session_id=current_session,
        code_verifier=code_verifier,
        expires_at=time.time() + max(state_ttl, 60),
    )

    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _scopes(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })

    return {
        "session_id": current_session,
        "state": state,
        "auth_url": f"{TWITTER_AUTH_URL}?{query}",
    }


def handle_oauth_callback(code: str, state: str) -> dict[str, Any]:
    """Exchange OAuth code for token and store for session."""
    client_id = _require_env("TWITTER_CLIENT_ID")
    redirect_uri = _require_env("TWITTER_REDIRECT_URI")

    state_data = consume_twitter_oauth_state(state)
    if not state_data:
        raise RuntimeError("Invalid or expired OAuth state")

    session_id = state_data["session_id"]
    code_verifier = state_data["code_verifier"]

    token_response = requests.post(
        TWITTER_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if token_response.status_code >= 400:
        raise RuntimeError(f"Twitter token exchange failed: {token_response.text}")

    token_data = token_response.json()
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = int(token_data.get("expires_in", 7200) or 7200)

    if not access_token:
        raise RuntimeError("Twitter did not return access_token")

    # Fetch user profile
    me_response = requests.get(
        TWITTER_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if me_response.status_code >= 400:
        raise RuntimeError(f"Twitter user profile fetch failed: {me_response.text}")

    me = me_response.json().get("data", {})
    username = me.get("username", "")
    user_id = me.get("id", "")

    expires_at = time.time() + expires_in - 30

    upsert_twitter_session(session_id, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "username": username,
        "user_id": user_id,
    })

    return {
        "session_id": session_id,
        "username": username,
        "expires_at": expires_at,
    }


def get_connection_status(session_id: str) -> dict[str, Any]:
    """Return whether OAuth session is connected and non-expired."""
    session_record = get_twitter_session(session_id)
    if not session_record:
        return {"connected": False, "session_id": session_id}

    expires_at = float(session_record.get("expiresAt", 0) or 0)
    if expires_at and time.time() >= expires_at:
        # Try refresh before declaring disconnected
        refresh_token = session_record.get("refreshToken", "")
        if refresh_token:
            try:
                _refresh_access_token(session_id, refresh_token)
                session_record = get_twitter_session(session_id)
            except Exception:
                delete_twitter_session(session_id)
                return {"connected": False, "session_id": session_id}
        else:
            delete_twitter_session(session_id)
            return {"connected": False, "session_id": session_id}

    return {
        "connected": True,
        "session_id": session_id,
        "username": session_record.get("username", ""),
        "expires_at": session_record.get("expiresAt", 0),
    }


def disconnect_session(session_id: str) -> None:
    """Forget OAuth session tokens."""
    delete_twitter_session(session_id)


def publish_tweet(
    session_id: str,
    text: str,
) -> dict[str, Any]:
    """Publish a tweet using X API v2."""
    if not text.strip():
        raise RuntimeError("Tweet text cannot be empty")

    token_data = _token_for_session(session_id)
    access_token = str(token_data["access_token"])

    payload = {"text": text}

    response = requests.post(
        TWITTER_TWEET_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Twitter publish failed: {response.text}")

    data = response.json().get("data", {})
    return {
        "session_id": session_id,
        "tweet_id": data.get("id", ""),
        "text": data.get("text", text),
        "status_code": response.status_code,
    }
