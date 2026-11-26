"""Live-only checks (``BW_LIVE_COSMOS=1``) and the live suite's write guard.

The guard tests always run (against MemoryStore): they prove the live suite cannot write outside
``test-`` ids in ``jobs`` and ``ephemeral``, whatever a test does.
"""

from __future__ import annotations

import os

import pytest
from azure.core.credentials_async import AsyncTokenCredential

from blogwriter.db import CosmosStore, InvalidRequest, NotFound, Store

LIVE = os.environ.get("BW_LIVE_COSMOS") == "1"

type Guarded = tuple[Store, list[tuple[str, str]]]


@pytest.mark.skipif(not LIVE, reason="needs BW_LIVE_COSMOS=1 and BW_COSMOS_ENDPOINT")
async def test_registry_containers_exist_in_the_real_database(
    live_credential: AsyncTokenCredential,
) -> None:
    """Read-only: every registry container exists with its Terraform partition key."""
    store = await CosmosStore.from_settings(credential=live_credential)
    try:
        await store.ensure_containers()
    finally:
        await store.close()


@pytest.mark.parametrize("container", ["settings", "articles", "published-blogs", "usage"])
async def test_guard_blocks_writes_outside_the_live_containers(
    guarded: Guarded, container: str
) -> None:
    store, written = guarded
    with pytest.raises(AssertionError, match="may not write"):
        await store.upsert(container, {"id": "test-x", "month": "2026-11"})
    assert written == []


async def test_guard_blocks_ids_without_the_test_prefix(guarded: Guarded) -> None:
    store, written = guarded
    with pytest.raises(AssertionError, match="test-"):
        await store.create("jobs", {"id": "real-job"})
    with pytest.raises(AssertionError, match="test-"):
        await store.upsert("ephemeral", {"id": "state"})
    with pytest.raises(AssertionError, match="test-"):
        await store.replace("jobs", {"id": "job-1"})
    with pytest.raises(AssertionError, match="test-"):
        await store.delete("jobs", "job-1")
    assert written == []


async def test_guard_records_writes_before_sending_them(guarded: Guarded) -> None:
    store, written = guarded
    await store.create("jobs", {"id": "test-a"})
    with pytest.raises(NotFound):
        await store.replace("jobs", {"id": "test-missing"})
    await store.delete("jobs", "test-a")
    assert written == [("jobs", "test-a"), ("jobs", "test-missing"), ("jobs", "test-a")]


async def test_guard_lets_invalid_requests_reach_validation(guarded: Guarded) -> None:
    store, written = guarded
    with pytest.raises(InvalidRequest):
        await store.create("article-versions", {"id": "test-v1"})
    with pytest.raises(InvalidRequest):
        await store.create("jobs", {"id": "bad/id"})
    with pytest.raises(InvalidRequest):
        await store.delete("drafts", "test-x")
    with pytest.raises(InvalidRequest):
        await store.delete("signals", "test-x")
    assert written == []
