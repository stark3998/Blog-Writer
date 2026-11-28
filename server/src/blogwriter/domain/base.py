"""Shared building blocks of every stored document: the base models, field types and helpers.

Conventions (binding for every package; see ``docs/v2/build/contract.md`` section 1):

- **Wire and Cosmos naming.** Documents are camelCase JSON (``createdAt``, ``statusChangedAt``);
  Python attributes are snake_case. :class:`DomainModel` does the mapping, accepts either name on
  input and writes camelCase.
- **Unknown fields survive.** Models keep fields they do not know (``extra="allow"``) and write
  them back unchanged, so a document written by a newer revision survives a write by an older one
  during a revision overlap (the same rule as ``jobs.models``). API *request* models are a
  different thing: packages define them with ``extra="forbid"`` and never accept a whole document
  from a client.
- **Timestamps** are fixed-width ISO-8601 UTC strings with microseconds,
  ``2026-11-26T15:04:05.123456Z`` (:func:`to_iso`), identical to ``jobs.clock`` and the AI ledger,
  so ordinal string comparison is time comparison in store queries.
- **Ids** are ``<prefix>-<12 hex ms><16 hex random>`` (:func:`new_id`): unique, safe for Cosmos
  (:func:`blogwriter.db.base.validate_id`) and sortable by creation time. Documents created by
  job steps use :func:`derived_id` (``<prefix>-<28 hex>`` from stable parts) so a resumed step
  gets a ``Conflict`` instead of creating a duplicate.
- **Every document carries ``kind``** (:attr:`Document.KIND`), and every query filters on it,
  because several kinds share a container.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Annotated, Any, ClassVar, Final, Literal, Self, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from blogwriter.db.base import CONTAINERS, validate_id

__all__ = [
    "CODE_PATTERN",
    "HOST_PATTERN",
    "PARTIAL_DATE_PATTERN",
    "SLUG_PATTERN",
    "WEEKDAYS",
    "Code",
    "DocId",
    "Document",
    "DomainModel",
    "ExpiringDocument",
    "HostName",
    "HttpsUrl",
    "LocalDate",
    "LocalTime",
    "PartialDate",
    "Sha256Hex",
    "SingletonDocument",
    "Slug",
    "Timestamp",
    "Weekday",
    "WrongKind",
    "ZoneName",
    "canonical_json",
    "check_https_url",
    "derived_id",
    "distinct_stamps",
    "is_timestamp",
    "new_id",
    "now_iso",
    "parse_iso",
    "sha256_hex",
    "to_iso",
    "utc_now",
]

# --------------------------------------------------------------------------------------------
# Timestamps and calendar values
# --------------------------------------------------------------------------------------------

_TIMESTAMP_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_DATE_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE: Final = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def utc_now() -> datetime:
    """The current time, timezone-aware UTC."""
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Format an aware datetime as fixed-width ISO-8601 UTC (``...T..:..:...ffffffZ``)."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def now_iso() -> str:
    """:func:`to_iso` of :func:`utc_now`."""
    return to_iso(utc_now())


def parse_iso(text: str) -> datetime:
    """Parse a timestamp written by :func:`to_iso`; anything else raises ``ValueError``."""
    if not _TIMESTAMP_RE.fullmatch(text):
        raise ValueError("timestamp must look like 2026-11-26T15:04:05.000000Z")
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)


def distinct_stamps(start: datetime, count: int) -> list[str]:
    """``count`` strictly increasing timestamps from ``start``, one microsecond apart.

    For documents written together (one step saving a batch): no two share an ``updatedAt``, so
    a keyset page on it (``Filter("updatedAt", "<", cursor)``) never splits a tie.
    """
    if count < 0:
        raise ValueError("count must not be negative")
    return [to_iso(start + timedelta(microseconds=index)) for index in range(count)]


def is_timestamp(value: object) -> bool:
    """Whether ``value`` is a string :func:`parse_iso` accepts."""
    if not isinstance(value, str):
        return False
    try:
        parse_iso(value)
    except ValueError:
        return False
    return True


def _check_timestamp(value: str) -> str:
    parse_iso(value)
    return value


def _check_date(value: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise ValueError("date must look like 2026-11-30")
    date.fromisoformat(value)
    return value


def _check_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an IANA zone name such as America/New_York") from None
    return value


type Timestamp = Annotated[str, AfterValidator(_check_timestamp)]
"""Fixed-width ISO-8601 UTC with microseconds (:func:`to_iso`)."""
type LocalDate = Annotated[str, AfterValidator(_check_date)]
"""A calendar date ``YYYY-MM-DD`` (local to the zone stored next to it)."""
type LocalTime = Annotated[str, StringConstraints(pattern=_TIME_RE.pattern)]
"""A wall-clock time ``HH:MM`` (00:00-23:59)."""
type ZoneName = Annotated[str, AfterValidator(_check_zone)]
"""An IANA time zone name, checked with :mod:`zoneinfo`."""

type Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
"""Same spelling as ``blogwriter.jobs.scheduler.Weekday``."""
WEEKDAYS: Final[tuple[Weekday, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
"""Index = ``date.weekday()``."""

PARTIAL_DATE_PATTERN: Final = r"^[0-9]{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12][0-9]|3[01]))?)?$"
type PartialDate = Annotated[str, StringConstraints(pattern=PARTIAL_DATE_PATTERN)]
"""``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` (the published-blogs citation date rule)."""

# --------------------------------------------------------------------------------------------
# Identifiers, slugs, hashes and URLs
# --------------------------------------------------------------------------------------------

SLUG_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
"""Lowercase kebab-case, the published-blogs contract and dialect slug rule."""
CODE_PATTERN: Final = r"^[a-z][a-z0-9_.-]{0,63}$"
"""Machine-readable codes (finding codes, error codes, step names)."""
HOST_PATTERN: Final = r"^(?=.{4,253}$)(?:(?!-)[a-z0-9-]{1,63}(?<!-)\.)+(?!-)[a-z0-9-]{1,63}(?<!-)$"
"""A bare lowercase host name with at least two labels (``learn.microsoft.com``)."""
_PREFIX_RE: Final = re.compile(r"[a-z][a-z0-9]{0,11}")
_HOST_RE: Final = re.compile(HOST_PATTERN)
_URL_FORBIDDEN: Final = re.compile(r"[\s\\\x00-\x1f\x7f]")
MAX_URL_CHARS: Final = 2048

type DocId = Annotated[str, AfterValidator(validate_id)]
"""A Cosmos-safe document id (:func:`blogwriter.db.base.validate_id`)."""
type Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=80)]
type Code = Annotated[str, StringConstraints(pattern=CODE_PATTERN)]
type Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A SHA-256 digest as 64 lowercase hex characters."""


def _check_host(value: str) -> str:
    if not _HOST_RE.fullmatch(value):
        raise ValueError("host must be a bare lowercase host name such as learn.microsoft.com")
    return value


type HostName = Annotated[str, AfterValidator(_check_host)]


def check_https_url(value: str) -> str:
    """Return ``value`` if it is an absolute ``https`` URL we are willing to store.

    No whitespace, backslashes or control characters, no userinfo, a host, at most
    :data:`MAX_URL_CHARS` characters. The egress helper still re-checks every fetch.
    """
    if len(value) > MAX_URL_CHARS:
        raise ValueError(f"URL is longer than {MAX_URL_CHARS} characters")
    if _URL_FORBIDDEN.search(value):
        raise ValueError("URL contains whitespace, a backslash or a control character")
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("URL must use https")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("URL must have a host and no credentials")
    return value


type HttpsUrl = Annotated[str, AfterValidator(check_https_url)]


def new_id(prefix: str, *, now: datetime | None = None) -> str:
    """A fresh id ``<prefix>-<12 hex ms><16 hex random>``, sortable by creation time.

    ``prefix`` is 1-12 lowercase letters or digits starting with a letter (``art``, ``post``).
    """
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(
            "id prefix must be 1-12 lowercase letters or digits, starting with a letter"
        )
    moment = now if now is not None else utc_now()
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    millis = int(moment.timestamp() * 1000)
    if millis < 0:
        raise ValueError("ids cannot be minted for times before 1970")
    return f"{prefix}-{millis:012x}{secrets.token_hex(8)}"


def derived_id(prefix: str, *parts: str | int) -> str:
    """A deterministic id ``<prefix>-<28 hex>`` for a document a retry-safe step creates.

    The hex is the SHA-256 of the parts (joined with a unit separator), so the same parts always
    give the same id and a retried create is a ``Conflict`` instead of a duplicate. Same shape
    as :func:`new_id` (so it passes the same checks), but not sortable by time, and the parts
    cannot be read back from it (keys may hold ids only, never content).
    """
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(
            "id prefix must be 1-12 lowercase letters or digits, starting with a letter"
        )
    if not parts:
        raise ValueError("a derived id needs at least one part")
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}-{sha256_hex(material)[:28]}"


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 as lowercase hex; ``str`` input is UTF-8 encoded."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: object) -> bytes:
    """Deterministic JSON bytes for hashing: sorted keys, compact, UTF-8, no NaN."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


# --------------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------------


class WrongKind(ValueError):
    """A stored body is not the kind of document the caller asked for."""

    def __init__(self, expected: str, found: object) -> None:
        found_text = found if isinstance(found, str) and len(found) <= 64 else type(found).__name__
        super().__init__(f"expected kind {expected!r}, found {found_text!r}")
        self.expected = expected


class DomainModel(BaseModel):
    """Base of every stored model and sub-model: camelCase on the wire, unknown fields kept."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="allow",
    )


@cache
def _computed_keys(cls: type[BaseModel]) -> frozenset[str]:
    """The names and aliases of ``cls``'s computed fields."""
    keys: set[str] = set()
    for name, info in cls.model_computed_fields.items():
        keys.add(name)
        keys.add(info.alias or to_camel(name))
    return frozenset(keys)


class Document(DomainModel):
    """A top-level document in one Cosmos container.

    Subclasses set the class variables:

    ``CONTAINER``
        A name from :data:`blogwriter.db.CONTAINERS`.
    ``KIND``
        The ``kind`` value written to every document of this class (snake_case).
    ``ID_PREFIX``
        Prefix for :meth:`new_id` (empty for deterministic or singleton ids).
    ``PARTITION_FIELD``
        The attribute holding the partition key value (``id`` unless the container is
        partitioned by another path, for example ``article_id`` in ``article-versions``).
    ``OWNER``
        The package that writes documents of this kind (documentation and tests only).
    """

    CONTAINER: ClassVar[str] = ""
    KIND: ClassVar[str] = ""
    ID_PREFIX: ClassVar[str] = ""
    PARTITION_FIELD: ClassVar[str] = "id"
    OWNER: ClassVar[str] = ""

    id: DocId
    schema_version: int = Field(default=1, ge=1)
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="before")
    @classmethod
    def _accept_kind(cls, data: object) -> object:
        """Accept (and drop) a matching ``kind``; refuse another kind. Values of computed fields
        (read-only views such as ``Post.approvalCurrent``) are dropped too: they are recomputed,
        never stored or taken from input."""
        if not isinstance(data, Mapping):
            return data
        body = cast(Mapping[str, object], data)
        if "kind" in body and body["kind"] != cls.KIND:
            raise WrongKind(cls.KIND, body["kind"])
        dropped = {"kind", *_computed_keys(cls)}
        if not dropped.intersection(body):
            return body
        return {key: value for key, value in body.items() if key not in dropped}

    @classmethod
    def new_id(cls, *, now: datetime | None = None) -> str:
        """A fresh id for this kind (``TypeError`` for kinds with deterministic ids)."""
        if not cls.ID_PREFIX:
            raise TypeError(f"{cls.__name__} has deterministic ids; build the id explicitly")
        return new_id(cls.ID_PREFIX, now=now)

    @classmethod
    def from_document(cls, body: Mapping[str, Any]) -> Self:
        """Parse a stored body. :class:`WrongKind` unless ``body["kind"] == KIND``.

        Raises ``pydantic.ValidationError`` when the body does not fit the model.
        """
        if body.get("kind") != cls.KIND:
            raise WrongKind(cls.KIND, body.get("kind"))
        return cls.model_validate(dict(body))

    def to_document(self, *, warn: bool = True) -> dict[str, Any]:
        """The JSON body to store: ``id`` and ``kind`` first, camelCase, unknown fields kept.

        A ``ttl`` of ``None`` is left out (the store refuses a null ``ttl``). ``warn=False``
        silences pydantic's serializer warnings for a document that is about to be validated
        (and refused) anyway.
        """
        computed = set(type(self).model_computed_fields)
        body = self.model_dump(mode="json", by_alias=True, warnings=warn, exclude=computed or None)
        if "ttl" in body and body["ttl"] is None:
            del body["ttl"]
        out: dict[str, Any] = {"id": body.pop("id"), "kind": self.KIND}
        out.update(body)
        return out

    @property
    def partition_key(self) -> str:
        """The partition key value of this document (see ``PARTITION_FIELD``)."""
        value: object = getattr(self, self.PARTITION_FIELD)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{type(self).__name__}.{self.PARTITION_FIELD} must be a non-empty id")
        return value

    @classmethod
    def partition_path(cls) -> str:
        """The container's partition key path (``/id``, ``/articleId``...), from the registry."""
        return CONTAINERS[cls.CONTAINER]

    def touched(self, now: str) -> Self:
        """A copy with ``updated_at`` set to ``now`` (validated)."""
        _check_timestamp(now)
        return self.model_copy(update={"updated_at": now})


class ExpiringDocument(Document):
    """A document with an explicit expiry.

    ``ttl`` (seconds, Cosmos garbage collection) is never trusted: readers check
    :meth:`expired` against ``expires_at``.
    """

    expires_at: Timestamp
    ttl: int | None = None

    @field_validator("ttl")
    @classmethod
    def _check_ttl(cls, value: int | None) -> int | None:
        if value is not None and value != -1 and value < 1:
            raise ValueError("ttl must be -1 or at least 1 (seconds)")
        return value

    def expired(self, now: datetime) -> bool:
        """Whether ``expires_at`` is at or before ``now``."""
        return parse_iso(self.expires_at) <= now


class SingletonDocument(Document):
    """A one-per-app document in ``settings`` whose id equals ``SINGLETON_ID``.

    Readers fall back to :meth:`default` when the document has never been saved (the owner has
    not changed anything yet); the API reports such a document with ``etag: null``.
    """

    CONTAINER: ClassVar[str] = "settings"
    SINGLETON_ID: ClassVar[str] = ""

    @model_validator(mode="after")
    def _check_singleton_id(self) -> Self:
        if self.id != self.SINGLETON_ID:
            raise ValueError(f"{type(self).__name__} id must be {self.SINGLETON_ID!r}")
        return self

    @classmethod
    def default(cls, now: str) -> Self:
        """The plan's default document, not yet stored (override where defaults matter)."""
        return cls.model_validate({"id": cls.SINGLETON_ID, "createdAt": now, "updatedAt": now})
