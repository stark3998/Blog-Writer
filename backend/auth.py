"""Entra ID (Azure AD) JWT validation for FastAPI."""

import logging
import os
from typing import Optional

import requests as http_requests
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from backend.models.user import UserInfo

logger = logging.getLogger(__name__)

_jwks_cache: Optional[dict] = None


def _get_entra_config() -> tuple[str, str]:
    """Return (client_id, tenant_id) from environment."""
    client_id = os.environ.get("ENTRA_CLIENT_ID", "")
    tenant_id = os.environ.get("ENTRA_TENANT_ID", "")
    return client_id, tenant_id


def _get_jwks(tenant_id: str) -> dict:
    """Fetch and cache the JSON Web Key Set from Entra ID."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    resp = http_requests.get(jwks_url, timeout=10)
    resp.raise_for_status()
    _jwks_cache = resp.json()
    return _jwks_cache


def validate_token(token: str) -> dict:
    """Decode and validate a JWT issued by Entra ID.

    Returns the token claims dict on success, raises HTTPException on failure.
    """
    client_id, tenant_id = _get_entra_config()
    if not client_id or not tenant_id:
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured (missing ENTRA_CLIENT_ID or ENTRA_TENANT_ID)",
        )

    try:
        jwks = _get_jwks(tenant_id)
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch authentication keys")

    # Get the unverified header to find the signing key
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token header")

    # Find the matching key
    rsa_key = {}
    for key in jwks.get("keys", []):
        if key.get("kid") == unverified_header.get("kid"):
            rsa_key = key
            break

    if not rsa_key:
        # Key not found — clear cache and retry once (key rotation)
        global _jwks_cache
        _jwks_cache = None
        try:
            jwks = _get_jwks(tenant_id)
            for key in jwks.get("keys", []):
                if key.get("kid") == unverified_header.get("kid"):
                    rsa_key = key
                    break
        except Exception:
            pass

    if not rsa_key:
        raise HTTPException(status_code=401, detail="Unable to find appropriate signing key")

    try:
        claims = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        )
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except JWTError as e:
        logger.warning(f"JWT validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(request: Request) -> UserInfo:
    """FastAPI dependency that extracts and validates the Entra ID bearer token.

    When ENTRA_CLIENT_ID is not set (local dev), returns a default local user.
    """
    client_id, _ = _get_entra_config()
    if not client_id:
        # Auth not configured — return a default local user for development
        return UserInfo(user_id="local-dev", name="Local Developer", email="dev@localhost")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # Strip "Bearer "
    claims = validate_token(token)

    return UserInfo(
        user_id=claims.get("oid", ""),
        name=claims.get("name", ""),
        email=claims.get("preferred_username", ""),
    )
