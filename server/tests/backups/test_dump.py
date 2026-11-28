"""dump_containers: which containers and kinds, keyset paging, determinism, overwrite."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from blogwriter.backups.dump import (
    NEVER_DUMPED,
    dump_blob_name,
    dump_containers,
    encode_lines,
    restore_drill_due,
    valid_dump_date,
)
from blogwriter.db import CONTAINERS, Doc, Filter, MemoryStore, OrderBy, Store
from blogwriter.domain import DOCUMENT_TYPES
from blogwriter.domain.backups import BACKUP_CONTAINERS, BackupStatus
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.docstore import create_doc
from blogwriter.domain.engagement import EngagementItem, EngagementRecord, WatchEntry
from blogwriter.domain.planner import Reminder
from blogwriter.domain.signals import Signal
from blogwriter.domain.testing import MemoryBlobStore, sample_document
from blogwriter.jobs.queue import enqueue
from blogwriter.jobs.testing import DelegatingStore, FakeClock

NOW = datetime(2026, 11, 26, 8, 30, tzinfo=UTC)
DATE = "2026-11-26"


def lines(data: bytes) -> list[dict[str, Any]]:
    text = gzip.decompress(data).decode("ascii")
    assert text.endswith("\n") or text == ""
    return [json.loads(line) for line in text.splitlines()]


async def seed_everything(store: Store) -> None:
    """One document of every domain kind, plus the raw containers' documents and a job."""
    for cls in DOCUMENT_TYPES:
        await create_doc(store, sample_document(cls))
    await store.create(
        "published-blogs", {"id": "hello-world", "slug": "hello-world", "schemaVersion": 2}
    )
    await store.create("published-blogs-staging", {"id": "preview-post", "slug": "preview-post"})
    await store.create("usage", {"id": "use-1", "month": "2026-11", "costUsd": 0.12})
    await enqueue(store, "backups.dump", None, job_id="job-1", clock=FakeClock(NOW))


async def test_every_durable_container_is_dumped_and_jobs_and_ephemeral_never() -> None:
    store = MemoryStore()
    blobs = MemoryBlobStore()
    await seed_everything(store)
    files = await dump_containers(store, blobs, date=DATE, now=NOW)
    assert [file.container for file in files] == list(BACKUP_CONTAINERS)
    names = {name for (container, name) in blobs.blobs if container == "backups"}
    assert names == {dump_blob_name(DATE, container) for container in BACKUP_CONTAINERS}
    assert set(CONTAINERS) - set(BACKUP_CONTAINERS) == NEVER_DUMPED == {"jobs", "ephemeral"}
    for file in files:
        data = await blobs.get("backups", file.blob)
        assert file.sha256 == hashlib.sha256(data).hexdigest()
        assert file.size_bytes == len(data)
        assert file.documents == len(lines(data)) >= 1
    kinds = {
        body["kind"]
        for file in files
        for body in lines(await blobs.get("backups", file.blob))
        if "kind" in body
    }
    assert "oauth_state" not in kinds
    assert "seed_text" not in kinds
    assert "job" not in kinds


async def test_engagement_keeps_only_records_and_watch_entries() -> None:
    store = MemoryStore()
    blobs = MemoryBlobStore()
    for cls in (EngagementItem, EngagementRecord, WatchEntry, Reminder):
        await create_doc(store, sample_document(cls))
    (file,) = await dump_containers(store, blobs, date=DATE, now=NOW, containers=["engagement"])
    dumped = lines(await blobs.get("backups", file.blob))
    assert sorted(body["kind"] for body in dumped) == ["engagement_record", "watch_entry"]
    assert all("pastedText" not in body for body in dumped)


async def test_bodies_only_no_etags_or_system_properties() -> None:
    store = MemoryStore()
    blobs = MemoryBlobStore()
    await store.create("ideas", {"id": "idea-1", "kind": "idea", "title": "x"})
    (file,) = await dump_containers(store, blobs, date=DATE, now=NOW, containers=["ideas"])
    (body,) = lines(await blobs.get("backups", file.blob))
    assert body == {"id": "idea-1", "kind": "idea", "title": "x"}


class CountingStore(DelegatingStore):
    def __init__(self, inner: Store) -> None:
        super().__init__(inner)
        self.pages: list[int] = []

    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> list[Doc]:
        assert order_by == OrderBy("id")
        assert limit is not None
        found = await super().find(
            container,
            filters,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=fields,
        )
        self.pages.append(len(found))
        return found


async def test_keyset_pages_split_same_url_signals_of_two_sources_without_loss() -> None:
    inner = MemoryStore()
    url_hash = sha256_hex("https://example.com/same-story")
    sources = [f"feed-{index:028x}" for index in range(1, 4)]
    created: list[str] = []
    for source in sources:
        for item in range(4):
            digest = url_hash if item == 0 else sha256_hex(f"{source}-{item}")
            signal = sample_document(
                Signal, id=Signal.id_for(source, digest), source_id=source, url_sha256=digest
            )
            await create_doc(inner, signal)
            created.append(signal.id)
    ordered = sorted(created)
    same_url = sorted(
        Signal.id_for(source, url_hash) for source in sources
    )  # three signals, one URL, three partitions
    boundary = ordered.index(same_url[1])  # a page ends just before the second one
    assert boundary >= 1
    store = CountingStore(inner)
    blobs = MemoryBlobStore()
    (file,) = await dump_containers(
        store, blobs, date=DATE, now=NOW, containers=["signals"], page_size=boundary
    )
    dumped = [body["id"] for body in lines(await blobs.get("backups", file.blob))]
    assert dumped == ordered
    assert file.documents == len(created)
    assert len(store.pages) >= 2
    assert same_url[0] in dumped
    assert same_url[1] in dumped
    assert all("url" not in body for body in lines(await blobs.get("backups", file.blob)))


async def test_output_is_deterministic_and_sorted_by_id() -> None:
    store = MemoryStore()
    for title in ("b", "c", "a"):
        await store.create("ideas", {"id": f"idea-{title}", "kind": "idea", "z": 1, "a": title})
    first, second = MemoryBlobStore(), MemoryBlobStore()
    (one,) = await dump_containers(store, first, date=DATE, now=NOW, containers=["ideas"])
    (two,) = await dump_containers(
        store, second, date=DATE, now=NOW + timedelta(hours=3), containers=["ideas"], page_size=1
    )
    data = await first.get("backups", one.blob)
    assert data == await second.get("backups", two.blob)
    assert one.sha256 == two.sha256
    assert [body["id"] for body in lines(data)] == ["idea-a", "idea-b", "idea-c"]
    raw = gzip.decompress(data).decode("ascii").splitlines()[0]
    assert raw == '{"a":"a","id":"idea-a","kind":"idea","z":1}'


async def test_a_re_run_the_same_day_overwrites() -> None:
    store = MemoryStore()
    blobs = MemoryBlobStore()
    await store.create("ideas", {"id": "idea-1", "kind": "idea"})
    (first,) = await dump_containers(store, blobs, date=DATE, now=NOW, containers=["ideas"])
    await store.create("ideas", {"id": "idea-2", "kind": "idea"})
    (second,) = await dump_containers(store, blobs, date=DATE, now=NOW, containers=["ideas"])
    assert first.blob == second.blob
    assert second.documents == 2
    assert lines(await blobs.get("backups", second.blob))[-1]["id"] == "idea-2"
    assert len(blobs.blobs) == 1


async def test_an_empty_container_is_an_empty_file() -> None:
    blobs = MemoryBlobStore()
    (file,) = await dump_containers(MemoryStore(), blobs, date=DATE, now=NOW, containers=["posts"])
    assert file.documents == 0
    assert lines(await blobs.get("backups", file.blob)) == []


async def test_done_containers_are_skipped() -> None:
    blobs = MemoryBlobStore()
    files = await dump_containers(
        MemoryStore(), blobs, date=DATE, now=NOW, done=set(BACKUP_CONTAINERS) - {"posts"}
    )
    assert [file.container for file in files] == ["posts"]


@pytest.mark.parametrize("container", ["jobs", "ephemeral", "nope"])
async def test_never_dumped_or_unknown_containers_are_refused(container: str) -> None:
    with pytest.raises(ValueError, match=container):
        await dump_containers(
            MemoryStore(), MemoryBlobStore(), date=DATE, now=NOW, containers=[container]
        )


@pytest.mark.parametrize("date", ["2026-13-01", "26-11-2026", "2026-11-26/../x", ""])
async def test_bad_dates_are_refused(date: str) -> None:
    assert not valid_dump_date(date)
    with pytest.raises(ValueError, match="date"):
        await dump_containers(MemoryStore(), MemoryBlobStore(), date=date, now=NOW)


def test_encode_lines_escapes_non_ascii() -> None:
    assert encode_lines([{"t": "caf\u00e9\u2028"}]) == b'{"t":"caf\\u00e9\\u2028"}\n'


def test_restore_drill_due_after_31_days() -> None:
    never = BackupStatus.default("2026-11-01T00:00:00.000000Z")
    assert restore_drill_due(never, now=NOW)
    recent = never.model_copy(update={"last_restore_drill_at": "2026-11-01T00:00:00.000000Z"})
    assert not restore_drill_due(recent, now=datetime(2026, 12, 2, tzinfo=UTC))
    assert restore_drill_due(recent, now=datetime(2026, 12, 2, 0, 0, 1, tzinfo=UTC))
