"""db.startup / db.shutdown / db.get_store: wiring the store into a FastAPI app, fail-closed."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from blogwriter import db
from blogwriter.db import ContainerCheckFailed, CosmosStore, MemoryStore, Store, StoreError


class RecordingStore(MemoryStore):
    def __init__(self, *, check_error: StoreError | None = None) -> None:
        super().__init__()
        self.checks = 0
        self.closes = 0
        self._check_error = check_error

    async def ensure_containers(self) -> None:
        self.checks += 1
        if self._check_error is not None:
            raise self._check_error

    async def close(self) -> None:
        self.closes += 1


def app_with_route() -> FastAPI:
    app = FastAPI()

    @app.get("/settings/{item_id}")
    async def read_setting(  # pyright: ignore[reportUnusedFunction]
        item_id: str, store: Annotated[Store, Depends(db.get_store)]
    ) -> dict[str, Any]:
        return (await store.read("settings", item_id)).body

    return app


def patch_cosmos(monkeypatch: pytest.MonkeyPatch, store: Store) -> list[int]:
    calls: list[int] = []

    async def from_settings(*args: object, **kwargs: object) -> Store:
        calls.append(1)
        return store

    monkeypatch.setattr(CosmosStore, "from_settings", from_settings)
    return calls


async def test_startup_keeps_an_injected_store_and_checks_it() -> None:
    app = app_with_route()
    injected = RecordingStore()
    app.state.store = injected
    await db.startup(app)
    assert app.state.store is injected
    assert injected.checks == 1


async def test_startup_rejects_a_non_store() -> None:
    app = FastAPI()
    app.state.store = object()
    with pytest.raises(TypeError):
        await db.startup(app)


async def test_startup_without_an_endpoint_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BW_COSMOS_ENDPOINT", raising=False)
    app = FastAPI()
    with pytest.raises(StoreError, match="BW_COSMOS_ENDPOINT"):
        await db.startup(app)
    assert getattr(app.state, "store", None) is None


async def test_startup_opens_cosmos_and_attaches_it(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = RecordingStore()
    calls = patch_cosmos(monkeypatch, opened)
    app = FastAPI()
    await db.startup(app)
    assert calls == [1]
    assert app.state.store is opened
    assert opened.checks == 1
    await db.shutdown(app)
    assert opened.closes == 1


async def test_startup_closes_the_store_when_the_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = RecordingStore(check_error=ContainerCheckFailed("missing: jobs", missing=["jobs"]))
    patch_cosmos(monkeypatch, broken)
    app = FastAPI()
    with pytest.raises(ContainerCheckFailed):
        await db.startup(app)
    assert broken.closes == 1
    assert getattr(app.state, "store", None) is None


async def test_shutdown_without_a_store_is_a_no_op() -> None:
    await db.shutdown(FastAPI())


async def test_routes_get_the_store_through_the_dependency() -> None:
    app = app_with_route()
    store = MemoryStore()
    await store.create("settings", {"id": "cadence", "postsPerWeek": 3})
    app.state.store = store
    await db.startup(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings/cadence")
    assert response.status_code == 200
    assert response.json() == {"id": "cadence", "postsPerWeek": 3}


async def test_get_store_without_startup_raises() -> None:
    app = app_with_route()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="startup"):
            await client.get("/settings/cadence")
