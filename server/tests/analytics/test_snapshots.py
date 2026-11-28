"""The snapshot form: If-Match, live posts only, one +48 h and one +7 d snapshot per post."""

from __future__ import annotations

import httpx

from blogwriter.analytics.snapshots import MAX_SNAPSHOTS, form_snapshot_id, offset_for
from blogwriter.analytics.testing import posted_post, snapshot
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.testing import FakeServices

POSTED_AT = "2026-11-19T15:00:00.000000Z"


def url(post_id: str) -> str:
    return f"/api/posts/{post_id}/snapshots"


async def test_the_form_adds_and_corrects_snapshots(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    post = await posted_post(services, posted_at=POSTED_AT)
    etag = (await read_doc(services.store, Post, post.id)).etag
    first = await client.post(
        url(post.id),
        json={"offset": "h48", "impressions": 1200, "reactions": 30, "saves": 4},
        headers={**write_headers, "If-Match": etag},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert first.headers["ETag"] == body["etag"] != etag
    [stored] = body["item"]["snapshots"]
    assert stored["id"] == "form-h48"
    assert stored["source"] == "form"
    assert stored["takenAt"] == "2026-11-26T17:00:00.000000Z"
    assert (stored["impressions"], stored["reactions"], stored["comments"]) == (1200, 30, None)
    corrected = await client.post(
        url(post.id),
        json={"offset": "h48", "impressions": 1250, "reactions": 31},
        headers={**write_headers, "If-Match": body["etag"]},
    )
    assert corrected.status_code == 200
    [again] = corrected.json()["item"]["snapshots"]
    assert (again["id"], again["impressions"]) == ("form-h48", 1250)
    other = await client.post(
        url(post.id),
        json={"offset": "other", "impressions": 1300, "takenAt": "2026-11-25T09:30:00.000000Z"},
        headers={**write_headers, "If-Match": corrected.json()["etag"]},
    )
    assert other.status_code == 200
    ids = [item["id"] for item in other.json()["item"]["snapshots"]]
    assert ids == ["form-20261125t093000", "form-h48"]  # ordered by takenAt


async def test_refusals(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    draft = await posted_post(services, posted_at=POSTED_AT, status="approved")
    etag = (await read_doc(services.store, Post, draft.id)).etag
    numbers = {"offset": "d7", "impressions": 10}
    not_live = await client.post(
        url(draft.id), json=numbers, headers={**write_headers, "If-Match": etag}
    )
    assert not_live.status_code == 409
    assert not_live.json()["code"] == "not_posted"

    live = await posted_post(services, posted_at=POSTED_AT)
    live_etag = (await read_doc(services.store, Post, live.id)).etag
    missing = await client.post(url(live.id), json=numbers, headers=write_headers)
    assert missing.status_code == 428
    stale = await client.post(
        url(live.id), json=numbers, headers={**write_headers, "If-Match": '"stale"'}
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == live_etag
    empty = await client.post(
        url(live.id), json={"offset": "d7"}, headers={**write_headers, "If-Match": live_etag}
    )
    assert empty.status_code == 422
    future = await client.post(
        url(live.id),
        json={**numbers, "takenAt": "2026-11-27T17:00:00.000000Z"},
        headers={**write_headers, "If-Match": live_etag},
    )
    assert future.json()["code"] == "snapshot_in_future"
    early = await client.post(
        url(live.id),
        json={**numbers, "takenAt": "2026-11-18T17:00:00.000000Z"},
        headers={**write_headers, "If-Match": live_etag},
    )
    assert early.json()["code"] == "snapshot_before_post"
    negative = await client.post(
        url(live.id),
        json={"offset": "d7", "impressions": -1},
        headers={**write_headers, "If-Match": live_etag},
    )
    assert negative.status_code == 422
    unknown = await client.post(
        url("post-00000000000000000000000000ff"),
        json=numbers,
        headers={**write_headers, "If-Match": live_etag},
    )
    assert unknown.status_code == 404


async def test_a_full_post_refuses_more_snapshots(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    full = [
        snapshot(
            taken_at=f"2026-11-2{index // 10}T1{index % 10}:00:00.000000Z",
            offset="other",
            snapshot_id=f"s-{index}",
            impressions=100 + index,
        )
        for index in range(MAX_SNAPSHOTS)
    ]
    post = await posted_post(services, posted_at=POSTED_AT, snapshots=full)
    etag = (await read_doc(services.store, Post, post.id)).etag
    response = await client.post(
        url(post.id),
        json={"offset": "other", "impressions": 1},
        headers={**write_headers, "If-Match": etag},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "too_many_snapshots"
    replace = await client.post(
        url(post.id),
        json={"offset": "other", "impressions": 5, "takenAt": "2026-11-20T10:00:00.000000Z"},
        headers={**write_headers, "If-Match": etag},
    )
    assert replace.status_code == 409  # a new id: still too many


def test_ids_and_offsets() -> None:
    assert form_snapshot_id("d7", "2026-11-26T17:00:00.000000Z") == "form-d7"
    assert form_snapshot_id("other", "2026-11-26T17:05:09.123456Z") == "form-20261126t170509"
    assert offset_for(POSTED_AT, "2026-11-21T15:00:00.000000Z") == "h48"
    assert offset_for(POSTED_AT, "2026-11-26T15:00:00.000000Z") == "d7"
    assert offset_for(POSTED_AT, "2026-11-20T15:00:00.000000Z") == "other"
    assert offset_for(None, "2026-11-20T15:00:00.000000Z") == "other"
