"""Medium Publisher Tool — Publish articles to Medium via their API."""

import os
import time
from typing import Any

import requests

from backend.db.cosmos_client import (
    delete_medium_session,
    get_medium_session,
    upsert_medium_session,
)

MEDIUM_API_BASE = "https://api.medium.com/v1"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _token_for_session(session_id: str) -> dict[str, Any]:
    session_record = get_medium_session(session_id)
    if not session_record:
        raise RuntimeError("Medium session is not connected. Add your integration token first.")

    return {
        "access_token": session_record.get("accessToken", ""),
        "author_id": session_record.get("authorId", ""),
        "username": session_record.get("username", ""),
    }


def connect_with_token(session_id: str, integration_token: str) -> dict[str, Any]:
    """Connect to Medium using a self-issued integration token."""
    if not integration_token.strip():
        raise RuntimeError("Integration token cannot be empty")

    # Validate the token by fetching user info
    response = requests.get(
        f"{MEDIUM_API_BASE}/me",
        headers={"Authorization": f"Bearer {integration_token}"},
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Medium token validation failed: {response.text}")

    user_data = response.json().get("data", {})
    author_id = user_data.get("id", "")
    username = user_data.get("username", "")
    name = user_data.get("name", "")

    if not author_id:
        raise RuntimeError("Medium did not return a valid user ID")

    upsert_medium_session(session_id, {
        "access_token": integration_token,
        "author_id": author_id,
        "username": username,
        "name": name,
    })

    return {
        "session_id": session_id,
        "author_id": author_id,
        "username": username,
        "name": name,
    }


def get_connection_status(session_id: str) -> dict[str, Any]:
    """Return whether session has a valid Medium integration token."""
    session_record = get_medium_session(session_id)
    if not session_record:
        return {"connected": False, "session_id": session_id}

    return {
        "connected": True,
        "session_id": session_id,
        "username": session_record.get("username", ""),
        "author_id": session_record.get("authorId", ""),
    }


def disconnect_session(session_id: str) -> None:
    """Remove stored Medium session."""
    delete_medium_session(session_id)


def publish_article(
    session_id: str,
    title: str,
    content_html: str,
    tags: list[str] | None = None,
    canonical_url: str = "",
    publish_status: str = "draft",
) -> dict[str, Any]:
    """Publish an article to Medium.

    Args:
        publish_status: "public", "draft", or "unlisted"
    """
    if not title.strip():
        raise RuntimeError("Article title cannot be empty")
    if not content_html.strip():
        raise RuntimeError("Article content cannot be empty")

    token_data = _token_for_session(session_id)
    access_token = str(token_data["access_token"])
    author_id = str(token_data["author_id"])

    payload: dict[str, Any] = {
        "title": title,
        "contentFormat": "html",
        "content": content_html,
        "publishStatus": publish_status,
    }

    if tags:
        payload["tags"] = tags[:5]  # Medium allows max 5 tags
    if canonical_url:
        payload["canonicalUrl"] = canonical_url

    response = requests.post(
        f"{MEDIUM_API_BASE}/users/{author_id}/posts",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Medium publish failed: {response.text}")

    data = response.json().get("data", {})
    return {
        "session_id": session_id,
        "post_id": data.get("id", ""),
        "url": data.get("url", ""),
        "title": data.get("title", title),
        "publish_status": data.get("publishStatus", publish_status),
        "status_code": response.status_code,
    }
