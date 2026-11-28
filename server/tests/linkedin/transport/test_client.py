"""The LinkedIn REST client: headers, fixed hosts, no redirects, no retries, parsing."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.core.logging import configure_logging
from blogwriter.domain.settings import LINKEDIN_API_VERSION
from blogwriter.linkedin.transport.client import (
    API_BASE,
    INTROSPECT_URL,
    TOKEN_URL,
    USERINFO_URL,
    CreateOutcome,
    LinkedInClient,
    LinkedInError,
)
from blogwriter.linkedin.transport.testing import (
    CLIENT_SECRET,
    DOC_URN,
    MEMBER_TOKEN,
    MEMBER_URN,
    POST_URN,
    POSTS_URL,
    UPLOAD_URL,
    FakeLinkedIn,
    fake_client,
    make_linkedin_settings,
    media_status_url,
)

BODY = {"author": MEMBER_URN, "commentary": "Hello"}


async def test_rest_calls_carry_the_version_headers_and_the_token(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    linkedin.created()
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome == CreateOutcome(status=201, urn=POST_URN, error_code=None)
    assert outcome.outcome == "posted"
    (request,) = linkedin.calls("POST", POSTS_URL)
    assert request.headers["Authorization"] == f"Bearer {MEMBER_TOKEN}"
    assert request.headers["Linkedin-Version"] == LINKEDIN_API_VERSION == "202611"
    assert request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert json.loads(request.content) == BODY


async def test_an_encoded_urn_header_is_read(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    encoded = POST_URN.replace(":", "%3A")
    linkedin.script("POST", POSTS_URL, httpx.Response(201, headers={"x-restli-id": encoded}))
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome == CreateOutcome(status=201, urn=POST_URN, error_code=None)


async def test_a_201_without_a_post_urn_is_an_unknown_outcome(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    linkedin.script("POST", POSTS_URL, httpx.Response(201, headers={"x-restli-id": "garbage"}))
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome == CreateOutcome(status=201, urn=None, error_code="urn_missing")
    assert outcome.outcome == "unknown"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (httpx.Response(500), CreateOutcome(status=500, urn=None, error_code="linkedin_500")),
        (httpx.Response(503), CreateOutcome(status=503, urn=None, error_code="linkedin_503")),
        (httpx.ReadTimeout("slow"), CreateOutcome(status=None, urn=None, error_code="timeout")),
        (
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            CreateOutcome(status=None, urn=None, error_code="network_error"),
        ),
        (httpx.ReadError("Connection reset by peer"), CreateOutcome(None, None, "network_error")),
        (httpx.ConnectError("refused"), CreateOutcome(None, None, "network_error")),
        (httpx.Response(408), CreateOutcome(status=408, urn=None, error_code="linkedin_408")),
    ],
    ids=["500", "503", "timeout", "reset", "read-error", "connect-error", "408"],
)
async def test_create_post_makes_exactly_one_attempt_and_reports_unknown(
    linkedin: FakeLinkedIn,
    li_client: LinkedInClient,
    answer: httpx.Response | Exception,
    expected: CreateOutcome,
) -> None:
    linkedin.always("POST", POSTS_URL, answer)  # would answer a retry the same way
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome == expected
    assert outcome.outcome == "unknown"
    assert len(linkedin.calls("POST", POSTS_URL)) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 422, 429])
async def test_a_definite_4xx_is_rejected_after_one_attempt(
    linkedin: FakeLinkedIn, li_client: LinkedInClient, status: int
) -> None:
    linkedin.always("POST", POSTS_URL, httpx.Response(status))
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome.outcome == "rejected"
    assert outcome.error_code == f"linkedin_{status}"
    assert len(linkedin.calls("POST", POSTS_URL)) == 1


async def test_redirects_are_never_followed(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    linkedin.always(
        "POST", POSTS_URL, httpx.Response(307, headers={"Location": "https://evil.test/posts"})
    )
    outcome = await li_client.create_post(MEMBER_TOKEN, BODY)
    assert outcome.outcome == "unknown"
    assert [str(r.url) for r in linkedin.requests] == [POSTS_URL]


async def test_upload_urls_must_be_https_on_linkedin_hosts(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    init = f"{API_BASE}/documents?action=initializeUpload"
    for bad in (
        "https://evil.test/upload",
        "http://www.linkedin.com/dms-uploads/x",
        "https://www.linkedin.com:8443/dms-uploads/x",
        "https://user@www.linkedin.com/dms-uploads/x",
    ):
        linkedin.script(
            "POST",
            init,
            httpx.Response(200, json={"value": {"uploadUrl": bad, "document": DOC_URN}}),
        )
        with pytest.raises(LinkedInError) as caught:
            await li_client.initialize_upload(MEMBER_TOKEN, "document", MEMBER_URN)
        assert caught.value.code == "upload_url_refused"
    before = len(linkedin.requests)
    with pytest.raises(LinkedInError):
        await li_client.upload_bytes(
            "https://evil.test/up", b"pdf", "application/pdf", token=MEMBER_TOKEN
        )
    assert len(linkedin.requests) == before  # the token never left


async def test_initialize_upload_returns_the_url_and_urn(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    linkedin.upload_flow("document", DOC_URN)
    url, urn = await li_client.initialize_upload(MEMBER_TOKEN, "document", MEMBER_URN)
    assert (url, urn) == (UPLOAD_URL, DOC_URN)
    (request,) = linkedin.calls("POST", f"{API_BASE}/documents?action=initializeUpload")
    assert json.loads(request.content) == {"initializeUploadRequest": {"owner": MEMBER_URN}}
    await li_client.upload_bytes(url, b"%PDF-1.4", "application/pdf", token=MEMBER_TOKEN)
    (put,) = linkedin.calls("PUT", UPLOAD_URL)
    assert put.content == b"%PDF-1.4"
    assert put.headers["Content-Type"] == "application/pdf"
    assert put.headers["Authorization"] == f"Bearer {MEMBER_TOKEN}"
    assert await li_client.media_status(MEMBER_TOKEN, "document", urn) == "AVAILABLE"


async def test_a_status_the_token_may_not_read_is_unreadable(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    linkedin.script("GET", media_status_url("image", "urn:li:image:X1"), httpx.Response(403))
    assert await li_client.media_status(MEMBER_TOKEN, "image", "urn:li:image:X1") == "UNREADABLE"


async def test_edit_and_delete_use_restli_methods(
    linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    url = f"{API_BASE}/posts/urn%3Ali%3Ashare%3A7300000000000000001"
    linkedin.script("POST", url, httpx.Response(204))
    await li_client.update_commentary(MEMBER_TOKEN, POST_URN, "New \\(text\\)")
    (patch,) = linkedin.calls("POST", url)
    assert patch.headers["X-RestLi-Method"] == "PARTIAL_UPDATE"
    assert json.loads(patch.content) == {"patch": {"$set": {"commentary": "New \\(text\\)"}}}
    linkedin.script("DELETE", url, httpx.Response(204), httpx.Response(404), httpx.Response(500))
    await li_client.delete_post(MEMBER_TOKEN, POST_URN)
    await li_client.delete_post(MEMBER_TOKEN, POST_URN)  # already gone counts as deleted
    with pytest.raises(LinkedInError) as caught:
        await li_client.delete_post(MEMBER_TOKEN, POST_URN)
    assert (caught.value.code, caught.value.transient) == ("http_500", True)
    assert linkedin.calls("DELETE", url)[0].headers["X-RestLi-Method"] == "DELETE"


async def test_oauth_calls(linkedin: FakeLinkedIn, li_client: LinkedInClient) -> None:
    linkedin.token_exchange()
    grant = await li_client.exchange_code(
        "code-1", "https://writer.test/api/linkedin/oauth/callback"
    )
    assert grant.access_token == MEMBER_TOKEN
    assert grant.scopes == ("openid", "profile", "w_member_social")
    assert MEMBER_TOKEN not in repr(grant)
    (request,) = linkedin.calls("POST", TOKEN_URL)
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": "https://writer.test/api/linkedin/oauth/callback",
        "client_id": "testclient123",
        "client_secret": CLIENT_SECRET,
    }
    linkedin.userinfo()
    assert await li_client.userinfo(MEMBER_TOKEN) == MEMBER_URN
    expires = datetime(2027, 1, 25, 12, 0, tzinfo=UTC)
    linkedin.introspection(active=True, expires_at=expires)
    verdict = await li_client.introspect(MEMBER_TOKEN)
    assert (verdict.active, verdict.status, verdict.expires_at) == (True, "active", expires)
    linkedin.script("GET", USERINFO_URL, httpx.Response(200, json={"sub": "../evil"}))
    with pytest.raises(LinkedInError, match="bad_response"):
        await li_client.userinfo(MEMBER_TOKEN)


async def test_oauth_needs_the_app_credentials(linkedin: FakeLinkedIn) -> None:
    client = fake_client(linkedin, make_linkedin_settings(client_id=None, client_secret=None))
    try:
        with pytest.raises(LinkedInError, match="not_configured"):
            await client.introspect(MEMBER_TOKEN)
        with pytest.raises(LinkedInError, match="not_configured"):
            await client.exchange_code("c", "https://writer.test/api/linkedin/oauth/callback")
    finally:
        await client.aclose()
    assert linkedin.requests == []


async def test_logs_carry_no_token_url_or_secret(
    linkedin: FakeLinkedIn, li_client: LinkedInClient, caplog: pytest.LogCaptureFixture
) -> None:
    configure_logging()  # as the app does: httpx's own request lines (with URLs) are silenced
    caplog.set_level(logging.DEBUG)
    linkedin.upload_flow("document", DOC_URN)
    url, _urn = await li_client.initialize_upload(MEMBER_TOKEN, "document", MEMBER_URN)
    await li_client.upload_bytes(url, b"%PDF", "application/pdf", token=MEMBER_TOKEN)
    linkedin.introspection(active=True)
    await li_client.introspect(MEMBER_TOKEN)
    linkedin.script("POST", INTROSPECT_URL, httpx.ReadTimeout("slow"))
    with pytest.raises(LinkedInError, match="timeout"):
        await li_client.introspect(MEMBER_TOKEN)
    text = " ".join(repr(record.__dict__) for record in caplog.records)
    assert "linkedin_call" in text
    for secret in (MEMBER_TOKEN, CLIENT_SECRET, "ut=cap", "dms-uploads"):
        assert secret not in text
