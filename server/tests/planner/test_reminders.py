"""Reminders: +15/+60/+180 min and +48 h/+7 d after go-live with stable ids (a resumed step
creates none twice), expiry of stale ones, the kit confirmation keyed on T, dismissal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from blogwriter.domain.base import parse_iso
from blogwriter.domain.docstore import find_docs, read_doc, update_doc
from blogwriter.domain.jobtypes import remind_job_id, slot_stamp
from blogwriter.domain.planner import Reminder
from blogwriter.domain.posts import KitInfo, MetricsSnapshot, Post
from blogwriter.domain.settings import CadenceSettings, ReminderOffsets
from blogwriter.domain.states import KitState, PostStatus, ReminderStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, RecordingNotifier, sample_document
from blogwriter.jobs import Job, StepState
from blogwriter.planner import reminders_for_go_live
from blogwriter.planner.reminders import dismissed_at
from blogwriter.planner.slots import slot_at
from blogwriter.planner.testing import post, run_job, run_until_settled

CADENCE = CadenceSettings.default(SAMPLE_NOW)
LIVE = "2026-12-01T15:00:00.000000Z"  # Tuesday 10:00 in New York
NOW = datetime(2026, 12, 1, 15, 0, 5, tzinfo=UTC)


def test_reminders_for_go_live() -> None:
    target = sample_document(Post, lane="native", pillar="agents", format="text")
    reminders = reminders_for_go_live(target, CADENCE, LIVE, now=NOW)
    assert [(r.reminder_kind, r.key, r.due_at, r.expires_at) for r in reminders] == [
        (
            "reply",
            f"reply:{target.id}:15",
            "2026-12-01T15:15:00.000000Z",
            "2026-12-01T17:15:00.000000Z",
        ),
        (
            "reply",
            f"reply:{target.id}:60",
            "2026-12-01T16:00:00.000000Z",
            "2026-12-01T18:00:00.000000Z",
        ),
        (
            "reply",
            f"reply:{target.id}:180",
            "2026-12-01T18:00:00.000000Z",
            "2026-12-01T20:00:00.000000Z",
        ),
        (
            "snapshot",
            f"snapshot:{target.id}:48",
            "2026-12-03T15:00:00.000000Z",
            "2026-12-06T15:00:00.000000Z",
        ),
        (
            "snapshot",
            f"snapshot:{target.id}:168",
            "2026-12-08T15:00:00.000000Z",
            "2026-12-11T15:00:00.000000Z",
        ),
    ]
    assert all(r.id == Reminder.id_for(r.key) for r in reminders)
    assert [r.title for r in reminders] == [
        "Live for 15 min: reply to comments on your post",
        "Live for 1 h: reply to comments on your post",
        "Live for 3 h: reply to comments on your post",
        "Log the 48-hour numbers for your post",
        "Log the 7-day numbers for your post",
    ]
    assert reminders[0].url == f"/today?engage={target.id}"
    assert reminders[3].url == f"/today?snapshot={target.id}"
    assert all(r.status is ReminderStatus.PENDING and r.target_id == target.id for r in reminders)
    assert all(r.ttl is not None and r.ttl > 30 * 86400 for r in reminders)
    # The same post and time give the same ids: creating them again is a Conflict.
    again = reminders_for_go_live(target, CADENCE, LIVE, now=NOW + timedelta(hours=1))
    assert [r.id for r in again] == [r.id for r in reminders]


def test_reminder_offsets_follow_the_cadence() -> None:
    target = sample_document(Post, lane="native", pillar="agents", format="text")
    cadence = CADENCE.model_copy(
        update={"reminders": ReminderOffsets(reply_minutes=[30], snapshot_hours=[24])}
    )
    reminders = reminders_for_go_live(target, cadence, LIVE, now=NOW)
    assert [r.key for r in reminders] == [f"reply:{target.id}:30", f"snapshot:{target.id}:24"]
    assert reminders[1].title == "Log the 24-hour numbers for your post"


async def live_post(services: FakeServices) -> Post:
    return await post(
        services,
        status=PostStatus.POSTED,
        slot=slot_at(CADENCE, "2026-12-01", "10:00"),
        posted_at=LIVE,
        linkedin_urn="urn:li:share:7",
    )


async def go_live(services: FakeServices, target: Post, job_id: str) -> Job:
    return await run_job(
        services,
        "planner.post-live",
        {"postId": target.id, "goLiveAt": LIVE, "origin": "owner"},
        job_id=job_id,
    )


async def test_post_live_creates_the_reminders_once(services: FakeServices) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    first = await go_live(services, target, "postlive-1")
    assert first.result == {"postId": target.id, "reminders": 5, "created": 5, "existing": 0}
    stored = await find_docs(services.store, Reminder)
    assert len(stored) == 5
    jobs = [await services.jobs.get(remind_job_id(r.doc.id)) for r in stored]
    assert {job.type for job in jobs} == {"planner.remind"}
    assert sorted(job.run_after for job in jobs) == sorted(r.doc.due_at for r in stored)
    assert all(isinstance(job.input, dict) and job.input["origin"] == "owner" for job in jobs)
    # The hook delivered again (a resumed step): nothing new.
    second = await go_live(services, target, "postlive-2")
    assert second.result == {"postId": target.id, "reminders": 5, "created": 0, "existing": 5}
    assert len(await find_docs(services.store, Reminder)) == 5
    assert len([j for j in services.jobs.enqueued if j.type == "planner.remind"]) == 5


async def test_post_live_ignores_a_post_that_is_not_live(services: FakeServices) -> None:
    draft = await post(services, status=PostStatus.DRAFT)
    job = await go_live(services, draft, "postlive-3")
    assert job.result == {"skipped": "draft"}
    assert await find_docs(services.store, Reminder) == []


async def remind(services: FakeServices, reminder_id: str) -> Job:
    """Run the reminder's own delayed job (``remind-<reminderId>``, enqueued by post-live)."""
    return await run_until_settled(services, remind_job_id(reminder_id))


async def test_a_due_reminder_is_pushed_once_and_marked_sent(services: FakeServices) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-4")
    reply = Reminder.id_for(f"reply:{target.id}:15")
    services.clock.set(datetime(2026, 12, 1, 15, 15, tzinfo=UTC))
    job = await remind(services, reply)
    assert job.result == {"status": "sent", "delivered": True}
    sent = (await read_doc(services.store, Reminder, reply)).doc
    assert sent.status is ReminderStatus.SENT
    assert sent.delivered is True
    payload, _ = services.notifier.sent[-1]
    assert payload.title == "Live for 15 min: reply to comments on your post"
    assert payload.url == f"/today?engage={target.id}"
    again = await run_job(
        services, "planner.remind", {"reminderId": reply, "origin": "owner"}, job_id="remind-again"
    )
    assert again.result == {"status": "sent"}
    assert len(services.notifier.sent) == 1


async def test_without_a_receipt_the_reminder_is_sent_but_undelivered(
    services: FakeServices,
) -> None:
    services.notifier = RecordingNotifier(deliver=False)
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-5")
    reply = Reminder.id_for(f"reply:{target.id}:60")
    services.clock.set(datetime(2026, 12, 1, 16, 0, tzinfo=UTC))
    job = await remind(services, reply)
    assert job.result == {"status": "sent", "delivered": False}


async def test_a_stale_reminder_expires_instead_of_firing(services: FakeServices) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-6")
    reply = Reminder.id_for(f"reply:{target.id}:15")
    services.clock.set(datetime(2026, 12, 1, 23, 0, tzinfo=UTC))  # downtime: 7 h later
    job = await remind(services, reply)
    assert job.result == {"status": "expired"}
    assert (await read_doc(services.store, Reminder, reply)).doc.status is ReminderStatus.EXPIRED
    assert services.notifier.sent == []


async def test_a_snapshot_reminder_skips_when_the_numbers_are_in(services: FakeServices) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-7")

    def logged(doc: Post) -> Post:
        snap = MetricsSnapshot(
            id="s48", taken_at="2026-12-03T12:00:00.000000Z", offset="h48", impressions=900
        )
        return doc.model_copy(update={"snapshots": [snap]})

    await update_doc(services.store, Post, target.id, logged)
    snapshot = Reminder.id_for(f"snapshot:{target.id}:48")
    services.clock.set(datetime(2026, 12, 3, 15, 0, tzinfo=UTC))
    job = await remind(services, snapshot)
    assert job.result == {"status": "cancelled", "reason": "snapshot_logged"}
    assert services.notifier.sent == []


async def test_a_resumed_send_never_pushes_twice(services: FakeServices) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-8")
    reply = Reminder.id_for(f"reply:{target.id}:180")
    services.clock.set(datetime(2026, 12, 1, 18, 0, tzinfo=UTC))
    raw = await services.store.read("jobs", remind_job_id(reply))
    job = Job.from_body(raw.body)
    # The earlier attempt crashed after it may have pushed.
    job.steps = [StepState(name="send", output={"pushing": True})]
    await services.store.replace("jobs", job.to_body(), if_match=raw.etag)
    done = await run_until_settled(services, remind_job_id(reply))
    assert done.result == {"status": "sent", "delivered": None}
    assert services.notifier.sent == []


async def test_dismissing_reminders(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-9")
    first = Reminder.id_for(f"reply:{target.id}:15")
    second = Reminder.id_for(f"reply:{target.id}:60")
    services.clock.set(datetime(2026, 12, 1, 15, 15, tzinfo=UTC))
    await remind(services, first)  # sent
    services.clock.set(datetime(2026, 12, 1, 16, 5, tzinfo=UTC))
    today = (await client.get("/api/today", headers=read_headers)).json()
    replies = [i for i in today["items"] if i["kind"] == "reply"]
    assert [i["refs"]["reminderId"] for i in replies] == [first, second]  # sent and due-pending
    assert replies[0]["href"] == f"/today?engage={target.id}"
    dismissed = await client.post(f"/api/reminders/{first}/dismiss", headers=write_headers)
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["item"]["status"] == "sent"
    assert dismissed.json()["item"]["dismissedAt"] is not None
    pending = await client.post(f"/api/reminders/{second}/dismiss", headers=write_headers)
    assert pending.json()["item"]["status"] == "cancelled"
    assert (await services.jobs.get(remind_job_id(second))).status == "cancelled"
    again = await client.post(f"/api/reminders/{second}/dismiss", headers=write_headers)
    assert again.status_code == 200
    later = (await client.get("/api/today", headers=read_headers)).json()
    assert [i for i in later["items"] if i["kind"] == "reply"] == []
    stored = (await read_doc(services.store, Reminder, first)).doc
    assert dismissed_at(stored) is not None
    assert (
        await client.post("/api/reminders/rem-missing/dismiss", headers=write_headers)
    ).status_code == 404


async def test_listing_reminders_by_due_time(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    services.clock.set(NOW)
    target = await live_post(services)
    await go_live(services, target, "postlive-10")
    page = (await client.get("/api/reminders?limit=3", headers=read_headers)).json()
    dues = [i["item"]["dueAt"] for i in page["items"]]
    assert dues == sorted(dues)
    assert len(dues) == 3
    rest = (
        await client.get(f"/api/reminders?cursor={page['nextCursor']}", headers=read_headers)
    ).json()
    assert len(rest["items"]) == 2
    assert rest["nextCursor"] is None
    sent = (await client.get("/api/reminders?status=sent", headers=read_headers)).json()
    assert sent["items"] == []


# --------------------------------------------------------------------------------------------
# planner.post-scheduled: the kit confirmation keyed on T
# --------------------------------------------------------------------------------------------


async def kit_post(services: FakeServices, scheduled_at: str) -> Post:
    kit = KitInfo(
        state=KitState.NATIVELY_SCHEDULED, prepared_at=SAMPLE_NOW, scheduled_at=scheduled_at
    )
    return await post(services, status=PostStatus.KIT, kit=kit, go_live_mode="kit")


async def scheduled(services: FakeServices, target: Post, at: str, job_id: str) -> Job:
    return await run_job(
        services,
        "planner.post-scheduled",
        {"postId": target.id, "scheduledAt": at, "origin": "owner"},
        job_id=job_id,
    )


async def test_kit_confirmation_follows_the_native_time(services: FakeServices) -> None:
    first_t = slot_at(CADENCE, "2026-12-02", "10:00").utc
    target = await kit_post(services, first_t)
    job = await scheduled(services, target, first_t, "ps-1")
    assert isinstance(job.result, dict)
    assert job.result["cancelled"] == 0
    assert job.result["spacing"] == []
    found = [stored.doc for stored in await find_docs(services.store, Reminder)]
    [confirm] = [r for r in found if r.reminder_kind == "kit_confirm"]
    assert confirm.due_at == "2026-12-02T15:30:00.000000Z"  # T + 30 min
    # LinkedIn posts it at T: the reply nudges key on T, not on the owner's confirmation.
    replies = sorted(r.due_at for r in found if r.reminder_kind == "reply")
    assert replies == [
        "2026-12-02T15:15:00.000000Z",
        "2026-12-02T16:00:00.000000Z",
        "2026-12-02T18:00:00.000000Z",
    ]
    assert confirm.title == "Did it go out? Add the final text"
    assert confirm.url == f"/studio/{target.id}/go-live?post={target.id}"
    # The owner moves T: the old confirmation is cancelled, a new one keyed on the new T.
    new_t = slot_at(CADENCE, "2026-12-03", "11:00").utc

    def moved(doc: Post) -> Post:
        assert doc.kit is not None
        return doc.model_copy(update={"kit": doc.kit.model_copy(update={"scheduled_at": new_t})})

    await update_doc(services.store, Post, target.id, moved)
    stale = await scheduled(services, target, first_t, "ps-2")
    assert stale.result == {"skipped": "rescheduled"}
    fresh = await scheduled(services, target, new_t, "ps-3")
    assert isinstance(fresh.result, dict)
    assert fresh.result["cancelled"] == 4  # the old confirmation and its three reply nudges
    stored_now = [r.doc for r in await find_docs(services.store, Reminder)]
    statuses = {r.due_at: r.status for r in stored_now if r.reminder_kind == "kit_confirm"}
    assert statuses == {
        "2026-12-02T15:30:00.000000Z": ReminderStatus.CANCELLED,
        "2026-12-03T16:30:00.000000Z": ReminderStatus.PENDING,
    }
    pending = sorted(
        r.due_at
        for r in stored_now
        if r.reminder_kind == "reply" and r.status is ReminderStatus.PENDING
    )
    assert pending == [
        "2026-12-03T16:15:00.000000Z",
        "2026-12-03T17:00:00.000000Z",
        "2026-12-03T19:00:00.000000Z",
    ]
    remind_job = await services.jobs.get(remind_job_id(str(fresh.result["reminderId"])))
    assert remind_job.run_after == "2026-12-03T16:30:00.000000Z"


async def test_a_native_time_on_a_busy_day_warns(services: FakeServices) -> None:
    await post(services, status=PostStatus.APPROVED, slot=slot_at(CADENCE, "2026-12-02", "10:00"))
    t = slot_at(CADENCE, "2026-12-02", "16:00").utc
    target = await kit_post(services, t)
    job = await scheduled(services, target, t, "ps-4")
    assert isinstance(job.result, dict)
    assert job.result["spacing"] == ["slot-same-day"]
    payload, _ = services.notifier.sent[-1]
    assert payload.title == "Your scheduled post shares a day with another post"


async def test_kit_confirmation_is_cancelled_once_posted(services: FakeServices) -> None:
    t = slot_at(CADENCE, "2026-12-02", "10:00").utc
    target = await kit_post(services, t)
    job = await scheduled(services, target, t, "ps-5")
    assert isinstance(job.result, dict)
    reminder_id = str(job.result["reminderId"])

    def posted(doc: Post) -> Post:
        assert doc.kit is not None
        kit = doc.kit.model_copy(update={"state": KitState.POSTED, "posted_at": t})
        return doc.model_copy(update={"status": PostStatus.POSTED, "kit": kit, "posted_at": t})

    await update_doc(services.store, Post, target.id, posted)
    services.clock.set(parse_iso(t) + timedelta(minutes=30))
    result = await remind(services, reminder_id)
    assert result.result == {"status": "cancelled", "reason": "already_posted"}


async def test_a_kit_post_that_went_out_before_its_native_time_drops_its_t_nudges(
    services: FakeServices,
) -> None:
    t = slot_at(CADENCE, "2026-12-02", "10:00").utc
    target = await kit_post(services, t)
    await scheduled(services, target, t, "ps-early")
    went_out = "2026-12-01T15:00:00.000000Z"  # the owner posted it by hand a day early

    def posted(doc: Post) -> Post:
        assert doc.kit is not None
        return doc.model_copy(
            update={
                "status": PostStatus.POSTED,
                "posted_at": went_out,
                "kit": doc.kit.model_copy(update={"state": KitState.POSTED}),
            }
        )

    await update_doc(services.store, Post, target.id, posted)
    job = await run_job(
        services,
        "planner.post-live",
        {"postId": target.id, "goLiveAt": went_out, "origin": "owner"},
        job_id="pl-early",
    )
    assert job.status == "succeeded", job.error
    replies = {
        r.doc.key: r.doc.status
        for r in await find_docs(services.store, Reminder)
        if r.doc.reminder_kind == "reply"
    }
    for minutes in (15, 60, 180):
        assert replies[f"reply:{target.id}:{slot_stamp(t)}:{minutes}"] is ReminderStatus.CANCELLED
        assert replies[f"reply:{target.id}:{minutes}"] is ReminderStatus.PENDING
