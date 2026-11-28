"""The owner-input checklist, counted against seeded documents."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.core.testing import OWNER_OID, principal_headers
from blogwriter.db import MemoryStore
from blogwriter.domain.analytics import EvalResult
from blogwriter.domain.base import Document, parse_iso
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.engagement import WatchEntry
from blogwriter.domain.settings import Blocklist, BrandKit, EmployerPolicy, LinkedInConnection
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.domain.voice import VoiceSample
from blogwriter.kb import OwnerInputs, owner_inputs

READ = principal_headers(OWNER_OID)
NOW = parse_iso(SAMPLE_NOW)
CODES = [
    "linkedin_samples",
    "stories_approved",
    "povs_per_pillar",
    "blocklist",
    "employer_policy",
    "ai_use_confirmed",
    "brand_kit",
    "linkedin_connected",
    "watchlist",
    "eval_labels",
]


def summary(inputs: OwnerInputs) -> dict[str, tuple[bool, int | None, int | None]]:
    return {item.code: (item.done, item.have, item.need) for item in inputs.items}


async def add[D: Document](store: MemoryStore, cls: type[D], count: int, **fields: Any) -> None:
    for _ in range(count):
        await create_doc(store, sample_document(cls, **fields))


async def test_nothing_provided_yet() -> None:
    inputs = await owner_inputs(MemoryStore(), now=NOW)
    assert [item.code for item in inputs.items] == CODES
    assert summary(inputs) == {
        "linkedin_samples": (False, 0, 5),
        "stories_approved": (False, 0, 10),
        "povs_per_pillar": (False, 0, 3),
        "blocklist": (False, None, None),
        "employer_policy": (False, None, None),
        "ai_use_confirmed": (False, None, None),
        "brand_kit": (False, None, None),
        "linkedin_connected": (False, None, None),
        "watchlist": (False, 0, 5),
        "eval_labels": (False, 0, 12),
    }
    for item in inputs.items:
        assert item.href.startswith("/")
        assert item.label


async def test_counts_against_seeded_documents(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    store = services.store
    await add(store, VoiceSample, 5, source_type="linkedin_post")
    await add(store, VoiceSample, 1, source_type="linkedin_post", status="excluded")
    await add(store, VoiceSample, 3, source_type="portfolio_post")
    await add(store, StoryEntry, 10, status="approved")
    await add(store, StoryEntry, 4, status="proposed")
    await add(store, PovEntry, 3, status="approved", pillar="agents")
    await add(store, PovEntry, 4, status="approved", pillar="identity")
    await add(store, PovEntry, 2, status="approved", pillar="governance")
    await add(store, PovEntry, 2, status="proposed", pillar="governance")
    await add(store, WatchEntry, 4, active=True)
    await add(store, WatchEntry, 3, active=False)
    for topic in range(11):
        await add(store, EvalResult, 1, topic_id=f"evt-{topic:028x}", owner_label="as_is")
    await add(store, EvalResult, 1, topic_id=f"evt-{0:028x}", owner_label="reject")
    await add(store, EvalResult, 1, topic_id=f"evt-{11:028x}")  # not labelled yet
    await create_doc(store, sample_document(Blocklist, configured=True))
    await create_doc(store, sample_document(EmployerPolicy, configured=True))
    await create_doc(store, sample_document(BrandKit, authorName="A. Author"))
    await create_doc(
        store,
        sample_document(LinkedInConnection, status="needs_reconnect", connectedAt=SAMPLE_NOW),
    )

    inputs = await owner_inputs(store, now=NOW)
    assert summary(inputs) == {
        "linkedin_samples": (True, 5, 5),
        "stories_approved": (True, 10, 10),
        "povs_per_pillar": (False, 2, 3),
        "blocklist": (True, None, None),  # saved empty: configured, so done
        "employer_policy": (True, None, None),
        "ai_use_confirmed": (False, None, None),
        "brand_kit": (True, None, None),
        "linkedin_connected": (False, None, None),
        "watchlist": (False, 4, 5),
        "eval_labels": (False, 11, 12),
    }

    response = await client.get("/api/kb/inputs", headers=READ)
    assert response.status_code == 200
    assert OwnerInputs.model_validate(response.json()) == inputs


async def test_every_item_done(services: FakeServices) -> None:
    store = services.store
    await add(store, VoiceSample, 6, source_type="linkedin_post")
    await add(store, StoryEntry, 12, status="approved")
    for pillar in ("agents", "identity", "governance"):
        await add(store, PovEntry, 3, status="approved", pillar=pillar)
    await add(store, WatchEntry, 5)
    for topic in range(12):
        await add(store, EvalResult, 1, topic_id=f"evt-{topic:028x}", owner_label="needs_edits")
    await create_doc(store, sample_document(Blocklist, configured=True))
    await create_doc(store, sample_document(EmployerPolicy, configured=True, aiUseConfirmed=True))
    await create_doc(store, sample_document(BrandKit, authorName="A. Author"))
    await create_doc(
        store, sample_document(LinkedInConnection, status="connected", connectedAt=SAMPLE_NOW)
    )
    inputs = await owner_inputs(store, now=NOW)
    assert all(item.done for item in inputs.items)


async def test_an_unreadable_settings_document_counts_as_open(
    client: httpx.AsyncClient, services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    await services.store.create(
        "settings", {"id": "blocklist", "kind": "blocklist", "terms": "not a list"}
    )
    response = await client.get("/api/kb/inputs", headers=READ)
    assert response.status_code == 200
    items = {item["code"]: item["done"] for item in response.json()["items"]}
    assert items["blocklist"] is False
    assert any(r.getMessage() == "kb_unreadable_settings" for r in caplog.records)
