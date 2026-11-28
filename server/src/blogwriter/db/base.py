"""The storage contract: documents, errors, the container registry, the filter DSL and `Store`.

Every v2 container lives in the Cosmos database ``blog-writer`` and is declared in Terraform
(``infra/v2/data/main.tf``, ``locals.containers``). :data:`CONTAINERS` mirrors that block
(name -> partition key path); a test fails if the two drift apart. The app never creates or
deletes containers.

A document is a JSON object with a string ``id``. Its partition key value is read from the body at
the container's partition key path (``/id`` for most containers, so the id is the partition key).
Both :class:`Store` implementations validate writes the same way before any I/O, so a document
the in-memory store accepts is one Cosmos accepts too:

- ``id``: a non-empty string of at most :data:`MAX_ID_LENGTH` characters, without ``/ \\ ? #``,
  control characters, or leading/trailing whitespace.
- Partition key value: a non-empty string of at most :data:`MAX_PARTITION_KEY_LENGTH` characters.
- Body: JSON only (dict with string keys, list/tuple, str, int, float, bool, None), nested at
  most :data:`MAX_NESTING_DEPTH` levels. Integers must be exactly representable as an IEEE double
  (Cosmos stores every number as one), floats must be finite, and the compact UTF-8 JSON must fit
  in :data:`MAX_DOCUMENT_BYTES`.
- Cosmos system properties (:data:`SYSTEM_PROPERTIES`) may not be written; they are stripped from
  every body a store returns. The etag is exposed as :attr:`Doc.etag` instead.
- ``ttl``, if present, is an int: ``-1`` or at least ``1`` (seconds). TTL is garbage collection
  only: code must still check an explicit ``expiresAt``.

Query semantics (:class:`Filter`, :class:`OrderBy`) follow Cosmos SQL; see
:mod:`blogwriter.db.query`.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal, Protocol, cast, runtime_checkable

__all__ = [
    "CONTAINERS",
    "FILTER_OPS",
    "MAX_DOCUMENT_BYTES",
    "MAX_FIELD_DEPTH",
    "MAX_ID_LENGTH",
    "MAX_NESTING_DEPTH",
    "MAX_PARTITION_KEY_LENGTH",
    "MAX_SAFE_INTEGER",
    "SYSTEM_PROPERTIES",
    "UNINDEXED_FIELDS",
    "Conflict",
    "ContainerCheckFailed",
    "Doc",
    "Filter",
    "FilterOp",
    "InvalidRequest",
    "JsonScalar",
    "NotFound",
    "OrderBy",
    "PreconditionFailed",
    "PreparedWrite",
    "Store",
    "StoreError",
    "StoreUnavailable",
    "doc_from_stored",
    "parse_field_path",
    "partition_key_field",
    "partition_key_path",
    "prepare_write",
    "resolve_partition_key",
    "validate_etag",
    "validate_id",
]

type JsonScalar = str | int | float | bool | None
"""A JSON value that is not an array or an object."""

type FilterOp = Literal["==", "!=", "<", "<=", ">", ">=", "in", "exists"]
"""Operators of the filter DSL."""

FILTER_OPS: Final[frozenset[str]] = frozenset({"==", "!=", "<", "<=", ">", ">=", "in", "exists"})

CONTAINERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "settings": "/id",
        "stories": "/id",
        "ideas": "/id",
        "research": "/id",
        "articles": "/id",
        "article-versions": "/articleId",
        "posts": "/id",
        "sources": "/id",
        "signals": "/sourceId",
        "jobs": "/id",
        "usage": "/month",
        "engagement": "/id",
        "ephemeral": "/id",
        "published-blogs": "/id",
        "published-blogs-staging": "/id",
    }
)
"""Container name -> partition key path. Mirrors ``locals.containers`` in ``infra/v2/data``."""

_UNINDEXED_EVERYWHERE: Final = frozenset({"embedding", "markdown", "htmlContent", "mdxContent"})
_UNINDEXED_EXTRA: Final[Mapping[str, frozenset[str]]] = {
    "stories": frozenset({"body", "text"}),  # style guides, voice samples
    "articles": frozenset({"gates", "servedBy"}),
    "posts": frozenset({"payload", "displayText", "finalText", "aiDraftText"}),
    "jobs": frozenset({"events"}),
    "engagement": frozenset({"pastedText"}),
}
UNINDEXED_FIELDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {name: _UNINDEXED_EVERYWHERE | _UNINDEXED_EXTRA.get(name, frozenset()) for name in CONTAINERS}
)
"""Top-level fields each container excludes from indexing (Terraform ``excluded_paths`` plus
the container's ``extra_excluded_paths``): large text, vectors and reports.

Cosmos refuses to filter or sort on them without a full scan, so both stores reject such
queries up front. Projections (``Store.find(..., fields=...)``) may still read them.
"""

SYSTEM_PROPERTIES: Final[frozenset[str]] = frozenset(
    {"_rid", "_self", "_etag", "_attachments", "_ts", "_lsn"}
)
"""Top-level properties Cosmos owns. Never written; stripped from returned bodies."""

MAX_ID_LENGTH: Final = 255
MAX_PARTITION_KEY_LENGTH: Final = 1024
MAX_DOCUMENT_BYTES: Final = 2_000_000
"""Compact UTF-8 JSON size limit; Cosmos allows 2 MiB including its system properties."""
MAX_NESTING_DEPTH: Final = 64
MAX_SAFE_INTEGER: Final = 2**53 - 1
"""Largest integer an IEEE double holds exactly; Cosmos stores every number as a double."""
MAX_FIELD_DEPTH: Final = 8
"""Most segments a filter or order field path may have (``a.b.c``)."""

_ID_FORBIDDEN = re.compile(r"[/\\?#\x00-\x1f\x7f]")
_FIELD_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RANGE_OPS: Final = frozenset({"<", "<=", ">", ">="})


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class StoreError(Exception):
    """Base class for every error a :class:`Store` raises.

    ``status`` is the HTTP status Cosmos answered with, when there was one.
    """

    default_status: ClassVar[int | None] = None

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status: int | None = status if status is not None else self.default_status


class NotFound(StoreError):
    """The document does not exist (HTTP 404)."""

    default_status = 404


class Conflict(StoreError):
    """A document with this id already exists in the partition (HTTP 409)."""

    default_status = 409


class PreconditionFailed(StoreError):
    """The document changed since the given etag was read (HTTP 412). For claims: claim lost."""

    default_status = 412


class StoreUnavailable(StoreError):
    """A transient failure (throttling, timeout, network, 5xx) after the SDK's own retries.

    The operation may or may not have been applied. Retry later; re-read before re-writing.
    """


class InvalidRequest(StoreError, ValueError):
    """The caller passed something no store accepts. Raised before any I/O; a bug, not a fault."""


class ContainerCheckFailed(StoreError):
    """The database does not match :data:`CONTAINERS` (raised by ``ensure_containers``)."""

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str] = (),
        mismatched: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing: tuple[str, ...] = tuple(missing)
        self.mismatched: dict[str, str] = dict(mismatched or {})


# --------------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Doc:
    """A stored document.

    ``body`` is a fresh copy owned by the caller (no Cosmos system properties); ``etag`` changes on
    every write. To update with compare-and-swap, copy ``body``, change it, and pass ``etag`` as
    ``if_match`` to :meth:`Store.replace`.
    """

    id: str
    partition_key: str
    body: dict[str, Any]
    etag: str


@dataclass(frozen=True, slots=True)
class PreparedWrite:
    """A validated write: normalised body plus its id, partition key value and compact JSON."""

    id: str
    partition_key: str
    body: dict[str, Any]
    text: str


def partition_key_path(container: object) -> str:
    """Return the container's partition key path; :class:`InvalidRequest` if it is unknown."""
    if not isinstance(container, str) or container not in CONTAINERS:
        raise InvalidRequest(f"unknown container {container!r}; see blogwriter.db.CONTAINERS")
    return CONTAINERS[container]


def validate_id(value: object) -> str:
    """Return ``value`` if it is a valid document id, else raise :class:`InvalidRequest`."""
    if not isinstance(value, str) or not value:
        raise InvalidRequest("id must be a non-empty string")
    if len(value) > MAX_ID_LENGTH:
        raise InvalidRequest(f"id is longer than {MAX_ID_LENGTH} characters")
    if _ID_FORBIDDEN.search(value):
        raise InvalidRequest("id contains a forbidden character (/ \\ ? # or a control character)")
    if value.strip() != value:
        raise InvalidRequest("id has leading or trailing whitespace")
    return str.__str__(value)


def validate_etag(value: object) -> str:
    """Return ``value`` if it is a usable etag (a non-empty string)."""
    if not isinstance(value, str) or not value:
        raise InvalidRequest("if_match must be a non-empty etag string")
    return value


def _validate_partition_key_value(container: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        path = CONTAINERS[container]
        raise InvalidRequest(f"{container}: the partition key ({path}) must be a non-empty string")
    if len(value) > MAX_PARTITION_KEY_LENGTH:
        raise InvalidRequest(
            f"{container}: the partition key is longer than {MAX_PARTITION_KEY_LENGTH} characters"
        )
    return str.__str__(value)


def resolve_partition_key(container: str, item_id: str, partition_key: str | None) -> str:
    """Partition key value for a point operation: explicit, or the id for ``/id`` containers."""
    path = partition_key_path(container)
    if partition_key is not None:
        return _validate_partition_key_value(container, partition_key)
    if path == "/id":
        return item_id
    raise InvalidRequest(f"{container} is partitioned by {path}; pass partition_key")


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(path.strip("/").split("/"))


def partition_key_field(container: object) -> str:
    """The top-level field holding the container's partition key (``id``, ``articleId``...)."""
    return _path_segments(partition_key_path(container))[0]


def _value_at(body: Mapping[str, Any], segments: Sequence[str]) -> object:
    current: object = body
    for segment in segments:
        if not isinstance(current, dict):
            return None
        current = cast(dict[str, object], current).get(segment)
    return current


# --------------------------------------------------------------------------------------------
# Write validation (shared by every Store, so they reject exactly the same documents)
# --------------------------------------------------------------------------------------------


class _JsonProblem(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.path: list[str | int] = []


def _render_path(path: Sequence[str | int]) -> str:
    out = "$"
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _normalize(value: object, depth: int) -> Any:
    """Return a plain-JSON deep copy of ``value`` or raise :class:`_JsonProblem`."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise _JsonProblem(f"integer outside +/-{MAX_SAFE_INTEGER} (not exact in Cosmos)")
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _JsonProblem("non-finite float")
        return float(value)
    if depth >= MAX_NESTING_DEPTH:
        raise _JsonProblem(f"nesting deeper than {MAX_NESTING_DEPTH} levels")
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in cast(Mapping[object, object], value).items():
            if not isinstance(key, str):
                raise _JsonProblem(f"non-string key of type {type(key).__name__}")
            try:
                out[str.__str__(key)] = _normalize(item, depth + 1)
            except _JsonProblem as problem:
                problem.path.insert(0, key)
                raise
        return out
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for index, item in enumerate(cast(Sequence[object], value)):
            try:
                items.append(_normalize(item, depth + 1))
            except _JsonProblem as problem:
                problem.path.insert(0, index)
                raise
        return items
    raise _JsonProblem(f"unsupported type {type(value).__name__}")


def prepare_write(container: str, body: object) -> PreparedWrite:
    """Validate and normalise a document for create, replace or upsert into ``container``."""
    path = partition_key_path(container)
    if not isinstance(body, Mapping):
        raise InvalidRequest("document body must be a mapping")
    raw = cast(Mapping[object, object], body)
    reserved = sorted(key for key in raw if isinstance(key, str) and key in SYSTEM_PROPERTIES)
    if reserved:
        raise InvalidRequest(f"document sets Cosmos system properties: {', '.join(reserved)}")
    try:
        normalized: dict[str, Any] = _normalize(raw, 0)
    except _JsonProblem as problem:
        where = _render_path(problem.path)
        raise InvalidRequest(
            f"document is not storable JSON: {problem.reason} at {where}"
        ) from None
    item_id = validate_id(normalized.get("id"))
    if "ttl" in normalized:
        ttl = normalized["ttl"]
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not (ttl == -1 or ttl >= 1):
            raise InvalidRequest("ttl must be an int: -1 or at least 1 (seconds)")
    partition_key = (
        item_id
        if path == "/id"
        else _validate_partition_key_value(container, _value_at(normalized, _path_segments(path)))
    )
    text = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise InvalidRequest(f"document is larger than {MAX_DOCUMENT_BYTES} bytes as JSON")
    return PreparedWrite(id=item_id, partition_key=partition_key, body=normalized, text=text)


def doc_from_stored(container: str, stored: Mapping[str, Any], etag: object) -> Doc:
    """Build a :class:`Doc` from a stored item, dropping Cosmos system properties."""
    body = {key: value for key, value in stored.items() if key not in SYSTEM_PROPERTIES}
    item_id = body.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise StoreError(f"{container}: stored document has no string id")
    if not isinstance(etag, str) or not etag:
        raise StoreError(f"{container}/{item_id}: stored document has no etag")
    path = partition_key_path(container)
    partition_key = item_id if path == "/id" else _value_at(body, _path_segments(path))
    if not isinstance(partition_key, str) or not partition_key:
        raise StoreError(f"{container}/{item_id}: stored document has no string partition key")
    return Doc(id=item_id, partition_key=partition_key, body=body, etag=etag)


# --------------------------------------------------------------------------------------------
# Filter DSL
# --------------------------------------------------------------------------------------------


def parse_field_path(field: object) -> tuple[str, ...]:
    """Split and validate a dotted field path such as ``lease.until``.

    Segments are ASCII identifiers, so a path can be written into SQL text safely (values are
    always parameters). Cosmos system properties cannot be filtered or ordered on.
    """
    if not isinstance(field, str) or not field:
        raise InvalidRequest("field path must be a non-empty string")
    segments = tuple(field.split("."))
    if len(segments) > MAX_FIELD_DEPTH:
        raise InvalidRequest(f"field path {field!r} has more than {MAX_FIELD_DEPTH} segments")
    for segment in segments:
        if _FIELD_SEGMENT.fullmatch(segment) is None:
            raise InvalidRequest(
                f"field path {field!r}: each segment must match [A-Za-z_][A-Za-z0-9_]*"
            )
    if segments[0] in SYSTEM_PROPERTIES:
        raise InvalidRequest(f"field path {field!r} names a Cosmos system property")
    return segments


def _check_scalar(value: object, *, what: str) -> JsonScalar:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise InvalidRequest(f"{what}: integer outside +/-{MAX_SAFE_INTEGER}")
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidRequest(f"{what}: non-finite float")
        return float(value)
    raise InvalidRequest(
        f"{what}: expected str, int, float, bool or None, got {type(value).__name__}"
    )


@dataclass(frozen=True, slots=True)
class Filter:
    """One condition of a query; a query's filters are ANDed.

    ``field`` is a dotted path (``status``, ``lease.until``). By operator, ``value`` is:

    - ``==``: a JSON scalar. Matches only a stored value of the same JSON type that is equal
      (``1`` never matches ``true`` or ``"1"``; ``None`` matches an explicit null, not a missing
      field).
    - ``!=``: a JSON scalar. Matches every stored primitive (null, boolean, number, string) that
      is not an equal value of the same type, so ``!= 1`` matches ``"1"`` and ``null``, and
      ``!= None`` means "present, primitive and not null".
    - ``<``, ``<=``, ``>``, ``>=``: a number or a string. Only stored values of the same type
      match. Strings compare ordinally, so keep range fields ASCII (ISO-8601 UTC timestamps).
    - ``in``: a list or tuple of JSON scalars; ``==`` against any of them (empty matches nothing).
    - ``exists``: ``True`` (the field is present, even if null) or ``False`` (it is absent).

    A missing field never matches anything except ``exists False``; arrays and objects never
    match anything except ``exists``.
    """

    field: str
    op: FilterOp
    value: JsonScalar | Sequence[JsonScalar] = None
    _operand: JsonScalar = dataclasses.field(init=False, repr=False, compare=False, default=None)
    _values: tuple[JsonScalar, ...] = dataclasses.field(
        init=False, repr=False, compare=False, default=()
    )

    def __post_init__(self) -> None:
        parse_field_path(self.field)
        op: object = self.op
        value: object = self.value
        what = f"filter {self.field!r} {op}"
        if op not in FILTER_OPS:
            raise InvalidRequest(f"unknown filter operator {op!r}; use one of {sorted(FILTER_OPS)}")
        if op == "exists":
            if not isinstance(value, bool):
                raise InvalidRequest(f"{what}: value must be True or False")
            object.__setattr__(self, "_operand", value)
        elif op == "in":
            if not isinstance(value, (list, tuple)):
                raise InvalidRequest(f"{what}: value must be a list or tuple of scalars")
            items = tuple(_check_scalar(item, what=what) for item in cast(Sequence[object], value))
            object.__setattr__(self, "value", items)
            object.__setattr__(self, "_values", items)
        else:
            scalar = _check_scalar(value, what=what)
            if op in _RANGE_OPS and (scalar is None or isinstance(scalar, bool)):
                raise InvalidRequest(f"{what}: value must be a number or a string")
            object.__setattr__(self, "value", scalar)
            object.__setattr__(self, "_operand", scalar)

    @property
    def path(self) -> tuple[str, ...]:
        """The field path split into segments."""
        return tuple(self.field.split("."))

    @property
    def operand(self) -> JsonScalar:
        """The value of a comparison (or the bool of ``exists``); ``None`` for ``in``."""
        return self._operand

    @property
    def values(self) -> tuple[JsonScalar, ...]:
        """The candidates of an ``in`` filter; empty for other operators."""
        return self._values


@dataclass(frozen=True, slots=True)
class OrderBy:
    """Sort by one field.

    Only documents whose field holds a JSON primitive (string, number, boolean or null) are
    returned by an ordered query; documents where it is missing, an array or an object are left
    out (both stores add Cosmos's ``IS_PRIMITIVE`` filter). Mixed types sort as null < false <
    true < numbers < strings (reversed when descending). Ties have no defined order.
    """

    field: str
    descending: bool = False

    def __post_init__(self) -> None:
        parse_field_path(self.field)
        descending = cast(object, self.descending)
        if not isinstance(descending, bool):
            raise InvalidRequest("OrderBy.descending must be a bool")

    @property
    def path(self) -> tuple[str, ...]:
        """The field path split into segments."""
        return tuple(self.field.split("."))


# --------------------------------------------------------------------------------------------
# The Store protocol
# --------------------------------------------------------------------------------------------


@runtime_checkable
class Store(Protocol):
    """Document storage with Cosmos semantics. Implemented by ``CosmosStore`` and ``MemoryStore``.

    ``container`` is a name from :data:`CONTAINERS`. ``partition_key`` is only needed for point
    operations on containers not partitioned by ``/id``. Every write returns the new :class:`Doc`
    with a fresh etag.
    """

    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        """Return the document; :class:`NotFound` if it does not exist."""
        ...

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        """Insert a new document; :class:`Conflict` if the id exists in its partition."""
        ...

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        """Overwrite an existing document (located by the body's id and partition key).

        :class:`NotFound` if it does not exist; :class:`PreconditionFailed` if ``if_match`` is
        given and the stored etag differs.
        """
        ...

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        """Insert or overwrite unconditionally."""
        ...

    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        """Delete the document; :class:`NotFound` / :class:`PreconditionFailed` as for replace."""
        ...

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
        """Return the documents matching every filter (see :mod:`blogwriter.db.query`).

        Without ``order_by`` the result order is unspecified. ``limit`` caps the result count;
        ``partition_key`` scopes the query to one partition.

        ``fields`` is a projection: top-level field names (``[A-Za-z_][A-Za-z0-9_]*``, not
        system properties). Each body then holds only ``id``, the partition key field and those
        of the listed fields the document has (a missing one stays missing); the etag is the
        whole document's. A projected body is never a whole document: never write it back.
        Filters and ordering still see the whole document.
        """
        ...

    async def ensure_containers(self) -> None:
        """Fail fast (:class:`ContainerCheckFailed`) unless every registry container exists."""
        ...

    async def close(self) -> None:
        """Release connections. Idempotent."""
        ...
