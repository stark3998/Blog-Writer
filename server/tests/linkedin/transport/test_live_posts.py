"""Editing and deleting a live post: confirm token, lint and blocklist first, one LinkedIn call,
then the post."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, read_doc, save_singleton
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, BlocklistTerm
from blogwriter.domain.states import ConnectionStatus, PostStatus
from blogwriter.domain.testing import FakeServices
from blogwriter.linkedin.littletext import api_commentary, parse_display
from blogwriter.linkedin.transport.client import API_BASE, LinkedInClient
from blogwriter.linkedin.transport.content import post_url_for
from blogwriter.linkedin.transport.testing import (
    POST_URN,
    FakeLinkedIn,
    approved_post,
    connect_member,
)
from blogwriter.linkedin.transport.tokens import read_connection
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
DISPLAY = "One token per tool call. #AISecurity"
EDITED = "One token per tool call (not per agent). #AISecurity"
POST_API = f"{API_BASE}/posts/urn%3Ali%3Ashare%3A7300000000000000001"


@pytest.fixture
async def ready(services: FakeServices, li_client: LinkedInClient) -> FakeServices:
    del li_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    await save_singleton(
        services.store,
        revalidated(
            Blocklist.default(to_iso(T0)),
            configured=True,
            terms=[BlocklistTerm(id="client-1", term="Contoso Bank")],
        ),
        if_match=None,
    )
    return services


async def live(services: FakeServices, *, urn: str | None = POST_URN) -> Stored[Post]:
    post = approved_post(
        display=DISPLAY, hashtags=["AISecurity"], lane="native", pillar="agents", format="text"
    )
    posted = revalidated(
        post,
        status=PostStatus.POSTED,
        linkedin_urn=urn,
        posted_at=to_iso(T0),
        post_url=post_url_for(POST_URN),
        final_text=DISPLAY,
    )
    return await create_doc(services.store, posted)


async def token_for(http: httpx.AsyncClient, post_id: str) -> tuple[str, str]:
    view = (await http.get(f"/api/posts/{post_id}/go-live", headers=READ)).json()
    return view["confirmToken"], view["post"]["etag"]


async def edit(
    http: httpx.AsyncClient, post_id: str, text: str, token: str, etag: str
) -> httpx.Response:
    return await http.patch(
        f"/api/posts/{post_id}/linkedin",
        json={"displayText": text, "confirmToken": token},
        headers={**WRITE, "If-Match": etag},
    )


async def delete(http: httpx.AsyncClient, post_id: str, token: str, etag: str) -> httpx.Response:
    return await http.post(
        f"/api/posts/{post_id}/linkedin/delete",
        json={"confirmToken": token},
        headers={**WRITE, "If-Match": etag},
    )


async def test_editing_a_live_post(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await live(ready)
    token, etag = await token_for(http, stored.doc.id)
    linkedin.script("POST", POST_API, httpx.Response(204))
    response = await edit(http, stored.doc.id, EDITED, token, etag)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    expected_payload = api_commentary(parse_display(EDITED, ["AISecurity"]))
    assert (item["finalText"], item["payload"]) == (EDITED, expected_payload)
    assert item["displayText"] == DISPLAY  # the approved text stays on record
    (request,) = linkedin.calls("POST", POST_API)
    assert request.headers["X-RestLi-Method"] == "PARTIAL_UPDATE"
    body: Any = linkedin.body(request)
    assert body == {"patch": {"$set": {"commentary": expected_payload}}}
    assert "\\(not per agent\\)" in expected_payload
    assert "#AISecurity" in expected_payload


async def test_lint_and_the_blocklist_come_first(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await live(ready)
    token, etag = await token_for(http, stored.doc.id)
    blocked = await edit(http, stored.doc.id, "\n" + EDITED, token, etag)
    assert (blocked.status_code, blocked.json()["code"]) == (409, "lint_failed")
    assert blocked.json()["findings"][0]["code"] == "leading-blank"
    confidential = await edit(http, stored.doc.id, "We helped Contoso Bank ship it.", token, etag)
    assert (confidential.status_code, confidential.json()["code"]) == (422, "confidential_text")
    assert "Contoso" not in confidential.text
    await save_singleton(
        ready.store,
        Blocklist.default(to_iso(T0)),
        if_match=(await read_doc(ready.store, Blocklist, "blocklist")).etag,
    )
    unconfigured = await edit(http, stored.doc.id, EDITED, token, etag)
    assert (unconfigured.status_code, unconfigured.json()["code"]) == (
        409,
        "blocklist_not_configured",
    )
    assert linkedin.requests == []


async def test_live_changes_need_a_valid_token_and_a_live_post(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await live(ready)
    _token, etag = await token_for(http, stored.doc.id)
    bad = await edit(
        http, stored.doc.id, EDITED, "v1.1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", etag
    )
    assert (bad.status_code, bad.json()["code"]) == (409, "confirm_invalid")
    by_hand = await live(ready, urn=None)
    token, etag = await token_for(http, by_hand.doc.id)
    response = await delete(http, by_hand.doc.id, token, etag)
    assert (response.status_code, response.json()["code"]) == (409, "no_linkedin_urn")
    draft = await create_doc(
        ready.store,
        approved_post(
            display=DISPLAY,
            lane="native",
            pillar="agents",
            format="text",
            status=PostStatus.APPROVED,
        ),
    )
    token, etag = await token_for(http, draft.doc.id)
    response = await edit(http, draft.doc.id, EDITED, token, etag)
    assert (response.status_code, response.json()["code"]) == (409, "not_live")
    assert linkedin.requests == []


async def test_deleting_a_live_post(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await live(ready)
    token, etag = await token_for(http, stored.doc.id)
    linkedin.script("DELETE", POST_API, httpx.Response(500), httpx.Response(404))
    failed = await delete(http, stored.doc.id, token, etag)
    assert (failed.status_code, failed.json()["code"]) == (503, "linkedin_unavailable")
    assert (await read_doc(ready.store, Post, stored.doc.id)).doc.status is PostStatus.POSTED
    gone = await delete(http, stored.doc.id, token, etag)  # already gone on LinkedIn: deleted
    assert gone.status_code == 200
    assert gone.json()["item"]["status"] == "deleted"
    assert len(linkedin.calls("DELETE", POST_API)) == 2


async def test_a_401_on_a_live_change_needs_a_reconnect(
    http: httpx.AsyncClient, ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await live(ready)
    token, etag = await token_for(http, stored.doc.id)
    linkedin.script("POST", POST_API, httpx.Response(401))
    response = await edit(http, stored.doc.id, EDITED, token, etag)
    assert (response.status_code, response.json()["code"]) == (409, "reconnect_required")
    assert (await read_connection(ready.store, T0)).status is ConnectionStatus.NEEDS_RECONNECT
    assert (await read_doc(ready.store, Post, stored.doc.id)).doc.final_text == DISPLAY
