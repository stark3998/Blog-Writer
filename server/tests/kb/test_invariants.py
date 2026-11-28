"""Invariants for every kb route: the contract's routes and the seed review routes exist, every
route is owner-only, every write passes CSRF, and no GET changes state."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

from blogwriter.core.testing import (
    OTHER_OID,
    OWNER_OID,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import CONTAINERS, MemoryStore
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.seeds import KbSeed
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import FakeServices, make_app, make_services, sample_document
from blogwriter.kb import get_router

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}

CONTRACT_ROUTES = {
    *(
        (method, f"/api/kb/{name}")
        for name in (
            "pillars",
            "formats",
            "lanes",
            "brand",
            "blocklist",
            "policy",
            "cadence",
            "models",
            "autonomy",
        )
        for method in ("GET", "PUT")
    ),
    ("POST", "/api/kb/autonomy/pause"),
    ("POST", "/api/kb/autonomy/resume"),
    ("GET", "/api/kb/inputs"),
}
SEED_ROUTES = {
    ("GET", "/api/kb/seeds"),
    ("GET", "/api/kb/seeds/{seed_id}"),
    ("POST", "/api/kb/seeds/{seed_id}/approve"),
    ("POST", "/api/kb/seeds/{seed_id}/dismiss"),
    ("POST", "/api/kb/seeds/{seed_id}/restore"),
}
"""The owner's review of the v1 migration's keyword seeds (``kb.seeds``)."""
ROUTES = CONTRACT_ROUTES | SEED_ROUTES
SEED_ID = KbSeed.id_for("cloud security")


def concrete(path: str) -> str:
    return path.replace("{seed_id}", SEED_ID)


def kb_routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in get_router().routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }


def test_the_router_serves_exactly_the_contract_routes() -> None:
    assert kb_routes() == ROUTES


def test_the_openapi_document_builds() -> None:
    schema = make_app(make_services(), routers=[get_router()]).openapi()
    paths: dict[str, Any] = schema["paths"]
    assert {path for path in paths if path.startswith("/api/kb/")} == {path for _, path in ROUTES}
    models = schema["components"]["schemas"]["ModelsView"]["properties"]
    assert "webSearchUsdPer1k" in models


@pytest.mark.parametrize(("method", "path"), sorted(ROUTES))
async def test_every_route_is_owner_only(client: httpx.AsyncClient, method: str, path: str) -> None:
    path = concrete(path)
    anonymous = await client.request(method, path, json={})
    assert anonymous.status_code == 401
    stranger = await client.request(method, path, headers=principal_headers(OTHER_OID), json={})
    assert stranger.status_code == 403
    assert stranger.json()["code"] == "not_owner"


@pytest.mark.parametrize(("method", "path"), sorted(route for route in ROUTES if route[0] != "GET"))
async def test_every_write_passes_csrf(client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, concrete(path), headers=READ, json={})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


async def snapshot(store: MemoryStore) -> dict[str, list[tuple[str, str, str]]]:
    out: dict[str, list[tuple[str, str, str]]] = {}
    for container in CONTAINERS:
        docs = await store.find(container)
        out[container] = sorted((doc.id, doc.etag, repr(doc.body)) for doc in docs)
    return out


async def test_no_get_changes_state(client: httpx.AsyncClient, services: FakeServices) -> None:
    await create_doc(services.store, sample_document(StoryEntry, status="approved"))
    await create_doc(services.store, sample_document(PovEntry, status="approved"))
    await create_doc(services.store, sample_document(KbSeed))
    for state in ("never saved", "saved"):
        before = await snapshot(services.store)
        for method, path in sorted(ROUTES):
            if method == "GET":
                response = await client.get(concrete(path), headers=READ)
                assert response.status_code == 200, (state, path)
        assert await snapshot(services.store) == before, state
        await client.post("/api/kb/autonomy/pause", headers=WRITE)
        await client.put(
            "/api/kb/blocklist", headers={**WRITE, "If-None-Match": "*"}, json={"terms": []}
        )
