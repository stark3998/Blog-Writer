"""The LinkedIn chains end to end, as the owner through the HTTP API.

One-click: derive posts from the published flagship -> lint -> render -> approve (bound to the
SHA-256 of text and media) -> staging at T-60 (upload, token check) -> the owner's tap at the
slot (a fresh request with the go-live page's confirm token: the intent is written first, then
exactly one ``POST /rest/posts``) -> reply reminders -> the snapshot prompt.

The kit: prepared -> native_draft_saved -> natively_scheduled (a new native time re-keys the
"did it go out?" reminder) -> posted with the owner's final text, without any LinkedIn call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta

import pytest
from pydantic import JsonValue

from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.jobtypes import (
    PLANNER_POST_LIVE,
    derive_job_id,
    post_live_job_id,
    post_scheduled_job_id,
    slot_check_job_id,
    slot_stamp,
    stage_job_id,
)
from blogwriter.domain.planner import Reminder
from blogwriter.domain.posts import Post
from blogwriter.domain.states import KitState, PostStatus, ReminderStatus
from blogwriter.linkedin.littletext import api_commentary, parse_display
from blogwriter.linkedin.transport.client import API_BASE, INTROSPECT_URL
from blogwriter.linkedin.transport.testing import IMAGE_URNS, POST_URN, POSTS_URL, UPLOAD_URL

from .conftest import (
    SLOT_UTC,
    STAGED_AT,
    CreateSeen,
    System,
    approve_for_slot,
    assert_nothing_leaked,
    connect_linkedin,
    derive_posts,
    dig,
    draft_flagship,
    prepare_owner,
    publish_click,
    review_and_publish,
    start_system,
)

P = PostStatus
INIT_IMAGE = f"{API_BASE}/images?action=initializeUpload"
KIT_FIRST_TIME = "2026-12-10T15:00:00.000000Z"
"""Thursday 10:00 in New York: the native time the owner first schedules in LinkedIn."""
KIT_TIME = "2026-12-10T16:00:00.000000Z"
"""Thursday 11:00: the time the owner moved it to."""
FINAL_TEXT = "Retire the borrowed human token first. Then inventory the tool calls."
POSTED_URL = "https://www.linkedin.com/feed/update/urn:li:share:7300000000000000002/"


async def post_of(system: System, post_id: str) -> Post:
    return (await read_doc(system.store, Post, post_id)).doc


async def reminders_of(system: System, post_id: str) -> dict[str, Reminder]:
    found = await find_docs(system.store, Reminder)
    return {stored.doc.key: stored.doc for stored in found if stored.doc.target_id == post_id}


async def test_one_click_post_with_reply_reminders(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with start_system() as system:
        owner = await prepare_owner(system)
        await connect_linkedin(system)
        flagship = await draft_flagship(system, owner)
        published = await review_and_publish(system, flagship)
        card_id, kit_id = await derive_posts(system, published)

        # Derived: drafted and checked, payload = the escaped display text, no links.
        article = await system.ok("GET", f"/api/articles/{published.article_id}")
        version = dig(article, "item", "version")
        assert isinstance(version, int)
        assert [card_id, kit_id] == [
            Post.id_for_derivative(published.article_id, version, n) for n in (1, 2)
        ]
        derive = await system.job(derive_job_id(published.article_id, version))
        assert derive.status == "succeeded"
        for post_id in (card_id, kit_id):
            post = await post_of(system, post_id)
            assert post.status is P.DRAFT
            assert post.lane == "derivative"
            assert post.article_id == published.article_id
            assert post.payload == api_commentary(parse_display(post.display_text, post.hashtags))
            assert post.ai_draft_text == post.display_text
            assert post.lint is not None
            assert post.lint.passed
            assert post.derived_claims is not None
            assert post.derived_claims.passed
            assert post.near_duplicate is not None
            assert post.near_duplicate.passed
            assert "http" not in post.display_text
            assert post.go_live_mode == "one_click"  # LinkedIn is connected

        # Approved for Tuesday 10:00: bound to the SHA of text and media, slot jobs queued.
        sha = await approve_for_slot(system, card_id)
        approved = await post_of(system, card_id)
        assert approved.status is P.APPROVED
        assert approved.compute_approval_hash() == sha
        assert [item.role for item in approved.media] == ["image_card"]
        slot = to_iso(SLOT_UTC)
        stage = await system.job(stage_job_id(card_id, slot))
        assert stage.run_after == to_iso(STAGED_AT)
        slot_check = await system.job(slot_check_job_id(card_id, slot))
        assert slot_check.run_after > slot

        # Staging at T-60: media uploaded and available, the token checked, nothing posted.
        await system.run_until(STAGED_AT)
        staged = await post_of(system, card_id)
        assert staged.status is P.STAGED
        assert staged.staging is not None
        assert staged.staging.staged_at == to_iso(STAGED_AT)
        assert staged.staging.token_ok
        assert [(m.linkedin_urn, m.linkedin_status) for m in staged.media] == [
            (IMAGE_URNS[0], "available")
        ]
        linkedin = system.linkedin
        assert len(linkedin.calls("POST", INIT_IMAGE)) == 1
        assert len(linkedin.calls("PUT", UPLOAD_URL)) == 1
        assert linkedin.calls("POST", INTROSPECT_URL)
        assert linkedin.calls("POST", POSTS_URL) == []
        push, _ = system.services.notifier.sent[-1]
        assert push.title == "Post at 10:00: tap to publish"
        assert push.url == f"/studio/{published.article_id}/go-live?post={card_id}"

        # A tap that does not carry the current approval is refused before anything is sent.
        view = await system.ok("GET", f"/api/posts/{card_id}/go-live")
        etag = dig(view, "post", "etag")
        assert isinstance(etag, str)
        stale = await system.send(
            "POST",
            f"/api/posts/{card_id}/publish",
            {"approvalSha256": "0" * 64, "confirmToken": view["confirmToken"]},
            etag=etag,
        )
        assert stale.status_code == 409, stale.text
        assert linkedin.calls("POST", POSTS_URL) == []

        # The owner's tap at the slot: the intent first, then exactly one POST.
        await system.run_until(SLOT_UTC)
        tapped = await publish_click(system, card_id)
        item = tapped["item"]
        assert isinstance(item, dict)
        assert item["status"] == "posted"
        assert linkedin.creates == [
            CreateSeen(
                in_owner_click=True, post_id=card_id, status="publishing", intent_status="pending"
            )
        ]
        (sent,) = linkedin.calls("POST", POSTS_URL)
        body = json.loads(sent.content)
        posted = await post_of(system, card_id)
        assert body["commentary"] == posted.payload
        assert body["content"]["media"]["id"] == IMAGE_URNS[0]
        assert posted.linkedin_urn == POST_URN
        assert posted.final_text == posted.display_text
        assert posted.posted_at == slot
        assert posted.intent is not None
        assert posted.intent.status == "succeeded"
        live_job = await system.job(post_live_job_id(card_id))
        assert live_job.type == PLANNER_POST_LIVE

        # Reply reminders at +15, +60 and +180 minutes; the slot check sees a posted post.
        await system.run_until(SLOT_UTC + timedelta(hours=3, minutes=1))
        assert system.pushes()[-3:] == [
            "Live for 15 min: reply to comments on your post",
            "Live for 1 h: reply to comments on your post",
            "Live for 3 h: reply to comments on your post",
        ]
        reminders = await reminders_of(system, card_id)
        assert {key: r.status for key, r in reminders.items()} == {
            f"reply:{card_id}:15": ReminderStatus.SENT,
            f"reply:{card_id}:60": ReminderStatus.SENT,
            f"reply:{card_id}:180": ReminderStatus.SENT,
            f"snapshot:{card_id}:48": ReminderStatus.PENDING,
            f"snapshot:{card_id}:168": ReminderStatus.PENDING,
        }
        assert (await system.job(slot_check.id)).status == "succeeded"
        assert (await post_of(system, card_id)).status is P.POSTED

        # The 48-hour snapshot prompt; the owner logs the numbers.
        await system.run_until(SLOT_UTC + timedelta(hours=48))
        assert system.pushes()[-1] == "Log the 48-hour numbers for your post"
        _, etag = await system.item(f"/api/posts/{card_id}")
        await system.ok(
            "POST",
            f"/api/posts/{card_id}/snapshots",
            {"offset": "h48", "impressions": 1200, "reactions": 31, "comments": 6},
            etag=etag,
        )
        assert [s.offset for s in (await post_of(system, card_id)).snapshots] == ["h48"]
        assert len(linkedin.calls("POST", POSTS_URL)) == 1  # never a second POST

        await assert_nothing_leaked(system, caplog)


async def test_kit_path_to_posted_with_the_final_text() -> None:
    async with start_system() as system:
        owner = await prepare_owner(system)  # LinkedIn stays disconnected: the kit
        flagship = await draft_flagship(system, owner)
        published = await review_and_publish(system, flagship)
        _, kit_id = await derive_posts(system, published)
        path = f"/api/posts/{kit_id}"
        post, etag = await system.item(path)
        assert post["goLiveMode"] == "kit"
        approved = await system.ok("POST", f"{path}/approve", {}, etag=etag)
        assert dig(approved, "item", "status") == "kit"  # kit mode: straight into the kit
        moved = await system.ok("POST", f"{path}/mode", {"mode": "kit"}, etag=_etag(approved))
        assert dig(moved, "item", "status") == "kit"
        assert dig(moved, "item", "kit", "state") == "prepared"

        # The kit copies the display text exactly (never the escaped payload).
        view = await system.ok("GET", f"{path}/kit")
        current = await post_of(system, kit_id)
        assert view["displayText"] == current.display_text
        assert view["state"] == "prepared"
        checklist = view["checklist"]
        assert isinstance(checklist, list)
        assert len(checklist) == 4

        saved = await system.ok(
            "POST", f"{path}/kit", {"to": "native_draft_saved"}, etag=_etag(moved)
        )
        first = await system.ok(
            "POST",
            f"{path}/kit",
            {"to": "natively_scheduled", "scheduledAt": KIT_FIRST_TIME},
            etag=_etag(saved),
        )
        await system.idle()
        moved_again = await system.ok(
            "POST",
            f"{path}/kit",
            {"to": "natively_scheduled", "scheduledAt": KIT_TIME},
            etag=_etag(first),
        )
        assert dig(moved_again, "item", "kit", "scheduledAt") == KIT_TIME
        await system.idle()
        for scheduled in (KIT_FIRST_TIME, KIT_TIME):
            job = await system.job(post_scheduled_job_id(kit_id, scheduled))
            assert job.status == "succeeded"
        confirms = {
            key: r.status
            for key, r in (await reminders_of(system, kit_id)).items()
            if key.startswith("kit_confirm:")
        }
        assert sorted(confirms.values()) == [ReminderStatus.CANCELLED, ReminderStatus.PENDING]

        # "Did it go out?" at the new T + 30 min, once.
        await system.run_until(system_time(KIT_TIME) + timedelta(minutes=31))
        assert system.pushes().count("Did it go out? Add the final text") == 1

        _, etag = await system.item(path)
        done = await system.ok(
            "POST",
            f"{path}/kit",
            {"to": "posted", "postUrl": POSTED_URL, "finalText": FINAL_TEXT},
            etag=etag,
        )
        assert dig(done, "item", "status") == "posted"
        posted = await post_of(system, kit_id)
        assert posted.status is P.POSTED
        assert posted.kit is not None
        assert posted.kit.state is KitState.POSTED
        assert posted.final_text == FINAL_TEXT
        assert posted.ai_draft_text == current.display_text  # what the AI drafted, for voice
        assert posted.post_url == POSTED_URL
        assert posted.posted_at == KIT_TIME
        await system.run_until(system_time(KIT_TIME) + timedelta(hours=3, minutes=1))
        reminders = await reminders_of(system, kit_id)
        # The reply nudges key on the native time T (created when it was scheduled); the
        # first T's were cancelled when the owner moved it.
        assert {
            key
            for key, reminder in reminders.items()
            if key.startswith("reply:") and reminder.status is not ReminderStatus.CANCELLED
        } == {f"reply:{kit_id}:{slot_stamp(KIT_TIME)}:{minutes}" for minutes in (15, 60, 180)}
        assert system.linkedin.fake.requests == []  # the kit never calls LinkedIn


def _etag(body: Mapping[str, JsonValue]) -> str:
    etag = body["etag"]
    assert isinstance(etag, str)
    return etag


def system_time(stamp: str) -> datetime:
    return parse_iso(stamp)
