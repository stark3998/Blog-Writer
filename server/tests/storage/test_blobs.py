"""AzureBlobStore over a fake container-client protocol: no Azure, every SDK call recorded."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobClient, ContainerClient

from blogwriter.domain.common import BlobContainer
from blogwriter.domain.deps import problem_from
from blogwriter.domain.ports import BlobExists, BlobNotFound, BlobStore, BlobStoreUnavailable
from blogwriter.storage import blobs as blobs_module
from blogwriter.storage.blobs import (
    AzureBlobStore,
    BlobClientLike,
    BlobContainerClient,
    StorageError,
    StorageUnavailable,
    validate_blob_name,
)
from blogwriter.storage.settings import StorageSettings

SRC = Path(blobs_module.__file__).parent

# --------------------------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------------------------


@dataclass
class StoredBlob:
    data: bytes
    content_type: str | None
    metadata: dict[str, str]
    etag: str


@dataclass
class FakeProperties:
    name: str
    size: int
    etag: str
    metadata: dict[str, str]
    content_settings: ContentSettings


class FakeDownloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        await asyncio.sleep(0)
        return self._data


def _not_found(container_missing: bool = False) -> ResourceNotFoundError:
    error = ResourceNotFoundError("not found")
    if container_missing:
        error.error_code = "ContainerNotFound"  # pyright: ignore[reportAttributeAccessIssue]
    return error


@dataclass
class FakeContainer:
    """A container client fake: every method call is recorded in ``calls``."""

    exists: bool = True
    blobs: dict[str, StoredBlob] = field(default_factory=dict[str, StoredBlob])
    calls: list[str] = field(default_factory=list[str])
    upload_kwargs: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    fail_with: Exception | None = None

    def check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        if not self.exists:
            raise _not_found(container_missing=True)

    def get_blob_client(self, blob: str) -> FakeBlob:
        self.calls.append(f"get_blob_client:{blob}")
        return FakeBlob(self, blob)

    def list_blobs(
        self, name_starts_with: str | None = None, include: list[str] | None = None
    ) -> AsyncIterator[FakeProperties]:
        self.calls.append(f"list_blobs:{name_starts_with}:{include}")

        async def iterate() -> AsyncIterator[FakeProperties]:
            self.check()
            for name in sorted(self.blobs, reverse=True):  # unsorted on purpose
                if name_starts_with is None or name.startswith(name_starts_with):
                    await asyncio.sleep(0)
                    yield self.properties(name)

        return iterate()

    async def get_container_properties(self) -> dict[str, object]:
        self.calls.append("get_container_properties")
        await asyncio.sleep(0)
        self.check()
        return {"name": "fake"}

    def properties(self, name: str) -> FakeProperties:
        stored = self.blobs[name]
        return FakeProperties(
            name=name,
            size=len(stored.data),
            etag=stored.etag,
            metadata=dict(stored.metadata),
            content_settings=ContentSettings(content_type=stored.content_type),
        )


class FakeBlob:
    def __init__(self, container: FakeContainer, name: str) -> None:
        self._container = container
        self._name = name

    async def upload_blob(
        self,
        data: bytes,
        *,
        overwrite: bool,
        content_settings: ContentSettings,
        metadata: dict[str, str],
    ) -> Mapping[str, Any]:
        container = self._container
        container.calls.append(f"upload_blob:{self._name}")
        container.upload_kwargs.append(
            {
                "overwrite": overwrite,
                "content_settings": content_settings,
                "metadata": dict(metadata),
            }
        )
        await asyncio.sleep(0)
        container.check()
        if not overwrite and self._name in container.blobs:
            raise ResourceExistsError("exists")
        etag = f'"{hashlib.sha256(data).hexdigest()[:12]}"'
        container.blobs[self._name] = StoredBlob(
            bytes(data), content_settings.content_type, dict(metadata), etag
        )
        return {"etag": etag}

    async def download_blob(self) -> FakeDownloader:
        self._container.calls.append(f"download_blob:{self._name}")
        await asyncio.sleep(0)
        self._container.check()
        stored = self._container.blobs.get(self._name)
        if stored is None:
            raise _not_found()
        return FakeDownloader(stored.data)

    async def get_blob_properties(self) -> FakeProperties:
        self._container.calls.append(f"get_blob_properties:{self._name}")
        await asyncio.sleep(0)
        self._container.check()
        if self._name not in self._container.blobs:
            raise _not_found()
        return self._container.properties(self._name)

    async def delete_blob(self, delete_snapshots: str | None = None) -> None:
        self._container.calls.append(f"delete_blob:{self._name}:{delete_snapshots}")
        await asyncio.sleep(0)
        self._container.check()
        if self._name not in self._container.blobs:
            raise _not_found()
        del self._container.blobs[self._name]


def fake_containers() -> dict[BlobContainer, FakeContainer]:
    return {"media": FakeContainer(), "published": FakeContainer(), "backups": FakeContainer()}


def store_over(containers: Mapping[BlobContainer, FakeContainer]) -> AzureBlobStore:
    clients: dict[BlobContainer, BlobContainerClient] = dict(containers)
    return AzureBlobStore(clients)


def all_calls(containers: Mapping[BlobContainer, FakeContainer]) -> list[str]:
    return [call for container in containers.values() for call in container.calls]


# --------------------------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------------------------


def _sdk_container(client: ContainerClient) -> BlobContainerClient:
    return client  # pyright proves the real SDK client satisfies the protocol


def _sdk_blob(client: BlobClient) -> BlobClientLike:
    return client


def _port(store: AzureBlobStore) -> BlobStore:
    return store


def test_the_sdk_clients_and_the_store_satisfy_the_protocols() -> None:
    assert _sdk_container is not None
    assert _sdk_blob is not None
    assert isinstance(_port(store_over(fake_containers())), AzureBlobStore)


def test_a_store_needs_all_three_containers() -> None:
    containers = fake_containers()
    del containers["backups"]
    with pytest.raises(ValueError, match="backups"):
        store_over(containers)


# --------------------------------------------------------------------------------------------
# put / get / head / list / delete
# --------------------------------------------------------------------------------------------


async def test_put_sets_content_type_and_sha256_metadata() -> None:
    containers = fake_containers()
    store = store_over(containers)
    data = b"\x89PNG fake bytes"
    info = await store.put("media", "uploads/2026-11/a.png", data, content_type="image/png")
    digest = hashlib.sha256(data).hexdigest()
    assert info.sha256 == digest
    assert info.size_bytes == len(data)
    assert info.content_type == "image/png"
    assert info.container == "media"
    assert info.etag is not None
    stored = containers["media"].blobs["uploads/2026-11/a.png"]
    assert stored.metadata == {"sha256": digest}
    assert stored.content_type == "image/png"
    assert containers["media"].upload_kwargs[0]["overwrite"] is False
    assert containers["published"].blobs == {}


async def test_put_refuses_to_overwrite_unless_asked() -> None:
    containers = fake_containers()
    store = store_over(containers)
    await store.put("backups", "2026-11-26/settings.jsonl.gz", b"one", content_type="x/y")
    with pytest.raises(BlobExists):
        await store.put("backups", "2026-11-26/settings.jsonl.gz", b"two", content_type="x/y")
    assert containers["backups"].blobs["2026-11-26/settings.jsonl.gz"].data == b"one"
    info = await store.put(
        "backups", "2026-11-26/settings.jsonl.gz", b"two", content_type="x/y", overwrite=True
    )
    assert containers["backups"].blobs["2026-11-26/settings.jsonl.gz"].data == b"two"
    assert info.sha256 == hashlib.sha256(b"two").hexdigest()


async def test_get_head_list_and_delete() -> None:
    containers = fake_containers()
    store = store_over(containers)
    await store.put("published", "slug/b.png", b"bb", content_type="image/png")
    await store.put("published", "slug/a.png", b"a", content_type="image/png")
    await store.put("published", "other/c.png", b"c", content_type="image/png")

    assert await store.get("published", "slug/a.png") == b"a"
    head = await store.head("published", "slug/b.png")
    assert head is not None
    assert head.sha256 == hashlib.sha256(b"bb").hexdigest()
    assert head.size_bytes == 2
    assert head.content_type == "image/png"
    assert await store.head("published", "slug/missing.png") is None

    listed = await store.list_blobs("published", "slug/")
    assert [info.name for info in listed] == ["slug/a.png", "slug/b.png"]
    assert all(info.sha256 for info in listed)
    assert "list_blobs:slug/:['metadata']" in containers["published"].calls
    everything = await store.list_blobs("published")
    assert [info.name for info in everything] == ["other/c.png", "slug/a.png", "slug/b.png"]

    await store.delete("published", "slug/a.png")
    await store.delete("published", "slug/a.png")  # idempotent
    with pytest.raises(BlobNotFound):
        await store.get("published", "slug/a.png")
    assert "delete_blob:slug/a.png:include" in containers["published"].calls


async def test_blobs_without_hash_metadata_report_an_empty_hash() -> None:
    containers = fake_containers()
    containers["media"].blobs["brand/x.png"] = StoredBlob(b"x", None, {}, '"e"')
    info = await store_over(containers).head("media", "brand/x.png")
    assert info is not None
    assert info.sha256 == ""
    assert info.content_type == "application/octet-stream"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/abs.png",
        "../up.png",
        "uploads/../../etc/passwd",
        "uploads/./a.png",
        "uploads//a.png",
        "uploads/a.png/",
        "uploads\\a.png",
        "uploads/a\x00.png",
        "uploads/\na.png",
        "x" * 1025,
    ],
)
async def test_traversal_and_malformed_names_are_refused_before_any_call(name: str) -> None:
    containers = fake_containers()
    store = store_over(containers)
    with pytest.raises(ValueError, match="blob names"):
        await store.put("media", name, b"x", content_type="image/png")
    with pytest.raises(ValueError, match="blob names"):
        await store.get("media", name)
    with pytest.raises(ValueError, match="blob names"):
        await store.head("media", name)
    with pytest.raises(ValueError, match="blob names"):
        await store.delete("media", name)
    assert all_calls(containers) == []


def test_validate_blob_name_accepts_ordinary_names() -> None:
    for name in ("uploads/2026-11/abc.png", "v1-export/2026-11-26/drafts.jsonl", "a"):
        assert validate_blob_name(name) == name


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


def _http_error(status: int) -> HttpResponseError:
    error = HttpResponseError("service said something private")
    error.status_code = status
    return error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(503), StorageUnavailable),
        (_http_error(429), StorageUnavailable),
        (_http_error(403), StorageError),
        (ClientAuthenticationError("no token for you"), StorageError),
        (ServiceRequestError("connection reset"), StorageUnavailable),
    ],
)
async def test_sdk_errors_map_to_storage_errors_without_sdk_text(
    error: Exception, expected: type[StorageError]
) -> None:
    containers = fake_containers()
    containers["media"].fail_with = error
    store = store_over(containers)
    for call in (
        store.put("media", "brand/a.png", b"a", content_type="image/png"),
        store.get("media", "brand/a.png"),
        store.head("media", "brand/a.png"),
        store.delete("media", "brand/a.png"),
        store.list_blobs("media"),
    ):
        with pytest.raises(expected) as caught:
            await call
        assert "private" not in str(caught.value)
        assert "token" not in str(caught.value)
        assert "reset" not in str(caught.value)


async def test_the_store_reports_degraded_until_blob_storage_answers_again() -> None:
    """``degraded`` feeds the System page's "storage degraded": an outage at boot (kept by
    ``check_opened(strict=False)``) or later marks it; any answer, a "not found" included,
    clears it."""
    containers = fake_containers()
    store = store_over(containers)
    assert not store.degraded
    containers["media"].fail_with = _http_error(503)
    await store.check_opened(strict=False)  # kept: the boot goes on
    assert store.degraded
    with pytest.raises(StorageUnavailable):
        await store.head("media", "brand/a.png")
    assert store.degraded
    containers["media"].fail_with = None
    assert await store.head("media", "brand/a.png") is None  # not found is an answer
    assert not store.degraded
    containers["media"].fail_with = _http_error(403)
    with pytest.raises(StorageError):
        await store.get("media", "brand/a.png")
    assert store.degraded
    containers["media"].fail_with = None
    await store.put("media", "brand/a.png", b"a", content_type="image/png")
    assert not store.degraded


async def test_a_missing_container_is_a_storage_error_not_a_missing_blob() -> None:
    containers = fake_containers()
    containers["media"].exists = False
    store = store_over(containers)
    with pytest.raises(StorageError):
        await store.get("media", "brand/a.png")
    with pytest.raises(StorageError):
        await store.head("media", "brand/a.png")
    with pytest.raises(StorageError):
        await store.list_blobs("media")


# --------------------------------------------------------------------------------------------
# open / check / close
# --------------------------------------------------------------------------------------------


async def test_check_containers_requires_all_three() -> None:
    containers = fake_containers()
    store = store_over(containers)
    await store.check_containers()
    containers["published"].exists = False
    with pytest.raises(StorageError, match="published"):
        await store.check_containers()


async def test_open_requires_an_endpoint() -> None:
    with pytest.raises(StorageError, match="BW_STORAGE_BLOB_ENDPOINT"):
        await AzureBlobStore.open(StorageSettings(blob_endpoint=None))


async def test_close_is_idempotent_and_blocks_further_use() -> None:
    closed: list[str] = []

    async def on_close() -> None:
        await asyncio.sleep(0)
        closed.append("closed")

    clients: dict[BlobContainer, BlobContainerClient] = dict(fake_containers())
    store = AzureBlobStore(clients, on_close=on_close)
    await store.close()
    await store.close()
    assert closed == ["closed"]
    with pytest.raises(StorageError, match="closed"):
        await store.get("media", "brand/a.png")


# --------------------------------------------------------------------------------------------
# Never public
# --------------------------------------------------------------------------------------------


async def test_nothing_ever_sets_public_access_or_creates_containers() -> None:
    containers = fake_containers()
    store = store_over(containers)
    await store.check_containers()
    await store.put("media", "brand/a.png", b"a", content_type="image/png")
    await store.put("media", "brand/a.png", b"b", content_type="image/png", overwrite=True)
    await store.get("media", "brand/a.png")
    await store.head("media", "brand/a.png")
    await store.list_blobs("media")
    await store.delete("media", "brand/a.png")
    called = {call.split(":", 1)[0] for call in all_calls(containers)}
    assert called <= {
        "get_blob_client",
        "upload_blob",
        "download_blob",
        "get_blob_properties",
        "delete_blob",
        "list_blobs",
        "get_container_properties",
    }
    for kwargs in containers["media"].upload_kwargs:
        assert set(kwargs) == {"overwrite", "content_settings", "metadata"}


def test_the_storage_package_never_mentions_public_access() -> None:
    pattern = re.compile(
        r"\.(?:create_container|set_container_access_policy|set_container_metadata)\s*\(|"
        r"public_access\s*=|PublicAccess\.|generate_\w*sas\s*\(|account_key\s*="
    )
    offenders = [
        path.name for path in SRC.glob("*.py") if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_outages_map_to_storage_unavailable_problems() -> None:
    problem = problem_from(StorageUnavailable("throttled", status=503))
    assert (problem.status, problem.code) == (503, "storage_unavailable")
    assert isinstance(StorageUnavailable("x"), BlobStoreUnavailable)
