"""The singleton save rules every ``PUT /api/kb/*`` shares: defaults with ``etag: null``, first
save with ``If-None-Match: *``, races and stale etags as 412 with the current etag, 428 without a
precondition, request bodies that cannot set server fields, and 422 for values out of range."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.settings import CadenceSettings, LaneSettings
from blogwriter.domain.testing import FakeServices
from blogwriter.kb.models import (
    AutonomyIn,
    BlocklistIn,
    BrandIn,
    CadenceIn,
    FormatPacksIn,
    LanesIn,
    ModelPreferencesIn,
    PillarsIn,
    PolicyIn,
)

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CREATE = {**WRITE, "If-None-Match": "*"}

# path, request model, where the document sits in the GET body (None: Versioned at the top)
ROUTES: list[tuple[str, type[BaseModel], str | None]] = [
    ("pillars", PillarsIn, None),
    ("formats", FormatPacksIn, None),
    ("lanes", LanesIn, None),
    ("brand", BrandIn, None),
    ("blocklist", BlocklistIn, None),
    ("policy", PolicyIn, None),
    ("cadence", CadenceIn, None),
    ("models", ModelPreferencesIn, "preferences"),
    ("autonomy", AutonomyIn, "settings"),
]
IDS = [route[0] for route in ROUTES]


def body_for(model: type[BaseModel], item: Mapping[str, Any]) -> dict[str, Any]:
    """The request body that saves ``item`` unchanged (the request model's fields only)."""
    return {
        field.alias or name: item[field.alias or name]
        for name, field in model.model_fields.items()
        if (field.alias or name) in item
    }


async def read(
    client: httpx.AsyncClient, path: str, inner: str | None
) -> tuple[dict[str, Any], str | None]:
    response = await client.get(f"/api/kb/{path}", headers=READ)
    assert response.status_code == 200
    body = response.json()
    versioned = body if inner is None else body[inner]
    return versioned["item"], versioned["etag"]


def etag_of(response: httpx.Response) -> str:
    etag = response.headers.get("etag")
    assert etag, response.text
    return etag


@pytest.mark.parametrize(("path", "model", "inner"), ROUTES, ids=IDS)
async def test_save_cycle(
    client: httpx.AsyncClient, path: str, model: type[BaseModel], inner: str | None
) -> None:
    item, etag = await read(client, path, inner)
    assert etag is None
    body = body_for(model, item)

    created = await client.put(f"/api/kb/{path}", headers=CREATE, json=body)
    assert created.status_code == 200, created.text
    first = etag_of(created)
    item, etag = await read(client, path, inner)
    assert etag == first

    again = await client.put(f"/api/kb/{path}", headers=CREATE, json=body)
    assert again.status_code == 412
    assert again.json()["code"] == "etag_mismatch"
    assert again.json()["current"]["etag"] == first

    replaced = await client.put(
        f"/api/kb/{path}", headers={**WRITE, "If-Match": first}, json=body_for(model, item)
    )
    assert replaced.status_code == 200, replaced.text
    second = etag_of(replaced)
    assert second != first

    stale = await client.put(f"/api/kb/{path}", headers={**WRITE, "If-Match": first}, json=body)
    assert stale.status_code == 412
    problem = stale.json()
    assert problem["code"] == "etag_mismatch"
    assert problem["current"]["etag"] == second
    assert set(problem["current"]) >= {"etag", "updatedAt"}


@pytest.mark.parametrize(("path", "model", "inner"), ROUTES, ids=IDS)
async def test_preconditions(
    client: httpx.AsyncClient, path: str, model: type[BaseModel], inner: str | None
) -> None:
    item, _ = await read(client, path, inner)
    body = body_for(model, item)
    missing = await client.put(f"/api/kb/{path}", headers=WRITE, json=body)
    assert missing.status_code == 428
    assert missing.json()["code"] == "if_match_required"
    never_saved = await client.put(
        f"/api/kb/{path}", headers={**WRITE, "If-Match": '"made-up"'}, json=body
    )
    assert never_saved.status_code == 412
    assert "current" not in never_saved.json()


@pytest.mark.parametrize(("path", "model", "inner"), ROUTES, ids=IDS)
async def test_first_save_race_has_one_winner(
    client: httpx.AsyncClient, path: str, model: type[BaseModel], inner: str | None
) -> None:
    item, _ = await read(client, path, inner)
    body = body_for(model, item)
    results = await asyncio.gather(
        client.put(f"/api/kb/{path}", headers=CREATE, json=body),
        client.put(f"/api/kb/{path}", headers=CREATE, json=body),
    )
    assert sorted(result.status_code for result in results) == [200, 412]


@pytest.mark.parametrize(("path", "model", "inner"), ROUTES, ids=IDS)
@pytest.mark.parametrize("field", ["id", "createdAt", "configured", "kind", "calibration"])
async def test_server_fields_cannot_be_sent(
    client: httpx.AsyncClient,
    path: str,
    model: type[BaseModel],
    inner: str | None,
    field: str,
) -> None:
    item, _ = await read(client, path, inner)
    body = {**body_for(model, item), field: item.get(field, "x")}
    response = await client.put(f"/api/kb/{path}", headers=CREATE, json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    _, etag = await read(client, path, inner)
    assert etag is None


def test_lane_and_cadence_requests_cover_every_editable_field() -> None:
    server = {"id", "schema_version", "created_at", "updated_at"}
    assert set(LanesIn.model_fields) == set(LaneSettings.model_fields) - server
    assert set(CadenceIn.model_fields) == set(CadenceSettings.model_fields) - server


async def test_validation_failures_point_at_the_field(client: httpx.AsyncClient) -> None:
    item, _ = await read(client, "formats", None)
    packs = item["packs"]
    packs[0] = {**packs[0], "minWords": 3000}  # above maxWords: the domain model refuses it
    response = await client.put("/api/kb/formats", headers=CREATE, json={"packs": packs})
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors[0]["loc"][:2] == ["body", "packs"]
    assert "3000" not in response.text  # values are never echoed

    item, _ = await read(client, "models", "preferences")
    body = {**body_for(ModelPreferencesIn, item), "noveltyMaxSimilarity": 1.5}
    response = await client.put("/api/kb/models", headers=CREATE, json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "noveltyMaxSimilarity"]


async def test_saved_documents_keep_unknown_stored_fields(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    """A field a newer revision wrote survives a save by this one (contract 1.2)."""
    item, _ = await read(client, "lanes", None)
    created = await client.put("/api/kb/lanes", headers=CREATE, json=body_for(LanesIn, item))
    raw = await services.store.read("settings", "lanes")
    await services.store.replace(
        "settings", {**raw.body, "futureField": {"kept": True}}, if_match=raw.etag
    )
    item, etag = await read(client, "lanes", None)
    assert etag is not None
    assert etag != etag_of(created)
    body = {**body_for(LanesIn, item), "hookVariants": 4}
    saved = await client.put("/api/kb/lanes", headers={**WRITE, "If-Match": etag}, json=body)
    assert saved.status_code == 200
    stored = await services.store.read("settings", "lanes")
    assert stored.body["futureField"] == {"kept": True}
    assert stored.body["hookVariants"] == 4
