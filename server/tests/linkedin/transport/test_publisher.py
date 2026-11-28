"""The publisher: one owner tap, one intent, one POST, never retried; the outcomes; the confirm
token; the go-live view."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from blogwriter.core.auth import Principal
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, read_doc, replace_doc
from blogwriter.domain.jobtypes import PLANNER_POST_LIVE, post_live_job_id
from blogwriter.domain.posts import Post
from blogwriter.domain.states import ConnectionStatus, PostStatus
from blogwriter.domain.testing import FakeServices, StoreJobQueue, make_app, pdf_bytes, png_bytes
from blogwriter.linkedin.settings import LinkedInSettings
from blogwriter.linkedin.transport import get_router
from blogwriter.linkedin.transport.client import API_BASE, LinkedInClient, client_installed
from blogwriter.linkedin.transport.lifecycle import sweep_stale_intents
from blogwriter.linkedin.transport.publisher import (
    OwnerClick,
    PublishIn,
    confirm_key,
    issue_confirm_token,
    owner_click,
    publish_post,
    verify_confirm_token,
)
from blogwriter.linkedin.transport.testing import (
    DOC_URN,
    MEMBER_URN,
    POST_URN,
    POSTS_URL,
    UPLOAD_URL,
    FakeLinkedIn,
    approved_post,
    connect_member,
    post_media,
    save_blocklist,
)
from blogwriter.linkedin.transport.tokens import disconnect, read_connection
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
DISPLAY = "We scope every token to one tool call (C# included).\n\nThree steps below. #AISecurity"


@pytest.fixture
async def ready(services: FakeServices, li_client: LinkedInClient) -> FakeServices:
    del li_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    await save_blocklist(services.store, (), now=T0)
    return services


async def text_post(services: FakeServices, **fields: Any) -> Stored[Post]:
    values: dict[str, Any] = {"lane": "native", "pillar": "agents", "format": "text", **fields}
    post = approved_post(display=DISPLAY, hashtags=["AISecurity"], **values)
    return await create_doc(services.store, post)


async def go_live(http: httpx.AsyncClient, post_id: str) -> dict[str, Any]:
    response = await http.get(f"/api/posts/{post_id}/go-live", headers=READ)
    assert response.status_code == 200, response.text
    view: dict[str, Any] = response.json()
    return view


async def tap(
    http: httpx.AsyncClient, post_id: str, view: dict[str, Any], *, etag: str | None = None
) -> httpx.Response:
    return await http.post(
        f"/api/posts/{post_id}/publish",
        json={"approvalSha256": view["approvalSha256"], "confirmToken": view["confirmToken"]},
        headers={**WRITE, "If-Match": etag or view["post"]["etag"]},
    )


def alert_refs(caplog: pytest.LogCaptureFixture) -> list[object]:
    return [getattr(r, "ref_id", None) for r in caplog.records if r.getMessage() == "alert_event"]


# --------------------------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------------------------


async def test_one_tap_posts_once(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    linkedin.created()
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "posted"
    assert item["linkedinUrn"] == POST_URN
    assert item["postUrl"] == f"https://www.linkedin.com/feed/update/{POST_URN}/"
    assert item["postedAt"] == to_iso(T0)
    assert item["finalText"] == DISPLAY == item["displayText"]
    assert item["intent"]["status"] == "succeeded"
    assert response.headers["etag"] == response.json()["etag"]
    (request,) = linkedin.calls("POST", POSTS_URL)
    body = linkedin.body(request)
    assert isinstance(body, dict)
    assert body["author"] == MEMBER_URN
    assert body["commentary"] == stored.doc.payload
    job = await ready.jobs.get(post_live_job_id(stored.doc.id))
    assert job.type == PLANNER_POST_LIVE
    assert job.input == {"origin": "owner", "postId": stored.doc.id, "goLiveAt": to_iso(T0)}


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500),
        httpx.Response(504),
        httpx.ReadTimeout("no answer"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ReadError("Connection reset by peer"),
    ],
    ids=["500", "504", "timeout", "connection-reset", "read-error"],
)
async def test_an_unknown_answer_is_never_retried(
    http: httpx.AsyncClient,
    ready: FakeServices,
    linkedin: FakeLinkedIn,
    caplog: pytest.LogCaptureFixture,
    answer: httpx.Response | Exception,
) -> None:
    stored = await text_post(ready, article_id="art-0000000000000000000000000001")
    linkedin.always("POST", POSTS_URL, answer)  # a retry would be answered the same way
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "needs_attention"
    assert item["intent"]["status"] == "unknown"
    assert item["linkedinUrn"] is None
    assert len(linkedin.calls("POST", POSTS_URL)) == 1
    assert stored.doc.id in alert_refs(caplog)
    (push, urgency) = ready.notifier.sent[-1]
    assert push.title == "Check LinkedIn: did your post go out?"
    assert push.url == f"/studio/art-0000000000000000000000000001/go-live?post={stored.doc.id}"
    assert urgency == "high"


@pytest.mark.parametrize("status", [400, 403, 422, 429])
async def test_a_definite_4xx_leaves_the_post_approved(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn, status: int
) -> None:
    stored = await text_post(ready)
    linkedin.always("POST", POSTS_URL, httpx.Response(status))
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["status"] == "approved"
    assert (item["intent"]["status"], item["intent"]["httpStatus"]) == ("failed", status)
    assert item["lastError"] == f"linkedin_{status}"
    assert len(linkedin.calls("POST", POSTS_URL)) == 1  # never a second POST
    after = await read_doc(ready.store, Post, stored.doc.id)
    assert after.doc.approval_current  # the owner may tap again
    assert (await read_connection(ready.store, T0)).status is ConnectionStatus.CONNECTED


async def test_a_401_also_marks_the_connection(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    linkedin.script("POST", POSTS_URL, httpx.Response(401))
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.json()["item"]["status"] == "approved"
    conn = await read_connection(ready.store, T0)
    assert (conn.status, conn.last_error) == (ConnectionStatus.NEEDS_RECONNECT, "unauthorized")


async def test_a_double_tap_publishes_once(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    linkedin.created()
    view = await go_live(http, stored.doc.id)
    first, second = await asyncio.gather(
        tap(http, stored.doc.id, view), tap(http, stored.doc.id, view)
    )
    assert sorted([first.status_code, second.status_code]) == [200, 412]
    assert len(linkedin.calls("POST", POSTS_URL)) == 1


async def test_a_stale_screen_is_refused(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    await replace_doc(ready.store, revalidated(stored.doc, pillar="identity"), if_match=stored.etag)
    response = await tap(http, stored.doc.id, view)
    assert response.status_code == 412
    assert response.json()["code"] == "etag_mismatch"
    assert response.json()["current"]["status"] == "approved"
    assert linkedin.requests == []


async def test_refusals_happen_before_any_post(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    draft = await text_post(ready)
    draft = await replace_doc(
        ready.store, revalidated(draft.doc, status=PostStatus.DRAFT), if_match=draft.etag
    )
    response = await tap(http, draft.doc.id, await go_live(http, draft.doc.id))
    assert (response.status_code, response.json()["code"]) == (409, "not_approved")

    stale = await text_post(ready)
    view = await go_live(http, stale.doc.id)
    edited = await replace_doc(
        ready.store,
        revalidated(stale.doc, display_text="Edited after approval."),
        if_match=stale.etag,
    )
    response = await tap(http, stale.doc.id, view, etag=edited.etag)
    assert (response.status_code, response.json()["code"]) == (409, "approval_stale")

    expired = await text_post(
        ready, time_sensitive=True, expires_at=to_iso(T0 - timedelta(minutes=1))
    )
    response = await tap(http, expired.doc.id, await go_live(http, expired.doc.id))
    assert (response.status_code, response.json()["code"]) == (409, "post_expired")

    await disconnect(ready.store, now=T0)
    fresh = await text_post(ready)
    view = await go_live(http, fresh.doc.id)
    assert view["canPublish"] is False
    assert "reconnect_required" in view["reasons"]
    response = await tap(http, fresh.doc.id, view)
    assert (response.status_code, response.json()["code"]) == (409, "reconnect_required")
    assert linkedin.calls("POST", POSTS_URL) == []


async def test_without_a_vault_the_tap_is_503(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    ready.vault = None
    response = await tap(http, stored.doc.id, view)
    assert (response.status_code, response.json()["code"]) == (503, "vault_unavailable")
    assert linkedin.requests == []


async def test_carousel_media_is_uploaded_before_the_post(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    assert ready.blobs is not None
    pdf = await post_media(
        ready.blobs,
        pdf_bytes(2),
        role="carousel_pdf",
        content_type="application/pdf",
        name="posts/c/deck.pdf",
    )
    cover = await post_media(
        ready.blobs,
        png_bytes(),
        role="carousel_cover",
        content_type="image/png",
        name="posts/c/cover.png",
    )
    post = approved_post(
        display="Five checks.",
        lane="derivative",
        pillar="agents",
        format="carousel",
        document_title="Agent tokens: 5 checks",
        media=[cover, pdf],
    )
    stored = await create_doc(ready.store, post)
    view = await go_live(http, stored.doc.id)
    assert view["canPublish"] is True
    assert view["cover"]["role"] == "carousel_cover"
    assert view["documentTitle"] == "Agent tokens: 5 checks"
    linkedin.upload_flow("document", DOC_URN)
    linkedin.created()
    response = await tap(http, stored.doc.id, view)
    assert response.json()["item"]["status"] == "posted"
    assert [r.method for r in linkedin.requests] == ["POST", "PUT", "GET", "POST"]
    body = linkedin.body(linkedin.calls("POST", POSTS_URL)[0])
    assert isinstance(body, dict)
    assert body["content"] == {"media": {"title": "Agent tokens: 5 checks", "id": DOC_URN}}


async def test_a_failed_upload_refuses_the_tap(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    assert ready.blobs is not None
    pdf = await post_media(
        ready.blobs,
        pdf_bytes(1),
        role="carousel_pdf",
        content_type="application/pdf",
        name="posts/d/deck.pdf",
    )
    post = approved_post(
        display="Deck.",
        lane="native",
        pillar="agents",
        format="carousel",
        document_title="Deck",
        media=[pdf],
    )
    stored = await create_doc(ready.store, post)
    linkedin.script("POST", f"{API_BASE}/documents?action=initializeUpload", httpx.Response(500))
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.status_code == 409
    assert (response.json()["code"], response.json()["reason"]) == (
        "media_upload_failed",
        "http_500",
    )
    assert linkedin.calls("POST", POSTS_URL) == []
    assert linkedin.calls("PUT", UPLOAD_URL) == []
    assert (await read_doc(ready.store, Post, stored.doc.id)).doc.status is PostStatus.APPROVED


async def test_an_unregistered_planner_hook_does_not_undo_a_post(
    http: httpx.AsyncClient,
    ready: FakeServices,
    linkedin: FakeLinkedIn,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready.jobs = StoreJobQueue(ready.store, clock=ready.clock, registered=set())
    stored = await text_post(ready)
    linkedin.created()
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert response.json()["item"]["status"] == "posted"
    assert any(r.getMessage() == "job_hook_unregistered" for r in caplog.records)


# --------------------------------------------------------------------------------------------
# Confirm tokens and the owner's click
# --------------------------------------------------------------------------------------------


async def test_the_tap_needs_a_valid_fresh_confirm_token(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    other = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    bad_views = [
        {**view, "confirmToken": "v1.1795000000.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
        {**view, "confirmToken": "nonsense"},
        {**view, "confirmToken": (await go_live(http, other.doc.id))["confirmToken"]},
        {**view, "approvalSha256": "0" * 64},
    ]
    for bad in bad_views:
        response = await tap(http, stored.doc.id, bad)
        assert (response.status_code, response.json()["code"]) == (409, "confirm_invalid")
    ready.clock.advance(timedelta(seconds=601))
    response = await tap(http, stored.doc.id, view)
    assert (response.status_code, response.json()["code"]) == (409, "confirm_invalid")
    no_if_match = await http.post(
        f"/api/posts/{stored.doc.id}/publish",
        json={"approvalSha256": view["approvalSha256"], "confirmToken": view["confirmToken"]},
        headers=WRITE,
    )
    assert no_if_match.status_code == 428
    assert linkedin.requests == []


async def test_the_tap_needs_the_owner_and_csrf(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    payload = {"approvalSha256": view["approvalSha256"], "confirmToken": view["confirmToken"]}
    etag = {"If-Match": view["post"]["etag"]}
    http.cookies.clear()  # the GET above left the app cookie in the client's jar
    anonymous = await http.post(f"/api/posts/{stored.doc.id}/publish", json=payload, headers=etag)
    no_csrf = await http.post(
        f"/api/posts/{stored.doc.id}/publish", json=payload, headers={**READ, **etag}
    )
    assert (anonymous.status_code, no_csrf.status_code) == (401, 403)
    assert linkedin.requests == []


async def test_a_term_blocklisted_after_approval_stops_the_tap(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    await save_blocklist(ready.store, ["Three steps"], now=T0)  # added after the approval
    response = await tap(http, stored.doc.id, view)
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "confidential_text"
    assert {finding["field"] for finding in problem["findings"]} >= {"displayText"}
    assert "Three steps" not in response.text  # the term id only
    assert linkedin.requests == []  # no upload, no POST
    after = await read_doc(ready.store, Post, stored.doc.id)
    assert after.doc.status is PostStatus.APPROVED
    assert after.doc.intent is None


async def test_the_tap_screens_slides_and_alt_texts_too(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(
        ready,
        format="carousel",
        document_title="Scoped calls",
        carousel_spec={"title": "Scoped calls", "slides": [{"type": "cover", "title": "Contoso"}]},
    )
    view = await go_live(http, stored.doc.id)
    await save_blocklist(ready.store, ["Contoso"], now=T0)
    response = await tap(http, stored.doc.id, view)
    assert response.status_code == 422, response.text
    fields = {finding["field"] for finding in response.json()["findings"]}
    assert fields == {"carouselSpec.slides[0].title"}
    assert linkedin.requests == []


async def test_the_tap_needs_a_saved_blocklist(
    http: httpx.AsyncClient, services: FakeServices, li_client: LinkedInClient
) -> None:
    del li_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    stored = await text_post(services)
    response = await tap(http, stored.doc.id, await go_live(http, stored.doc.id))
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")
    after = await read_doc(services.store, Post, stored.doc.id)
    assert after.doc.status is PostStatus.APPROVED


async def test_a_confirm_token_survives_a_restart(
    ready: FakeServices, li_settings: LinkedInSettings, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)

    def booted(settings: Any) -> FastAPI:
        app = make_app(ready, routers=[get_router()], settings=settings)
        app.state.linkedin_settings = li_settings
        return app

    async with asgi_client(booted(make_settings())) as before:
        view = await go_live(before, stored.doc.id)
    other_key = make_settings(cookie_key="another-cookie-key-for-tests-0123456789")
    async with asgi_client(booted(other_key)) as elsewhere:
        refused = await elsewhere.post(
            f"/api/posts/{stored.doc.id}/publish",
            json={"approvalSha256": view["approvalSha256"], "confirmToken": view["confirmToken"]},
            headers={**READ, **write_headers(other_key), "If-Match": view["post"]["etag"]},
        )
    assert (refused.status_code, refused.json()["code"]) == (409, "confirm_invalid")
    linkedin.created()
    async with asgi_client(booted(make_settings())) as after:  # a new process, same BW_COOKIE_KEY
        response = await tap(after, stored.doc.id, view)
    assert response.json()["item"]["status"] == "posted"


def test_confirm_token_keys() -> None:
    key = confirm_key(SETTINGS)
    assert key == confirm_key(make_settings())  # derived from BW_COOKIE_KEY, not random
    assert key != SETTINGS.cookie_key.get_secret_value().encode() if SETTINGS.cookie_key else True
    unset = make_settings(cookie_key=None)
    assert confirm_key(unset) == confirm_key(unset) != key  # per-process outside prod
    token, expires = issue_confirm_token("post-1", "a" * 64, now=T0, key=key, ttl_seconds=600)
    assert expires == to_iso(T0 + timedelta(seconds=600))
    ok = verify_confirm_token(
        token, "post-1", "a" * 64, now=T0 + timedelta(seconds=600), key=key, ttl_seconds=600
    )
    late = verify_confirm_token(
        token, "post-1", "a" * 64, now=T0 + timedelta(seconds=601), key=key, ttl_seconds=600
    )
    early = verify_confirm_token(
        token, "post-1", "a" * 64, now=T0 - timedelta(minutes=5), key=key, ttl_seconds=600
    )
    assert (ok, late, early) == (True, False, False)


async def owner_click_for(
    services: FakeServices, stored: Stored[Post], li_settings: LinkedInSettings
) -> OwnerClick:
    approval = stored.doc.approval
    assert approval is not None
    token, _ = issue_confirm_token(
        stored.doc.id, approval.sha256, now=T0, key=confirm_key(SETTINGS), ttl_seconds=600
    )
    body = PublishIn(approval_sha256=approval.sha256, confirm_token=token)
    principal = Principal(oid=OWNER_OID, name=None, source="cookie")
    return await owner_click(stored.doc.id, body, principal, SETTINGS, li_settings, services.clock)


async def test_an_owner_click_cannot_be_forged_copied_or_reused(
    ready: FakeServices, li_settings: LinkedInSettings, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    with pytest.raises(TypeError):
        OwnerClick(stored.doc.id, "a" * 64)  # pyright: ignore[reportCallIssue]
    with pytest.raises(TypeError, match="only from the publish route"):
        OwnerClick(stored.doc.id, "a" * 64, object())
    click = await owner_click_for(ready, stored, li_settings)
    with pytest.raises(TypeError):
        await publish_post(
            ready, stored.doc.id, click=dataclasses.replace(click), if_match=stored.etag
        )
    fake: Any = type(
        "Click", (), {"post_id": stored.doc.id, "approval_sha256": click.approval_sha256}
    )()
    with pytest.raises(TypeError):
        await publish_post(ready, stored.doc.id, click=fake, if_match=stored.etag)
    assert linkedin.requests == []
    linkedin.created()
    posted = await publish_post(ready, stored.doc.id, click=click, if_match=stored.etag)
    assert posted.doc.status is PostStatus.POSTED
    with pytest.raises(TypeError):
        await publish_post(ready, stored.doc.id, click=click, if_match=posted.etag)  # used once
    assert len(linkedin.calls("POST", POSTS_URL)) == 1


# --------------------------------------------------------------------------------------------
# The attempt always settles
# --------------------------------------------------------------------------------------------


class SlowLinkedIn:
    """``POST /rest/posts`` waits for ``release``; other calls are not expected."""

    def __init__(self, before_answer: Any = None) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.posts = 0
        self.before_answer = before_answer

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, str(request.url)) == ("POST", POSTS_URL)
        self.posts += 1
        self.started.set()
        await self.release.wait()
        if self.before_answer is not None:
            await self.before_answer()
        return httpx.Response(201, headers={"x-restli-id": POST_URN})


@pytest.fixture
async def slow_client(
    li_settings: LinkedInSettings,
) -> AsyncIterator[tuple[SlowLinkedIn, LinkedInClient]]:
    slow = SlowLinkedIn()
    client = LinkedInClient(li_settings, transport=httpx.MockTransport(slow))
    yield slow, client
    await client.aclose()


async def test_a_cancelled_request_still_settles_its_attempt(
    services: FakeServices,
    li_settings: LinkedInSettings,
    slow_client: tuple[SlowLinkedIn, LinkedInClient],
) -> None:
    slow, client = slow_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    await save_blocklist(services.store, (), now=T0)
    stored = await text_post(services)
    click = await owner_click_for(services, stored, li_settings)
    request = asyncio.create_task(
        publish_post(services, stored.doc.id, click=click, if_match=stored.etag, client=client)
    )
    await slow.started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    publishing = await read_doc(services.store, Post, stored.doc.id)
    assert publishing.doc.status is PostStatus.PUBLISHING
    slow.release.set()
    for _ in range(100):
        await asyncio.sleep(0)
        if (await read_doc(services.store, Post, stored.doc.id)).doc.status is PostStatus.POSTED:
            break
    final = await read_doc(services.store, Post, stored.doc.id)
    assert (final.doc.status, final.doc.linkedin_urn, slow.posts) == (
        PostStatus.POSTED,
        POST_URN,
        1,
    )


async def test_a_late_201_after_the_sweep_still_records_the_post(
    services: FakeServices, li_settings: LinkedInSettings, caplog: pytest.LogCaptureFixture
) -> None:
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    await save_blocklist(services.store, (), now=T0)
    stored = await text_post(services)

    async def sweep_first() -> None:
        services.clock.advance(timedelta(minutes=3))
        assert await sweep_stale_intents(services.store, now=services.clock.now()) == [
            stored.doc.id
        ]

    slow = SlowLinkedIn(before_answer=sweep_first)
    client = LinkedInClient(li_settings, transport=httpx.MockTransport(slow))
    try:
        click = await owner_click_for(services, stored, li_settings)
        slow.release.set()
        with client_installed(client):
            result = await publish_post(services, stored.doc.id, click=click, if_match=stored.etag)
    finally:
        await client.aclose()
    assert result.doc.status is PostStatus.POSTED
    assert result.doc.intent is not None
    assert result.doc.intent.status == "succeeded"
    assert result.doc.linkedin_urn == POST_URN
    assert slow.posts == 1


# --------------------------------------------------------------------------------------------
# The go-live view
# --------------------------------------------------------------------------------------------


async def test_the_go_live_view_has_no_side_effects(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await text_post(ready)
    view = await go_live(http, stored.doc.id)
    assert view["post"]["etag"] == stored.etag
    assert view["foldIndex"] > 0
    assert view["approvalCurrent"] is True
    assert view["approvalSha256"] == stored.doc.compute_approval_hash()
    assert view["connection"] == {"status": "connected", "tokenDay": 0}
    assert view["confirmExpiresAt"] == to_iso(T0 + timedelta(seconds=600))
    assert (view["canPublish"], view["reasons"]) == (True, [])
    assert view["altTexts"] == []
    again = await read_doc(ready.store, Post, stored.doc.id)
    assert again.etag == stored.etag
    assert linkedin.requests == []
    missing = await http.get("/api/posts/post-00000000000000000000000000ff/go-live", headers=READ)
    assert missing.status_code == 404


async def test_the_go_live_view_explains_blockers(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    post = approved_post(
        display="Deck.",
        lane="native",
        pillar="agents",
        format="carousel",
        approve=False,
        status=PostStatus.DRAFT,
    )
    stored = await create_doc(services.store, post)
    view = await go_live(http, stored.doc.id)
    assert view["canPublish"] is False
    assert view["reasons"] == [
        "not_approved",
        "approval_stale",
        "media_missing",
        "reconnect_required",
    ]
    assert (view["confirmToken"], view["approvalSha256"]) == (None, None)
    assert view["connection"] == {"status": "disconnected", "tokenDay": None}
