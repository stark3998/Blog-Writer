"""Read-only access to v1's Cosmos database (``blog-writer`` in the separate v1 account).

:class:`V1Source` has no write method, and :class:`CosmosV1Source` holds the SDK database only
through :class:`V1Database`/:class:`V1Container`, which expose listing and queries and nothing
else: the migration cannot change v1, whatever the key it was given allows.

Session and OAuth-state containers (LinkedIn, X and Medium tokens) are never read
(:func:`is_excluded_container`). Every v1 container used ``/id`` as its partition key; items are
read by query anyway, so another layout still works.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Final, Protocol, Self

from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential

from blogwriter.backups.migrate_v1.settings import MigrateV1Settings

__all__ = [
    "EXCLUDED_CONTAINERS",
    "CosmosV1Source",
    "MemoryV1Source",
    "V1Container",
    "V1Database",
    "V1Source",
    "V1SourceError",
    "is_excluded_container",
    "strip_system_properties",
]

EXCLUDED_CONTAINERS: Final = frozenset(
    {
        "linkedin-sessions",
        "linkedin-oauth-states",
        "twitter-sessions",
        "twitter-oauth-states",
        "medium-sessions",
    }
)
"""v1's session and OAuth-state containers (access tokens). Never read, never exported."""
_EXCLUDED_RE: Final = re.compile(r"session|state|oauth|token|secret|credential", re.IGNORECASE)
_DROPPED_SYSTEM: Final = frozenset({"_rid", "_self", "_etag", "_attachments", "_lsn"})
"""Cosmos system properties left out of exports (``_ts``, the last-write time, is kept)."""


def is_excluded_container(name: str) -> bool:
    """Session and state containers, by name or by pattern (fail closed on a lookalike)."""
    return name in EXCLUDED_CONTAINERS or _EXCLUDED_RE.search(name) is not None


def strip_system_properties(item: Mapping[str, Any]) -> dict[str, Any]:
    """The item without Cosmos system properties (``_ts`` kept)."""
    return {key: value for key, value in item.items() if key not in _DROPPED_SYSTEM}


class V1SourceError(RuntimeError):
    """v1 could not be read. The message names the operation only (no SDK text, no key)."""


class V1Source(Protocol):
    """What the migration reads from v1. There is deliberately no write method."""

    async def list_containers(self) -> list[str]: ...

    def iter_items(self, container: str) -> AsyncIterator[dict[str, Any]]: ...

    async def read_item(self, container: str, item_id: str) -> dict[str, Any] | None: ...

    async def close(self) -> None: ...


class V1Container(Protocol):
    """The read-only part of ``azure.cosmos.aio.ContainerProxy`` the source uses."""

    def query_items(
        self, query: str, *, parameters: list[dict[str, object]] | None = None
    ) -> AsyncIterator[dict[str, Any]]: ...


class V1Database(Protocol):
    """The read-only part of ``azure.cosmos.aio.DatabaseProxy`` the source uses."""

    def get_container_client(self, container: str) -> V1Container: ...

    def list_containers(self) -> AsyncIterator[dict[str, Any]]: ...


class CosmosV1Source:
    """:class:`V1Source` over the v1 account (an account key or ``DefaultAzureCredential``)."""

    def __init__(
        self, database: V1Database, *, on_close: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        self._database = database
        self._on_close = on_close
        self._closed = False

    @classmethod
    async def open(
        cls, settings: MigrateV1Settings, *, credential: AsyncTokenCredential | None = None
    ) -> Self:
        """Connect to the v1 account. :class:`V1SourceError` without an endpoint or access."""
        if settings.endpoint is None:
            raise V1SourceError("BW_MIGRATE_V1_ENDPOINT is not set")
        owned: AsyncTokenCredential | None = None
        if settings.key is not None:
            client = CosmosClient(settings.endpoint, credential=settings.key.get_secret_value())
        elif credential is not None:
            client = CosmosClient(settings.endpoint, credential=credential)
        else:
            owned = DefaultAzureCredential()
            client = CosmosClient(settings.endpoint, credential=owned)

        async def shutdown() -> None:
            try:
                await client.close()
            finally:
                if owned is not None:
                    await owned.close()

        try:
            await client.__aenter__()
        except AzureError as exc:
            await shutdown()
            raise V1SourceError(f"connect to v1 failed ({type(exc).__name__})") from None
        return cls(client.get_database_client(settings.database), on_close=shutdown)

    async def list_containers(self) -> list[str]:
        names: list[str] = []
        try:
            async for properties in self._database.list_containers():
                name: object = properties.get("id")
                if isinstance(name, str):
                    names.append(name)
        except AzureError as exc:
            raise V1SourceError(f"list v1 containers failed ({type(exc).__name__})") from None
        return sorted(names)

    def iter_items(self, container: str) -> AsyncIterator[dict[str, Any]]:
        if is_excluded_container(container):
            raise V1SourceError(f"{container} is a session or state container and is never read")
        client = self._database.get_container_client(container)

        async def items() -> AsyncIterator[dict[str, Any]]:
            try:
                async for item in client.query_items("SELECT * FROM c"):
                    yield dict(item)
            except AzureError as exc:
                raise V1SourceError(f"read v1 {container} failed ({type(exc).__name__})") from None

        return items()

    async def read_item(self, container: str, item_id: str) -> dict[str, Any] | None:
        if is_excluded_container(container):
            raise V1SourceError(f"{container} is a session or state container and is never read")
        client = self._database.get_container_client(container)
        try:
            async for item in client.query_items(
                "SELECT * FROM c WHERE c.id = @id", parameters=[{"name": "@id", "value": item_id}]
            ):
                return dict(item)
        except AzureError as exc:
            raise V1SourceError(f"read v1 {container} failed ({type(exc).__name__})") from None
        return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            await self._on_close()


class MemoryV1Source:
    """An in-memory :class:`V1Source` for tests and rehearsals. Records what was read."""

    def __init__(self, containers: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self._containers = {
            name: [dict(item) for item in items] for name, items in containers.items()
        }
        self.iterated: list[str] = []
        self.reads: list[tuple[str, str]] = []

    async def list_containers(self) -> list[str]:
        return sorted(self._containers)

    def iter_items(self, container: str) -> AsyncIterator[dict[str, Any]]:
        if is_excluded_container(container):
            raise V1SourceError(f"{container} is a session or state container and is never read")
        self.iterated.append(container)
        items = [dict(item) for item in self._containers.get(container, [])]

        async def generate() -> AsyncIterator[dict[str, Any]]:
            for item in items:
                yield item

        return generate()

    async def read_item(self, container: str, item_id: str) -> dict[str, Any] | None:
        if is_excluded_container(container):
            raise V1SourceError(f"{container} is a session or state container and is never read")
        self.reads.append((container, item_id))
        for item in self._containers.get(container, []):
            if item.get("id") == item_id:
                return dict(item)
        return None

    async def close(self) -> None:
        return None
