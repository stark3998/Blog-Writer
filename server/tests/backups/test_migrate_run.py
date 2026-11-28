"""The migration run end to end: MemoryV1Source -> MemoryStore + in-memory Blob storage."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from blogwriter.backups.migrate_v1 import (
    EXCLUDED_CONTAINERS,
    MemoryV1Source,
    MigrationError,
    MigrationOptions,
    MigrationReport,
    run_migration,
)
from blogwriter.backups.migrate_v1.seeds import KbSeed, VoiceSeed
from blogwriter.db import Filter, MemoryStore
from blogwriter.domain.articles import Article
from blogwriter.domain.base import derived_id
from blogwriter.domain.docstore import create_doc, find_docs, save_singleton
from blogwriter.domain.settings import Pillar, PillarSettings
from blogwriter.domain.signals import Signal, Source
from blogwriter.domain.testing import MemoryBlobStore, sample_document
from blogwriter.publish.contract import validate_published_doc

type V1Data = dict[str, list[dict[str, Any]]]

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
DRAFT_ID = "0f1e2d3c-1111-2222-3333-444455556666"
SESSION_CONTAINERS = {
    "linkedin-sessions",
    "linkedin-oauth-states",
    "twitter-sessions",
    "medium-sessions",
}


async def migrate(
    data: V1Data,
    store: MemoryStore,
    blobs: MemoryBlobStore | None,
    *,
    now: datetime = NOW,
    **options: Any,
) -> tuple[MigrationReport, MemoryV1Source]:
    source = MemoryV1Source(data)
    report = await run_migration(source, store, blobs, options=MigrationOptions(**options), now=now)
    return report, source


def blob_names(blobs: MemoryBlobStore, container: str) -> set[str]:
    return {name for (where, name) in blobs.blobs if where == container}


async def test_a_dry_run_writes_nothing(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, source = await migrate(v1_data, store, blobs)
    assert report.ok, report.failures
    assert blobs.blobs == {}
    for container in ("published-blogs", "articles", "sources", "signals", "settings"):
        assert await store.find(container) == []
    assert report.containers["published-blogs"].written == 2  # would write
    assert report.containers["published-blogs"].converted == 2
    assert set(report.excluded) == SESSION_CONTAINERS
    assert SESSION_CONTAINERS.isdisjoint(source.iterated)
    assert "dry run" in report.lines()[0]


async def test_apply_exports_every_container_but_sessions(
    v1_data: V1Data, canaries: tuple[str, str]
) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, source = await migrate(v1_data, store, blobs, apply=True)
    assert report.ok, report.failures
    expected = {
        f"v1-export/2026-11-26/{name}.jsonl" for name in v1_data if name not in SESSION_CONTAINERS
    }
    assert blob_names(blobs, "backups") == expected
    assert SESSION_CONTAINERS.isdisjoint(source.iterated)
    assert SESSION_CONTAINERS <= EXCLUDED_CONTAINERS
    token, _ = canaries
    for name in expected:
        data = await blobs.get("backups", name)
        assert token.encode() not in data
        lines = [json.loads(line) for line in data.decode("ascii").splitlines()]
        ids = [line["id"] for line in lines]
        assert ids == sorted(ids)
        for line in lines:
            assert not {"_rid", "_self", "_etag", "_attachments", "_lsn"} & set(line)
    published = await blobs.get("backups", "v1-export/2026-11-26/published-blogs.jsonl")
    first = json.loads(published.decode("ascii").splitlines()[0])
    assert first["_ts"] == 1772438400
    assert first["htmlContent"].startswith("<!DOCTYPE html>")  # the export is verbatim


async def test_apply_writes_every_converted_document(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, _ = await migrate(v1_data, store, blobs, apply=True)
    assert report.ok, report.failures
    published = await store.find("published-blogs")
    assert sorted(doc.id for doc in published) == ["agent-identity-mcp", "okta-to-entra-lessons"]
    for doc in published:
        assert validate_published_doc(doc.body, now=NOW) == []
        for asset in doc.body["assets"]:
            data = await blobs.get("published", asset["blob"])
            assert len(data) > 0
    articles = await find_docs(store, Article)
    assert len(articles) == 3
    assert len(await store.find("article-versions")) == 3
    assert {stored.doc.status.value for stored in articles} == {"archived", "in_review"}
    sources = await find_docs(store, Source)
    assert len(sources) == 2
    signals = await find_docs(store, Signal)
    assert len(signals) == 1
    assert len(await find_docs(store, KbSeed)) == 1
    assert len(await find_docs(store, VoiceSeed)) == 1
    assert blob_names(blobs, "media")
    assert all(name.startswith("articles/art-") for name in blob_names(blobs, "media"))
    assert report.containers["crawled-articles"].skipped == {"orphan": 1, "stale": 1}
    assert report.containers["images"].read == 3
    assert report.containers["prompts"].read == 1


async def test_a_second_run_changes_nothing(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    await migrate(v1_data, store, blobs, apply=True)
    before = {
        container: {doc.id: (doc.body, doc.etag) for doc in await store.find(container)}
        for container in ("published-blogs", "articles", "article-versions", "sources", "signals")
    }
    blob_count = len(blobs.blobs)
    report, _ = await migrate(v1_data, store, blobs, apply=True, now=LATER)
    assert report.ok
    for name in ("published-blogs", "drafts", "feed-sources", "keywords", "voice-profiles"):
        section = report.containers[name]
        assert section.written == 0
        assert section.unchanged == section.converted
    after = {
        container: {doc.id: (doc.body, doc.etag) for doc in await store.find(container)}
        for container in before
    }
    assert after == before
    # One more export file set for the new date, no new media or published blobs.
    new = len(blobs.blobs) - blob_count
    assert new == len([name for name in v1_data if name not in SESSION_CONTAINERS])


async def test_documents_changed_in_v2_are_not_overwritten(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    await migrate(v1_data, store, blobs, apply=True)
    article_id = derived_id("art", "v1", "1a2b3c4d-0000-0000-0000-000000000003")
    stored = await store.read("articles", article_id)
    edited = {
        **stored.body,
        "markdown": "The owner rewrote this in v2.\n",
        "updatedAt": "2026-11-26T13:00:00.000000Z",
    }
    await store.replace("articles", edited, if_match=stored.etag)
    draft = next(d for d in v1_data["drafts"] if d["id"].endswith("0003"))
    draft["content"] = "Changed in v1 after the first run.\n"
    draft["updatedAt"] = "2026-11-20T00:00:00+00:00"
    report, _ = await migrate(v1_data, store, blobs, apply=True)
    assert report.containers["drafts"].skipped == {"changed_in_v2": 1}
    assert (await store.read("articles", article_id)).body["markdown"] == (
        "The owner rewrote this in v2.\n"
    )


async def test_a_migrated_document_follows_changes_in_v1(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    await migrate(v1_data, store, blobs, apply=True)
    draft = next(d for d in v1_data["drafts"] if d["id"].endswith("0003"))
    draft["content"] = "Changed in v1 after the first run.\n"
    draft["updatedAt"] = "2026-11-20T00:00:00+00:00"
    report, _ = await migrate(v1_data, store, blobs, apply=True)
    assert report.containers["drafts"].written == 1
    article_id = derived_id("art", "v1", draft["id"])
    article = Article.from_document((await store.read("articles", article_id)).body)
    assert article.markdown == "Changed in v1 after the first run.\n"
    assert article.updated_at == "2026-11-20T00:00:00.000000Z"


async def test_published_documents_owned_by_v2_are_never_replaced(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    await store.create(
        "published-blogs",
        {
            "id": "agent-identity-mcp",
            "slug": "agent-identity-mcp",
            "schemaVersion": 2,
            "status": "redirect",
            "redirectTo": "agent-identity-in-mcp",
            "updatedAt": "2026-11-01T00:00:00Z",
        },
    )
    v2_post = {
        "id": "okta-to-entra-lessons",
        "slug": "okta-to-entra-lessons",
        "schemaVersion": 2,
        "status": "published",
        "updatedAt": "2026-11-02T00:00:00Z",
    }
    await store.create("published-blogs", v2_post)
    owner = sample_document(Article).model_copy(deep=True)
    owner.publish.slug = "okta-to-entra-lessons"
    await create_doc(store, owner)
    report, _ = await migrate(v1_data, store, blobs, apply=True)
    assert report.containers["published-blogs"].skipped == {"v2_stub": 1, "slug_owned_by_v2": 1}
    assert (await store.read("published-blogs", "agent-identity-mcp")).body["status"] == "redirect"
    assert (await store.read("published-blogs", "okta-to-entra-lessons")).body == v2_post


async def test_a_legacy_v1_copy_in_v2_is_replaced(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    await store.create(
        "published-blogs", {"id": "okta-to-entra-lessons", "slug": "okta-to-entra-lessons"}
    )
    report, _ = await migrate(
        v1_data, store, blobs, apply=True, only=frozenset({"published-blogs"})
    )
    assert report.containers["published-blogs"].written == 2
    doc = (await store.read("published-blogs", "okta-to-entra-lessons")).body
    assert doc["schemaVersion"] == 2


async def test_only_filters_export_and_conversion(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, source = await migrate(
        v1_data, store, blobs, apply=True, only=frozenset({"published-blogs"})
    )
    assert report.ok
    assert set(report.containers) == {"published-blogs"}
    assert source.iterated == ["published-blogs"]
    assert ("images", "img-hero") in source.reads
    assert blob_names(blobs, "backups") == {"v1-export/2026-11-26/published-blogs.jsonl"}
    assert await store.find("articles") == []
    assert len(await store.find("published-blogs")) == 2


async def test_only_signals_still_know_the_feeds(v1_data: V1Data) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, source = await migrate(
        v1_data, store, blobs, apply=True, only=frozenset({"crawled-articles"})
    )
    assert report.containers["crawled-articles"].converted == 1
    assert "feed-sources" in source.iterated  # ids only, to skip orphans
    assert "feed-sources" not in report.containers
    assert await store.find("sources") == []


@pytest.mark.parametrize("name", ["linkedin-sessions", "no-such-container"])
async def test_only_refuses_session_and_unknown_containers(v1_data: V1Data, name: str) -> None:
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, source = await migrate(v1_data, store, blobs, apply=True, only=frozenset({name}))
    assert report.refused == [name]
    assert not report.ok
    assert source.iterated == []
    assert blobs.blobs == {}


async def test_failures_are_listed_with_references_and_codes_only(
    v1_data: V1Data, canaries: tuple[str, str]
) -> None:
    v1_data["published-blogs"].append(
        {
            "id": "Bad Slug",
            "slug": "Bad Slug",
            "title": f"Broken {canaries[1]}",
            "mdxContent": f"Body {canaries[1]}\n",
        }
    )
    v1_data["keywords"].append({"id": f"secret topic {canaries[1]}", "keywords": ["x"] * 3})
    store, blobs = MemoryStore(), MemoryBlobStore()
    report, _ = await migrate(v1_data, store, blobs)
    assert not report.ok
    lines = report.lines()
    failures = [line for line in lines if line.startswith("FAIL ")]
    assert len(failures) == 1
    # An id that could be text (it has a space) is printed as a hash.
    assert re.fullmatch(
        r"FAIL published-blogs sha256:[0-9a-f]{12}: invalid_slug at /slug", failures[0]
    )
    text = "\n".join(lines)
    token, content = canaries
    assert content not in text
    assert token not in text
    assert "Agent identity" not in text
    assert "secret topic" not in text
    assert lines[-1] == "result: 1 failure(s)"


async def test_apply_needs_blob_storage(v1_data: V1Data) -> None:
    with pytest.raises(MigrationError, match="Blob"):
        await migrate(v1_data, MemoryStore(), None, apply=True)


async def test_unknown_pillar_or_format_options_are_refused(v1_data: V1Data) -> None:
    with pytest.raises(MigrationError, match="pillar"):
        await migrate(v1_data, MemoryStore(), None, pillar="nope")
    with pytest.raises(MigrationError, match="format"):
        await migrate(v1_data, MemoryStore(), None, format="nope")


async def test_the_owner_pillars_in_v2_settings_are_used(v1_data: V1Data) -> None:
    store = MemoryStore()
    custom = PillarSettings.default("2026-11-01T00:00:00.000000Z").model_copy(
        update={"pillars": [Pillar(id="cloud", name="Cloud"), Pillar(id="identity", name="IAM")]}
    )
    await save_singleton(store, custom, if_match=None)
    v1_data["published-blogs"].append(
        {
            "id": "weekly-notes",
            "slug": "weekly-notes",
            "title": "Weekly notes",
            "excerpt": "Notes.",
            "mdxContent": "Some notes about gardening and sourdough.\n",
            "publishedAt": "2026-05-01T00:00:00+00:00",
            "updatedAt": "2026-05-01T00:00:00+00:00",
        }
    )
    report, _ = await migrate(v1_data, store, MemoryBlobStore(), apply=True)
    assert report.ok, report.failures
    pillars = {doc.id: doc.body["pillar"] for doc in await store.find("published-blogs")}
    assert pillars == {
        "agent-identity-mcp": "identity",
        "okta-to-entra-lessons": "identity",
        "weekly-notes": "cloud",  # no keyword hit: the owner's first pillar
    }


async def test_signals_and_seeds_never_hold_feed_text(
    v1_data: V1Data, canaries: tuple[str, str]
) -> None:
    store = MemoryStore()
    await migrate(v1_data, store, MemoryBlobStore(), apply=True)
    for doc in await store.find("signals"):
        text = json.dumps(doc.body)
        assert canaries[1] not in text
        assert "azure.microsoft.com" not in text
    owners = await store.find("settings", [Filter("kind", "==", "kb_seed")])
    assert len(owners) == 1
