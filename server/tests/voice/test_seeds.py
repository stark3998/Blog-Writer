"""The review of the v1 migration's voice seeds (``/api/voice/seeds``): list, approve into the
corpus through ``add_sample`` (scored, blocklist first) with ETags, dismiss and restore along the
review machine, crash safety, and a migration re-run that never undoes a decision."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from blogwriter.backups.migrate_v1 import (
    MemoryV1Source,
    MigrationOptions,
    MigrationReport,
    run_migration,
)
from blogwriter.core.testing import OTHER_OID, principal_headers
from blogwriter.db import Filter
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.docstore import create_doc, find_docs, read_doc
from blogwriter.domain.seeds import VoiceSeed
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.testing import FakeServices, MemoryBlobStore, sample_document
from blogwriter.domain.voice import VoiceSample
from blogwriter.voice.corpus import add_sample
from blogwriter.voice.testing import LINKEDIN_POSTS, seed_policy

CURLY = "\u2019"
TEXT = LINKEDIN_POSTS[0]
MARKER = "qzv-seed-marker"
"""Profile text that must never reach a log or a problem."""
CLIENT = "Zorblax"
"""A made-up client name on the blocklist."""


async def add_seed(services: FakeServices, v1_id: str = "vp-1", /, **fields: object) -> str:
    doc = sample_document(VoiceSeed, id=VoiceSeed.id_for(v1_id), **fields)
    return (await create_doc(services.store, doc)).etag


def seed_id(v1_id: str = "vp-1") -> str:
    return VoiceSeed.id_for(v1_id)


async def approve(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    etag: str,
    body: Mapping[str, object] | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/voice/seeds/{seed_id()}/approve", headers={**headers, "If-Match": etag}, json=body
    )


async def decide(
    client: httpx.AsyncClient, headers: dict[str, str], action: str, etag: str
) -> httpx.Response:
    return await client.post(
        f"/api/voice/seeds/{seed_id()}/{action}", headers={**headers, "If-Match": etag}
    )


async def samples(services: FakeServices) -> list[VoiceSample]:
    return [stored.doc for stored in await find_docs(services.store, VoiceSample)]


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.extend(str(value) for value in record.__dict__.values())
    return "\n".join(parts)


# --------------------------------------------------------------------------------------------
# Owner only, CSRF, reads that never write
# --------------------------------------------------------------------------------------------

SEED_ROUTES = [
    ("GET", "/api/voice/seeds"),
    ("GET", f"/api/voice/seeds/{seed_id()}"),
    ("POST", f"/api/voice/seeds/{seed_id()}/approve"),
    ("POST", f"/api/voice/seeds/{seed_id()}/dismiss"),
    ("POST", f"/api/voice/seeds/{seed_id()}/restore"),
]


@pytest.mark.parametrize(("method", "path"), SEED_ROUTES)
async def test_every_seed_route_is_owner_only_and_every_write_passes_csrf(
    client: httpx.AsyncClient, read_headers: dict[str, str], method: str, path: str
) -> None:
    anonymous = await client.request(method, path, json={})
    assert anonymous.status_code == 401
    stranger = await client.request(method, path, headers=principal_headers(OTHER_OID), json={})
    assert (stranger.status_code, stranger.json()["code"]) == (403, "not_owner")
    if method != "GET":
        forged = await client.request(method, path, headers=read_headers, json={})
        assert (forged.status_code, forged.json()["code"]) == (403, "csrf_failed")


async def test_reading_seeds_writes_nothing(
    client: httpx.AsyncClient, services: FakeServices, read_headers: dict[str, str]
) -> None:
    await add_seed(services, text=TEXT)
    before = sorted((doc.id, doc.etag) for doc in await services.store.find("stories"))
    for method, path in SEED_ROUTES:
        if method == "GET":
            assert (await client.get(path, headers=read_headers)).status_code == 200
    assert sorted((doc.id, doc.etag) for doc in await services.store.find("stories")) == before


# --------------------------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------------------------


async def test_seeds_are_listed_by_id_with_a_status_filter_and_pages(
    client: httpx.AsyncClient, services: FakeServices, read_headers: dict[str, str]
) -> None:
    for v1_id in ("vp-1", "vp-2", "vp-3"):
        await add_seed(services, v1_id, name=v1_id)
    await add_seed(services, "vp-4", name="vp-4", status="rejected")
    await create_doc(services.store, sample_document(VoiceSample))  # same container, other kind
    everything = (await client.get("/api/voice/seeds", headers=read_headers)).json()
    expected = sorted(VoiceSeed.id_for(f"vp-{index}") for index in range(1, 5))
    assert [entry["item"]["id"] for entry in everything["items"]] == expected
    queue = (await client.get("/api/voice/seeds?status=proposed", headers=read_headers)).json()
    assert {entry["item"]["name"] for entry in queue["items"]} == {"vp-1", "vp-2", "vp-3"}

    first = (await client.get("/api/voice/seeds?limit=3", headers=read_headers)).json()
    assert len(first["items"]) == 3
    cursor = first["nextCursor"]
    assert cursor is not None
    rest = await client.get(f"/api/voice/seeds?limit=3&cursor={cursor}", headers=read_headers)
    assert [entry["item"]["id"] for entry in rest.json()["items"]] == expected[3:]
    assert rest.json()["nextCursor"] is None

    one = await client.get(f"/api/voice/seeds/{seed_id('vp-2')}", headers=read_headers)
    assert one.status_code == 200
    assert one.headers["ETag"] == one.json()["etag"]
    sample_id = (await samples(services))[0].id
    other_kind = await client.get(f"/api/voice/seeds/{sample_id}", headers=read_headers)
    assert other_kind.status_code == 404


# --------------------------------------------------------------------------------------------
# Approving
# --------------------------------------------------------------------------------------------


async def test_approving_adds_the_profile_text_to_the_corpus(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await seed_policy(services)
    text = f"Let{CURLY}s dive in.\r\n\r\n{TEXT}\n{MARKER}"
    etag = await add_seed(services, name="Default voice", text=f"  {text}  ")
    response = await approve(client, write_headers, etag)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert response.headers["ETag"] == response.json()["etag"] != etag
    assert item["status"] == "approved"
    assert item["statusChangedAt"] == item["updatedAt"]

    [sample] = await samples(services)
    assert item["sampleId"] == sample.id
    assert sample.source_ref == seed_id()
    assert sample.source_type == "portfolio_post"
    assert sample.title == "Default voice"
    assert sample.owner_unassisted is False
    assert sample.weight == 1.0
    assert sample.text == f"Let{CURLY}s dive in.\n\n{TEXT}\n{MARKER}"
    assert sample.content_sha256 == sha256_hex(sample.text)
    assert [flag.code for flag in sample.flags] == ["ai-tell"]  # scored like any sample
    assert sample.exemplar_eligible is False
    assert MARKER not in log_text(caplog)
    approved = [r for r in caplog.records if r.getMessage() == "voice_seed_approved"]
    assert [getattr(r, "doc_id", None) for r in approved] == [seed_id()]


async def test_the_owner_names_the_source_title_and_pillar(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=TEXT)
    body = {
        "sourceType": "linkedin_post",
        "title": None,
        "pillar": "identity",
        "ownerUnassisted": True,
    }
    response = await approve(client, write_headers, etag, body)
    assert response.status_code == 200, response.text
    [sample] = await samples(services)
    assert (sample.source_type, sample.title, sample.pillar) == ("linkedin_post", None, "identity")
    assert sample.weight == 4.0
    assert sample.exemplar_eligible is True
    guide = (await client.get("/api/voice/guide", headers=read_headers)).json()
    assert guide["linkedinSamples"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"sourceType": "interview_answer"},  # pipeline sources are not owner input
        {"sourceType": "final_post"},
        {"pillar": "Not A Slug"},
        {"title": "x" * 201},
        {"unknown": True},
    ],
)
async def test_bad_choices_are_refused_before_anything_changes(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    body: dict[str, object],
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=TEXT)
    response = await approve(client, write_headers, etag, body)
    assert (response.status_code, response.json()["code"]) == (422, "validation_failed")
    assert await samples(services) == []
    assert (await read_doc(services.store, VoiceSeed, seed_id())).etag == etag


@pytest.mark.parametrize("text", ["", "   \n\t  ", "\x00\x01"])
async def test_a_profile_without_text_cannot_be_approved(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str], text: str
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=text)
    response = await approve(client, write_headers, etag)
    assert (response.status_code, response.json()["code"]) == (409, "seed_empty")
    assert await samples(services) == []
    dismissed = await decide(client, write_headers, "dismiss", etag)
    assert dismissed.json()["item"]["status"] == "rejected"


async def test_an_unconfigured_blocklist_refuses_every_approval(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    etag = await add_seed(services, text=TEXT)
    response = await approve(client, write_headers, etag)
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")
    await seed_policy(services, blocklist_configured=False)
    response = await approve(client, write_headers, etag)
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")
    assert await samples(services) == []


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"text": f"{TEXT} Built for {CLIENT}. {MARKER}"}, "text"),
        ({"text": TEXT, "name": f"{CLIENT} voice"}, "title"),
    ],
)
async def test_blocklisted_text_is_refused_without_quoting_it(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    fields: dict[str, object],
    field: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    await seed_policy(services, terms=[CLIENT])
    etag = await add_seed(services, **fields)
    response = await approve(client, write_headers, etag)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "confidential_text"
    assert [(finding["field"], finding["ref"]) for finding in problem["findings"]] == [
        (field, "t-1")
    ]
    for secret in (CLIENT, MARKER):
        assert secret not in json.dumps(problem)
        assert secret not in log_text(caplog)
    assert await samples(services) == []
    assert (
        await read_doc(services.store, VoiceSeed, seed_id())
    ).doc.status is ReviewStatus.PROPOSED
    if field == "title":  # a title of the owner's own clears it
        retitled = await approve(client, write_headers, etag, {"title": "Old blog voice"})
        assert retitled.status_code == 200


# --------------------------------------------------------------------------------------------
# Preconditions, the review machine, crash safety
# --------------------------------------------------------------------------------------------


async def test_stale_or_missing_preconditions_change_nothing(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=TEXT)
    stale = await approve(client, write_headers, '"stale"')
    assert (stale.status_code, stale.json()["code"]) == (412, "etag_mismatch")
    assert stale.json()["current"]["etag"] == etag
    missing = await client.post(f"/api/voice/seeds/{seed_id()}/dismiss", headers=write_headers)
    assert (missing.status_code, missing.json()["code"]) == (428, "if_match_required")
    assert await samples(services) == []


async def test_decisions_follow_the_review_machine(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=TEXT)
    dismissed = await decide(client, write_headers, "dismiss", etag)
    assert dismissed.json()["item"]["status"] == "rejected"
    refused = await approve(client, write_headers, dismissed.json()["etag"])
    assert (refused.status_code, refused.json()["code"]) == (409, "invalid_transition")
    assert await samples(services) == []
    restored = await decide(client, write_headers, "restore", dismissed.json()["etag"])
    assert restored.json()["item"]["status"] == "proposed"
    approved = await approve(client, write_headers, restored.json()["etag"])
    assert approved.status_code == 200
    again = await approve(client, write_headers, approved.json()["etag"])
    assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    dismiss_approved = await decide(client, write_headers, "dismiss", approved.json()["etag"])
    assert dismiss_approved.status_code == 409
    assert len(await samples(services)) == 1


async def test_a_sample_left_by_a_crashed_approval_is_reused(
    client: httpx.AsyncClient, services: FakeServices, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    etag = await add_seed(services, text=TEXT)
    # The first approval wrote the sample, then the process died before the seed was marked.
    earlier = await add_sample(
        services.store,
        source_type="portfolio_post",
        text=TEXT,
        now=services.clock.now(),
        slop=services.slop,
        source_ref=seed_id(),
    )
    response = await approve(client, write_headers, etag, {"sourceType": "linkedin_post"})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["sampleId"] == earlier.doc.id
    found = await find_docs(services.store, VoiceSample, [Filter("sourceRef", "==", seed_id())])
    assert [stored.doc.id for stored in found] == [earlier.doc.id]


# --------------------------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------------------------


def v1_profile(sample_text: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "voice-profiles": [
            {
                "id": "vp-1",
                "userId": "owner-user-id",
                "name": "Default voice",
                "tone": "direct",
                "styleNotes": "Short sentences.",
                "sampleText": sample_text,
                "isDefault": True,
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-11-26T12:00:00+00:00",  # the fake clock's time
            }
        ]
    }


async def migrate(services: FakeServices, data: dict[str, list[dict[str, Any]]]) -> MigrationReport:
    return await run_migration(
        MemoryV1Source(data),
        services.store,
        MemoryBlobStore(),
        options=MigrationOptions(apply=True),
        now=datetime(2026, 11, 26, 13, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize("action", ["approve", "dismiss"])
async def test_a_rerun_of_the_migration_never_undoes_a_decision(
    client: httpx.AsyncClient,
    services: FakeServices,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
    action: str,
) -> None:
    await seed_policy(services)
    first = await migrate(services, v1_profile(TEXT))
    assert first.ok, first.failures
    queue = (await client.get("/api/voice/seeds?status=proposed", headers=read_headers)).json()
    [entry] = queue["items"]
    assert entry["item"]["id"] == seed_id()
    assert "userId" not in entry["item"]
    if action == "approve":
        decided = await approve(client, write_headers, entry["etag"])
    else:
        decided = await decide(client, write_headers, action, entry["etag"])
    assert decided.status_code == 200, decided.text
    item = decided.json()["item"]
    assert item["updatedAt"] > item["migratedFrom"]["updatedAt"]

    rerun = await migrate(services, v1_profile(f"{TEXT} Changed in v1."))
    assert rerun.containers["voice-profiles"].skipped == {"changed_in_v2": 1}
    after = (await client.get(f"/api/voice/seeds/{seed_id()}", headers=read_headers)).json()
    assert after["item"] == item
    assert len(await samples(services)) == (1 if action == "approve" else 0)
