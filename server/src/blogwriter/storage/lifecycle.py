"""App wiring for Blob storage: ``app.state.blobs`` (:class:`StorageLifecycle`).

- Unset ``BW_STORAGE_BLOB_ENDPOINT``: storage is off. ``app.state.blobs = None``, the warning
  ``storage_not_configured`` is logged and routes that need storage answer 503
  ``storage_unavailable`` (``domain.deps.BlobsDep``); jobs fail ``storage_unavailable``.
- Set endpoint: :meth:`AzureBlobStore.open` connects with Entra ID and checks the three
  containers. A configuration error (a missing container, refused access) stops the boot (like
  ``db``): a configured store that does not work is a deployment error, not a feature switch.
  A transient outage (throttling, a 5xx, the network) does not: the store is kept and logged
  as ``storage_degraded``, so only what needs Blob answers ``storage_unavailable`` until it
  recovers (the contract's rule for a missing service), instead of a boot loop.
- A store already on ``app.state.blobs`` (tests inject ``MemoryBlobStore``) is kept.

Shutdown closes the store this lifecycle opened.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import FastAPI

from blogwriter.storage.blobs import AzureBlobStore
from blogwriter.storage.settings import StorageSettings, storage_settings

__all__ = ["StorageLifecycle", "shutdown", "startup"]

logger: Final = logging.getLogger(__name__)


class StorageLifecycle:
    """``startup`` sets ``app.state.blobs`` to an :class:`AzureBlobStore` or ``None``."""

    def __init__(self, settings: StorageSettings | None = None) -> None:
        self._settings = settings
        self._opened: AzureBlobStore | None = None

    async def startup(self, app: FastAPI) -> None:
        existing: object = getattr(app.state, "blobs", None)
        if existing is not None:
            return
        settings = self._settings if self._settings is not None else storage_settings()
        if not settings.configured:
            app.state.blobs = None
            logger.warning("storage_not_configured")
            return
        store = await AzureBlobStore.open(settings, strict=False)
        self._opened = store
        app.state.blobs = store

    async def shutdown(self, app: FastAPI) -> None:
        store = self._opened
        self._opened = None
        if store is not None:
            if getattr(app.state, "blobs", None) is store:
                app.state.blobs = None
            await store.close()


_default = StorageLifecycle()


async def startup(app: FastAPI) -> None:
    """:meth:`StorageLifecycle.startup` with ``BW_STORAGE_*`` settings."""
    await _default.startup(app)


async def shutdown(app: FastAPI) -> None:
    """Close the store :func:`startup` opened."""
    await _default.shutdown(app)
