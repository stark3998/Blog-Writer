"""Opt-in live check of AzureBlobStore against the real account (skipped by default).

Run with ``BW_LIVE_STORAGE=1 BW_STORAGE_BLOB_ENDPOINT=https://<account>.blob.core.windows.net/``
after ``az login`` (Storage Blob Data Contributor). It writes one ``test-live/<uuid>.txt`` blob
in ``backups`` and deletes it again, whatever happens.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from blogwriter.domain.ports import BlobExists, BlobNotFound
from blogwriter.storage.blobs import AzureBlobStore
from blogwriter.storage.settings import StorageSettings

LIVE = os.environ.get("BW_LIVE_STORAGE") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="set BW_LIVE_STORAGE=1 to run against Azure")


async def test_round_trip_on_the_real_account() -> None:
    settings = StorageSettings()
    assert settings.configured, "BW_STORAGE_BLOB_ENDPOINT is required for the live test"
    store = await AzureBlobStore.open(settings)
    name = f"test-live/{uuid.uuid4().hex}.txt"
    data = b"blogwriter live storage check"
    try:
        info = await store.put("backups", name, data, content_type="text/plain")
        assert info.sha256 == hashlib.sha256(data).hexdigest()
        with pytest.raises(BlobExists):
            await store.put("backups", name, data, content_type="text/plain")
        assert await store.get("backups", name) == data
        head = await store.head("backups", name)
        assert head is not None
        assert head.sha256 == info.sha256
        assert head.content_type == "text/plain"
        listed = await store.list_blobs("backups", "test-live/")
        assert name in {blob.name for blob in listed}
    finally:
        await store.delete("backups", name)
        with pytest.raises(BlobNotFound):
            await store.get("backups", name)
        await store.close()
