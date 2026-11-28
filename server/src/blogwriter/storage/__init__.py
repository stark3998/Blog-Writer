"""Private Blob storage for media, published assets and backups (work order W-A1).

- :class:`AzureBlobStore`: the ``domain.ports.BlobStore`` over ``azure.storage.blob.aio`` with
  Entra ID; content type and ``sha256`` metadata on every put; never public.
- :mod:`.media`: :func:`ingest_image` (owner uploads re-encoded, metadata stripped),
  :func:`media_name` (content-addressed names), :func:`store_media`.
- :func:`get_router`: ``POST /api/media`` and ``GET /api/media/{name}``.
- :class:`StorageLifecycle`: puts the store (or ``None`` when unconfigured) on
  ``app.state.blobs``.

Settings: :class:`StorageSettings` (``BW_STORAGE_*``).
"""

from __future__ import annotations

from blogwriter.storage.blobs import (
    AzureBlobStore,
    BlobContainerClient,
    StorageError,
    StorageUnavailable,
    validate_blob_name,
)
from blogwriter.storage.lifecycle import StorageLifecycle
from blogwriter.storage.media import (
    MEDIA_AREAS,
    IngestedImage,
    MediaArea,
    MediaRejected,
    MediaRole,
    ingest_image,
    media_name,
    sniff_image_type,
    store_media,
)
from blogwriter.storage.routes import get_router, upload_limits
from blogwriter.storage.settings import StorageSettings, storage_settings

__all__ = [
    "MEDIA_AREAS",
    "AzureBlobStore",
    "BlobContainerClient",
    "IngestedImage",
    "MediaArea",
    "MediaRejected",
    "MediaRole",
    "StorageError",
    "StorageLifecycle",
    "StorageSettings",
    "StorageUnavailable",
    "get_router",
    "ingest_image",
    "media_name",
    "sniff_image_type",
    "storage_settings",
    "store_media",
    "upload_limits",
    "validate_blob_name",
]
