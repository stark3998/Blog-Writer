"""Plan routes: drag and drop (UTC recomputed, warnings, posts follow their slots), the
availability mode, generation and the week lists."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from blogwriter.domain.docstore import read_doc, upsert_doc
from blogwriter.domain.golive import enqueue_slot_jobs
from blogwriter.domain.jobtypes import slot_check_job_id, stage_job_id
from blogwriter.domain.planner import FlagshipPlan, WeekPlan
from blogwriter.domain.posts import Approval, Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import KitState, PlanStatus, PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices
from blogwriter.domain.weeks import plan_id
from blogwriter.planner import plan_week_job_id
from blogwriter.planner.slots import slot_at
from blogwriter.planner.testing import planned_slot, post, save_plan

WEEK = "2026-11-30"
FALL_WEEK = "2026-11-02"
CADENCE = CadenceSettings.default(SAMPLE_NOW)


def put_body(*slots: Mapping[str, object], **extra: object) -> dict[str, object]:
    return {"slots": [dict(slot) for slot in slots], **extra}


async def put(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    etag: str,
    body: dict[str, object],
    week: str = WEEK,
) -> httpx.Response:
    return await client.put(
        f"/api/plan/weeks/{week}", json=body, headers={**headers, "If-Match": etag}
    )


async def test_drag_and_drop_recomputes_utc_and_warnings(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    services.clock.set(services.clock.now().replace(month=10, day=20))
    slots = [
        planned_slot("s1", "2026-11-03", lane="derivative", pillar="agents"),
        planned_slot("s2", "2026-11-04"),
        planned_slot("s3", "2026-11-05", pillar="governance"),
    ]
    stored = await save_plan(services, FALL_WEEK, slots, flagship=None)
    body = put_body(
        {"id": "s1", "localDate": "2026-11-02", "localTime": "12:00"},
        {"id": "s2", "localDate": "2026-11-04", "localTime": "10:00"},
        {"id": "s3", "localDate": "2026-11-04", "localTime": "16:00"},
    )
    response = await put(client, write_headers, stored.etag, body, FALL_WEEK)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert [
        (s["slot"]["localDate"], s["slot"]["localTime"], s["slot"]["utc"], s["slot"]["kind"])
        for s in item["slots"]
    ] == [
        ("2026-11-02", "12:00", "2026-11-02T17:00:00.000000Z", "optional"),  # EST after fall back
        ("2026-11-04", "10:00", "2026-11-04T15:00:00.000000Z", "primary"),
        ("2026-11-04", "16:00", "2026-11-04T21:00:00.000000Z", "custom"),
    ]
    assert [s["lane"] for s in item["slots"]] == ["derivative", "native", "native"]  # kept
    warnings = {(w["code"], w["severity"]) for w in item["warnings"]}
    assert ("slot-same-day", "block") in warnings
    assert ("flagship-missing", "warn") in warnings
    assert item["pillarBalance"] == {"agents": 1, "identity": 1, "governance": 1}
    assert response.headers["ETag"] == response.json()["etag"]


async def test_moving_an_approved_post_reslots_it(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    old_slot = slot_at(CADENCE, "2026-12-02", "10:00")
    staged = await post(services, status=PostStatus.STAGED, slot=old_slot, pillar="identity")
    await services.jobs.enqueue(
        "posts.stage", {"postId": staged.id}, job_id=stage_job_id(staged.id, old_slot.utc)
    )
    slots = [
        planned_slot("s1", "2026-12-01", lane="derivative", pillar="agents"),
        planned_slot("s2", "2026-12-02", post_id=staged.id),
        planned_slot("s3", "2026-12-03", pillar="governance"),
    ]
    flagship = FlagshipPlan(title="Flagship", pillar="governance", format="framework")
    stored = await save_plan(services, WEEK, slots, status=PlanStatus.APPROVED, flagship=flagship)
    body = put_body(
        {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00"},
        {"id": "s2", "localDate": "2026-12-04", "localTime": "15:00"},
        {"id": "s3", "localDate": "2026-12-03", "localTime": "10:00"},
    )
    response = await put(client, write_headers, stored.etag, body)
    assert response.status_code == 200, response.text
    assert response.json()["item"]["status"] == "approved"  # a move needs no re-approval
    moved = (await read_doc(services.store, Post, staged.id)).doc
    assert moved.status is PostStatus.APPROVED  # staged -> approved
    assert moved.slot is not None
    assert (moved.slot.local_date, moved.slot.local_time) == ("2026-12-04", "15:00")
    assert moved.week_plan_id == plan_id(WEEK)
    new_utc = moved.slot.utc
    stage = await services.jobs.get(stage_job_id(staged.id, new_utc))
    check = await services.jobs.get(slot_check_job_id(staged.id, new_utc))
    assert stage.run_after == "2026-12-04T19:00:00.000000Z"  # slot - 60 min
    assert check.run_after == "2026-12-04T22:00:00.000000Z"  # slot + 2 h grace
    assert (await services.jobs.get(stage_job_id(staged.id, old_slot.utc))).status == "cancelled"


async def test_changing_what_approval_generates_reopens_the_plan(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    slots = [planned_slot("s1", "2026-12-01", pillar="agents"), planned_slot("s2", "2026-12-02")]
    flagship = FlagshipPlan(title="Flagship", pillar="governance", format="framework")
    stored = await save_plan(
        services, WEEK, slots, status=PlanStatus.APPROVED, flagship=flagship, mode="light"
    )
    body = put_body(
        {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00", "pillar": "governance"},
        {"id": "s2", "localDate": "2026-12-02", "localTime": "10:00"},
    )
    response = await put(client, write_headers, stored.etag, body)
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "proposed"


async def test_a_slot_with_a_live_post_cannot_be_removed(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    live = await post(
        services, status=PostStatus.DRAFT, slot=slot_at(CADENCE, "2026-12-02", "10:00")
    )
    slots = [
        planned_slot("s1", "2026-12-01", pillar="agents"),
        planned_slot("s2", "2026-12-02", post_id=live.id),
    ]
    stored = await save_plan(services, WEEK, slots)
    response = await put(
        client,
        write_headers,
        stored.etag,
        put_body({"id": "s1", "localDate": "2026-12-01", "localTime": "10:00"}),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "slot_has_post"


async def test_edit_validation(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    stored = await save_plan(services, WEEK, [planned_slot("s1", "2026-12-01")])
    cases = [
        (
            put_body({"id": "s1", "localDate": "2026-12-08", "localTime": "10:00"}),
            "slot_outside_week",
        ),
        (
            put_body(
                {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00", "pillar": "cooking"}
            ),
            "unknown_pillar",
        ),
        (
            put_body(
                {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00"},
                {"id": "s1", "localDate": "2026-12-02", "localTime": "10:00"},
            ),
            "duplicate_slot",
        ),
        (
            put_body(
                *(
                    {"id": f"s{n}", "localDate": f"2026-12-0{n}", "localTime": "10:00"}
                    for n in range(1, 7)
                )
            ),
            "too_many_slots",
        ),
        (
            put_body(
                {"id": "s1", "localDate": "2026-12-01", "localTime": "10:00"},
                flagship={"title": "X", "pillar": "agents", "format": "memo"},
            ),
            "unknown_format",
        ),
        (
            put_body(
                {
                    "id": "s1",
                    "localDate": "2026-12-01",
                    "localTime": "10:00",
                    "postId": "post-0000000000000000000000000000",
                }
            ),
            "unknown_post",
        ),
    ]
    for body, code in cases:
        response = await put(client, write_headers, stored.etag, body)
        assert response.status_code == 422, (code, response.text)
        assert response.json()["code"] == code
    stale = await put(client, write_headers, '"stale"', put_body())
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == stored.etag


async def test_a_plan_being_generated_is_not_editable(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    job = await services.jobs.enqueue("planner.plan-week", {}, job_id="plan-run-1")
    generating = await save_plan(services, WEEK, status=PlanStatus.GENERATING, job_id=job.id)
    response = await put(client, write_headers, generating.etag, put_body())
    assert response.status_code == 409
    assert response.json()["code"] == "plan_generating"


async def test_a_placeholder_plan_becomes_proposed_when_edited(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    created = await client.put(
        f"/api/plan/weeks/{WEEK}/mode",
        json={"mode": "light"},
        headers={**write_headers, "If-None-Match": "*"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["item"]["status"] == "generating"
    assert created.json()["item"]["mode"] == "light"
    body = put_body(
        {
            "id": "a",
            "localDate": "2026-12-01",
            "localTime": "10:00",
            "lane": "native",
            "pillar": "agents",
            "format": "text",
        }
    )
    response = await put(client, write_headers, created.json()["etag"], body)
    assert response.status_code == 200, response.text
    assert response.json()["item"]["status"] == "proposed"


async def test_mode_route(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    url = f"/api/plan/weeks/{WEEK}/mode"
    missing = await client.put(url, json={"mode": "away"}, headers=write_headers)
    assert missing.status_code == 428
    created = await client.put(
        url, json={"mode": "away"}, headers={**write_headers, "If-None-Match": "*"}
    )
    assert created.status_code == 200
    raced = await client.put(
        url, json={"mode": "light"}, headers={**write_headers, "If-None-Match": "*"}
    )
    assert raced.status_code == 412
    changed = await client.put(
        url, json={"mode": "light"}, headers={**write_headers, "If-Match": created.json()["etag"]}
    )
    assert changed.status_code == 200
    assert changed.json()["item"]["mode"] == "light"
    listed = await client.get("/api/plan/weeks?from=2026-11-30", headers=read_headers)
    assert [(i["item"]["weekStart"], i["item"]["mode"]) for i in listed.json()["items"]] == [
        (WEEK, "light")
    ]
    plan = (await read_doc(services.store, WeekPlan, plan_id(WEEK))).doc
    closed = plan.model_copy(update={"status": PlanStatus.CLOSED})
    stored = await services.store.replace(
        "ideas", closed.to_document(), if_match=changed.json()["etag"]
    )
    refused = await client.put(
        url, json={"mode": "normal"}, headers={**write_headers, "If-Match": stored.etag}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "plan_closed"


async def test_generate_route(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    url = f"/api/plan/weeks/{WEEK}/generate"
    first = await client.post(url, headers=write_headers)
    assert first.status_code == 202, first.text
    assert first.json() == {"jobId": plan_week_job_id(WEEK), "status": "queued"}
    job = await services.jobs.get(plan_week_job_id(WEEK))
    assert job.type == "planner.plan-week"
    assert job.input == {"origin": "owner", "weekStart": WEEK}
    again = await client.post(url, headers=write_headers)
    assert again.json()["jobId"] == plan_week_job_id(WEEK)  # the active run
    past = await client.post("/api/plan/weeks/2026-11-16/generate", headers=write_headers)
    assert past.status_code == 409
    assert past.json()["code"] == "week_past"
    assert (
        await client.post("/api/plan/weeks/2026-12-01/generate", headers=write_headers)
    ).status_code == 404
    await save_plan(services, "2026-12-07", status=PlanStatus.APPROVED)
    approved = await client.post("/api/plan/weeks/2026-12-07/generate", headers=write_headers)
    assert approved.status_code == 409
    assert approved.json()["code"] == "plan_approved"


async def test_week_reads(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    for week in ("2026-11-23", "2026-11-30", "2026-12-07", "2026-12-14"):
        await save_plan(services, week)
    listed = await client.get("/api/plan/weeks?limit=2", headers=read_headers)
    assert listed.status_code == 200
    page = listed.json()
    assert [i["item"]["weekStart"] for i in page["items"]] == ["2026-11-23", "2026-11-30"]
    nxt = await client.get(
        f"/api/plan/weeks?limit=2&cursor={page['nextCursor']}", headers=read_headers
    )
    assert [i["item"]["weekStart"] for i in nxt.json()["items"]] == ["2026-12-07", "2026-12-14"]
    assert nxt.json()["nextCursor"] is None
    from_week = await client.get("/api/plan/weeks?from=2026-12-09", headers=read_headers)
    assert [i["item"]["weekStart"] for i in from_week.json()["items"]] == [
        "2026-12-07",
        "2026-12-14",
    ]
    bad = await client.get("/api/plan/weeks?from=next-week", headers=read_headers)
    assert bad.status_code == 422
    one = await client.get("/api/plan/weeks/2026-11-30", headers=read_headers)
    assert one.status_code == 200
    assert one.json()["item"]["id"] == "plan-2026-11-30"
    assert (await client.get("/api/plan/weeks/2026-11-31", headers=read_headers)).status_code == 404
    assert (await client.get("/api/plan/weeks/2026-12-21", headers=read_headers)).status_code == 404
    bad_cursor = await client.get("/api/plan/weeks?cursor=%%%", headers=read_headers)
    assert bad_cursor.status_code == 422


async def test_swapping_two_approved_posts_works_but_stacking_them_does_not(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    tuesday = slot_at(CADENCE, "2026-12-01", "10:00")
    wednesday = slot_at(CADENCE, "2026-12-02", "10:00")
    first = await post(services, status=PostStatus.APPROVED, slot=tuesday, pillar="agents")
    second = await post(services, status=PostStatus.APPROVED, slot=wednesday, pillar="identity")
    slots = [
        planned_slot("s1", "2026-12-01", pillar="agents", post_id=first.id),
        planned_slot("s2", "2026-12-02", post_id=second.id),
    ]
    stored = await save_plan(services, WEEK, slots, status=PlanStatus.APPROVED, mode="light")
    swap = put_body(
        {"id": "s1", "localDate": "2026-12-02", "localTime": "10:00"},
        {"id": "s2", "localDate": "2026-12-01", "localTime": "10:00"},
    )
    swapped = await put(client, write_headers, stored.etag, swap)
    assert swapped.status_code == 200, swapped.text
    assert (await read_doc(services.store, Post, first.id)).doc.slot == wednesday
    assert (await read_doc(services.store, Post, second.id)).doc.slot == tuesday
    stack = put_body(
        {"id": "s1", "localDate": "2026-12-02", "localTime": "10:00"},
        {"id": "s2", "localDate": "2026-12-02", "localTime": "16:00"},
    )
    stacked = await put(client, write_headers, swapped.json()["etag"], stack)
    assert stacked.status_code == 409
    assert stacked.json()["code"] == "slot_conflict"
    assert (await read_doc(services.store, Post, second.id)).doc.slot == tuesday  # unmoved


async def test_an_away_week_moves_its_one_click_posts_to_the_kit(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    """Away: nobody taps at a slot, so the week's approved one-click posts go to the kit for
    native scheduling and their staging and slot checks are cancelled."""
    plan = await save_plan(
        services, WEEK, [planned_slot("s1", "2026-12-01")], status=PlanStatus.APPROVED
    )
    tuesday = slot_at(CADENCE, "2026-12-01", "10:00")
    draft = await post(services, slot=tuesday)
    approved = draft.model_copy(
        update={"approval": Approval(sha256=draft.compute_approval_hash(), approved_at=SAMPLE_NOW)}
    )
    await upsert_doc(services.store, approved)
    await enqueue_slot_jobs(services.jobs, approved, cadence=CADENCE)
    next_week = slot_at(CADENCE, "2026-12-08", "10:00")
    later = await post(services, slot=next_week)
    response = await client.put(
        f"/api/plan/weeks/{WEEK}/mode",
        json={"mode": "away"},
        headers={**write_headers, "If-Match": plan.etag},
    )
    assert response.status_code == 200, response.text
    moved = (await read_doc(services.store, Post, approved.id)).doc
    assert moved.status is PostStatus.KIT
    assert moved.go_live_mode == "kit"
    assert moved.kit is not None
    assert moved.kit.state is KitState.PREPARED
    for job_id in (
        stage_job_id(approved.id, tuesday.utc),
        slot_check_job_id(approved.id, tuesday.utc),
    ):
        assert (await services.jobs.get(job_id)).status == "cancelled"
    untouched = (await read_doc(services.store, Post, later.id)).doc
    assert untouched.status is PostStatus.APPROVED  # another week
