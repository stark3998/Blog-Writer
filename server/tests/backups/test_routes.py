"""The backup routes through the real app (auth, CSRF, problems) with faked services."""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.backups import get_router
from blogwriter.backups.dump import dump_blob_name, dump_containers, manifest_blob_name
from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.core.testing import write_headers as csrf_headers
from blogwriter.domain.backups import BackupStatus
from blogwriter.domain.docstore import read_doc, save_singleton
from blogwriter.domain.jobtypes import BACKUPS_DUMP
from blogwriter.domain.testing import FakeServices, MemoryBlobStore, make_app, make_services
from blogwriter.jobs.testing import FakeClock

SETTINGS = make_settings()
READ = principal_headers(OWNER_OID)
WRITE = {**READ, **csrf_headers(SETTINGS)}
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def services() -> FakeServices:
    return make_services(clock=FakeClock(NOW))


@pytest.fixture
async def client(services: FakeServices) -> AsyncIterator[httpx.AsyncClient]:
    app = make_app(services, routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        yield http


def blobs_of(services: FakeServices) -> MemoryBlobStore:
    assert services.blobs is not None
    return services.blobs


async def test_the_view_shows_defaults_before_any_backup(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/backups", headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert body["status"]["etag"] is None
    assert body["status"]["item"]["id"] == "backup-status"
    assert body["status"]["item"]["consecutiveFailures"] == 0
    assert body["dumps"] == []
    assert body["restoreDrillDue"] is True


async def test_the_view_lists_the_last_35_days_newest_first(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    blobs = blobs_of(services)
    await services.store.create("ideas", {"id": "idea-1", "kind": "idea"})
    await dump_containers(services.store, blobs, date="2026-11-26", now=NOW, containers=["ideas"])
    await blobs.put("backups", manifest_blob_name("2026-11-26"), b"{}", content_type="x/y")
    await dump_containers(services.store, blobs, date="2026-11-01", now=NOW, containers=["posts"])
    await dump_containers(services.store, blobs, date="2026-10-01", now=NOW, containers=["posts"])
    await blobs.put("backups", "v1-export/2026-11-26/drafts.jsonl", b"{}", content_type="x/y")
    await blobs.put("backups", "notes.txt", b"x", content_type="text/plain")
    body = (await client.get("/api/backups", headers=READ)).json()
    assert [dump["date"] for dump in body["dumps"]] == ["2026-11-26", "2026-11-01"]
    newest = body["dumps"][0]
    assert newest["complete"] is True
    assert body["dumps"][1]["complete"] is False
    (file,) = newest["files"]
    data = await blobs.get("backups", dump_blob_name("2026-11-26", "ideas"))
    assert file["container"] == "ideas"
    assert file["blob"] == "2026-11-26/ideas.jsonl.gz"
    assert file["sizeBytes"] == len(data)
    assert len(file["sha256"]) == 64


async def test_run_starts_one_owner_job_for_today(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    first = await client.post("/api/backups/run", headers=WRITE)
    assert first.status_code == 202
    assert first.json() == {"jobId": "backups-run-2026-11-26", "status": "queued"}
    again = await client.post("/api/backups/run", headers=WRITE)
    assert again.json()["jobId"] == "backups-run-2026-11-26"
    job = await services.jobs.get("backups-run-2026-11-26")
    assert job.type == BACKUPS_DUMP
    assert job.input == {"origin": "owner", "date": "2026-11-26"}
    assert services.jobs.types() == [BACKUPS_DUMP]


async def test_run_needs_csrf(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/backups/run", headers=READ)
    assert response.status_code == 403


async def test_download_returns_the_gzip_as_an_attachment(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await services.store.create("ideas", {"id": "idea-1", "kind": "idea"})
    await dump_containers(
        services.store, blobs_of(services), date="2026-11-26", now=NOW, containers=["ideas"]
    )
    response = await client.get("/api/backups/2026-11-26/ideas", headers=READ)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="backup-2026-11-26-ideas.jsonl.gz"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert gzip.decompress(response.content) == b'{"id":"idea-1","kind":"idea"}\n'


@pytest.mark.parametrize(
    "path",
    [
        "2026-11-26/ideas",  # no dump that day
        "2026-11-26/jobs",
        "2026-11-26/ephemeral",
        "2026-11-26/manifest",
        "2026-13-01/ideas",
        "latest/ideas",
        "2026-11-26/..%2Fsecrets",
    ],
)
async def test_download_is_404_for_anything_else(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(f"/api/backups/{path}", headers=READ)
    assert response.status_code == 404


async def test_restore_drill_attestation(client: httpx.AsyncClient, services: FakeServices) -> None:
    response = await client.post("/api/backups/restore-drill", headers=WRITE)
    assert response.status_code == 200
    body = response.json()
    assert body["item"]["lastRestoreDrillAt"] == "2026-11-26T12:00:00.000000Z"
    assert body["etag"] == response.headers["etag"]
    stored = await read_doc(services.store, BackupStatus, BackupStatus.SINGLETON_ID)
    assert stored.etag == body["etag"]
    view = (await client.get("/api/backups", headers=READ)).json()
    assert view["restoreDrillDue"] is False
    assert view["status"]["etag"] == body["etag"]


async def test_restore_drill_keeps_the_rest_of_the_status(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    earlier = BackupStatus.default("2026-11-20T00:00:00.000000Z").model_copy(
        update={"consecutive_failures": 2, "last_error": "backup_failed"}
    )
    await save_singleton(services.store, earlier, if_match=None)
    body = (await client.post("/api/backups/restore-drill", headers=WRITE)).json()
    assert body["item"]["consecutiveFailures"] == 2
    assert body["item"]["lastError"] == "backup_failed"


async def test_without_storage_the_storage_routes_answer_503() -> None:
    services = make_services(clock=FakeClock(NOW), blobs=None)
    app = make_app(services, routers=[get_router()], settings=SETTINGS)
    async with asgi_client(app) as http:
        responses = [
            await http.get("/api/backups", headers=READ),
            await http.post("/api/backups/run", headers=WRITE),
            await http.get("/api/backups/2026-11-26/ideas", headers=READ),
        ]
        drill = await http.post("/api/backups/restore-drill", headers=WRITE)
    for response in responses:
        assert response.status_code == 503
        assert response.json()["code"] == "storage_unavailable"
    assert drill.status_code == 200


async def test_every_route_needs_the_owner(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/backups")).status_code == 401
    assert (await client.get("/api/backups/2026-11-26/ideas")).status_code == 401
