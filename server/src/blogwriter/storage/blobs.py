"""Private Blob storage over ``azure.storage.blob.aio`` with Entra ID (:class:`AzureBlobStore`).

:class:`AzureBlobStore` satisfies :class:`blogwriter.domain.ports.BlobStore`. It maps the logical
containers ``media``, ``published`` and ``backups`` to the real container names in
:class:`~blogwriter.storage.settings.StorageSettings` and:

- writes block blobs with their content type and the metadata ``sha256`` (hex SHA-256 of the
  bytes), refusing to overwrite unless asked (:class:`~blogwriter.domain.ports.BlobExists`);
- reads, heads, lists and deletes (idempotently);
- never creates containers and never sets any access level: the account allows no public access
  and Terraform owns the containers. :meth:`AzureBlobStore.open` only checks that the three
  containers exist, so a misconfigured endpoint or a missing role fails the boot.

Blob names are validated before any I/O (:func:`validate_blob_name`): no leading ``/``, no empty,
``.`` or ``..`` segments, no backslashes or control characters.

Errors: :class:`~blogwriter.domain.ports.BlobNotFound` and ``BlobExists`` from the port,
:class:`StorageUnavailable` for throttling, 5xx and network failures (after the SDK's retries)
and :class:`StorageError` for everything else (authorization, a missing container). Messages
carry the operation and the logical container only, never blob content or SDK message text.

Tests use fakes of :class:`BlobContainerClient` (no Azure).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Generator, Mapping
from typing import Any, Final, Protocol, Self

from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from blogwriter.domain.common import BlobContainer
from blogwriter.domain.ports import BlobExists, BlobInfo, BlobNotFound, BlobStoreUnavailable
from blogwriter.storage.settings import StorageSettings, storage_settings

__all__ = [
    "BLOB_CONTAINERS",
    "MAX_BLOB_NAME_CHARS",
    "SHA256_METADATA",
    "AzureBlobStore",
    "BlobClientLike",
    "BlobContainerClient",
    "BlobPropertiesLike",
    "StorageError",
    "StorageUnavailable",
    "validate_blob_name",
]

logger: Final = logging.getLogger(__name__)

BLOB_CONTAINERS: Final[tuple[BlobContainer, ...]] = ("media", "published", "backups")
SHA256_METADATA: Final = "sha256"
"""Metadata key holding the hex SHA-256 of a blob's bytes (set on every put)."""
MAX_BLOB_NAME_CHARS: Final = 1024
_TRANSIENT_STATUSES: Final = frozenset({408, 429, 500, 502, 503, 504})
_FORBIDDEN_CHARS: Final = re.compile(r"[\\\x00-\x1f\x7f]")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


class StorageError(RuntimeError):
    """A Blob operation failed for a reason other than "not found" or "exists"."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class StorageUnavailable(StorageError, BlobStoreUnavailable):
    """Throttling, a 5xx or a network failure: the operation may succeed later (routes inside
    ``domain.deps.problems()`` answer 503 ``storage_unavailable``)."""


def validate_blob_name(name: str) -> str:
    """Return ``name`` if it is a safe blob name, else raise ``ValueError``.

    Names are ``/``-separated segments of printable characters: never empty, never starting
    with ``/``, without empty, ``.`` or ``..`` segments, backslashes or control characters,
    at most :data:`MAX_BLOB_NAME_CHARS` characters.
    """
    if not name or len(name) > MAX_BLOB_NAME_CHARS:
        raise ValueError("blob names have 1-1024 characters")
    if _FORBIDDEN_CHARS.search(name):
        raise ValueError("blob names contain no backslashes or control characters")
    if any(segment in ("", ".", "..") for segment in name.split("/")):
        raise ValueError("blob names have no empty, '.' or '..' segments")
    return name


# --------------------------------------------------------------------------------------------
# The SDK surface the store uses (fakes implement these in tests)
# --------------------------------------------------------------------------------------------


class ContentSettingsLike(Protocol):
    @property
    def content_type(self) -> str | None: ...


class BlobPropertiesLike(Protocol):
    """The part of ``azure.storage.blob.BlobProperties`` the store reads."""

    @property
    def name(self) -> str: ...
    @property
    def size(self) -> int: ...
    @property
    def etag(self) -> str: ...
    @property
    def metadata(self) -> Mapping[str, str]: ...
    @property
    def content_settings(self) -> ContentSettingsLike: ...


class DownloaderLike(Protocol):
    def readall(self) -> Awaitable[bytes]: ...


class BlobClientLike(Protocol):
    """The part of ``azure.storage.blob.aio.BlobClient`` the store uses."""

    def upload_blob(
        self,
        data: bytes,
        *,
        overwrite: bool,
        content_settings: ContentSettings,
        metadata: dict[str, str],
    ) -> Awaitable[Mapping[str, Any]]: ...

    def download_blob(self) -> Awaitable[DownloaderLike]: ...

    def get_blob_properties(self) -> Awaitable[BlobPropertiesLike]: ...

    def delete_blob(self, delete_snapshots: str | None = None) -> Awaitable[None]: ...


class BlobContainerClient(Protocol):
    """The part of ``azure.storage.blob.aio.ContainerClient`` the store uses.

    Deliberately no ``create_container``, ``set_container_access_policy`` or anything else
    that could change a container or its access level.
    """

    def get_blob_client(self, blob: str) -> BlobClientLike: ...

    def list_blobs(
        self, name_starts_with: str | None = None, include: list[str] | None = None
    ) -> AsyncIterator[BlobPropertiesLike]: ...

    def get_container_properties(self) -> Awaitable[object]: ...


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


def _status(exc: AzureError) -> int | None:
    status: object = getattr(exc, "status_code", None)
    return status if isinstance(status, int) and status > 0 else None


def _translate(exc: AzureError, action: str) -> StorageError:
    status = _status(exc)
    if isinstance(exc, ClientAuthenticationError):
        return StorageError(f"{action}: Entra ID authentication failed", status=status)
    if isinstance(exc, HttpResponseError):
        if status is None or status in _TRANSIENT_STATUSES:
            return StorageUnavailable(f"{action}: Blob storage unavailable", status=status)
        return StorageError(f"{action}: Blob storage refused (status {status})", status=status)
    return StorageUnavailable(f"{action}: {type(exc).__name__}", status=status)


@contextlib.contextmanager
def _translated(action: str) -> Generator[None]:
    try:
        yield
    except (ResourceNotFoundError, ResourceExistsError):
        raise
    except AzureError as exc:
        raise _translate(exc, action) from exc


def _container_missing(exc: ResourceNotFoundError) -> bool:
    code: object = getattr(exc, "error_code", None)
    return isinstance(code, str) and code == "ContainerNotFound"


# --------------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------------


class AzureBlobStore:
    """A :class:`~blogwriter.domain.ports.BlobStore` over three Blob containers.

    Build it with :meth:`open` (production) or the constructor (tests inject fake
    :class:`BlobContainerClient` objects), and :meth:`close` it at shutdown.
    """

    def __init__(
        self,
        containers: Mapping[BlobContainer, BlobContainerClient],
        *,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        missing = [name for name in BLOB_CONTAINERS if name not in containers]
        if missing:
            raise ValueError(f"no client for the container(s): {', '.join(missing)}")
        self._containers = dict(containers)
        self._on_close = on_close
        self._closed = False
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """The last call to Blob storage failed (throttling, a 5xx, the network, refused
        access): the System page says "storage degraded" until a call gets an answer again."""
        return self._degraded

    @contextlib.contextmanager
    def _call(self, action: str) -> Generator[None]:
        """:func:`_translated`, recording the store's health: any answer from Blob storage (a
        "not found" included) means it is reachable; a :class:`StorageError` means it is not."""
        try:
            with _translated(action):
                yield
        except (ResourceNotFoundError, ResourceExistsError):
            self._degraded = False
            raise
        except StorageError:
            self._degraded = True
            raise
        else:
            self._degraded = False

    @classmethod
    async def open(
        cls,
        settings: StorageSettings | None = None,
        *,
        credential: AsyncTokenCredential | None = None,
        strict: bool = True,
    ) -> Self:
        """Connect with Entra ID and check that the three containers exist.

        Without ``credential`` a ``DefaultAzureCredential`` is created and closed with the
        store; an injected credential stays the caller's to close. Raises :class:`StorageError`
        when the endpoint is unset, a container is missing or access is refused. With
        ``strict=False`` a check that failed only for a transient reason (throttling, a 5xx, the
        network: :class:`StorageUnavailable`) keeps the store (:meth:`check_opened`).
        """
        resolved = settings if settings is not None else storage_settings()
        if resolved.blob_endpoint is None:
            raise StorageError("BW_STORAGE_BLOB_ENDPOINT is not set")
        owned: AsyncTokenCredential | None
        if credential is None:
            owned = DefaultAzureCredential()
            active: AsyncTokenCredential = owned
        else:
            owned = None
            active = credential
        service = BlobServiceClient(resolved.blob_endpoint, credential=active)

        async def shutdown() -> None:
            try:
                await service.close()
            finally:
                if owned is not None:
                    await owned.close()

        clients: dict[BlobContainer, BlobContainerClient] = {
            name: service.get_container_client(resolved.container_name(name))
            for name in BLOB_CONTAINERS
        }
        store = cls(clients, on_close=shutdown)
        try:
            await store.check_opened(strict=strict)
        except BaseException:
            await store.close()
            raise
        return store

    async def check_opened(self, *, strict: bool = True) -> None:
        """:meth:`check_containers` at startup. A configuration error (a missing container,
        refused access) always raises; a transient outage (:class:`StorageUnavailable`) raises
        only when ``strict``: otherwise it is logged (``storage_degraded``) and the store is
        kept, so a Blob blip at boot takes down only what needs Blob (those operations answer
        ``storage_unavailable`` until it recovers), never the whole app."""
        try:
            await self.check_containers()
        except StorageUnavailable as exc:
            if strict:
                raise
            logger.warning("storage_degraded", extra={"status": exc.status})

    async def check_containers(self) -> None:
        """Raise :class:`StorageError` unless every container exists and is readable."""
        for name in BLOB_CONTAINERS:
            client = self._client(name)
            try:
                with self._call(f"check container {name}"):
                    await client.get_container_properties()
            except ResourceNotFoundError:
                raise StorageError(
                    f"check container {name}: it does not exist", status=404
                ) from None

    def _client(self, container: BlobContainer) -> BlobContainerClient:
        if self._closed:
            raise StorageError("the Blob store is closed")
        try:
            return self._containers[container]
        except KeyError:
            raise ValueError(f"unknown Blob container {container!r}") from None

    async def put(
        self,
        container: BlobContainer,
        name: str,
        data: bytes,
        *,
        content_type: str,
        overwrite: bool = False,
    ) -> BlobInfo:
        """Upload ``data`` with its content type and ``sha256`` metadata.

        :class:`~blogwriter.domain.ports.BlobExists` when the blob exists and ``overwrite`` is
        false (content-addressed names make that harmless).
        """
        blob_name = validate_blob_name(name)
        if not content_type or len(content_type) > 200:
            raise ValueError("content_type must be set")
        payload = bytes(data)
        digest = hashlib.sha256(payload).hexdigest()
        client = self._client(container).get_blob_client(blob_name)
        try:
            with self._call(f"put {container}"):
                result = await client.upload_blob(
                    payload,
                    overwrite=overwrite,
                    content_settings=ContentSettings(content_type=content_type),
                    metadata={SHA256_METADATA: digest},
                )
        except ResourceExistsError:
            raise BlobExists(blob_name) from None
        except ResourceNotFoundError as exc:
            raise StorageError(f"put {container}: container not found", status=404) from exc
        etag: object = result.get("etag")
        return BlobInfo(
            container=container,
            name=blob_name,
            size_bytes=len(payload),
            sha256=digest,
            content_type=content_type,
            etag=etag if isinstance(etag, str) else None,
        )

    async def get(self, container: BlobContainer, name: str) -> bytes:
        """The blob's bytes; :class:`~blogwriter.domain.ports.BlobNotFound` when missing."""
        blob_name = validate_blob_name(name)
        client = self._client(container).get_blob_client(blob_name)
        try:
            with self._call(f"get {container}"):
                downloader = await client.download_blob()
                return await downloader.readall()
        except ResourceNotFoundError as exc:
            if _container_missing(exc):
                raise StorageError(f"get {container}: container not found", status=404) from exc
            raise BlobNotFound(blob_name) from None

    async def head(self, container: BlobContainer, name: str) -> BlobInfo | None:
        """The blob's properties, or ``None`` when it does not exist."""
        blob_name = validate_blob_name(name)
        client = self._client(container).get_blob_client(blob_name)
        try:
            with self._call(f"head {container}"):
                properties = await client.get_blob_properties()
        except ResourceNotFoundError as exc:
            if _container_missing(exc):
                raise StorageError(f"head {container}: container not found", status=404) from exc
            return None
        return _info(container, properties, name=blob_name)

    async def delete(self, container: BlobContainer, name: str) -> None:
        """Delete the blob and its snapshots; deleting a missing blob is not an error."""
        blob_name = validate_blob_name(name)
        client = self._client(container).get_blob_client(blob_name)
        try:
            with self._call(f"delete {container}"):
                await client.delete_blob(delete_snapshots="include")
        except ResourceNotFoundError as exc:
            if _container_missing(exc):
                raise StorageError(f"delete {container}: container not found", status=404) from exc

    async def list_blobs(self, container: BlobContainer, prefix: str = "") -> list[BlobInfo]:
        """Every blob whose name starts with ``prefix``, sorted by name."""
        if prefix and _FORBIDDEN_CHARS.search(prefix):
            raise ValueError("prefixes contain no backslashes or control characters")
        client = self._client(container)
        found: list[BlobInfo] = []
        try:
            with self._call(f"list {container}"):
                async for properties in client.list_blobs(
                    name_starts_with=prefix or None, include=["metadata"]
                ):
                    found.append(_info(container, properties))
        except ResourceNotFoundError as exc:
            raise StorageError(f"list {container}: container not found", status=404) from exc
        found.sort(key=lambda info: info.name)
        return found

    async def close(self) -> None:
        """Close the clients (and the credential the store created). Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            try:
                await self._on_close()
            except Exception as exc:
                logger.warning("storage_close_failed", extra={"error_type": type(exc).__name__})


def _info(
    container: BlobContainer, properties: BlobPropertiesLike, *, name: str | None = None
) -> BlobInfo:
    metadata = properties.metadata or {}
    digest = metadata.get(SHA256_METADATA, "")
    content_type = properties.content_settings.content_type or "application/octet-stream"
    etag = properties.etag
    return BlobInfo(
        container=container,
        name=name if name is not None else properties.name,
        size_bytes=int(properties.size or 0),
        sha256=digest if _SHA256_RE.fullmatch(digest) else "",
        content_type=content_type,
        etag=etag or None,
    )
