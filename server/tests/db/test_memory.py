"""MemoryStore specifics: partitions not keyed by id, TTL, lifecycle and task interleaving.

The shared behaviour is covered by test_contract.py; these cases need containers the live suite
may not write to, or depend on MemoryStore's documented differences.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from blogwriter.db import (
    Conflict,
    Doc,
    Filter,
    InvalidRequest,
    MemoryStore,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
)


def test_memory_store_satisfies_the_protocol() -> None:
    store: Store = MemoryStore()
    assert isinstance(store, Store)


async def test_ids_are_unique_per_partition_not_per_container() -> None:
    store = MemoryStore()
    one = await store.create("article-versions", {"id": "v1", "articleId": "a1", "n": 1})
    two = await store.create("article-versions", {"id": "v1", "articleId": "a2", "n": 2})
    assert (one.partition_key, two.partition_key) == ("a1", "a2")
    assert (await store.read("article-versions", "v1", partition_key="a1")).body["n"] == 1
    assert (await store.read("article-versions", "v1", partition_key="a2")).body["n"] == 2
    with pytest.raises(Conflict):
        await store.create("article-versions", {"id": "v1", "articleId": "a1"})
    with pytest.raises(NotFound):
        await store.read("article-versions", "v1", partition_key="a3")


async def test_replace_cannot_move_a_document_between_partitions() -> None:
    store = MemoryStore()
    created = await store.create("signals", {"id": "s1", "sourceId": "feed-a"})
    with pytest.raises(NotFound):
        await store.replace("signals", {"id": "s1", "sourceId": "feed-b"}, if_match=created.etag)
    assert (await store.read("signals", "s1", partition_key="feed-a")) == created


async def test_delete_in_a_partitioned_container() -> None:
    store = MemoryStore()
    created = await store.create("usage", {"id": "openai", "month": "2026-11", "usd": 1.5})
    with pytest.raises(InvalidRequest):
        await store.delete("usage", "openai")
    with pytest.raises(NotFound):
        await store.delete("usage", "openai", partition_key="2026-10")
    with pytest.raises(PreconditionFailed):
        await store.delete("usage", "openai", partition_key="2026-11", if_match='"stale"')
    await store.delete("usage", "openai", partition_key="2026-11", if_match=created.etag)
    with pytest.raises(NotFound):
        await store.read("usage", "openai", partition_key="2026-11")


async def test_find_scoped_to_a_partition() -> None:
    store = MemoryStore()
    for version, article in (("v1", "a1"), ("v2", "a1"), ("v1", "a2")):
        await store.create("article-versions", {"id": version, "articleId": article})
    docs = await store.find("article-versions", partition_key="a1", order_by=OrderBy("id"))
    assert [(doc.partition_key, doc.id) for doc in docs] == [("a1", "v1"), ("a1", "v2")]
    everything = await store.find("article-versions")
    assert [(doc.partition_key, doc.id) for doc in everything] == [
        ("a1", "v1"),
        ("a1", "v2"),
        ("a2", "v1"),
    ]


async def test_containers_are_isolated() -> None:
    store = MemoryStore()
    await store.create("ideas", {"id": "x"})
    assert await store.find("stories") == []
    with pytest.raises(NotFound):
        await store.read("stories", "x")


async def test_ttl_is_kept_but_never_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    await store.create("ephemeral", {"id": "state-1", "ttl": 1, "expiresAt": "2026-11-26T10:00Z"})
    far_future = time.time() + 365 * 24 * 3600
    monkeypatch.setattr(time, "time", lambda: far_future)
    doc = await store.read("ephemeral", "state-1")
    assert doc.body["ttl"] == 1
    assert [found.id for found in await store.find("ephemeral")] == ["state-1"]


async def test_close_and_ensure_containers_are_no_ops() -> None:
    store = MemoryStore()
    await store.ensure_containers()
    await store.create("jobs", {"id": "j1"})
    await store.close()
    await store.close()
    assert (await store.read("jobs", "j1")).id == "j1"


async def test_etags_are_unique_quoted_strings() -> None:
    store = MemoryStore()
    etags = {(await store.upsert("jobs", {"id": "j1", "n": n})).etag for n in range(50)}
    assert len(etags) == 50
    assert all(etag.startswith('"') and etag.endswith('"') for etag in etags)


async def test_read_then_replace_races_interleave_and_one_claim_wins() -> None:
    """Two runners read the same claim and race to take it; the yield before each operation
    makes them interleave like remote calls, so exactly one CAS write succeeds."""
    store = MemoryStore()
    await store.create("jobs", {"id": "job-1", "owner": None, "fence": 0})

    async def claim(owner: str) -> Doc | PreconditionFailed:
        current = await store.read("jobs", "job-1")
        body = {**current.body, "owner": owner, "fence": current.body["fence"] + 1}
        try:
            return await store.replace("jobs", body, if_match=current.etag)
        except PreconditionFailed as lost:
            return lost

    results = await asyncio.gather(claim("runner-a"), claim("runner-b"))
    winners = [result for result in results if isinstance(result, Doc)]
    assert len(winners) == 1
    assert sum(isinstance(result, PreconditionFailed) for result in results) == 1
    final = await store.read("jobs", "job-1")
    assert final.body["fence"] == 1
    assert final.body["owner"] == winners[0].body["owner"]


async def test_stored_documents_are_isolated_from_returned_docs() -> None:
    store = MemoryStore()
    created = await store.create("ideas", {"id": "i1", "tags": ["a"]})
    found = await store.find("ideas", [Filter("tags", "exists", True)])
    found[0].body["tags"].append("b")
    created.body["tags"].append("c")
    assert (await store.read("ideas", "i1")).body["tags"] == ["a"]
