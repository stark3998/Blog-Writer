"""In-process :class:`~blogwriter.db.base.Store` with the same semantics as ``CosmosStore``.

For tests and local runs. It shares validation and the filter DSL with ``CosmosStore``, so it
accepts and rejects exactly the same requests. Differences, by design:

- TTL is ignored (``ttl`` is kept in the body; documents never expire). TTL is garbage collection
  only, so code must check an explicit ``expiresAt`` anyway.
- ``ensure_containers`` and ``close`` are no-ops; the store stays usable after ``close`` so one
  instance can back several app instances in a test.

Every operation yields to the event loop once before it runs and then completes atomically, so
concurrent tasks interleave the way they would against a remote store (two tasks that read the
same etag race on replace, and exactly one wins). Use it from one event loop only.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from blogwriter.db.base import (
    CONTAINERS,
    Conflict,
    Doc,
    Filter,
    NotFound,
    OrderBy,
    PreconditionFailed,
    partition_key_path,
    prepare_write,
    resolve_partition_key,
    validate_etag,
    validate_id,
)
from blogwriter.db.query import apply, find_spec

__all__ = ["MemoryStore"]


@dataclass(frozen=True, slots=True)
class _Entry:
    text: str
    etag: str


def _new_etag() -> str:
    return f'"{uuid.uuid4()}"'


class MemoryStore:
    """A dict-backed store. Bodies are kept as JSON text, so every read returns a fresh copy."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[tuple[str, str], _Entry]] = {
            name: {} for name in CONTAINERS
        }

    def _bucket(self, container: str) -> dict[tuple[str, str], _Entry]:
        partition_key_path(container)
        return self._containers[container]

    @staticmethod
    def _doc(item_id: str, partition_key: str, entry: _Entry) -> Doc:
        body: dict[str, Any] = json.loads(entry.text)
        return Doc(id=item_id, partition_key=partition_key, body=body, etag=entry.etag)

    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        item_id = validate_id(id)
        key = (resolve_partition_key(container, item_id, partition_key), item_id)
        entry = bucket.get(key)
        if entry is None:
            raise NotFound(f"read {container}/{item_id}: not found")
        return self._doc(item_id, key[0], entry)

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        prepared = prepare_write(container, body)
        key = (prepared.partition_key, prepared.id)
        if key in bucket:
            raise Conflict(f"create {container}/{prepared.id}: already exists")
        entry = _Entry(text=prepared.text, etag=_new_etag())
        bucket[key] = entry
        return self._doc(prepared.id, prepared.partition_key, entry)

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        prepared = prepare_write(container, body)
        expected = None if if_match is None else validate_etag(if_match)
        key = (prepared.partition_key, prepared.id)
        current = bucket.get(key)
        if current is None:
            raise NotFound(f"replace {container}/{prepared.id}: not found")
        if expected is not None and current.etag != expected:
            raise PreconditionFailed(f"replace {container}/{prepared.id}: etag mismatch")
        entry = _Entry(text=prepared.text, etag=_new_etag())
        bucket[key] = entry
        return self._doc(prepared.id, prepared.partition_key, entry)

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        prepared = prepare_write(container, body)
        entry = _Entry(text=prepared.text, etag=_new_etag())
        bucket[(prepared.partition_key, prepared.id)] = entry
        return self._doc(prepared.id, prepared.partition_key, entry)

    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        item_id = validate_id(id)
        key = (resolve_partition_key(container, item_id, partition_key), item_id)
        expected = None if if_match is None else validate_etag(if_match)
        current = bucket.get(key)
        if current is None:
            raise NotFound(f"delete {container}/{item_id}: not found")
        if expected is not None and current.etag != expected:
            raise PreconditionFailed(f"delete {container}/{item_id}: etag mismatch")
        del bucket[key]

    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> list[Doc]:
        await asyncio.sleep(0)
        bucket = self._bucket(container)
        spec = find_spec(container, filters, order_by, limit, partition_key, fields)
        docs = (self._doc(item_id, pk, entry) for (pk, item_id), entry in bucket.items())
        return apply(spec, docs)

    async def ensure_containers(self) -> None:
        """Every registry container exists by construction."""

    async def close(self) -> None:
        """Nothing to release; the store stays usable."""
