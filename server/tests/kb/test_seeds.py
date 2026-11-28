"""The review of the v1 migration's keyword seeds (``/api/kb/seeds``): list, approve into a pillar's
vocabulary through kb's own checks with ETags, dismiss and restore along the review machine, the
blocklist failing closed, crash and race safety, and a migration re-run that never undoes a
decision."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, override

import httpx
import pytest

from blogwriter.backups.migrate_v1 import (
    MemoryV1Source,
    MigrationOptions,
    MigrationReport,
    run_migration,
)
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)
from blogwriter.db import Doc, MemoryStore, StoreUnavailable
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, read_doc, upsert_doc
from blogwriter.domain.seeds import KbSeed
from blogwriter.domain.settings import Blocklist, BlocklistTerm, Pillar, PillarSettings
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    MemoryBlobStore,
    make_app,
    make_services,
    sample_document,
)
from blogwriter.jobs.testing import DelegatingStore
from blogwriter.kb import get_router
from blogwriter.kb.seeds import MAX_PILLAR_VOCABULARY, add_pillar_vocabulary

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(SETTINGS)}
CLIENT_NAME = "Zorblax"
"""A made-up client name on the blocklist: it must never leave the blocklist routes."""
TOPIC = "qzk-topic-marker"
KEYWORD = "qzk-keyword-marker"


async def save_blocklist(
    services: FakeServices, terms: Sequence[str] = (), *, configured: bool = True
) -> None:
    await upsert_doc(
        services.store,
        Blocklist(
            id=Blocklist.SINGLETON_ID,
            created_at=SAMPLE_NOW,
            updated_at=SAMPLE_NOW,
            configured=configured,
            terms=[
                BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
                for index, term in enumerate(terms)
            ],
        ),
    )


async def add_seed(
    services: FakeServices, v1_id: str = "cloud security", /, **fields: object
) -> str:
    doc = sample_document(KbSeed, id=KbSeed.id_for(v1_id), **fields)
    return (await create_doc(services.store, doc)).etag


def seed_id(v1_id: str = "cloud security") -> str:
    return KbSeed.id_for(v1_id)


async def approve(
    client: httpx.AsyncClient,
    etag: str,
    body: Mapping[str, object] | None = None,
    *,
    v1_id: str = "cloud security",
) -> httpx.Response:
    """``body=None`` sends no body at all (it is optional)."""
    return await client.post(
        f"/api/kb/seeds/{seed_id(v1_id)}/approve", headers={**WRITE, "If-Match": etag}, json=body
    )


async def decide(client: httpx.AsyncClient, action: str, etag: str) -> httpx.Response:
    return await client.post(
        f"/api/kb/seeds/{seed_id()}/{action}", headers={**WRITE, "If-Match": etag}
    )


async def pillars(client: httpx.AsyncClient) -> tuple[dict[str, Any], str | None]:
    body = (await client.get("/api/kb/pillars", headers=READ)).json()
    return {pillar["id"]: pillar for pillar in body["item"]["pillars"]}, body["etag"]


async def stored_seed(services: FakeServices) -> Stored[KbSeed]:
    return await read_doc(services.store, KbSeed, seed_id())


def problem_text(response: httpx.Response) -> str:
    return json.dumps(response.json())


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.extend(str(value) for value in record.__dict__.values())
    return "\n".join(parts)


# --------------------------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------------------------


async def test_seeds_are_listed_by_id_with_a_status_filter_and_pages(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    for v1_id in ("alpha", "beta", "gamma"):
        await add_seed(services, v1_id, topic=v1_id)
    await add_seed(services, "delta", topic="delta", status="rejected")
    everything = (await client.get("/api/kb/seeds", headers=READ)).json()
    ids = [entry["item"]["id"] for entry in everything["items"]]
    assert ids == sorted(KbSeed.id_for(name) for name in ("alpha", "beta", "gamma", "delta"))
    assert everything["nextCursor"] is None
    assert all(entry["etag"] for entry in everything["items"])

    queue = (await client.get("/api/kb/seeds?status=proposed", headers=READ)).json()
    assert {entry["item"]["topic"] for entry in queue["items"]} == {"alpha", "beta", "gamma"}

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        query = "?status=proposed&limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(f"/api/kb/seeds{query}", headers=READ)).json()
        seen.extend(entry["item"]["id"] for entry in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == sorted(KbSeed.id_for(name) for name in ("alpha", "beta", "gamma"))

    bad = await client.get("/api/kb/seeds?cursor=not*a*cursor", headers=READ)
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_cursor")

    one = await client.get(f"/api/kb/seeds/{KbSeed.id_for('beta')}", headers=READ)
    assert one.status_code == 200
    assert one.headers["ETag"] == one.json()["etag"]
    assert one.json()["item"]["status"] == "proposed"
    missing = await client.get(f"/api/kb/seeds/{KbSeed.id_for('nope')}", headers=READ)
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")


async def test_other_settings_documents_are_not_seeds(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    listed = (await client.get("/api/kb/seeds", headers=READ)).json()
    assert listed["items"] == []
    blocklist = await client.get(f"/api/kb/seeds/{Blocklist.SINGLETON_ID}", headers=READ)
    assert blocklist.status_code == 404


# --------------------------------------------------------------------------------------------
# Approving
# --------------------------------------------------------------------------------------------


async def test_approving_adds_the_keywords_to_the_suggested_pillar(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await save_blocklist(services)
    etag = await add_seed(
        services,
        topic=TOPIC,
        keywords=["zero trust", "conditional access", KEYWORD],
        suggested_pillar="identity",
    )
    before, pillars_etag = await pillars(client)
    assert pillars_etag is None  # never saved: the plan's defaults
    assert "Conditional Access" in before["identity"]["vocabulary"]

    response = await approve(client, etag)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert response.headers["ETag"] == response.json()["etag"] != etag
    assert item["status"] == "approved"
    assert item["appliedPillar"] == "identity"
    assert item["appliedKeywords"] == ["zero trust", "conditional access", KEYWORD]
    assert item["statusChangedAt"] == item["updatedAt"] > SAMPLE_NOW

    after, pillars_etag = await pillars(client)
    assert pillars_etag is not None  # created through kb's save
    vocabulary = after["identity"]["vocabulary"]
    assert vocabulary == [*before["identity"]["vocabulary"], "zero trust", KEYWORD]
    assert [term.casefold() for term in vocabulary].count("conditional access") == 1
    assert {key: after[key] for key in after if key != "identity"} == {
        key: before[key] for key in before if key != "identity"
    }
    logs = log_text(caplog)
    assert TOPIC not in logs
    assert KEYWORD not in logs
    approved = [r for r in caplog.records if r.getMessage() == "kb_seed_approved"]
    assert [(getattr(r, "doc_id", None), getattr(r, "count", None)) for r in approved] == [
        (seed_id(), 2)
    ]


async def test_the_owner_picks_the_pillar_and_the_keywords(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    etag = await add_seed(services, keywords=["zero trust", "SSO", "MFA"])
    current, _ = await pillars(client)
    saved = await client.put(
        "/api/kb/pillars",
        headers={**WRITE, "If-None-Match": "*"},
        json={"pillars": list(current.values())},
    )
    assert saved.status_code == 200
    old_etag = saved.headers["ETag"]

    response = await approve(client, etag, {"pillar": "agents", "keywords": ["MFA", "zero trust"]})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert (item["appliedPillar"], item["appliedKeywords"]) == ("agents", ["MFA", "zero trust"])
    after, new_etag = await pillars(client)
    assert after["agents"]["vocabulary"][-2:] == ["MFA", "zero trust"]
    assert "SSO" not in after["agents"]["vocabulary"]
    assert new_etag != old_etag
    stale = await client.put(
        "/api/kb/pillars",
        headers={**WRITE, "If-Match": old_etag},
        json={"pillars": list(current.values())},
    )
    assert (stale.status_code, stale.json()["code"]) == (412, "etag_mismatch")


async def test_an_empty_body_and_no_body_mean_the_defaults(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    first = await approve(client, await add_seed(services, "one", keywords=["a1"]), {}, v1_id="one")
    second = await approve(client, await add_seed(services, "two", keywords=["b2"]), v1_id="two")
    assert (first.status_code, second.status_code) == (200, 200)
    after, _ = await pillars(client)
    assert after["identity"]["vocabulary"][-2:] == ["a1", "b2"]


@pytest.mark.parametrize(
    ("fields", "body", "loc"),
    [
        ({}, {"keywords": ["not on the seed"]}, ["body", "keywords", 0]),
        ({}, {"pillar": "unknown-pillar"}, ["body", "pillar"]),
        ({"suggested_pillar": None}, None, ["body", "pillar"]),
        ({}, {"keywords": []}, ["body", "keywords"]),
        ({}, {"pillar": "Not A Slug"}, ["body", "pillar"]),
    ],
)
async def test_bad_choices_are_refused_before_anything_changes(
    client: httpx.AsyncClient,
    services: FakeServices,
    fields: dict[str, object],
    body: dict[str, object] | None,
    loc: list[object],
) -> None:
    await save_blocklist(services)
    etag = await add_seed(services, **fields)
    response = await approve(client, etag, body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_failed"
    assert loc in [error["loc"] for error in response.json()["errors"]]
    assert (await pillars(client))[1] is None
    assert (await stored_seed(services)).etag == etag


async def test_a_seed_without_keywords_cannot_be_approved(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    etag = await add_seed(services, keywords=[])
    response = await approve(client, etag)
    assert (response.status_code, response.json()["code"]) == (409, "seed_empty")
    dismissed = await decide(client, "dismiss", etag)
    assert dismissed.status_code == 200
    assert dismissed.json()["item"]["status"] == "rejected"


async def test_an_unconfigured_blocklist_refuses_every_approval(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    etag = await add_seed(services)
    response = await approve(client, etag)
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")
    await save_blocklist(services, configured=False)
    response = await approve(client, etag)
    assert (response.status_code, response.json()["code"]) == (409, "blocklist_not_configured")
    assert (await pillars(client))[1] is None
    assert (await stored_seed(services)).doc.status is ReviewStatus.PROPOSED


async def test_a_blocklisted_keyword_is_refused_without_quoting_it(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await save_blocklist(services, ["Ignored", CLIENT_NAME])
    etag = await add_seed(services, topic=TOPIC, keywords=["zero trust", f"{CLIENT_NAME} tenant"])
    response = await approve(client, etag)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "confidential_text"
    assert [(f["field"], f["ref"]) for f in problem["findings"]] == [("keywords.1", "t-2")]
    assert CLIENT_NAME not in problem_text(response)
    assert "tenant" not in problem_text(response)
    assert CLIENT_NAME not in log_text(caplog)
    assert TOPIC not in log_text(caplog)
    assert (await pillars(client))[1] is None
    assert (await stored_seed(services)).etag == etag
    # Leaving the blocklisted keyword out clears it.
    response = await approve(client, etag, {"keywords": ["zero trust"]})
    assert response.status_code == 200


async def test_existing_pillar_text_on_the_blocklist_refuses_the_save(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services, ["Okta"])  # the default identity pillar names it
    etag = await add_seed(services, keywords=["zero trust"])
    response = await approve(client, etag)
    assert response.status_code == 422
    fields = {finding["field"] for finding in response.json()["findings"]}
    assert fields
    assert all(field.startswith("pillars.") for field in fields)
    assert (await pillars(client))[1] is None
    assert (await stored_seed(services)).doc.status is ReviewStatus.PROPOSED


async def test_a_full_vocabulary_is_refused(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    settings = PillarSettings.default(SAMPLE_NOW)
    full = [f"term {index}" for index in range(MAX_PILLAR_VOCABULARY - 1)]
    pillars_list = [
        pillar.model_copy(update={"vocabulary": full}) if pillar.id == "identity" else pillar
        for pillar in settings.pillars
    ]
    saved = await create_doc(services.store, settings.model_copy(update={"pillars": pillars_list}))
    etag = await add_seed(services, keywords=["one more", "two more", "term 3"])
    response = await approve(client, etag)
    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["errors"]] == [["body", "keywords"]]
    assert "room for 1 more" in response.json()["errors"][0]["msg"]
    assert (await read_doc(services.store, PillarSettings, "pillars")).etag == saved.etag
    fits = await approve(client, etag, {"keywords": ["one more", "term 3"]})
    assert fits.status_code == 200
    after, _ = await pillars(client)
    assert len(after["identity"]["vocabulary"]) == MAX_PILLAR_VOCABULARY


def test_the_vocabulary_limit_is_the_domain_models() -> None:
    metadata: list[object] = list(Pillar.model_fields["vocabulary"].metadata)
    assert MAX_PILLAR_VOCABULARY in [getattr(item, "max_length", None) for item in metadata]


# --------------------------------------------------------------------------------------------
# Preconditions and the review machine
# --------------------------------------------------------------------------------------------


async def test_stale_or_missing_preconditions_change_nothing(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    etag = await add_seed(services)
    stale = await approve(client, '"stale"')
    assert (stale.status_code, stale.json()["code"]) == (412, "etag_mismatch")
    assert stale.json()["current"]["etag"] == etag
    assert stale.json()["current"]["status"] == "proposed"
    missing = await client.post(f"/api/kb/seeds/{seed_id()}/approve", headers=WRITE)
    assert (missing.status_code, missing.json()["code"]) == (428, "if_match_required")
    for action in ("dismiss", "restore"):
        refused = await decide(client, action, '"stale"')
        assert refused.status_code == 412
    assert (await pillars(client))[1] is None
    assert (await stored_seed(services)).etag == etag
    unknown = await client.post(
        f"/api/kb/seeds/{KbSeed.id_for('nope')}/approve", headers={**WRITE, "If-Match": etag}
    )
    assert unknown.status_code == 404


async def test_decisions_follow_the_review_machine(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    etag = await add_seed(services)
    restore_proposed = await decide(client, "restore", etag)
    assert restore_proposed.status_code == 409
    assert restore_proposed.json()["code"] == "invalid_transition"

    dismissed = await decide(client, "dismiss", etag)
    assert dismissed.status_code == 200
    assert dismissed.json()["item"]["status"] == "rejected"
    etag = dismissed.json()["etag"]
    refused = await approve(client, etag)
    assert refused.status_code == 409
    assert refused.json()["allowed"] == ["proposed"]
    assert (await pillars(client))[1] is None  # a refused approval applies nothing

    restored = await decide(client, "restore", etag)
    assert restored.json()["item"]["status"] == "proposed"
    approved = await approve(client, restored.json()["etag"])
    assert approved.status_code == 200
    etag = approved.json()["etag"]
    for action in ("dismiss", "restore"):
        again = await decide(client, action, etag)
        assert (again.status_code, again.json()["code"]) == (409, "invalid_transition")
    twice = await approve(client, etag)
    assert (twice.status_code, twice.json()["code"]) == (409, "invalid_transition")


# --------------------------------------------------------------------------------------------
# Crash and race safety
# --------------------------------------------------------------------------------------------


class FailingSeedWrite(DelegatingStore):
    """Loses the first write of a kb seed, after the pillar settings were saved (a crash)."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.failed = False

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        if body.get("kind") == "kb_seed" and not self.failed:
            self.failed = True
            raise StoreUnavailable("lost")
        return await super().replace(container, body, if_match=if_match)


async def test_a_crash_between_the_settings_and_the_seed_is_safe() -> None:
    store = FailingSeedWrite()
    services = make_services(store=store)
    await save_blocklist(services)
    etag = await add_seed(services, keywords=["zero trust", "passkey rollout"])
    async with asgi_client(make_app(services, routers=[get_router()])) as client:
        crashed = await approve(client, etag)
        assert crashed.status_code == 503
        assert (await stored_seed(services)).doc.status is ReviewStatus.PROPOSED
        first, settings_etag = await pillars(client)
        assert first["identity"]["vocabulary"][-2:] == ["zero trust", "passkey rollout"]

        again = await approve(client, etag)
        assert again.status_code == 200, again.text
        second, again_etag = await pillars(client)
    assert second == first  # nothing added twice, nothing written
    assert again_etag == settings_etag


class ConcurrentPillarSave(DelegatingStore):
    """Saves the pillars once from "another tab" right before the approval's own write."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.raced = False

    async def _race(self, body: Mapping[str, Any]) -> None:
        if self.raced or body.get("kind") != "pillars":
            return
        self.raced = True
        settings = PillarSettings.default(SAMPLE_NOW)
        pillars = [
            pillar.model_copy(update={"description": "Edited in another tab."})
            if pillar.id == "governance"
            else pillar
            for pillar in settings.pillars
        ]
        await self.inner.create(
            "settings", settings.model_copy(update={"pillars": pillars}).to_document()
        )

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        await self._race(body)
        return await super().create(container, body)

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        await self._race(body)
        return await super().replace(container, body, if_match=if_match)


async def test_a_concurrent_pillar_save_is_kept_and_the_keywords_still_land() -> None:
    store = ConcurrentPillarSave()
    services = make_services(store=store)
    await save_blocklist(services)
    etag = await add_seed(services, keywords=["zero trust"])
    async with asgi_client(make_app(services, routers=[get_router()])) as client:
        response = await approve(client, etag)
        assert response.status_code == 200, response.text
        after, _ = await pillars(client)
    assert store.raced
    assert after["governance"]["description"] == "Edited in another tab."
    assert after["identity"]["vocabulary"][-1] == "zero trust"


async def test_add_pillar_vocabulary_is_idempotent(services: FakeServices) -> None:
    await save_blocklist(services)
    blocklist = (await read_doc(services.store, Blocklist, Blocklist.SINGLETON_ID)).doc
    added = await add_pillar_vocabulary(
        services.store,
        "agents",
        ["Agent ID", "agent id", "A2A"],
        blocklist=blocklist,
        now=SAMPLE_NOW,
    )
    assert added == 2
    first = await read_doc(services.store, PillarSettings, "pillars")
    again = await add_pillar_vocabulary(
        services.store, "agents", ["AGENT ID", "a2a"], blocklist=blocklist, now=SAMPLE_NOW
    )
    assert again == 0
    assert (await read_doc(services.store, PillarSettings, "pillars")).etag == first.etag
    agents = first.doc.get("agents")
    assert agents is not None
    assert agents.vocabulary[-2:] == ["Agent ID", "A2A"]


# --------------------------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------------------------


def v1_keywords(keywords: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {
        "keywords": [
            {
                "id": "cloud security",
                "topic": "cloud security",
                "keywords": keywords,
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
    client: httpx.AsyncClient, services: FakeServices, action: str
) -> None:
    await save_blocklist(services)
    first = await migrate(services, v1_keywords(["entra", "zero trust"]))
    assert first.ok, first.failures
    listed = (await client.get("/api/kb/seeds?status=proposed", headers=READ)).json()
    [entry] = listed["items"]
    assert entry["item"]["id"] == seed_id()
    assert entry["item"]["suggestedPillar"] == "identity"
    # The v1 stamp is the clock's time: the decision must still move updatedAt forward.
    assert entry["item"]["updatedAt"] == to_iso(services.clock.now()) == SAMPLE_NOW

    if action == "approve":
        decided = await approve(client, entry["etag"])
    else:
        decided = await decide(client, action, entry["etag"])
    assert decided.status_code == 200, decided.text
    decided_item = decided.json()["item"]
    assert decided_item["updatedAt"] != decided_item["migratedFrom"]["updatedAt"]

    rerun = await migrate(services, v1_keywords(["entra", "zero trust", "passkeys"]))
    assert rerun.containers["keywords"].skipped == {"changed_in_v2": 1}
    after = (await client.get(f"/api/kb/seeds/{seed_id()}", headers=READ)).json()
    assert after["item"] == decided_item
    assert after["etag"] == decided.json()["etag"]
