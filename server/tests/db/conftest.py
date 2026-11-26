"""Fixtures for the db tests: one ``store`` fixture, two backends.

``memory`` always runs. ``cosmos`` is added only with ``BW_LIVE_COSMOS=1`` (and
``BW_COSMOS_ENDPOINT``): the same tests then also run against the real v2 account, authenticated
with ``AzureCliCredential`` (``az login``). ``test_live.py`` holds the live-only checks.

Live safety rules, enforced by :class:`_GuardedStore` rather than by test discipline:

- Writes (create, replace, upsert, delete) may only touch ids starting with ``test-`` in the
  ``ephemeral`` and ``jobs`` containers. Anything else fails the test before any request.
- Every write is recorded *before* it is sent, so a write whose response was lost is still
  cleaned up.
- Teardown (which runs on failure too) deletes every recorded id, then sweeps both containers
  for documents carrying this test's ``suite`` token. Test documents also carry ``ttl: 3600``,
  so anything a crashed run leaves behind expires within the hour.
"""

from __future__ import annotations

import itertools
import os
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from types import TracebackType
from typing import Any

import pytest
from azure.core.credentials import AccessToken
from azure.identity.aio import AzureCliCredential

from blogwriter.db import (
    CosmosStore,
    Doc,
    Filter,
    InvalidRequest,
    MemoryStore,
    NotFound,
    OrderBy,
    Store,
    StoreError,
)
from blogwriter.db.base import (
    partition_key_path,
    prepare_write,
    resolve_partition_key,
    validate_id,
)

LIVE = os.environ.get("BW_LIVE_COSMOS") == "1"
LIVE_CONTAINERS = frozenset({"ephemeral", "jobs"})
RUN_ID = f"test-{uuid.uuid4().hex[:10]}"
"""Prefix of every id this test session writes."""

_token_numbers = itertools.count(1)


class _TokenCache:
    """``AzureCliCredential`` with tokens cached per scope (one ``az`` call per scope per run)."""

    def __init__(self) -> None:
        self._inner = AzureCliCredential()
        self._tokens: dict[tuple[str, ...], AccessToken] = {}

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> AccessToken:
        cached = self._tokens.get(scopes)
        if claims is None and cached is not None and cached.expires_on - time.time() > 300:
            return cached
        token = await self._inner.get_token(*scopes, claims=claims, tenant_id=tenant_id, **kwargs)
        self._tokens[scopes] = token
        return token

    async def close(self) -> None:
        await self._inner.close()

    async def __aenter__(self) -> _TokenCache:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()


class _GuardedStore:
    """Live-test wrapper: polices and records writes; reads and queries pass through."""

    def __init__(self, inner: Store, written: list[tuple[str, str]]) -> None:
        self._inner = inner
        self._written = written

    def _admit(self, container: str, item_id: str) -> None:
        if container not in LIVE_CONTAINERS:
            raise AssertionError(f"live tests may not write to {container!r}")
        if not item_id.startswith("test-"):
            raise AssertionError(f"live tests may only write ids starting with 'test-': {item_id}")
        self._written.append((container, item_id))

    def _admit_write(self, container: str, body: Mapping[str, Any]) -> None:
        try:
            prepared = prepare_write(container, body)
        except InvalidRequest:
            return  # rejected by validation before any request is sent
        self._admit(container, prepared.id)

    def _admit_delete(self, container: str, item_id: str, partition_key: str | None) -> None:
        try:
            partition_key_path(container)
            resolve_partition_key(container, validate_id(item_id), partition_key)
        except InvalidRequest:
            return  # rejected by validation before any request is sent
        self._admit(container, item_id)

    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        return await self._inner.read(container, id, partition_key=partition_key)

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._admit_write(container, body)
        return await self._inner.create(container, body)

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        self._admit_write(container, body)
        return await self._inner.replace(container, body, if_match=if_match)

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._admit_write(container, body)
        return await self._inner.upsert(container, body)

    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        self._admit_delete(container, id, partition_key)
        await self._inner.delete(container, id, partition_key=partition_key, if_match=if_match)

    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
    ) -> list[Doc]:
        return await self._inner.find(
            container, filters, order_by=order_by, limit=limit, partition_key=partition_key
        )

    async def ensure_containers(self) -> None:
        await self._inner.ensure_containers()

    async def close(self) -> None:
        """The fixture closes the inner store after cleanup."""


async def _cleanup(store: Store, written: list[tuple[str, str]], token: str) -> None:
    """Delete everything the test wrote; every item is attempted even if one fails."""
    failures: list[str] = []

    async def remove(container: str, item_id: str) -> None:
        try:
            await store.delete(container, item_id)
        except NotFound:
            pass
        except StoreError as exc:
            failures.append(f"{container}/{item_id}: {type(exc).__name__}")

    for container, item_id in reversed(written):
        await remove(container, item_id)
    for container in sorted(LIVE_CONTAINERS):
        try:
            leftovers = await store.find(container, [Filter("suite", "==", token)])
        except StoreError as exc:
            failures.append(f"sweep {container}: {type(exc).__name__}")
            continue
        for doc in leftovers:
            if doc.id.startswith("test-"):
                await remove(container, doc.id)
    if failures:
        raise AssertionError(f"live cleanup incomplete (ttl expires them): {failures}")


@pytest.fixture(scope="session")
def live_credential() -> _TokenCache:
    """Shared across tests so the run asks ``az`` for a token once."""
    return _TokenCache()


@pytest.fixture
def token() -> str:
    """A unique ``test-...`` prefix and ``suite`` marker for one test."""
    return f"{RUN_ID}-{next(_token_numbers)}"


@pytest.fixture(params=["memory", "cosmos"] if LIVE else ["memory"])
async def store(
    request: pytest.FixtureRequest, token: str, live_credential: _TokenCache
) -> AsyncIterator[Store]:
    if request.param == "memory":
        yield MemoryStore()
        return
    inner = await CosmosStore.from_settings(credential=live_credential)
    written: list[tuple[str, str]] = []
    try:
        yield _GuardedStore(inner, written)
    finally:
        try:
            await _cleanup(inner, written, token)
        finally:
            await inner.close()


@pytest.fixture
def guarded() -> tuple[Store, list[tuple[str, str]]]:
    """The live-suite guard around a MemoryStore, plus its write log (tests the guard itself)."""
    written: list[tuple[str, str]] = []
    return _GuardedStore(MemoryStore(), written), written
