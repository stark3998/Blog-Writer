"""Opt-in, read-only check against the real LinkedIn (skipped by default; it never posts).

    BW_LIVE_LINKEDIN=1 LINKEDIN_ACCESS_TOKEN=... BW_LINKEDIN_CLIENT_ID=... \\
        BW_LINKEDIN_CLIENT_SECRET=... uv run pytest tests/linkedin/transport/test_live_linkedin.py

It reads the member from ``userinfo`` and introspects the token: the two calls the connection
and the daily job make. Nothing is created, changed or stored.
"""

from __future__ import annotations

import os

import pytest

from blogwriter.linkedin.settings import LinkedInSettings
from blogwriter.linkedin.transport.client import LinkedInClient

LIVE = os.environ.get("BW_LIVE_LINKEDIN") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="live LinkedIn check: set BW_LIVE_LINKEDIN=1")


async def test_userinfo_and_introspection_against_linkedin() -> None:
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
    if not token:
        pytest.skip("set LINKEDIN_ACCESS_TOKEN")
    client = LinkedInClient(LinkedInSettings())
    try:
        member = await client.userinfo(token)
        assert member.startswith("urn:li:person:")
        if client.settings.configured:
            verdict = await client.introspect(token)
            assert verdict.active
    finally:
        await client.aclose()
