"""The missed-slot policy (grace until slot + 2 h, then the next free slot, never stacked,
time-sensitive posts are offered nothing after they expire) and re-slotting."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from blogwriter.domain.docstore import read_doc
from blogwriter.domain.jobtypes import slot_check_job_id, stage_job_id
from blogwriter.domain.posts import KitInfo, Post, PublishIntent
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import KitState, PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices
from blogwriter.planner.slots import slot_at
from blogwriter.planner.testing import post, run_job

CADENCE = CadenceSettings.default(SAMPLE_NOW)
TUESDAY = slot_at(CADENCE, "2026-12-01", "10:00")  # 15:00Z
GRACE_OVER = datetime(2026, 12, 1, 17, 0, tzinfo=UTC)  # slot + 2 h


async def check(
    services: FakeServices, post_id: str, slot_utc: str, job_id: str = "check-1"
) -> object:
    job = await run_job(
        services,
        "planner.slot-check",
        {"postId": post_id, "slotUtc": slot_utc, "origin": "owner"},
        job_id=job_id,
    )
    assert job.status == "succeeded"
    return job.result


async def test_a_slot_missed_past_its_grace_offers_the_next_free_slot(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    waiting = await post(services, status=PostStatus.STAGED, slot=TUESDAY)
    services.clock.set(GRACE_OVER)
    result = await check(services, waiting.id, TUESDAY.utc)
    assert result == {
        "postId": waiting.id,
        "status": "missed",
        "offer": "2026-12-02T15:00:00.000000Z",
    }
    missed = (await read_doc(services.store, Post, waiting.id)).doc
    assert missed.status is PostStatus.MISSED
    payload, _urgency = services.notifier.sent[-1]
    assert payload.title == "Missed your Tue 10:00 post. Next free slot: Wed 10:00"
    assert payload.url == f"/studio/{waiting.id}/go-live?post={waiting.id}"
    today = (await client.get("/api/today", headers=read_headers)).json()
    item = next(i for i in today["items"] if i["kind"] == "missed_slot")
    assert item["title"] == "Missed Tue 10:00. Next free slot: Wed 10:00"
    assert item["dueAt"] == "2026-12-02T15:00:00.000000Z"


async def test_the_offer_never_stacks_posts(services: FakeServices) -> None:
    waiting = await post(services, status=PostStatus.APPROVED, slot=TUESDAY)
    await post(services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-12-02", "16:00"))
    kit = KitInfo(
        state=KitState.NATIVELY_SCHEDULED,
        prepared_at=SAMPLE_NOW,
        scheduled_at=slot_at(CADENCE, "2026-12-03", "08:00").utc,
    )
    await post(services, status=PostStatus.KIT, slot=None, kit=kit, go_live_mode="kit")
    services.clock.set(GRACE_OVER)
    result = await check(services, waiting.id, TUESDAY.utc)
    assert isinstance(result, dict)
    # Wed and Thu are held (an approved post, a natively scheduled one): Friday's optional slot.
    assert result["offer"] == slot_at(CADENCE, "2026-12-04", "15:00").utc


async def test_time_sensitive_posts_are_not_offered_a_slot_after_they_expire(
    services: FakeServices,
) -> None:
    waiting = await post(
        services,
        status=PostStatus.APPROVED,
        slot=TUESDAY,
        lane="quick_take",
        time_sensitive=True,
        expires_at="2026-12-02T12:00:00.000000Z",
    )
    services.clock.set(GRACE_OVER)
    result = await check(services, waiting.id, TUESDAY.utc)
    assert isinstance(result, dict)
    assert result["status"] == "missed"
    assert result["offer"] is None
    assert services.notifier.sent[-1][0].title == (
        "Missed your Tue 10:00 post. It expires before the next free slot"
    )


async def test_no_op_when_the_post_moved_went_out_or_is_in_the_kit(services: FakeServices) -> None:
    moved = await post(
        services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-12-02", "10:00")
    )
    posted = await post(
        services,
        status=PostStatus.POSTED,
        slot=TUESDAY,
        posted_at=TUESDAY.utc,
        linkedin_urn="urn:li:share:1",
    )
    intent = PublishIntent(id="intent-0001", created_at=TUESDAY.utc, approval_sha256="0" * 64)
    publishing = await post(services, status=PostStatus.PUBLISHING, slot=TUESDAY, intent=intent)
    kit = await post(
        services,
        status=PostStatus.KIT,
        slot=TUESDAY,
        kit=KitInfo(prepared_at=SAMPLE_NOW),
        go_live_mode="kit",
    )
    services.clock.set(GRACE_OVER)
    assert await check(services, moved.id, TUESDAY.utc, "c1") == {
        "postId": moved.id,
        "skipped": "reslotted",
    }
    assert await check(services, posted.id, TUESDAY.utc, "c2") == {
        "postId": posted.id,
        "skipped": "posted",
    }
    assert await check(services, publishing.id, TUESDAY.utc, "c3") == {
        "postId": publishing.id,
        "skipped": "publishing",
    }
    assert await check(services, kit.id, TUESDAY.utc, "c4") == {"postId": kit.id, "skipped": "kit"}
    assert await check(services, "post-0000000000000000000000000000", TUESDAY.utc, "c5") == {
        "postId": "post-0000000000000000000000000000",
        "skipped": "missing",
    }
    assert services.notifier.sent == []


async def test_after_downtime_the_offer_starts_from_now(services: FakeServices) -> None:
    waiting = await post(services, status=PostStatus.APPROVED, slot=TUESDAY)
    services.clock.set(datetime(2026, 12, 4, 13, 0, tzinfo=UTC))  # Friday 08:00, 3 days late
    result = await check(services, waiting.id, TUESDAY.utc)
    assert isinstance(result, dict)
    assert result["offer"] == slot_at(CADENCE, "2026-12-04", "15:00").utc


# --------------------------------------------------------------------------------------------
# POST /api/posts/{id}/reslot
# --------------------------------------------------------------------------------------------


async def with_etag(services: FakeServices, created: Post) -> tuple[Post, str]:
    return created, (await read_doc(services.store, Post, created.id)).etag


async def reslot(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    target: tuple[Post, str],
    body: dict[str, object],
) -> httpx.Response:
    created, tag = target
    return await client.post(
        f"/api/posts/{created.id}/reslot", json=body, headers={**headers, "If-Match": tag}
    )


async def test_next_slot_takes_a_missed_post_back_on_the_calendar(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    missed, tag = await with_etag(
        services, await post(services, status=PostStatus.MISSED, slot=TUESDAY)
    )
    services.clock.set(GRACE_OVER)
    response = await reslot(client, write_headers, (missed, tag), {"action": "next_slot"})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "approved"
    assert item["slot"]["utc"] == "2026-12-02T15:00:00.000000Z"
    stage = await services.jobs.get(stage_job_id(missed.id, item["slot"]["utc"]))
    check_job = await services.jobs.get(slot_check_job_id(missed.id, item["slot"]["utc"]))
    assert stage.run_after == "2026-12-02T14:00:00.000000Z"
    assert check_job.run_after == "2026-12-02T17:00:00.000000Z"
    assert stage.input == {"origin": "owner", "postId": missed.id, "slotUtc": item["slot"]["utc"]}


async def test_snooze_moves_the_slot_thirty_minutes_and_cancels_the_old_jobs(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    staged, tag = await with_etag(
        services, await post(services, status=PostStatus.STAGED, slot=TUESDAY)
    )
    await services.jobs.enqueue("posts.stage", {}, job_id=stage_job_id(staged.id, TUESDAY.utc))
    await services.jobs.enqueue(
        "planner.slot-check", {}, job_id=slot_check_job_id(staged.id, TUESDAY.utc)
    )
    services.clock.set(datetime(2026, 12, 1, 14, 50, tzinfo=UTC))  # 09:50, ten minutes before
    response = await reslot(client, write_headers, (staged, tag), {"action": "snooze"})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "approved"
    assert (item["slot"]["localTime"], item["slot"]["kind"]) == ("10:30", "custom")
    assert (await services.jobs.get(stage_job_id(staged.id, TUESDAY.utc))).status == "cancelled"
    assert (
        await services.jobs.get(slot_check_job_id(staged.id, TUESDAY.utc))
    ).status == "cancelled"
    assert (
        await services.jobs.get(slot_check_job_id(staged.id, item["slot"]["utc"]))
    ).status == "queued"


async def test_set_refuses_stacking_the_past_and_expiry(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    holder = await post(
        services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-12-02", "10:00")
    )
    mine = await with_etag(services, await post(services, status=PostStatus.APPROVED, slot=TUESDAY))
    stacked = await reslot(
        client,
        write_headers,
        mine,
        {"action": "set", "slot": {"localDate": "2026-12-02", "localTime": "16:00"}},
    )
    assert stacked.status_code == 409
    assert stacked.json()["code"] == "slot_conflict"
    assert stacked.json()["conflicts"] == [holder.id]
    past = await reslot(
        client,
        write_headers,
        mine,
        {"action": "set", "slot": {"localDate": "2026-11-25", "localTime": "10:00"}},
    )
    assert past.json()["code"] == "slot_in_past"
    missing = await reslot(client, write_headers, mine, {"action": "set"})
    assert missing.status_code == 422
    assert missing.json()["code"] == "slot_required"
    urgent = await with_etag(
        services,
        await post(
            services,
            status=PostStatus.APPROVED,
            slot=TUESDAY,
            time_sensitive=True,
            expires_at="2026-12-01T20:00:00.000000Z",
        ),
    )
    late = await reslot(
        client,
        write_headers,
        urgent,
        {"action": "set", "slot": {"localDate": "2026-12-03", "localTime": "10:00"}},
    )
    assert late.json()["code"] == "expires_first"
    no_slot = await reslot(client, write_headers, urgent, {"action": "next_slot"})
    assert no_slot.json()["code"] == "expires_first"


async def test_reslot_refuses_live_posts_and_stale_etags(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    live = await with_etag(
        services,
        await post(services, status=PostStatus.POSTED, slot=TUESDAY, posted_at=TUESDAY.utc),
    )
    response = await reslot(client, write_headers, live, {"action": "snooze"})
    assert response.status_code == 409
    assert response.json()["code"] == "post_not_movable"
    mine, tag = await with_etag(
        services, await post(services, status=PostStatus.APPROVED, slot=TUESDAY)
    )
    stale = await reslot(client, write_headers, (mine, '"stale"'), {"action": "snooze"})
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == tag
