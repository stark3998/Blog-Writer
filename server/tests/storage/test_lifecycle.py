"""StorageLifecycle (``app.state.blobs``) and StorageSettings."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from blogwriter.domain.common import BlobContainer
from blogwriter.domain.testing import MemoryBlobStore
from blogwriter.storage import blobs as blobs_module
from blogwriter.storage.blobs import AzureBlobStore, BlobContainerClient, StorageError
from blogwriter.storage.lifecycle import StorageLifecycle
from blogwriter.storage.settings import StorageSettings

ENDPOINT = "https://stblogwriter.blob.core.windows.net/"


def test_settings_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BLOB_ENDPOINT", "CONTAINER_MEDIA", "MAX_UPLOAD_BYTES", "MAX_IMAGE_PIXELS"):
        monkeypatch.delenv(f"BW_STORAGE_{name}", raising=False)
    settings = StorageSettings()
    assert settings.blob_endpoint is None
    assert not settings.configured
    assert (settings.container_media, settings.container_published, settings.container_backups) == (
        "media",
        "published",
        "backups",
    )
    assert settings.max_upload_bytes == 10_485_760
    assert settings.max_image_pixels == 12_000_000


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_STORAGE_BLOB_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("BW_STORAGE_CONTAINER_BACKUPS", "backups-grs")
    settings = StorageSettings()
    assert settings.configured
    assert settings.container_name("backups") == "backups-grs"
    assert settings.container_name("media") == "media"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://stblogwriter.blob.core.windows.net/",
        "https://user:pw@stblogwriter.blob.core.windows.net/",
        "https://stblogwriter.blob.core.windows.net/media",
        "https://stblogwriter.blob.core.windows.net/?sv=2024&sig=abc",
    ],
)
def test_settings_refuse_bad_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        StorageSettings(blob_endpoint=endpoint)


@pytest.mark.parametrize("name", ["Media", "me", "-media", "media-", "me--dia", "a" * 64])
def test_settings_refuse_bad_container_names(name: str) -> None:
    with pytest.raises(ValidationError):
        StorageSettings(container_media=name)


def test_an_empty_endpoint_counts_as_unset() -> None:
    assert StorageSettings(blob_endpoint="  ").blob_endpoint is None


async def test_unset_endpoint_means_no_storage(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    with caplog.at_level(logging.WARNING, logger="blogwriter.storage.lifecycle"):
        await StorageLifecycle(StorageSettings()).startup(app)
    assert app.state.blobs is None
    assert any(record.getMessage() == "storage_not_configured" for record in caplog.records)
    await StorageLifecycle(StorageSettings()).shutdown(app)


async def test_an_injected_store_is_kept() -> None:
    app = FastAPI()
    injected = MemoryBlobStore()
    app.state.blobs = injected
    lifecycle = StorageLifecycle(StorageSettings(blob_endpoint=ENDPOINT))
    await lifecycle.startup(app)
    assert app.state.blobs is injected
    await lifecycle.shutdown(app)
    assert app.state.blobs is injected


class _Healthy:
    """A container client whose container exists (only what ``check_containers`` calls)."""

    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    def get_blob_client(self, blob: str) -> object:
        raise AssertionError("not used")

    def list_blobs(
        self, name_starts_with: str | None = None, include: list[str] | None = None
    ) -> object:
        raise AssertionError("not used")

    async def get_container_properties(self) -> object:
        from azure.core.exceptions import ResourceNotFoundError

        if not self.exists:
            raise ResourceNotFoundError("missing")
        return {}


async def test_a_configured_endpoint_opens_and_closes_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    async def fake_open(
        cls: type[AzureBlobStore], settings: StorageSettings | None = None, **_: object
    ) -> AzureBlobStore:
        assert settings is not None
        assert settings.blob_endpoint == ENDPOINT

        async def on_close() -> None:
            closed.append(True)

        clients: dict[BlobContainer, BlobContainerClient] = {
            name: _Healthy()  # pyright: ignore[reportAssignmentType] - partial fake
            for name in ("media", "published", "backups")
        }
        store = cls(clients, on_close=on_close)
        await store.check_containers()
        return store

    monkeypatch.setattr(blobs_module.AzureBlobStore, "open", classmethod(fake_open))
    app = FastAPI()
    lifecycle = StorageLifecycle(StorageSettings(blob_endpoint=ENDPOINT))
    await lifecycle.startup(app)
    assert isinstance(app.state.blobs, AzureBlobStore)
    await lifecycle.shutdown(app)
    assert app.state.blobs is None
    assert closed == [True]


async def test_a_configured_endpoint_that_does_not_work_fails_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_open(
        cls: type[AzureBlobStore], settings: StorageSettings | None = None, **_: object
    ) -> AzureBlobStore:
        clients: dict[BlobContainer, BlobContainerClient] = {
            "media": _Healthy(),  # pyright: ignore[reportAssignmentType]
            "published": _Healthy(exists=False),  # pyright: ignore[reportAssignmentType]
            "backups": _Healthy(),  # pyright: ignore[reportAssignmentType]
        }
        store = cls(clients)
        await store.check_containers()
        return store

    monkeypatch.setattr(blobs_module.AzureBlobStore, "open", classmethod(failing_open))
    app = FastAPI()
    with pytest.raises(StorageError, match="published"):
        await StorageLifecycle(StorageSettings(blob_endpoint=ENDPOINT)).startup(app)
    assert getattr(app.state, "blobs", None) is None


class _Down(_Healthy):
    """A container client whose service answers with a network error (a transient outage)."""

    async def get_container_properties(self) -> object:
        from azure.core.exceptions import ServiceRequestError

        raise ServiceRequestError("connection reset")


def _store(client: type[_Healthy]) -> AzureBlobStore:
    clients: dict[BlobContainer, BlobContainerClient] = {
        name: client()  # pyright: ignore[reportAssignmentType] - partial fake
        for name in ("media", "published", "backups")
    }
    return AzureBlobStore(clients)


async def test_a_transient_blob_outage_at_boot_does_not_stop_the_app(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store(_Down)
    with pytest.raises(StorageError):
        await store.check_opened(strict=True)
    with caplog.at_level(logging.WARNING, logger="blogwriter.storage.blobs"):
        await store.check_opened(strict=False)  # kept: only Blob features answer 503
    assert any(record.getMessage() == "storage_degraded" for record in caplog.records)


async def test_a_missing_container_still_stops_the_boot() -> None:
    def missing() -> _Healthy:
        return _Healthy(exists=False)

    clients: dict[BlobContainer, BlobContainerClient] = {
        name: missing()  # pyright: ignore[reportAssignmentType] - partial fake
        for name in ("media", "published", "backups")
    }
    with pytest.raises(StorageError, match="does not exist"):
        await AzureBlobStore(clients).check_opened(strict=False)
