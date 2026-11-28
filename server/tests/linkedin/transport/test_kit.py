"""The ready-to-post kit, the go-live mode and resolving a needs-attention attempt."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, read_doc
from blogwriter.domain.jobtypes import (
    PLANNER_POST_LIVE,
    PLANNER_POST_SCHEDULED,
    post_live_job_id,
    post_scheduled_job_id,
)
from blogwriter.domain.posts import Post, PublishIntent, settle_publish_intent
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import FakeServices, pdf_bytes, png_bytes
from blogwriter.linkedin.transport.kit import kit_view
from blogwriter.linkedin.transport.testing import POST_URN, approved_post, post_media
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
DISPLAY = "Ship the agent with one token per tool call (not per agent).\n\nHere is why. #AISecurity"
FEED_URL = f"https://www.linkedin.com/feed/update/{POST_URN}/"


async def approved(services: FakeServices, **fields: Any) -> Stored[Post]:
    values: dict[str, Any] = {"lane": "native", "pillar": "agents", "format": "text", **fields}
    return await create_doc(
        services.store, approved_post(display=DISPLAY, hashtags=["AISecurity"], **values)
    )


async def call(
    http: httpx.AsyncClient, path: str, etag: str, body: dict[str, Any]
) -> httpx.Response:
    return await http.post(path, json=body, headers={**WRITE, "If-Match": etag})


async def in_kit(http: httpx.AsyncClient, services: FakeServices) -> tuple[str, str]:
    stored = await approved(services)
    response = await call(http, f"/api/posts/{stored.doc.id}/mode", stored.etag, {"mode": "kit"})
    assert response.status_code == 200, response.text
    return stored.doc.id, response.json()["etag"]


async def test_switching_to_the_kit_and_back(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await approved(services)
    to_kit = await call(http, f"/api/posts/{stored.doc.id}/mode", stored.etag, {"mode": "kit"})
    item = to_kit.json()["item"]
    assert (item["status"], item["goLiveMode"]) == ("kit", "kit")
    assert item["kit"]["state"] == "prepared"
    assert item["kit"]["preparedAt"] == to_iso(T0)
    same = await call(
        http, f"/api/posts/{stored.doc.id}/mode", to_kit.json()["etag"], {"mode": "kit"}
    )
    assert same.json()["etag"] == to_kit.json()["etag"]  # nothing to change
    back = await call(
        http, f"/api/posts/{stored.doc.id}/mode", to_kit.json()["etag"], {"mode": "one_click"}
    )
    item = back.json()["item"]
    assert (item["status"], item["goLiveMode"], item["kit"]) == ("approved", "one_click", None)
    missing = await http.post(
        f"/api/posts/{stored.doc.id}/mode", json={"mode": "kit"}, headers=WRITE
    )
    assert missing.status_code == 428


async def test_mode_rules(http: httpx.AsyncClient, services: FakeServices) -> None:
    draft = await create_doc(
        services.store,
        approved_post(
            display=DISPLAY, lane="native", pillar="agents", format="text", status=PostStatus.DRAFT
        ),
    )
    response = await call(http, f"/api/posts/{draft.doc.id}/mode", draft.etag, {"mode": "kit"})
    assert (response.status_code, response.json()["code"]) == (409, "not_approved")
    post_id, etag = await in_kit(http, services)
    saved = await call(http, f"/api/posts/{post_id}/kit", etag, {"to": "native_draft_saved"})
    response = await call(
        http, f"/api/posts/{post_id}/mode", saved.json()["etag"], {"mode": "one_click"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "kit_in_progress")
    stale = await call(http, f"/api/posts/{post_id}/mode", etag, {"mode": "one_click"})
    assert stale.status_code == 409  # the stale etag never gets as far as a write


async def test_kit_states_through_natively_scheduled_to_posted(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    post_id, etag = await in_kit(http, services)
    saved = await call(http, f"/api/posts/{post_id}/kit", etag, {"to": "native_draft_saved"})
    assert saved.json()["item"]["kit"]["state"] == "native_draft_saved"
    assert saved.json()["item"]["kit"]["draftSavedAt"] == to_iso(T0)
    first_t = "2026-11-27T15:00:00-05:00"
    scheduled = await call(
        http,
        f"/api/posts/{post_id}/kit",
        saved.json()["etag"],
        {"to": "natively_scheduled", "scheduledAt": first_t},
    )
    kit = scheduled.json()["item"]["kit"]
    assert (kit["state"], kit["scheduledAt"]) == (
        "natively_scheduled",
        "2026-11-27T20:00:00.000000Z",
    )
    job = await services.jobs.get(post_scheduled_job_id(post_id, "2026-11-27T20:00:00.000000Z"))
    assert job.type == PLANNER_POST_SCHEDULED
    assert job.input == {
        "origin": "owner",
        "postId": post_id,
        "scheduledAt": "2026-11-27T20:00:00.000000Z",
    }
    moved = await call(
        http,
        f"/api/posts/{post_id}/kit",
        scheduled.json()["etag"],
        {"to": "natively_scheduled", "scheduledAt": "2026-11-26T11:30:00Z"},  # a new T (an update)
    )
    assert moved.json()["item"]["kit"]["scheduledAt"] == "2026-11-26T11:30:00.000000Z"
    await services.jobs.get(post_scheduled_job_id(post_id, "2026-11-26T11:30:00.000000Z"))
    final_text = DISPLAY.replace("Here is why.", "Here is why it matters.")
    posted = await call(
        http,
        f"/api/posts/{post_id}/kit",
        moved.json()["etag"],
        {"to": "posted", "postUrl": FEED_URL, "finalText": final_text},
    )
    item = posted.json()["item"]
    assert (item["status"], item["kit"]["state"]) == ("posted", "posted")
    assert item["postedAt"] == "2026-11-26T11:30:00.000000Z"  # T had passed: it went live at T
    assert (item["postUrl"], item["linkedinUrn"], item["finalText"]) == (
        FEED_URL,
        POST_URN,
        final_text,
    )
    live = await services.jobs.get(post_live_job_id(post_id))
    assert live.type == PLANNER_POST_LIVE
    assert live.input == {
        "origin": "owner",
        "postId": post_id,
        "goLiveAt": "2026-11-26T11:30:00.000000Z",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"to": "natively_scheduled"},
        {"to": "posted", "postUrl": FEED_URL},
        {"to": "posted", "postUrl": "https://evil.test/post", "finalText": "x"},
        {"to": "posted", "postUrl": FEED_URL, "finalText": ""},
        {"to": "prepared"},
        {"to": "natively_scheduled", "scheduledAt": "2026-11-27T15:00:00"},  # no zone
    ],
)
async def test_kit_input_rules(
    http: httpx.AsyncClient, services: FakeServices, body: dict[str, Any]
) -> None:
    post_id, etag = await in_kit(http, services)
    response = await call(http, f"/api/posts/{post_id}/kit", etag, body)
    assert (response.status_code, response.json()["code"]) == (422, "validation_failed")


async def test_kit_transitions_follow_the_machine(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await approved(services)
    response = await call(
        http, f"/api/posts/{stored.doc.id}/kit", stored.etag, {"to": "native_draft_saved"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "not_in_kit")
    post_id, etag = await in_kit(http, services)
    saved = await call(http, f"/api/posts/{post_id}/kit", etag, {"to": "native_draft_saved"})
    again = await call(
        http, f"/api/posts/{post_id}/kit", saved.json()["etag"], {"to": "native_draft_saved"}
    )
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    stale = await call(
        http,
        f"/api/posts/{post_id}/kit",
        etag,
        {"to": "natively_scheduled", "scheduledAt": "2026-11-27T15:00:00Z"},
    )
    assert stale.status_code == 412


async def test_the_kit_copies_the_display_text(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    assert services.blobs is not None
    pdf = await post_media(
        services.blobs,
        pdf_bytes(2),
        role="carousel_pdf",
        content_type="application/pdf",
        name="posts/k/deck.pdf",
        alt_text="Five checks",
    )
    cover = await post_media(
        services.blobs,
        png_bytes(),
        role="carousel_cover",
        content_type="image/png",
        name="posts/k/cover.png",
    )
    stored = await approved(
        services, format="carousel", document_title="Agent tokens: 5 checks", media=[cover, pdf]
    )
    response = await http.get(f"/api/posts/{stored.doc.id}/kit", headers=READ)
    assert response.status_code == 200
    view = response.json()
    assert view["displayText"] == DISPLAY == stored.doc.display_text
    assert view["displayText"] != stored.doc.payload  # never the escaped payload
    assert view["documentTitle"] == "Agent tokens: 5 checks"
    assert view["altTexts"] == ["Five checks"]
    (download,) = view["downloads"]
    assert (download["url"], download["filename"]) == (
        "/api/media/posts/k/deck.pdf",
        f"{stored.doc.id}.pdf",
    )
    assert len(view["checklist"]) == 4
    assert "PDF" in view["checklist"][1]
    assert view["openLinkedInUrl"] == "https://www.linkedin.com/feed/"
    assert (view["state"], view["scheduledAt"]) == (None, None)
    for fmt in ("text", "image_card", "poll", "carousel"):
        assert kit_view(revalidated(stored.doc, format=fmt)).display_text == DISPLAY


async def attempt_needing_attention(services: FakeServices, **fields: Any) -> Stored[Post]:
    post = approved_post(
        display=DISPLAY,
        hashtags=["AISecurity"],
        lane="native",
        pillar="agents",
        format="text",
        **fields,
    )
    assert post.approval is not None
    intent = PublishIntent(
        id="intent-test-0001",
        created_at=to_iso(T0 - timedelta(minutes=5)),
        approval_sha256=post.approval.sha256,
    )
    publishing = revalidated(post, status=PostStatus.PUBLISHING, intent=intent)
    settled = settle_publish_intent(publishing, "unknown", now=to_iso(T0), error_code="timeout")
    return await create_doc(services.store, settled)


async def test_resolving_an_attempt_that_went_out(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await attempt_needing_attention(services)
    response = await call(
        http,
        f"/api/posts/{stored.doc.id}/resolve",
        stored.etag,
        {"outcome": "posted", "postUrl": FEED_URL},
    )
    item = response.json()["item"]
    assert (item["status"], item["linkedinUrn"], item["postUrl"]) == ("posted", POST_URN, FEED_URL)
    assert item["postedAt"] == to_iso(T0 - timedelta(minutes=5))  # when the tap sent it
    assert item["finalText"] == DISPLAY
    await services.jobs.get(post_live_job_id(stored.doc.id))


async def test_resolving_an_attempt_that_did_not_go_out(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await attempt_needing_attention(services)
    response = await call(
        http, f"/api/posts/{stored.doc.id}/resolve", stored.etag, {"outcome": "not_posted"}
    )
    item = response.json()["item"]
    assert (item["status"], item["lastError"]) == ("approved", None)
    edited = await attempt_needing_attention(services)
    changed = await services.store.replace(
        "posts",
        revalidated(edited.doc, display_text="Changed meanwhile.").to_document(),
        if_match=edited.etag,
    )
    response = await call(
        http, f"/api/posts/{edited.doc.id}/resolve", changed.etag, {"outcome": "not_posted"}
    )
    assert response.json()["item"]["status"] == "draft"  # the approval no longer holds


async def test_only_publish_attempts_can_be_resolved(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await approved(services)
    response = await call(
        http, f"/api/posts/{stored.doc.id}/resolve", stored.etag, {"outcome": "posted"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "not_a_publish_attempt")
    generated = await create_doc(
        services.store,
        approved_post(
            display=DISPLAY,
            lane="native",
            pillar="agents",
            format="text",
            status=PostStatus.NEEDS_ATTENTION,
            approve=False,
        ),
    )
    response = await call(
        http, f"/api/posts/{generated.doc.id}/resolve", generated.etag, {"outcome": "not_posted"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "not_a_publish_attempt")
    after = await read_doc(services.store, Post, generated.doc.id)
    assert after.doc.status is PostStatus.NEEDS_ATTENTION
