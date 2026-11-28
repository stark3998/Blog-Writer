"""Cosmos DB implementation of :class:`~blogwriter.db.base.Store` (``azure.cosmos.aio``, Entra ID).

The account has key auth disabled, so the store only ever authenticates with an Entra ID token
credential: ``DefaultAzureCredential`` by default (the managed identity on Azure Container Apps,
the Azure CLI login on a workstation), or any injected ``AsyncTokenCredential``.

Errors are mapped to the store's types: 404 -> :class:`NotFound` (point reads, replaces and
deletes; for creates, upserts and queries a 404 means the container or database is missing, a
plain :class:`StoreError`), 409 -> :class:`Conflict`, 412 -> :class:`PreconditionFailed`,
408/429/449/5xx and network failures -> :class:`StoreUnavailable` (after the SDK's own retries),
anything else -> :class:`StoreError` with the status. Messages carry the operation, container,
id and status only, never document content.

Queries are always parameterized (see :mod:`blogwriter.db.query`); a projection
(``find(..., fields=...)``) selects ``id``, ``_etag``, the partition key field and the listed
fields only. The store never creates or deletes containers: :meth:`CosmosStore.ensure_containers`
only checks that every registry container exists with the expected partition key.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Mapping, Sequence
from typing import Any, Final, Protocol, Self, cast

from azure.core import MatchConditions
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError, ClientAuthenticationError
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.identity.aio import DefaultAzureCredential

from blogwriter.db.base import (
    CONTAINERS,
    Conflict,
    ContainerCheckFailed,
    Doc,
    Filter,
    InvalidRequest,
    NotFound,
    OrderBy,
    PreconditionFailed,
    StoreError,
    StoreUnavailable,
    doc_from_stored,
    partition_key_path,
    prepare_write,
    resolve_partition_key,
    validate_etag,
    validate_id,
)
from blogwriter.db.query import compile_query, find_spec
from blogwriter.db.settings import DEFAULT_DATABASE, CosmosSettings, validate_endpoint

__all__ = [
    "TRANSIENT_STATUSES",
    "CosmosContainer",
    "CosmosDatabase",
    "CosmosStore",
    "translate_error",
]

logger = logging.getLogger(__name__)

TRANSIENT_STATUSES: Final[frozenset[int]] = frozenset({408, 429, 449, 500, 502, 503, 504})
"""Statuses reported as :class:`StoreUnavailable` (worth retrying later)."""


class CosmosContainer(Protocol):
    """The part of ``azure.cosmos.aio.ContainerProxy`` the store uses."""

    def read_item(self, item: str, partition_key: str) -> Awaitable[Mapping[str, Any]]: ...

    def create_item(self, body: dict[str, Any]) -> Awaitable[Mapping[str, Any]]: ...

    def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
    ) -> Awaitable[Mapping[str, Any]]: ...

    def upsert_item(self, body: dict[str, Any]) -> Awaitable[Mapping[str, Any]]: ...

    def delete_item(
        self,
        item: str,
        partition_key: str,
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
    ) -> Awaitable[None]: ...

    def query_items(
        self,
        query: str,
        *,
        parameters: list[dict[str, object]] | None = None,
        partition_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...


class CosmosDatabase(Protocol):
    """The part of ``azure.cosmos.aio.DatabaseProxy`` the store uses."""

    def get_container_client(self, container: str) -> CosmosContainer: ...

    def list_containers(self) -> AsyncIterator[dict[str, Any]]: ...


def _status(exc: CosmosHttpResponseError) -> int | None:
    status: object = getattr(exc, "status_code", None)
    return status if isinstance(status, int) and status > 0 else None


def translate_error(exc: AzureError, *, action: str, document_404: bool = True) -> StoreError:
    """Map an Azure SDK error to the store's error types (no SDK message text is copied)."""
    if isinstance(exc, CosmosHttpResponseError):
        status = _status(exc)
        if status == 404:
            if document_404:
                return NotFound(f"{action}: not found")
            return StoreError(f"{action}: container or database not found", status=404)
        if status == 409:
            return Conflict(f"{action}: already exists")
        if status == 412:
            return PreconditionFailed(f"{action}: etag mismatch")
        if status is None or status in TRANSIENT_STATUSES:
            return StoreUnavailable(
                f"{action}: Cosmos unavailable (status {status})", status=status
            )
        return StoreError(f"{action}: Cosmos rejected the request (status {status})", status=status)
    if isinstance(exc, ClientAuthenticationError):
        return StoreError(f"{action}: Entra ID authentication failed ({type(exc).__name__})")
    return StoreUnavailable(f"{action}: {type(exc).__name__}")


@contextlib.contextmanager
def _translated(action: str, *, document_404: bool = True) -> Generator[None]:
    try:
        yield
    except AzureError as exc:
        raise translate_error(exc, action=action, document_404=document_404) from exc


def _partition_paths(properties: Mapping[str, Any]) -> tuple[str, ...]:
    definition: object = properties.get("partitionKey")
    if not isinstance(definition, Mapping):
        return ()
    paths: object = cast(Mapping[str, object], definition).get("paths")
    if not isinstance(paths, list):
        return ()
    return tuple(path for path in cast(list[object], paths) if isinstance(path, str))


async def _close_quietly(client: CosmosClient, credential: AsyncTokenCredential | None) -> None:
    try:
        await client.close()
    except Exception as exc:
        logger.warning("cosmos client close failed", extra={"error": type(exc).__name__})
    if credential is not None:
        try:
            await credential.close()
        except Exception as exc:
            logger.warning("credential close failed", extra={"error": type(exc).__name__})


class CosmosStore:
    """A :class:`~blogwriter.db.base.Store` over one Cosmos database.

    Build it with :meth:`open` or :meth:`from_settings`, call :meth:`ensure_containers` at
    startup and :meth:`close` at shutdown. The constructor takes an already-connected database
    (any :class:`CosmosDatabase`), which is how tests inject fakes.
    """

    def __init__(
        self,
        database: CosmosDatabase,
        *,
        database_name: str = DEFAULT_DATABASE,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._database = database
        self._database_name = database_name
        self._on_close = on_close
        self._containers: dict[str, CosmosContainer] = {}
        self._closed = False

    @classmethod
    async def open(
        cls,
        endpoint: str,
        *,
        database: str = DEFAULT_DATABASE,
        credential: AsyncTokenCredential | None = None,
    ) -> Self:
        """Connect to the account with Entra ID and return a store for ``database``.

        Without ``credential`` a ``DefaultAzureCredential`` is created and closed with the store.
        An injected credential stays the caller's to close. Connecting reads the account
        metadata, so bad endpoints, missing roles and auth failures surface here as
        :class:`StoreError`.
        """
        try:
            url = validate_endpoint(endpoint)
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from None
        name = validate_id(database)
        owned: AsyncTokenCredential | None
        if credential is None:
            owned = DefaultAzureCredential()
            active: AsyncTokenCredential = owned
        else:
            owned = None
            active = credential
        # Compact UTF-8 bodies: without it non-ASCII text is \u-escaped (bigger documents, more RU).
        client = CosmosClient(url, credential=active, enable_compact_utf8_item_writes=True)
        try:
            await client.__aenter__()
        except BaseException as exc:
            await _close_quietly(client, owned)
            if isinstance(exc, AzureError):
                raise translate_error(exc, action="connect to Cosmos", document_404=False) from exc
            raise

        async def shutdown() -> None:
            try:
                await client.close()
            finally:
                if owned is not None:
                    await owned.close()

        return cls(client.get_database_client(name), database_name=name, on_close=shutdown)

    @classmethod
    async def from_settings(
        cls,
        settings: CosmosSettings | None = None,
        *,
        credential: AsyncTokenCredential | None = None,
    ) -> Self:
        """:meth:`open` with ``BW_COSMOS_*`` settings; :class:`StoreError` if no endpoint is set."""
        resolved = settings if settings is not None else CosmosSettings()
        if resolved.endpoint is None:
            raise StoreError("BW_COSMOS_ENDPOINT is not set")
        return await cls.open(resolved.endpoint, database=resolved.database, credential=credential)

    def _container(self, container: str) -> CosmosContainer:
        if self._closed:
            raise StoreError("the Cosmos store is closed")
        client = self._containers.get(container)
        if client is None:
            client = self._database.get_container_client(container)
            self._containers[container] = client
        return client

    @staticmethod
    def _doc(container: str, item: Mapping[str, Any]) -> Doc:
        return doc_from_stored(container, item, item.get("_etag"))

    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        partition_key_path(container)
        item_id = validate_id(id)
        key = resolve_partition_key(container, item_id, partition_key)
        target = self._container(container)
        with _translated(f"read {container}/{item_id}"):
            item = await target.read_item(item=item_id, partition_key=key)
        return self._doc(container, item)

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        prepared = prepare_write(container, body)
        target = self._container(container)
        with _translated(f"create {container}/{prepared.id}", document_404=False):
            item = await target.create_item(body=prepared.body)
        return self._doc(container, item)

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        prepared = prepare_write(container, body)
        expected = None if if_match is None else validate_etag(if_match)
        target = self._container(container)
        with _translated(f"replace {container}/{prepared.id}"):
            if expected is None:
                item = await target.replace_item(item=prepared.id, body=prepared.body)
            else:
                item = await target.replace_item(
                    item=prepared.id,
                    body=prepared.body,
                    etag=expected,
                    match_condition=MatchConditions.IfNotModified,
                )
        return self._doc(container, item)

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        prepared = prepare_write(container, body)
        target = self._container(container)
        with _translated(f"upsert {container}/{prepared.id}", document_404=False):
            item = await target.upsert_item(body=prepared.body)
        return self._doc(container, item)

    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        partition_key_path(container)
        item_id = validate_id(id)
        key = resolve_partition_key(container, item_id, partition_key)
        expected = None if if_match is None else validate_etag(if_match)
        target = self._container(container)
        with _translated(f"delete {container}/{item_id}"):
            if expected is None:
                await target.delete_item(item=item_id, partition_key=key)
            else:
                await target.delete_item(
                    item=item_id,
                    partition_key=key,
                    etag=expected,
                    match_condition=MatchConditions.IfNotModified,
                )

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
        partition_key_path(container)
        spec = find_spec(container, filters, order_by, limit, partition_key, fields)
        query = compile_query(spec)
        target = self._container(container)
        docs: list[Doc] = []
        with _translated(f"find {container}", document_404=False):
            if spec.partition_key is None:
                items = target.query_items(query.text, parameters=query.sdk_parameters())
            else:
                items = target.query_items(
                    query.text,
                    parameters=query.sdk_parameters(),
                    partition_key=spec.partition_key,
                )
            async for item in items:
                docs.append(self._doc(container, item))
        return docs

    async def ensure_containers(self) -> None:
        """Check every :data:`CONTAINERS` entry exists with its partition key; never creates any.

        Raises :class:`ContainerCheckFailed` listing what is missing or mismatched.
        """
        if self._closed:
            raise StoreError("the Cosmos store is closed")
        found: dict[str, tuple[str, ...]] = {}
        action = f"list containers of {self._database_name}"
        try:
            async for properties in self._database.list_containers():
                name: object = properties.get("id")
                if isinstance(name, str):
                    found[name] = _partition_paths(properties)
        except CosmosHttpResponseError as exc:
            if _status(exc) == 404:
                raise ContainerCheckFailed(
                    f"Cosmos database {self._database_name!r} does not exist",
                    missing=sorted(CONTAINERS),
                ) from exc
            raise translate_error(exc, action=action, document_404=False) from exc
        except AzureError as exc:
            raise translate_error(exc, action=action, document_404=False) from exc
        missing = sorted(name for name in CONTAINERS if name not in found)
        mismatched = {
            name: ",".join(found[name]) or "(none)"
            for name in sorted(CONTAINERS)
            if name in found and found[name] != (CONTAINERS[name],)
        }
        if missing or mismatched:
            problems: list[str] = []
            if missing:
                problems.append(f"missing: {', '.join(missing)}")
            if mismatched:
                details = ", ".join(
                    f"{name} (expected {CONTAINERS[name]}, found {paths})"
                    for name, paths in mismatched.items()
                )
                problems.append(f"partition key mismatch: {details}")
            raise ContainerCheckFailed(
                f"Cosmos database {self._database_name!r} does not match the container registry "
                f"({'; '.join(problems)}). Containers come from Terraform (infra/v2/data); "
                "the app never creates them.",
                missing=missing,
                mismatched=mismatched,
            )
        logger.info(
            "cosmos containers verified",
            extra={"database": self._database_name, "containers": len(CONTAINERS)},
        )

    async def close(self) -> None:
        """Close the client (and the credential the store created). Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._containers.clear()
        if self._on_close is not None:
            await self._on_close()
