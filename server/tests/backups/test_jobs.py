"""The ``backups.dump`` job on a real JobRunner (MemoryStore, fake clock, faked services)."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import JsonValue

from blogwriter.backups import register_jobs
from blogwriter.backups.dump import dump_blob_name, manifest_blob_name
from blogwriter.backups.jobs import DumpProgress
from blogwriter.db import Doc, Filter, MemoryStore, NotFound, OrderBy, Store, StoreUnavailable
from blogwriter.domain.backups import BACKUP_CONTAINERS, BackupManifest, BackupStatus
from blogwriter.domain.docstore import read_doc, save_singleton
from blogwriter.domain.jobtypes import BACKUPS_DUMP, ENTITY_CHANGED
from blogwriter.domain.ports import services_installed
from blogwriter.domain.testing import FakeServices, MemoryBlobStore, make_services
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.queue import enqueue, get_job
from blogwriter.jobs.testing import DelegatingStore, FakeClock, eventually, fast_settings

T0 = datetime(2026, 11, 26, 8, 30, tzinfo=UTC)


def registry() -> JobRegistry:
    jobs = JobRegistry()
    register_jobs(jobs)
    return jobs


async def run_job(
    services: FakeServices,
    *,
    job_input: JsonValue = None,
    job_id: str = "job-backup",
    scheduled_for: datetime | None = None,
    max_attempts: int = 3,
) -> Job:
    """Enqueue ``backups.dump`` and run it to a terminal status (or a retry wait)."""
    jobs = registry()
    job_type = jobs.get(BACKUPS_DUMP)
    assert job_type is not None
    await enqueue(
        services.store,
        BACKUPS_DUMP,
        job_input,
        job_id=job_id,
        steps=job_type.step_names,
        scheduled_for=scheduled_for,
        max_attempts=max_attempts,
        clock=services.clock,
    )
    runner = JobRunner(services.store, jobs, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:

            async def settled() -> bool:
                job = await get_job(services.store, job_id)
                return job.is_terminal or (job.status == "queued" and job.attempt > 0)

            await eventually(settled, within=10)
        finally:
            await runner.stop()
    return await get_job(services.store, job_id)


def blobs_of(services: FakeServices) -> MemoryBlobStore:
    assert services.blobs is not None
    return services.blobs


async def status_of(store: Store) -> BackupStatus:
    return (await read_doc(store, BackupStatus, BackupStatus.SINGLETON_ID)).doc


def lines(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in gzip.decompress(data).decode("ascii").splitlines()]


async def test_the_job_dumps_every_container_and_writes_a_matching_manifest() -> None:
    services = make_services(clock=FakeClock(T0))
    await services.store.create("ideas", {"id": "idea-1", "kind": "idea", "title": "x"})
    job = await run_job(services)
    assert job.status == "succeeded", job.error
    blobs = blobs_of(services)
    manifest = BackupManifest.model_validate_json(
        await blobs.get("backups", manifest_blob_name("2026-11-26"))
    )
    assert manifest.date == "2026-11-26"
    assert [file.container for file in manifest.files] == list(BACKUP_CONTAINERS)
    for file in manifest.files:
        data = await blobs.get("backups", file.blob)
        assert file.blob == dump_blob_name("2026-11-26", file.container)
        assert hashlib.sha256(data).hexdigest() == file.sha256
        assert len(data) == file.size_bytes
    ideas = next(file for file in manifest.files if file.container == "ideas")
    assert ideas.documents == 1
    assert lines(await blobs.get("backups", ideas.blob)) == [
        {"id": "idea-1", "kind": "idea", "title": "x"}
    ]
    status = await status_of(services.store)
    assert status.last_success == manifest
    assert status.consecutive_failures == 0
    assert status.last_error is None
    assert status.last_attempt_at == manifest.finished_at
    events = [event.type for event in job.events]
    assert events.count("progress") == len(BACKUP_CONTAINERS)
    changed = [event.data for event in job.events if event.type == ENTITY_CHANGED]
    assert changed == [{"entity": "backup_status", "id": "backup-status"}]
    assert job.result == {"date": "2026-11-26", "files": len(BACKUP_CONTAINERS), "documents": 1}


async def test_the_status_is_updated_through_update_singleton_after_failures() -> None:
    services = make_services(clock=FakeClock(T0))
    earlier = BackupStatus.default("2026-11-20T00:00:00.000000Z").model_copy(
        update={"consecutive_failures": 2, "last_error": "storage_unavailable"}
    )
    await save_singleton(services.store, earlier, if_match=None)
    job = await run_job(services)
    assert job.status == "succeeded"
    status = await status_of(services.store)
    assert status.consecutive_failures == 0
    assert status.last_error is None
    assert status.created_at == "2026-11-20T00:00:00.000000Z"


async def test_the_date_comes_from_the_input_then_the_scheduled_slot() -> None:
    services = make_services(clock=FakeClock(T0))
    await run_job(services, job_input={"date": "2026-11-20", "origin": "owner"}, job_id="j1")
    await run_job(services, job_id="j2", scheduled_for=datetime(2026, 11, 25, 8, 30, tzinfo=UTC))
    names = {name for (_, name) in blobs_of(services).blobs}
    assert manifest_blob_name("2026-11-20") in names
    assert manifest_blob_name("2026-11-25") in names


async def test_without_storage_the_job_fails_and_alerts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = make_services(clock=FakeClock(T0), blobs=None)
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        job = await run_job(services)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "storage_unavailable"
    status = await status_of(services.store)
    assert status.last_error == "storage_unavailable"
    assert status.consecutive_failures == 1
    assert status.last_success is None
    alerts = [r for r in caplog.records if r.getMessage() == "alert_event"]
    assert [(getattr(r, "alert_kind", None), getattr(r, "ref_id", None)) for r in alerts] == [
        ("job_failed", "job-backup")
    ]


class FlakyStore(DelegatingStore):
    """Fails every query on ``posts`` (the dump of that container cannot finish)."""

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
        if container == "posts":
            raise StoreUnavailable("find posts: unavailable")
        return await super().find(
            container,
            filters,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=fields,
        )


async def test_an_error_before_the_last_attempt_is_retried_without_counting() -> None:
    store = FlakyStore(MemoryStore())
    services = make_services(clock=FakeClock(T0), store=store)
    job = await run_job(services, max_attempts=3)
    assert job.status == "queued"  # waiting for its retry
    assert job.attempt == 1
    step = job.step("dump")
    assert step is not None
    progress = DumpProgress.from_json(step.output)
    assert [file.container for file in progress.files] == list(
        BACKUP_CONTAINERS[: BACKUP_CONTAINERS.index("posts")]
    )
    with pytest.raises(NotFound):
        await read_doc(services.store, BackupStatus, BackupStatus.SINGLETON_ID)


async def test_an_error_on_the_last_attempt_is_recorded_and_alerted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FlakyStore(MemoryStore())
    services = make_services(clock=FakeClock(T0), store=store)
    with caplog.at_level(logging.ERROR, logger="blogwriter.notify.alerts"):
        job = await run_job(services, max_attempts=1)
    assert job.status == "quarantined"
    status = await status_of(services.store)
    assert status.last_error == "backup_failed"
    assert status.consecutive_failures == 1
    assert any(r.getMessage() == "alert_event" for r in caplog.records)


async def test_a_resumed_job_skips_the_containers_it_already_dumped() -> None:
    services = make_services(clock=FakeClock(T0))
    done = BACKUP_CONTAINERS[:3]
    blobs = blobs_of(services)
    for container in done:
        await blobs.put(
            "backups", dump_blob_name("2026-11-26", container), b"old", content_type="x/y"
        )
    jobs = registry()
    job_type = jobs.get(BACKUPS_DUMP)
    assert job_type is not None
    queued = await enqueue(
        services.store,
        BACKUPS_DUMP,
        None,
        job_id="resumed",
        steps=job_type.step_names,
        clock=services.clock,
    )
    # A previous run checkpointed three containers before it crashed.
    body = queued.to_body()
    body["steps"][0]["output"] = {
        "date": "2026-11-26",
        "startedAt": "2026-11-26T08:30:00.000000Z",
        "files": [
            {
                "container": container,
                "blob": dump_blob_name("2026-11-26", container),
                "documents": 0,
                "sizeBytes": 3,
                "sha256": hashlib.sha256(b"old").hexdigest(),
            }
            for container in done
        ],
    }
    doc = await services.store.read("jobs", "resumed")
    await services.store.replace("jobs", body, if_match=doc.etag)
    runner = JobRunner(services.store, jobs, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:

            async def finished() -> bool:
                return (await get_job(services.store, "resumed")).is_terminal

            await eventually(finished, within=10)
        finally:
            await runner.stop()
    for container in done:
        assert await blobs.get("backups", dump_blob_name("2026-11-26", container)) == b"old"
    manifest = BackupManifest.model_validate_json(
        await blobs.get("backups", manifest_blob_name("2026-11-26"))
    )
    assert len(manifest.files) == len(BACKUP_CONTAINERS)
    assert manifest.started_at == "2026-11-26T08:30:00.000000Z"
