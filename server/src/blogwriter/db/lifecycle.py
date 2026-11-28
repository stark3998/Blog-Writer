"""App wiring for the store: ``startup(app)``, ``shutdown(app)`` and the ``get_store`` dependency.

The store lives on ``app.state.store``.

- ``startup`` keeps a store that is already there (tests inject a ``MemoryStore``) and otherwise
  opens a ``CosmosStore`` from ``BW_COSMOS_*``. It fails closed: no endpoint, no access, or a
  missing container stops the app from starting. There is no in-memory fallback.
- ``shutdown`` closes whatever store is on ``app.state.store``.
- ``get_store`` is the FastAPI dependency routes use (``store: Store = Depends(get_store)``).
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from blogwriter.db.base import Store
from blogwriter.db.cosmos import CosmosStore

__all__ = ["get_store", "shutdown", "startup"]


async def startup(app: FastAPI) -> None:
    """Attach a verified store to ``app.state.store`` (keeps an injected one)."""
    existing: object = getattr(app.state, "store", None)
    if existing is not None:
        if not isinstance(existing, Store):
            raise TypeError("app.state.store is set but is not a blogwriter.db.Store")
        await existing.ensure_containers()
        return
    store = await CosmosStore.from_settings()
    try:
        await store.ensure_containers()
    except BaseException:
        await store.close()
        raise
    app.state.store = store


async def shutdown(app: FastAPI) -> None:
    """Close the store on ``app.state.store``, if any."""
    store: object = getattr(app.state, "store", None)
    if isinstance(store, Store):
        await store.close()


def get_store(request: Request) -> Store:
    """FastAPI dependency: the app's store. Raises if ``startup`` has not run."""
    store: object = getattr(request.app.state, "store", None)
    if not isinstance(store, Store):
        raise RuntimeError("no store on app.state; call blogwriter.db.startup(app) first")
    return store
