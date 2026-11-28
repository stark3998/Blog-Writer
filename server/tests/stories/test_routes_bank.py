"""Story and POV routes: create, read, edit, delete, review, lists and the classifier."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as core_write_headers
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import (
    FakeClassifier,
    FakeServices,
    make_app,
    make_services,
    sample_document,
)
from blogwriter.stories import get_router
from blogwriter.stories.testing import save_policies

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **core_write_headers(make_settings())}

STORY: dict[str, Any] = {
    "title": "Okta to Entra ID migration for 2,000+ apps",
    "problem": "Two identity providers and no owner for half the apps.",
    "actions": "I sorted apps into waves by protocol and owner readiness.",
    "decisions": "We moved SAML apps last because their owners needed the most notice.",
    "metrics": [{"label": "Apps moved", "value": "2,000+"}],
    "lessons": "Inventory the owners before the apps.",
    "pillars": ["identity"],
    "confidentiality": "anonymized",
    "tags": ["okta", "entra"],
}


def with_etag(headers: dict[str, str], etag: str) -> dict[str, str]:
    return {**headers, "If-Match": etag}


async def create_story(client: httpx.AsyncClient, **changes: Any) -> httpx.Response:
    response = await client.post("/api/stories", json={**STORY, **changes}, headers=WRITE)
    assert response.status_code == 201, response.text
    return response


# ---------------------------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------------------------


async def test_create_story_is_approved_classified_and_usable(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    response = await create_story(client)
    body = response.json()
    item = body["item"]
    assert response.headers["ETag"] == body["etag"]
    assert response.headers["Location"] == f"/api/stories/{item['id']}"
    assert item["id"].startswith("story-")
    assert item["status"] == "approved"
    assert item["origin"] == "owner"
    assert item["approvedAt"] == item["createdAt"]
    assert item["classifier"]["passed"] is True
    stored = await read_doc(services.store, StoryEntry, item["id"])
    assert stored.doc.usable
    assert services.classifier is not None
    (texts, origin), *_ = services.classifier.calls
    assert origin == "owner"
    assert "Okta to Entra ID migration" in texts[0]


async def test_a_flagged_story_is_saved_but_not_usable(services: FakeServices) -> None:
    services.classifier = FakeClassifier(flag="SAML")
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        response = await create_story(http)
    item = response.json()["item"]
    assert item["classifier"]["passed"] is False
    assert not (await read_doc(services.store, StoryEntry, item["id"])).doc.usable


@pytest.mark.parametrize("missing", ["ai_use", "classifier", "ai"])
async def test_without_the_classifier_the_story_stays_unchecked(missing: str) -> None:
    services = make_services()
    await save_policies(services.store, ai_use=missing != "ai_use")
    if missing == "classifier":
        services.classifier = None
    if missing == "ai":
        services.ai = None
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        response = await create_story(http)
    item = response.json()["item"]
    assert item["status"] == "approved"
    assert item["classifier"]["passed"] is None
    assert not (await read_doc(services.store, StoryEntry, item["id"])).doc.usable


async def test_a_failing_classifier_never_blocks_the_save(services: FakeServices) -> None:
    services.classifier = FakeClassifier(error=RuntimeError("model down"))
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        response = await create_story(http)
    assert response.json()["item"]["classifier"]["passed"] is None


@pytest.mark.parametrize(
    ("field", "value", "code", "where"),
    [
        ("problem", "Contoso had two identity providers.", "blocklisted-term", "problem"),
        ("actions", "I asked avery.t@fabrikam-bank.com.", "email-address", "actions"),
        ("lessons", "See https://wiki.fabrikam.local/x", "url", "lessons"),
        ("tags", ["contoso-cutover"], "blocklisted-term", "tags[0]"),
        ("metrics", [{"label": "Northwind apps", "value": "2,000+"}], "blocklisted-term", None),
    ],
)
async def test_confirmed_text_with_blocking_findings_is_refused(
    client: httpx.AsyncClient,
    services: FakeServices,
    field: str,
    value: object,
    code: str,
    where: str | None,
) -> None:
    response = await client.post("/api/stories", json={**STORY, field: value}, headers=WRITE)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "confidential_text"
    finding = problem["findings"][0]
    assert finding["code"] == code
    if where is not None:
        assert finding["field"] == where
    else:
        assert finding["field"] == "metrics[0].label"
    for secret in ("Contoso", "Northwind", "avery.t@", "wiki.fabrikam"):
        assert secret not in response.text
    assert await services.store.find("stories") == []


async def test_a_blocklisted_public_source_is_refused(client: httpx.AsyncClient) -> None:
    metric = {"label": "Apps", "value": "2,000+", "publicSource": "https://contoso.com/case"}
    response = await client.post("/api/stories", json={**STORY, "metrics": [metric]}, headers=WRITE)
    assert response.status_code == 422
    assert response.json()["findings"][0]["field"] == "metrics[0].publicSource"
    fine = {"label": "Apps", "value": "2,000+", "publicSource": "https://learn.microsoft.com/a"}
    ok = await client.post("/api/stories", json={**STORY, "metrics": [fine]}, headers=WRITE)
    assert ok.status_code == 201


async def test_unknown_pillars_and_bad_tags_are_refused(client: httpx.AsyncClient) -> None:
    pillar = await client.post("/api/stories", json={**STORY, "pillars": ["nope"]}, headers=WRITE)
    assert pillar.status_code == 422
    assert pillar.json()["code"] == "unknown_pillar"
    assert pillar.json()["pillars"] == ["nope"]
    tag = await client.post("/api/stories", json={**STORY, "tags": ["Not A Slug"]}, headers=WRITE)
    assert tag.status_code == 422
    extra = await client.post("/api/stories", json={**STORY, "status": "approved"}, headers=WRITE)
    assert extra.status_code == 422


async def test_get_story_and_the_kinds_it_refuses(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    created = (await create_story(client)).json()
    got = await client.get(f"/api/stories/{created['item']['id']}", headers=READ)
    assert got.status_code == 200
    assert got.json() == created
    assert got.headers["ETag"] == created["etag"]
    pov = sample_document(PovEntry)
    await create_doc(services.store, pov)
    assert (await client.get(f"/api/stories/{pov.id}", headers=READ)).status_code == 404
    assert (await client.get(f"/api/povs/{created['item']['id']}", headers=READ)).status_code == 404
    assert (await client.get("/api/stories/story-missing", headers=READ)).status_code == 404
    assert (await client.get("/api/stories/a%23b", headers=READ)).status_code == 404


async def test_put_story_needs_a_fresh_etag(client: httpx.AsyncClient) -> None:
    created = (await create_story(client)).json()
    url = f"/api/stories/{created['item']['id']}"
    edited = {**STORY, "lessons": "Inventory owners first, then apps."}
    missing = await client.put(url, json=edited, headers=WRITE)
    assert missing.status_code == 428
    assert missing.json()["code"] == "if_match_required"
    stale = await client.put(url, json=edited, headers=with_etag(WRITE, '"stale"'))
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == created["etag"]
    assert stale.json()["current"]["status"] == "approved"
    fresh = await client.put(url, json=edited, headers=with_etag(WRITE, created["etag"]))
    assert fresh.status_code == 200
    assert fresh.json()["item"]["lessons"] == "Inventory owners first, then apps."
    assert fresh.json()["etag"] != created["etag"]


async def test_an_edit_makes_the_verdict_stale(services: FakeServices) -> None:
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        created = (await create_story(http)).json()
        story_id = created["item"]["id"]
        assert (await read_doc(services.store, StoryEntry, story_id)).doc.usable
        services.classifier = None  # the classifier is down while the owner edits
        same_text = {**STORY, "pillars": ["identity", "agents"], "confidentiality": "public"}
        kept = await http.put(
            f"/api/stories/{story_id}", json=same_text, headers=with_etag(WRITE, created["etag"])
        )
        assert kept.json()["item"]["classifier"]["passed"] is True  # same text: verdict holds
        changed = {**same_text, "actions": "I sorted apps into waves by protocol."}
        edited = await http.put(
            f"/api/stories/{story_id}", json=changed, headers=with_etag(WRITE, kept.json()["etag"])
        )
    assert edited.status_code == 200
    assert edited.json()["item"]["classifier"]["passed"] is None
    assert not (await read_doc(services.store, StoryEntry, story_id)).doc.usable


async def test_an_edit_reclassifies_when_it_can(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    created = (await create_story(client)).json()
    services.classifier = FakeClassifier(flag="waves")
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as http:
        edited = await http.put(
            f"/api/stories/{created['item']['id']}",
            json={**STORY, "actions": "I sorted apps into waves."},
            headers=with_etag(WRITE, created["etag"]),
        )
    assert edited.json()["item"]["classifier"]["passed"] is False


async def test_delete_story(client: httpx.AsyncClient, services: FakeServices) -> None:
    created = (await create_story(client)).json()
    url = f"/api/stories/{created['item']['id']}"
    assert (await client.delete(url, headers=WRITE)).status_code == 428
    assert (await client.delete(url, headers=with_etag(WRITE, '"old"'))).status_code == 412
    deleted = await client.delete(url, headers=with_etag(WRITE, created["etag"]))
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert (await client.get(url, headers=READ)).status_code == 404
    pov = sample_document(PovEntry)
    stored = await create_doc(services.store, pov)
    wrong_kind = await client.delete(
        f"/api/stories/{pov.id}", headers=with_etag(WRITE, stored.etag)
    )
    assert wrong_kind.status_code == 404
    assert (await read_doc(services.store, PovEntry, pov.id)).doc.id == pov.id


async def test_review_moves_along_the_review_machine(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    seeded = sample_document(StoryEntry, origin="seed")
    stored = await create_doc(services.store, seeded)
    url = f"/api/stories/{seeded.id}/review"
    etag = stored.etag

    async def review(decision: str) -> httpx.Response:
        nonlocal etag
        response = await client.post(
            url, json={"decision": decision}, headers=with_etag(WRITE, etag)
        )
        if response.status_code == 200:
            etag = response.json()["etag"]
        return response

    rejected = await review("reject")
    assert rejected.json()["item"]["status"] == "rejected"
    restored = await review("restore")
    assert restored.json()["item"]["status"] == "proposed"
    approved = await review("approve")
    item = approved.json()["item"]
    assert item["status"] == "approved"
    assert item["approvedAt"] == item["statusChangedAt"]
    assert (await review("archive")).json()["item"]["status"] == "archived"
    assert (await review("restore")).json()["item"]["status"] == "approved"
    refused = await review("reject")
    assert refused.status_code == 409
    assert refused.json()["code"] == "invalid_transition"
    assert refused.json()["allowed"] == ["archived"]
    no_etag = await client.post(url, json={"decision": "archive"}, headers=WRITE)
    assert no_etag.status_code == 428


async def test_approval_alone_does_not_make_a_seeded_story_usable(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    stored = await create_doc(services.store, sample_document(StoryEntry, origin="seed"))
    response = await client.post(
        f"/api/stories/{stored.doc.id}/review",
        json={"decision": "approve"},
        headers=with_etag(WRITE, stored.etag),
    )
    assert response.json()["item"]["status"] == "approved"
    assert not (await read_doc(services.store, StoryEntry, stored.doc.id)).doc.usable


# ---------------------------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------------------------


async def test_story_pages_follow_the_cursor(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    ids: list[str] = []
    for n in range(5):
        services.clock.advance(1)
        ids.append((await create_story(client, title=f"Story {n}")).json()["item"]["id"])
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/stories", params=params, headers=READ)).json()
        seen.extend(entry["item"]["id"] for entry in page["items"])
        assert all(entry["etag"] for entry in page["items"])
        pages += 1
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == list(reversed(ids))
    assert pages == 3
    bad = await client.get("/api/stories", params={"cursor": "!!"}, headers=READ)
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_cursor"


async def test_story_filters(client: httpx.AsyncClient, services: FakeServices) -> None:
    usable = (await create_story(client)).json()["item"]["id"]
    services.clock.advance(1)
    agents = (await create_story(client, pillars=["agents"])).json()["item"]["id"]
    proposed = sample_document(StoryEntry, origin="seed", pillars=["identity", "governance"])
    await create_doc(services.store, proposed)

    async def ids(**params: str) -> list[str]:
        page = (await client.get("/api/stories", params=params, headers=READ)).json()
        return [entry["item"]["id"] for entry in page["items"]]

    assert set(await ids(pillar="identity")) == {usable, proposed.id}
    assert await ids(pillar="agents") == [agents]
    assert await ids(status="proposed") == [proposed.id]
    assert set(await ids(usable="true")) == {usable, agents}
    assert await ids(usable="false") == [proposed.id]
    assert await ids(usable="true", pillar="agents") == [agents]
    assert (await client.get("/api/stories?status=nope", headers=READ)).status_code == 422


async def test_filtered_pages_scan_past_non_matching_entries(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    for n in range(4):
        services.clock.advance(1)
        await create_story(client, title=f"Agents {n}", pillars=["agents"])
    services.clock.advance(1)
    wanted = [(await create_story(client, title="Identity A")).json()["item"]["id"]]
    for n in range(3):
        services.clock.advance(1)
        await create_story(client, title=f"Agents late {n}", pillars=["agents"])
    page = (
        await client.get("/api/stories", params={"pillar": "identity", "limit": "1"}, headers=READ)
    ).json()
    assert [entry["item"]["id"] for entry in page["items"]] == wanted
    assert page["nextCursor"] is None


# ---------------------------------------------------------------------------------------------
# POVs
# ---------------------------------------------------------------------------------------------

POV: dict[str, Any] = {
    "pillar": "agents",
    "statement": "Agents need their own identities, not borrowed service accounts.",
    "context": "Most pilots run agents under a shared admin account.",
}


async def test_pov_lifecycle(client: httpx.AsyncClient, services: FakeServices) -> None:
    created = await client.post("/api/povs", json=POV, headers=WRITE)
    assert created.status_code == 201
    item = created.json()["item"]
    assert item["id"].startswith("pov-")
    assert (item["status"], item["origin"]) == ("approved", "owner")
    assert item["classifier"]["passed"] is True
    url = f"/api/povs/{item['id']}"
    edited = await client.put(
        url,
        json={**POV, "origin": "quick_capture", "statement": "Agents need their own identities."},
        headers=with_etag(WRITE, created.json()["etag"]),
    )
    assert edited.status_code == 200
    assert edited.json()["item"]["origin"] == "owner"  # provenance is kept
    archived = await client.post(
        f"{url}/review",
        json={"decision": "archive"},
        headers=with_etag(WRITE, edited.json()["etag"]),
    )
    assert archived.json()["item"]["status"] == "archived"
    gone = await client.delete(url, headers=with_etag(WRITE, archived.json()["etag"]))
    assert gone.status_code == 204
    assert await services.store.find("stories") == []


async def test_pov_checks(client: httpx.AsyncClient, services: FakeServices) -> None:
    blocked = await client.post(
        "/api/povs", json={**POV, "context": "Contoso runs agents as admins."}, headers=WRITE
    )
    assert blocked.status_code == 422
    assert blocked.json()["findings"][0]["field"] == "context"
    take = await client.post("/api/povs", json={**POV, "origin": "idea_take"}, headers=WRITE)
    assert take.status_code == 422
    unknown = await client.post(
        "/api/povs",
        json={**POV, "origin": "idea_take", "ideaId": "idea-00000000000000000000000000"},
        headers=WRITE,
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_idea"
    idea = await create_doc(services.store, sample_document(Idea))
    ok = await client.post(
        "/api/povs", json={**POV, "origin": "idea_take", "ideaId": idea.doc.id}, headers=WRITE
    )
    assert ok.status_code == 201
    assert ok.json()["item"]["ideaId"] == idea.doc.id
    pillar = await client.post("/api/povs", json={**POV, "pillar": "nope"}, headers=WRITE)
    assert pillar.json()["code"] == "unknown_pillar"


async def test_pov_lists_filter_in_the_store(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    idea = await create_doc(services.store, sample_document(Idea))
    first = await client.post(
        "/api/povs", json={**POV, "origin": "idea_take", "ideaId": idea.doc.id}, headers=WRITE
    )
    services.clock.advance(1)
    second = await client.post("/api/povs", json={**POV, "pillar": "identity"}, headers=WRITE)
    seeded = sample_document(PovEntry, origin="seed", status=ReviewStatus.PROPOSED)
    await create_doc(services.store, seeded)

    async def ids(**params: str) -> list[str]:
        page = (await client.get("/api/povs", params=params, headers=READ)).json()
        return [entry["item"]["id"] for entry in page["items"]]

    assert await ids(pillar="identity") == [second.json()["item"]["id"]]
    assert await ids(ideaId=idea.doc.id) == [first.json()["item"]["id"]]
    assert await ids(status="proposed") == [seeded.id]
    assert len(await ids()) == 3
    page = (await client.get("/api/povs", params={"limit": "2"}, headers=READ)).json()
    assert len(page["items"]) == 2
    assert page["nextCursor"] is not None
