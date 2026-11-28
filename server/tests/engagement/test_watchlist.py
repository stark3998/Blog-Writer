"""The watchlist (niche voices for the daily session) and the profile-alignment checklist."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.engagement import WatchEntry
from blogwriter.domain.settings import ProfileChecklist
from blogwriter.domain.testing import FakeServices

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
VOICE = {
    "name": "  A niche voice  ",
    "profileUrl": "https://www.linkedin.com/in/someone",
    "pillar": "agents",
    "note": "Writes about MCP authorization.",
}


def with_etag(etag: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag}


async def add(client: httpx.AsyncClient, **changes: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/engagement/watchlist", json={**VOICE, **changes}, headers=WRITE
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    assert response.headers["ETag"] == payload["etag"]
    return payload


async def test_add_edit_and_remove_a_voice(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await add(client)
    entry = payload["item"]
    assert entry["id"].startswith("watch-")
    assert entry["name"] == "A niche voice"
    assert entry["active"] is True
    assert entry["lastEngagedAt"] is None
    url = f"/api/engagement/watchlist/{entry['id']}"
    edited = await client.put(
        url,
        json={"name": "Renamed voice", "pillar": "identity", "note": "  ", "active": False},
        headers=with_etag(payload["etag"]),
    )
    assert edited.status_code == 200, edited.text
    changed = edited.json()["item"]
    assert changed["name"] == "Renamed voice"
    assert changed["profileUrl"] is None
    assert changed["note"] is None
    assert changed["active"] is False
    stale = await client.put(url, json={"name": "x"}, headers=with_etag(payload["etag"]))
    assert stale.status_code == 412
    no_etag = await client.delete(url, headers=WRITE)
    assert no_etag.status_code == 428
    removed = await client.delete(url, headers=with_etag(edited.json()["etag"]))
    assert removed.status_code == 204
    assert await services.store.find("engagement", []) == []
    missing = await client.put(url, json={"name": "x"}, headers=with_etag('"x"'))
    assert missing.status_code == 404


async def test_pillars_must_exist_and_bodies_are_checked(client: httpx.AsyncClient) -> None:
    unknown = await client.post(
        "/api/engagement/watchlist", json={**VOICE, "pillar": "cooking"}, headers=WRITE
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["loc"][-1] == "pillar"
    for body in (
        {"name": ""},
        {"name": "x" * 121},
        {"name": "x", "profileUrl": "javascript:alert(1)"},
        {"name": "x", "lastEngagedAt": "2026-11-26T12:00:00.000000Z"},
    ):
        response = await client.post("/api/engagement/watchlist", json=body, headers=WRITE)
        assert response.status_code == 422, body
    no_pillar = await add(client, pillar=None)
    assert no_pillar["item"]["pillar"] is None


async def test_engaged_is_a_one_tap_action(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await add(client)
    services.clock.advance(timedelta(hours=2))
    response = await client.post(
        f"/api/engagement/watchlist/{payload['item']['id']}/engaged", headers=WRITE
    )
    assert response.status_code == 200
    body = response.json()
    assert body["item"]["lastEngagedAt"] == to_iso(services.clock.now())
    assert response.headers["ETag"] == body["etag"]
    stored = await read_doc(services.store, WatchEntry, payload["item"]["id"])
    assert stored.doc.last_engaged_at == body["item"]["lastEngagedAt"]
    missing = await client.post("/api/engagement/watchlist/watch-missing/engaged", headers=WRITE)
    assert missing.status_code == 404


async def test_the_watchlist_pages_in_the_order_voices_were_added(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    ids: list[str] = []
    for index in range(3):
        ids.append((await add(client, name=f"Voice {index}", active=index != 1))["item"]["id"])
        services.clock.advance(timedelta(minutes=1))
    first = await client.get("/api/engagement/watchlist?limit=2", headers=READ)
    page = first.json()
    assert [entry["item"]["id"] for entry in page["items"]] == ids[:2]
    second = await client.get(
        f"/api/engagement/watchlist?limit=2&cursor={page['nextCursor']}", headers=READ
    )
    assert [entry["item"]["id"] for entry in second.json()["items"]] == ids[2:]
    active = await client.get("/api/engagement/watchlist?active=true", headers=READ)
    assert [entry["item"]["id"] for entry in active.json()["items"]] == [ids[0], ids[2]]
    inactive = await client.get("/api/engagement/watchlist?active=false", headers=READ)
    assert [entry["item"]["id"] for entry in inactive.json()["items"]] == [ids[1]]


# ---------------------------------------------------------------------------------------------
# Profile checklist
# ---------------------------------------------------------------------------------------------


async def test_the_checklist_starts_from_the_defaults(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/engagement/profile-checklist", headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert body["etag"] is None
    assert "ETag" not in response.headers
    assert [item["id"] for item in body["item"]["items"]] == ["headline", "about", "featured"]
    assert all(item["done"] is False for item in body["item"]["items"])


async def test_saving_the_checklist_stamps_done_items(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    url = "/api/engagement/profile-checklist"
    items: list[dict[str, Any]] = [
        {"id": "headline", "label": "Headline names your three pillars", "done": True},
        {"id": "about", "label": "About matches the pillars", "done": False, "note": " "},
        {"id": "featured", "label": "Featured shows the best flagship pieces"},
    ]
    no_precondition = await client.put(url, json={"items": items}, headers=WRITE)
    assert no_precondition.status_code == 428
    created = await client.put(url, json={"items": items}, headers={**WRITE, "If-None-Match": "*"})
    assert created.status_code == 200, created.text
    first = created.json()
    first_stamp = to_iso(services.clock.now())
    headline = first["item"]["items"][0]
    assert headline["done"] is True
    assert headline["doneAt"] == first_stamp
    assert first["item"]["items"][1]["note"] is None
    assert first["item"]["items"][1]["doneAt"] is None
    again = await client.put(url, json={"items": items}, headers={**WRITE, "If-None-Match": "*"})
    assert again.status_code == 412
    services.clock.advance(timedelta(days=2))
    items[0]["label"] = "Headline names the pillars"
    items[1]["done"] = True
    updated = await client.put(url, json={"items": items}, headers=with_etag(first["etag"]))
    assert updated.status_code == 200
    saved = updated.json()["item"]["items"]
    assert saved[0]["doneAt"] == first_stamp
    assert saved[1]["doneAt"] == to_iso(services.clock.now())
    items[0]["done"] = False
    undone = await client.put(url, json={"items": items}, headers=with_etag(updated.json()["etag"]))
    assert undone.json()["item"]["items"][0]["doneAt"] is None
    stale = await client.put(url, json={"items": items}, headers=with_etag(first["etag"]))
    assert stale.status_code == 412
    stored = await read_doc(services.store, ProfileChecklist, ProfileChecklist.SINGLETON_ID)
    assert stored.etag == undone.json()["etag"]


async def test_the_checklist_refuses_duplicates_and_too_many_items(
    client: httpx.AsyncClient,
) -> None:
    url = "/api/engagement/profile-checklist"
    headers = {**WRITE, "If-None-Match": "*"}
    duplicate = [{"id": "headline", "label": "A"}, {"id": "headline", "label": "B"}]
    response = await client.put(url, json={"items": duplicate}, headers=headers)
    assert response.status_code == 422
    many = [{"id": f"item-{index}", "label": "x"} for index in range(31)]
    response = await client.put(url, json={"items": many}, headers=headers)
    assert response.status_code == 422
    bad_id = await client.put(
        url, json={"items": [{"id": "Bad Id", "label": "x"}]}, headers=headers
    )
    assert bad_id.status_code == 422
