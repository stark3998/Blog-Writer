"""Pillars: the ``pillar_in_use`` guard (fail closed), the references behind it, and the pillar
rules the domain model leaves open."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import MemoryStore
from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import InvalidStoredDocument, create_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.signals import Idea, Source
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import FakeServices, sample_document
from blogwriter.kb import pillar_ids_in_use, pillar_references

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}


async def pillars(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], str | None]:
    body = (await client.get("/api/kb/pillars", headers=READ)).json()
    return body["item"]["pillars"], body["etag"]


async def save(
    client: httpx.AsyncClient, items: list[dict[str, Any]], etag: str | None
) -> httpx.Response:
    headers = CREATE if etag is None else {**WRITE, "If-Match": etag}
    return await client.put("/api/kb/pillars", headers=headers, json={"pillars": items})


async def seed_every_reference(store: MemoryStore, pillar: str) -> None:
    await create_doc(store, sample_document(Idea, pillar=pillar))
    await create_doc(store, sample_document(Article, meta={"pillar": pillar}, purpose="eval"))
    await create_doc(store, sample_document(Post, pillar=pillar))
    await create_doc(store, sample_document(StoryEntry, pillars=["identity", pillar]))
    await create_doc(store, sample_document(PovEntry, pillar=pillar))
    await create_doc(store, sample_document(Source, pillar=pillar))


async def test_removing_an_unused_pillar_saves(client: httpx.AsyncClient) -> None:
    items, etag = await pillars(client)
    response = await save(client, items[:2], etag)
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["item"]["pillars"]] == ["agents", "identity"]


async def test_removing_a_referenced_pillar_is_refused(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await seed_every_reference(services.store, "governance")
    items, etag = await pillars(client)
    lanes = (await client.get("/api/kb/lanes", headers=READ)).json()["item"]
    lanes["series"] = [{"id": "rmf-in-practice", "pillar": "governance", "title": "RMF notes"}]
    lanes_body = {
        k: v for k, v in lanes.items() if k not in {"id", "schemaVersion", "createdAt", "updatedAt"}
    }
    assert (await client.put("/api/kb/lanes", headers=CREATE, json=lanes_body)).status_code == 200

    response = await save(client, items[:2], etag)
    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "pillar_in_use"
    assert problem["pillars"] == ["governance"]
    assert problem["references"] == {
        "governance": ["idea", "article", "post", "story", "pov", "source", "series"]
    }
    _, still = await pillars(client)
    assert still is None  # nothing was written


async def test_renaming_a_referenced_id_is_refused(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await create_doc(services.store, sample_document(StoryEntry, pillars=["identity"]))
    items, etag = await pillars(client)
    items[1] = {**items[1], "id": "identity-modernization"}
    response = await save(client, items, etag)
    assert response.status_code == 409
    assert response.json()["references"] == {"identity": ["story"]}


async def test_an_unreadable_reference_fails_closed(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await services.store.create(
        "ideas", {"id": "idea-unreadable", "kind": "idea", "pillar": "governance"}
    )
    items, etag = await pillars(client)
    response = await save(client, items[:2], etag)
    assert response.status_code == 500
    assert response.json()["code"] == "invalid_document"
    with pytest.raises(InvalidStoredDocument):
        await pillar_ids_in_use(services.store)


async def test_pillar_ids_in_use() -> None:
    store = MemoryStore()
    assert await pillar_ids_in_use(store) == set()
    await seed_every_reference(store, "governance")
    await create_doc(store, sample_document(Idea, pillar=None))
    await create_doc(store, sample_document(Post, pillar="orphan"))
    assert await pillar_ids_in_use(store) == {"governance", "identity", "orphan"}
    assert await pillar_ids_in_use(store, among={"orphan", "agents"}) == {"orphan"}
    assert await pillar_ids_in_use(store, among=[]) == set()
    references = await pillar_references(store)
    assert references["identity"] == ["story"]
    assert references["orphan"] == ["post"]


@pytest.mark.parametrize(
    ("index", "field", "value", "loc"),
    [
        (0, "hashtags", ["AISecurity", "AIAgents", "aisecurity"], ["hashtags", 2]),
        (2, "hashtags", ["aiSecurity"], ["hashtags", 0]),
        (1, "vocabulary", ["SAML", "saml"], ["vocabulary", 1]),
        (1, "researchDomains", ["openid.net", "openid.net"], ["researchDomains", 1]),
        (1, "renames", {"Entra": "entra"}, ["renames"]),
        (
            1,
            "renames",
            {"Azure AD": "Microsoft Entra ID", "Microsoft Entra ID": "Entra"},
            ["renames"],
        ),
        (1, "renames", {"Old": "x" * 101}, ["renames"]),
        (1, "renames", {" ": "New"}, ["renames"]),
    ],
)
async def test_pillar_rules(
    client: httpx.AsyncClient, index: int, field: str, value: object, loc: list[object]
) -> None:
    items, etag = await pillars(client)
    items[index][field] = value
    response = await save(client, items, etag)
    assert response.status_code == 422, response.text
    assert ["body", "pillars", index, *loc] in [error["loc"] for error in response.json()["errors"]]


async def test_domain_rules_answer_422(client: httpx.AsyncClient) -> None:
    items, etag = await pillars(client)
    items[0]["targetShare"] = 0.9  # shares now add up to more than 1
    assert (await save(client, items, etag)).status_code == 422
    items, _ = await pillars(client)
    six = [{**items[0], "id": f"p{n}", "targetShare": 0.1} for n in range(6)]
    assert (await save(client, six, etag)).status_code == 422
    assert (await save(client, [items[0], items[0]], etag)).status_code == 422
