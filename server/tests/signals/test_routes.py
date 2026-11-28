"""The signals routes through the real app (auth, CSRF, problems): sources and ideas."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

import httpx
import pytest

from blogwriter.core.egress import EgressTimeoutError
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import create_doc, update_doc
from blogwriter.domain.hashing import url_sha256
from blogwriter.domain.jobtypes import SIGNALS_CRAWL
from blogwriter.domain.signals import Idea, Source
from blogwriter.domain.states import SourceStatus
from blogwriter.domain.testing import FakeServices, make_app, sample_document
from blogwriter.signals import get_router
from blogwriter.signals.testing import T0, FeedEntry, RecordingHttp, SignalsSeed, rss_bytes

READ: Final = principal_headers(OWNER_OID)
WRITE: Final = {**READ, **write_headers(make_settings())}
FEED: Final = "https://news.example.org/feed.xml"


def body(response: httpx.Response) -> dict[str, Any]:
    data: dict[str, Any] = response.json()
    return data


def with_etag(etag: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag}


async def create_feed(client: httpx.AsyncClient, **fields: object) -> dict[str, Any]:
    payload: dict[str, object] = {"type": "feed", "title": "News", "url": FEED, **fields}
    response = await client.post("/api/sources", json=payload, headers=WRITE)
    assert response.status_code == 201, response.text
    return body(response)


# --------------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------------


async def test_source_lifecycle(client: httpx.AsyncClient, seed: SignalsSeed) -> None:
    created = await create_feed(client, pillar="agents", siteUrl="https://news.example.org/")
    item = created["item"]
    assert item["id"].startswith("feed-")
    assert item["status"] == "active"
    assert item["type"] == "feed"
    etag = created["etag"]

    fetched = await client.get(f"/api/sources/{item['id']}", headers=READ)
    assert fetched.status_code == 200
    assert fetched.headers["ETag"] == etag == body(fetched)["etag"]

    missing = await client.put(
        f"/api/sources/{item['id']}",
        json={"type": "feed", "title": "X", "url": FEED},
        headers=WRITE,
    )
    assert missing.status_code == 428
    stale = await client.put(
        f"/api/sources/{item['id']}",
        json={"type": "feed", "title": "X", "url": FEED},
        headers=with_etag('"stale"'),
    )
    assert stale.status_code == 412
    assert body(stale)["current"]["etag"] == etag

    renamed = await client.put(
        f"/api/sources/{item['id']}",
        json={"type": "feed", "title": "  Renamed   feed ", "url": FEED, "pillar": "identity"},
        headers=with_etag(etag),
    )
    assert renamed.status_code == 200, renamed.text
    assert body(renamed)["item"]["title"] == "Renamed feed"
    assert body(renamed)["item"]["pillar"] == "identity"
    etag = body(renamed)["etag"]

    retyped = await client.put(
        f"/api/sources/{item['id']}",
        json={"type": "scout_query", "title": "Q", "query": "something new"},
        headers=with_etag(etag),
    )
    assert retyped.status_code == 422
    assert body(retyped)["code"] == "source_type_immutable"

    paused = await client.post(f"/api/sources/{item['id']}/pause", headers=with_etag(etag))
    assert paused.status_code == 200
    assert body(paused)["item"]["status"] == "paused"
    again = await client.post(
        f"/api/sources/{item['id']}/pause", headers=with_etag(body(paused)["etag"])
    )
    assert again.status_code == 409
    assert body(again)["code"] == "invalid_transition"
    resumed = await client.post(
        f"/api/sources/{item['id']}/resume", headers=with_etag(body(paused)["etag"])
    )
    assert body(resumed)["item"]["status"] == "active"

    deleted = await client.delete(
        f"/api/sources/{item['id']}", headers=with_etag(body(resumed)["etag"])
    )
    assert deleted.status_code == 204
    gone = await client.get(f"/api/sources/{item['id']}", headers=READ)
    assert gone.status_code == 404


async def test_a_new_feed_url_clears_the_crawl_state(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    created = await create_feed(client)
    source_id = created["item"]["id"]

    def failing(source: Source) -> Source:
        return source.model_copy(
            update={
                "status": SourceStatus.FAILING,
                "etag": '"old"',
                "consecutive_failures": 7,
                "last_error": "http_500",
            }
        )

    stored = await update_doc(services.store, Source, source_id, failing)
    moved = await client.put(
        f"/api/sources/{source_id}",
        json={"type": "feed", "title": "News", "url": "https://news.example.org/new-feed"},
        headers=with_etag(stored.etag),
    )
    item = body(moved)["item"]
    assert item["status"] == "active"
    assert item["etag"] is None
    assert item["consecutiveFailures"] == 0
    assert item["lastError"] is None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"type": "feed", "title": "Plain", "url": "http://news.example.org/feed"}, None),
        ({"type": "feed", "title": "No url"}, None),
        ({"type": "scout_query", "title": "No query"}, None),
        ({"type": "feed", "title": "   ", "url": FEED}, None),
        ({"type": "feed", "title": "Extra", "url": FEED, "status": "paused"}, None),
        ({"type": "feed", "title": "Pillar", "url": FEED, "pillar": "cooking"}, "unknown_pillar"),
    ],
)
async def test_invalid_sources_are_refused(
    client: httpx.AsyncClient, payload: dict[str, object], code: str | None
) -> None:
    response = await client.post("/api/sources", json=payload, headers=WRITE)
    assert response.status_code == 422
    assert body(response).get("code") == (code or "validation_failed")


async def test_duplicate_sources_are_refused(client: httpx.AsyncClient, seed: SignalsSeed) -> None:
    await create_feed(client)
    duplicate = await client.post(
        "/api/sources",
        json={"type": "feed", "title": "Again", "url": f"{FEED}?utm_source=x"},
        headers=WRITE,
    )
    assert duplicate.status_code == 409
    assert body(duplicate)["code"] == "duplicate_source"
    await seed.blocklist()
    query = {"type": "scout_query", "title": "Q", "query": "Agent identity in MCP"}
    assert (await client.post("/api/sources", json=query, headers=WRITE)).status_code == 201
    again = {**query, "query": "agent IDENTITY in mcp"}
    assert (await client.post("/api/sources", json=again, headers=WRITE)).status_code == 409


async def test_scout_queries_pass_the_blocklist(
    client: httpx.AsyncClient, seed: SignalsSeed
) -> None:
    query = {"type": "scout_query", "title": "Q", "query": "Contoso tenant migration"}
    unconfigured = await client.post("/api/sources", json=query, headers=WRITE)
    assert unconfigured.status_code == 409
    assert body(unconfigured)["code"] == "blocklist_not_configured"
    await seed.blocklist("Contoso")
    refused = await client.post("/api/sources", json=query, headers=WRITE)
    assert refused.status_code == 422
    problem = body(refused)
    assert problem["code"] == "confidential_text"
    assert [finding["field"] for finding in problem["findings"]] == ["query"]
    assert "contoso" not in refused.text.casefold()


async def test_source_lists_page_in_order(client: httpx.AsyncClient, seed: SignalsSeed) -> None:
    first = (await seed.source(FEED)).doc
    second = (await seed.source("https://two.example.com/rss", status=SourceStatus.PAUSED)).doc
    third = (await seed.scout("weekly agent security news")).doc
    page = body(await client.get("/api/sources?limit=2", headers=READ))
    assert [entry["item"]["id"] for entry in page["items"]] == sorted(
        [first.id, second.id, third.id]
    )[:2]
    assert page["items"][0]["etag"]
    rest = body(await client.get(f"/api/sources?limit=2&cursor={page['nextCursor']}", headers=READ))
    assert len(rest["items"]) == 1
    assert rest["nextCursor"] is None
    paused = body(await client.get("/api/sources?status=paused", headers=READ))
    assert [entry["item"]["id"] for entry in paused["items"]] == [second.id]
    scouts = body(await client.get("/api/sources?type=scout_query", headers=READ))
    assert [entry["item"]["id"] for entry in scouts["items"]] == [third.id]
    bad = await client.get("/api/sources?cursor=%%%", headers=READ)
    assert bad.status_code == 422
    assert body(bad)["code"] == "invalid_cursor"


async def test_discover_reads_the_owner_typed_site(
    client: httpx.AsyncClient, http: RecordingHttp
) -> None:
    page = (
        b'<html><head><link rel="alternate" type="application/rss+xml" title="Posts" '
        b'href="/feed.xml"></head><body></body></html>'
    )
    http.add("https://site.example.com/", page, content_type="text/html; charset=utf-8")
    found = await client.post(
        "/api/sources/discover", json={"siteUrl": "https://site.example.com/"}, headers=WRITE
    )
    assert found.status_code == 200
    assert body(found) == {
        "feeds": [{"url": "https://site.example.com/feed.xml", "title": "Posts"}]
    }
    (request,) = http.requests
    assert "text/html" in request[1]["Accept"]


async def test_discover_recognises_a_feed_url(
    client: httpx.AsyncClient, http: RecordingHttp
) -> None:
    http.add(FEED, rss_bytes([FeedEntry("A", "https://news.example.org/a")], title="News feed"))
    found = await client.post("/api/sources/discover", json={"siteUrl": FEED}, headers=WRITE)
    assert body(found) == {"feeds": [{"url": FEED, "title": "News feed"}]}


async def test_discover_failures(client: httpx.AsyncClient, http: RecordingHttp) -> None:
    http.add("https://down.example.com/", error=EgressTimeoutError("timeout"))
    down = await client.post(
        "/api/sources/discover", json={"siteUrl": "https://down.example.com/"}, headers=WRITE
    )
    assert down.status_code == 502
    assert body(down)["code"] == "fetch_failed"
    assert "down.example.com" not in down.text
    missing = await client.post(
        "/api/sources/discover", json={"siteUrl": "https://missing.example.com/"}, headers=WRITE
    )
    assert missing.status_code == 502
    plain = await client.post(
        "/api/sources/discover", json={"siteUrl": "http://site.example.com/"}, headers=WRITE
    )
    assert plain.status_code == 422
    assert http.requests[-1][0] == "https://missing.example.com/"


async def test_crawl_now_enqueues_an_owner_crawl(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    first = await client.post("/api/sources/crawl", headers=WRITE)
    assert first.status_code == 202
    accepted = body(first)
    assert accepted["status"] == "queued"
    again = await client.post("/api/sources/crawl", headers=WRITE)
    assert body(again)["jobId"] == accepted["jobId"]  # a double tap is one crawl
    job = await services.jobs.get(accepted["jobId"])
    assert job.type == SIGNALS_CRAWL
    assert job.input == {"origin": "owner"}
    services.clock.advance(timedelta(minutes=1))
    later = await client.post("/api/sources/crawl", headers=WRITE)
    assert body(later)["jobId"] != accepted["jobId"]


async def test_routes_are_owner_only(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/ideas")).status_code == 401
    assert (await client.get("/api/sources")).status_code == 401
    no_csrf = await client.post("/api/sources/crawl", headers=READ)
    assert no_csrf.status_code == 403


# --------------------------------------------------------------------------------------------
# Ideas
# --------------------------------------------------------------------------------------------


async def test_quick_capture(client: httpx.AsyncClient, seed: SignalsSeed) -> None:
    link = "https://modelcontextprotocol.io/spec?utm_source=x"
    payload: dict[str, object] = {
        "title": "Agent identity in MCP servers",
        "note": "Compare with workload identities.",
        "url": link,
        "pillar": "agents",
        "suggestedLane": "quick_take",
        "timeSensitive": True,
    }
    unconfigured = await client.post("/api/ideas", json=payload, headers=WRITE)
    assert unconfigured.status_code == 409
    assert body(unconfigured)["code"] == "blocklist_not_configured"
    await seed.blocklist("Fabrikam")
    created = await client.post("/api/ideas", json=payload, headers=WRITE)
    assert created.status_code == 201, created.text
    item = body(created)["item"]
    assert item["origin"] == "quick_capture"
    assert item["status"] == "new"
    assert item["urlSha256"] == url_sha256(link)
    assert item["expiresAt"] == to_iso(T0 + timedelta(hours=72))
    assert created.headers["ETag"] == body(created)["etag"]
    refused = await client.post(
        "/api/ideas", json={"title": "Fabrikam rollout", "note": "ask fabrikam"}, headers=WRITE
    )
    assert refused.status_code == 422
    problem = body(refused)
    assert problem["code"] == "confidential_text"
    assert sorted({finding["field"] for finding in problem["findings"]}) == ["note", "title"]
    assert "fabrikam" not in refused.text.casefold()
    unknown = await client.post(
        "/api/ideas", json={"title": "Fine", "pillar": "cooking"}, headers=WRITE
    )
    assert body(unknown)["code"] == "unknown_pillar"


async def test_idea_lists(client: httpx.AsyncClient, services: FakeServices) -> None:
    stamps = [to_iso(T0 - timedelta(minutes=n)) for n in range(5)]
    new = sample_document(Idea, created_at=stamps[0], pillar="agents", suggested_lane="flagship")
    accepted = sample_document(Idea, status="accepted", created_at=stamps[1], pillar="identity")
    dismissed = sample_document(Idea, status="dismissed", created_at=stamps[2])
    older = sample_document(Idea, created_at=stamps[3], pillar="agents")
    for idea in (new, accepted, dismissed, older):
        await create_doc(services.store, idea)

    def ids(page: dict[str, Any]) -> list[str]:
        return [entry["item"]["id"] for entry in page["items"]]

    inbox = body(await client.get("/api/ideas", headers=READ))
    assert ids(inbox) == [new.id, accepted.id, older.id]
    first = body(await client.get("/api/ideas?limit=2", headers=READ))
    assert ids(first) == [new.id, accepted.id]
    rest = body(await client.get(f"/api/ideas?limit=2&cursor={first['nextCursor']}", headers=READ))
    assert ids(rest) == [older.id]
    assert rest["nextCursor"] is None
    gone = body(await client.get("/api/ideas?status=dismissed", headers=READ))
    assert ids(gone) == [dismissed.id]
    several = body(await client.get("/api/ideas?status=dismissed&status=accepted", headers=READ))
    assert ids(several) == [accepted.id, dismissed.id]
    agents = body(await client.get("/api/ideas?pillar=agents", headers=READ))
    assert ids(agents) == [new.id, older.id]
    flagship = body(await client.get("/api/ideas?lane=flagship", headers=READ))
    assert ids(flagship) == [new.id]
    single = await client.get(f"/api/ideas/{new.id}", headers=READ)
    assert body(single)["item"]["id"] == new.id
    assert (await client.get("/api/ideas/idea-nope", headers=READ)).status_code == 404


async def test_idea_edits(
    client: httpx.AsyncClient, services: FakeServices, seed: SignalsSeed
) -> None:
    await seed.blocklist("Fabrikam")
    idea = sample_document(Idea, note="An old note")
    stored = await create_doc(services.store, idea)
    path = f"/api/ideas/{idea.id}"
    assert (await client.put(path, json={"angle": "x"}, headers=WRITE)).status_code == 428
    edited = await client.put(
        path,
        json={
            "angle": "Scope tokens per tool call.",
            "suggestedFormat": "deep-explainer",
            "note": None,
        },
        headers=with_etag(stored.etag),
    )
    assert edited.status_code == 200, edited.text
    item = body(edited)["item"]
    assert item["angle"] == "Scope tokens per tool call."
    assert item["suggestedFormat"] == "deep-explainer"
    assert item["note"] is None
    assert item["title"] == idea.title
    etag = body(edited)["etag"]
    for payload, code in (
        ({"note": "call Fabrikam"}, "confidential_text"),
        ({"suggestedFormat": "image-card"}, "unknown_format"),
        ({"pillar": "cooking"}, "unknown_pillar"),
        ({"title": None}, "validation_failed"),
        ({"status": "used"}, "validation_failed"),
    ):
        refused = await client.put(path, json=payload, headers=with_etag(etag))
        assert refused.status_code == 422, payload
        assert body(refused)["code"] == code


async def test_idea_transitions(client: httpx.AsyncClient, services: FakeServices) -> None:
    idea = sample_document(Idea)
    stored = await create_doc(services.store, idea)
    path = f"/api/ideas/{idea.id}/transition"
    past = await client.post(
        path,
        json={"to": "snoozed", "snoozedUntil": to_iso(T0 - timedelta(hours=1))},
        headers=with_etag(stored.etag),
    )
    assert past.status_code == 422
    assert body(past)["code"] == "invalid_snooze"
    until = to_iso(T0 + timedelta(days=2))
    snoozed = await client.post(
        path, json={"to": "snoozed", "snoozedUntil": until}, headers=with_etag(stored.etag)
    )
    assert snoozed.status_code == 200
    assert body(snoozed)["item"]["snoozedUntil"] == until
    assert body(snoozed)["item"]["statusChangedAt"] == to_iso(T0)
    accepted = await client.post(
        path, json={"to": "accepted"}, headers=with_etag(body(snoozed)["etag"])
    )
    assert accepted.status_code == 409
    assert body(accepted)["code"] == "invalid_transition"
    assert body(accepted)["allowed"] == ["dismissed", "expired", "new"]
    woken = await client.post(path, json={"to": "new"}, headers=with_etag(body(snoozed)["etag"]))
    assert body(woken)["item"]["snoozedUntil"] is None
    used = await client.post(path, json={"to": "used"}, headers=with_etag(body(woken)["etag"]))
    assert used.status_code == 422

    expired = sample_document(
        Idea, status="expired", time_sensitive=True, expires_at=to_iso(T0 - timedelta(hours=1))
    )
    stored_expired = await create_doc(services.store, expired)
    back = await client.post(
        f"/api/ideas/{expired.id}/transition",
        json={"to": "new"},
        headers=with_etag(stored_expired.etag),
    )
    assert body(back)["item"]["status"] == "new"
    assert body(back)["item"]["expiresAt"] is None


def test_the_router_documents_its_routes(services: FakeServices) -> None:
    spec = make_app(services, routers=[get_router()]).openapi()
    paths = {path for path in spec["paths"] if path.startswith(("/api/sources", "/api/ideas"))}
    assert paths == {
        "/api/sources",
        "/api/sources/discover",
        "/api/sources/crawl",
        "/api/sources/{source_id}",
        "/api/sources/{source_id}/pause",
        "/api/sources/{source_id}/resume",
        "/api/ideas",
        "/api/ideas/repurpose",
        "/api/ideas/{idea_id}",
        "/api/ideas/{idea_id}/transition",
    }
