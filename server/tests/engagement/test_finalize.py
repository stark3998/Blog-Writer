"""Finalize: keep only the owner's final text and the post URL; the item is deleted at once."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx

from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import CONTAINERS, Filter
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.posts import Post
from blogwriter.domain.testing import FakeServices, make_app, make_services, sample_document
from blogwriter.engagement import get_router, record_id_for
from blogwriter.engagement.testing import save_policies

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
MARKER = "Zebra-Marker-7731"
COMMENT = f"{MARKER}: how do you scope agent tokens when one MCP server fronts several APIs?"
FINAL = "We mint one token per tool call and cap it at five minutes, with alerts on renewals."
URL = "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001"


def with_etag(etag: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag}


async def create_item(client: httpx.AsyncClient, **changes: Any) -> dict[str, Any]:
    body = {"itemKind": "comment", "pastedText": COMMENT, **changes}
    response = await client.post("/api/engagement/items", json=body, headers=WRITE)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def finalize(
    client: httpx.AsyncClient, payload: dict[str, Any], **body: Any
) -> httpx.Response:
    return await client.post(
        f"/api/engagement/items/{payload['item']['id']}/finalize",
        json={"finalText": FINAL, **body},
        headers=with_etag(payload["etag"]),
    )


async def store_dump(services: FakeServices) -> str:
    raws = [
        raw.body for container in CONTAINERS for raw in await services.store.find(container, [])
    ]
    return json.dumps(raws)


async def test_finalize_keeps_the_final_text_and_deletes_the_item(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client, targetUrl=URL)
    services.clock.advance(timedelta(hours=3))
    response = await finalize(client, payload, url=URL)
    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["ETag"] == body["etag"]
    record = body["item"]
    now = to_iso(services.clock.now())
    assert record["id"] == record_id_for(payload["item"]["id"])
    assert record["id"].startswith("rec-")
    assert record["itemKind"] == "comment"
    assert record["finalText"] == FINAL
    assert record["url"] == URL
    assert record["postedAt"] == record["createdAt"] == now
    assert record["substantive"] is True
    assert record["postId"] is None
    assert record["watchEntryId"] is None
    assert await services.store.find("engagement", [Filter("kind", "==", "engagement_item")]) == []
    assert MARKER not in await store_dump(services)
    stored = await read_doc(services.store, EngagementRecord, record["id"])
    assert set(stored.doc.to_document()) == {
        "id",
        "kind",
        "schemaVersion",
        "createdAt",
        "updatedAt",
        "itemKind",
        "finalText",
        "url",
        "postId",
        "postedAt",
        "substantive",
        "watchEntryId",
    }


async def test_finalize_needs_the_current_etag(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client)
    url = f"/api/engagement/items/{payload['item']['id']}/finalize"
    missing = await client.post(url, json={"finalText": FINAL}, headers=WRITE)
    assert missing.status_code == 428
    stale = await client.post(url, json={"finalText": FINAL}, headers=with_etag('"old"'))
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == payload["etag"]
    gone = await client.post(
        "/api/engagement/items/eng-missing/finalize",
        json={"finalText": FINAL},
        headers=with_etag(payload["etag"]),
    )
    assert gone.status_code == 404
    assert (
        await services.store.find("engagement", [Filter("kind", "==", "engagement_record")]) == []
    )


async def test_a_blocklisted_name_in_the_final_text_is_refused_and_the_item_kept(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client)
    response = await finalize(client, payload, finalText="We did this for Contoso last year.")
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "confidential_text"
    assert problem["findings"][0]["field"] == "finalText"
    assert "Contoso" not in response.text
    url_hit = await finalize(client, payload, url="https://www.linkedin.com/posts/contoso-1")
    assert url_hit.status_code == 422
    assert url_hit.json()["findings"][0]["field"] == "url"
    stored = await read_doc(services.store, EngagementItem, payload["item"]["id"])
    assert stored.etag == payload["etag"]


async def test_an_unsaved_blocklist_refuses_finalize() -> None:
    services = make_services()
    await save_policies(services.store, blocklist=False)
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as client:
        payload = await create_item(client)
        response = await finalize(client, payload)
    assert response.status_code == 409
    assert response.json()["code"] == "blocklist_not_configured"


async def test_a_retried_finalize_finds_its_record(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    payload = await create_item(client)
    item_id = payload["item"]["id"]
    crashed = sample_document(
        EngagementRecord,
        id=record_id_for(item_id),
        item_kind="comment",
        final_text="An earlier text.",
    )
    await create_doc(services.store, crashed)
    response = await finalize(client, payload)
    assert response.status_code == 201
    record = response.json()["item"]
    assert record["id"] == crashed.id
    assert record["finalText"] == FINAL
    assert record["createdAt"] == crashed.created_at
    records = await services.store.find("engagement", [Filter("kind", "==", "engagement_record")])
    assert len(records) == 1
    assert await services.store.find("engagement", [Filter("kind", "==", "engagement_item")]) == []


async def test_a_reply_record_keeps_its_post_and_the_watch_entry_is_engaged(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    post = sample_document(Post)
    entry = sample_document(WatchEntry, name="A niche voice")
    await create_doc(services.store, post)
    await create_doc(services.store, entry)
    payload = await create_item(client, itemKind="reply", postId=post.id)
    response = await finalize(client, payload, watchEntryId=entry.id)
    record = response.json()["item"]
    assert record["itemKind"] == "reply"
    assert record["postId"] == post.id
    assert record["watchEntryId"] == entry.id
    engaged = await read_doc(services.store, WatchEntry, entry.id)
    assert engaged.doc.last_engaged_at == record["postedAt"]
    other = await create_item(client)
    unknown = await finalize(client, other, watchEntryId="watch-missing")
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["loc"][-1] == "watchEntryId"


async def test_short_or_restating_replies_are_not_substantive(
    client: httpx.AsyncClient,
) -> None:
    short = await finalize(client, await create_item(client), finalText="Per tool call, always.")
    assert short.json()["item"]["substantive"] is False
    restating = await finalize(
        client,
        await create_item(client),
        finalText="Scope agent tokens when one MCP server fronts several APIs? Per tool call.",
    )
    assert restating.json()["item"]["substantive"] is False


async def test_finalize_validates_its_body(client: httpx.AsyncClient) -> None:
    payload = await create_item(client)
    for body in (
        {"finalText": "   "},
        {"finalText": "x" * 1251},
        {"finalText": FINAL, "url": "http://www.linkedin.com/x"},
        {"finalText": FINAL, "pastedText": "smuggled"},
    ):
        response = await client.post(
            f"/api/engagement/items/{payload['item']['id']}/finalize",
            json=body,
            headers=with_etag(payload["etag"]),
        )
        assert response.status_code == 422, body


async def test_records_list_newest_first_with_a_cursor(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    ids: list[str] = []
    for _ in range(3):
        response = await finalize(client, await create_item(client))
        ids.append(response.json()["item"]["id"])
        services.clock.advance(timedelta(minutes=5))
    first = await client.get("/api/engagement/records?limit=2", headers=READ)
    page = first.json()
    assert [entry["item"]["id"] for entry in page["items"]] == [ids[2], ids[1]]
    second = await client.get(
        f"/api/engagement/records?limit=2&cursor={page['nextCursor']}", headers=READ
    )
    assert [entry["item"]["id"] for entry in second.json()["items"]] == [ids[0]]
    assert second.json()["nextCursor"] is None
