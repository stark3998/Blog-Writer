"""Typed access to domain documents over :class:`blogwriter.db.Store`.

Every package reads and writes its documents through these helpers so the conventions hold
everywhere: the ``kind`` filter on every query, ``kind`` checked on every read (a document of
another kind reads as :class:`~blogwriter.db.NotFound`), If-Match on every replace, and a
:class:`Stored` value carrying the etag the next write must quote.

**Fail closed.** Every write validates the document against its model and the store's rules
before any I/O (``model_copy(update=...)`` skips pydantic validation, so a bad value would
otherwise be committed and then vanish from queries). Lists skip a stored body that no longer
fits its model (one bad document cannot break a page), but checks that guard an action
(``slug_taken``, ``slot_conflict``, near-duplicates, ``pillar_in_use``, one running
experiment) pass ``strict=True`` so a bad document fails the check instead of passing it.

**Projections.** ``find_docs(..., fields=View)`` reads only the document fields a view model
declares (``Store.find(..., fields=...)``) and returns :class:`Projected` views with the
documents' etags: summary lists use them so they never read Markdown, embeddings or reports.
A view is a plain model, never a :class:`~blogwriter.domain.base.Document`, so nothing here can
write it back over the fields it did not read.

Errors are the store's (:class:`~blogwriter.db.NotFound`, :class:`~blogwriter.db.Conflict`,
:class:`~blogwriter.db.PreconditionFailed`, :class:`~blogwriter.db.StoreUnavailable`) plus
:class:`InvalidStoredDocument` for a stored body that no longer fits its model and
:class:`InvalidDocumentWrite` for a write refused before any I/O. An id that cannot exist (a
``/`` or ``#`` in a path parameter) reads as :class:`~blogwriter.db.NotFound`. Messages carry
the container and id only, never content. Routes turn them into problems with
:func:`blogwriter.domain.deps.problems`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Final, overload

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from blogwriter.core.logging import register_log_fields
from blogwriter.db import (
    Conflict,
    Doc,
    Filter,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
    StoreError,
)
from blogwriter.db.base import InvalidRequest, prepare_write
from blogwriter.domain.base import Document, SingletonDocument, WrongKind

__all__ = [
    "DEFAULT_CAS_ATTEMPTS",
    "InvalidDocumentWrite",
    "InvalidStoredDocument",
    "Projected",
    "Stored",
    "create_doc",
    "delete_doc",
    "find_docs",
    "read_doc",
    "read_singleton",
    "replace_doc",
    "save_singleton",
    "update_doc",
    "update_singleton",
    "upsert_doc",
]

DEFAULT_CAS_ATTEMPTS: Final = 5

_logger: Final = logging.getLogger("blogwriter.domain.docstore")
register_log_fields("container", "doc_id", "kind")


class InvalidStoredDocument(StoreError):
    """A stored body does not validate against its model (a bug or an incompatible revision)."""


class InvalidDocumentWrite(InvalidStoredDocument):
    """A document about to be written does not validate against its model or cannot be stored
    (over 2 MB, not JSON). Raised before any I/O: nothing was written. Always a bug."""


@dataclass(frozen=True, slots=True)
class Stored[D: Document]:
    """A document and the etag its next write must quote (``If-Match``)."""

    doc: D
    etag: str


@dataclass(frozen=True, slots=True)
class Projected[V: BaseModel]:
    """Some fields of a stored document (:func:`find_docs` with ``fields``) and the whole
    document's etag (the one its next write must quote).

    ``doc`` is a view, never a :class:`~blogwriter.domain.base.Document`: no write here takes
    one, so a projection cannot overwrite the fields it did not read. Writers read the whole
    document first (:func:`read_doc`, :func:`update_doc`).
    """

    doc: V
    etag: str


@cache
def _view_fields(cls: type[Document], view: type[BaseModel]) -> tuple[str, ...]:
    """The stored (camelCase) names of the fields of ``cls`` that ``view`` declares.

    ``TypeError`` (a bug) for a view that is a document itself or declares a field ``cls``
    does not have: a misspelt field would otherwise always read as missing.
    """
    if issubclass(view, Document):
        raise TypeError(f"{view.__name__} is a document; a projection view is a plain model")
    names: list[str] = []
    for name, field in view.model_fields.items():
        source = cls.model_fields.get(name)
        if source is None or field.alias is None or source.alias != field.alias:
            raise TypeError(f"{view.__name__}.{name} is not a field of {cls.__name__}")
        names.append(field.alias)
    return tuple(names)


def _unreadable(cls: type[Document], raw: Doc, *, strict: bool) -> None:
    """A stored body that does not fit: raise (``strict``) or log it and skip it. Called while
    the validation error is handled; it is never chained (its text may quote content)."""
    if strict:
        raise InvalidStoredDocument(
            f"{cls.CONTAINER}/{raw.id}: stored document does not fit {cls.__name__}"
        ) from None
    _logger.warning(
        "domain_invalid_document",
        extra={"container": cls.CONTAINER, "doc_id": raw.id, "kind": cls.KIND},
    )


def _parse[D: Document](cls: type[D], raw: Doc) -> D:
    try:
        return cls.from_document(raw.body)
    except WrongKind:
        raise NotFound(f"read {cls.CONTAINER}/{raw.id}: not a {cls.KIND}") from None
    except ValidationError:
        raise InvalidStoredDocument(
            f"{cls.CONTAINER}/{raw.id}: stored document does not fit {cls.__name__}"
        ) from None


def _checked_body(doc: Document) -> dict[str, Any]:
    """The body to write, after validating it against its model and the store's rules."""
    where = f"{doc.CONTAINER}/{doc.id}"
    try:
        body = doc.to_document(warn=False)  # validated right below; no warning for a bad value
    except PydanticSerializationError:
        raise InvalidDocumentWrite(f"{where}: the document is not JSON") from None
    try:
        type(doc).from_document(body)
    except (ValidationError, WrongKind):
        raise InvalidDocumentWrite(
            f"{where}: refusing to write a {type(doc).__name__} that does not validate"
        ) from None
    try:
        prepare_write(doc.CONTAINER, body)
    except InvalidRequest:
        raise InvalidDocumentWrite(f"{where}: the document cannot be stored") from None
    return body


async def read_doc[D: Document](
    store: Store, cls: type[D], doc_id: str, *, partition_key: str | None = None
) -> Stored[D]:
    """Read one document of ``cls``; :class:`NotFound` when missing, of another kind or when
    ``doc_id`` cannot be an id at all."""
    try:
        raw = await store.read(cls.CONTAINER, doc_id, partition_key=partition_key)
    except InvalidRequest:
        raise NotFound(f"read {cls.CONTAINER}: not a valid id") from None
    return Stored(_parse(cls, raw), raw.etag)


@overload
async def find_docs[D: Document](
    store: Store,
    cls: type[D],
    filters: Sequence[Filter] = (),
    *,
    order_by: OrderBy | None = None,
    limit: int | None = None,
    partition_key: str | None = None,
    strict: bool = False,
    fields: None = None,
) -> list[Stored[D]]: ...


@overload
async def find_docs[V: BaseModel](
    store: Store,
    cls: type[Document],
    filters: Sequence[Filter] = (),
    *,
    order_by: OrderBy | None = None,
    limit: int | None = None,
    partition_key: str | None = None,
    strict: bool = False,
    fields: type[V],
) -> list[Projected[V]]: ...


async def find_docs[D: Document, V: BaseModel](
    store: Store,
    cls: type[D],
    filters: Sequence[Filter] = (),
    *,
    order_by: OrderBy | None = None,
    limit: int | None = None,
    partition_key: str | None = None,
    strict: bool = False,
    fields: type[V] | None = None,
) -> list[Stored[D]] | list[Projected[V]]:
    """Query documents of ``cls`` (the ``kind`` filter is added).

    A stored body that does not fit the model is skipped and logged (container and id only),
    so one bad document cannot break a list page. With ``strict`` it raises
    :class:`InvalidStoredDocument` instead: use it for every query that guards an action, so a
    document the check cannot read fails the check (fail closed).

    ``fields`` is a projection: a view model (a :class:`~blogwriter.domain.base.DomainModel`
    that is not a document) declaring some fields of ``cls`` with the same names and types.
    Only those fields are read (``Store.find(..., fields=...)``) and each result is a
    :class:`Projected` view; filters and ordering still see the whole document. "Does not fit"
    then means the projected fields do not fit the view.
    """
    query = [Filter("kind", "==", cls.KIND), *filters]
    if fields is not None:
        names = _view_fields(cls, fields)
        raws = await store.find(
            cls.CONTAINER,
            query,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=["kind", *names],
        )
        views: list[Projected[V]] = []
        for raw in raws:
            try:
                if raw.body.get("kind") != cls.KIND:
                    raise WrongKind(cls.KIND, raw.body.get("kind"))
                values = {name: raw.body[name] for name in names if name in raw.body}
                views.append(Projected(fields.model_validate(values), raw.etag))
            except (WrongKind, ValidationError):
                _unreadable(cls, raw, strict=strict)
        return views
    raws = await store.find(
        cls.CONTAINER, query, order_by=order_by, limit=limit, partition_key=partition_key
    )
    out: list[Stored[D]] = []
    for raw in raws:
        try:
            out.append(Stored(cls.from_document(raw.body), raw.etag))
        except (WrongKind, ValidationError):
            _unreadable(cls, raw, strict=strict)
    return out


async def create_doc[D: Document](store: Store, doc: D) -> Stored[D]:
    """Insert ``doc``; :class:`Conflict` when the id exists."""
    raw = await store.create(doc.CONTAINER, _checked_body(doc))
    return Stored(_parse(type(doc), raw), raw.etag)


async def replace_doc[D: Document](store: Store, doc: D, *, if_match: str) -> Stored[D]:
    """Overwrite ``doc`` if its stored etag is still ``if_match`` (else
    :class:`PreconditionFailed`)."""
    raw = await store.replace(doc.CONTAINER, _checked_body(doc), if_match=if_match)
    return Stored(_parse(type(doc), raw), raw.etag)


async def upsert_doc[D: Document](store: Store, doc: D) -> Stored[D]:
    """Insert or overwrite unconditionally. Only for documents with one writer and idempotent
    content (ledger-like records); everything else uses :func:`replace_doc`."""
    raw = await store.upsert(doc.CONTAINER, _checked_body(doc))
    return Stored(_parse(type(doc), raw), raw.etag)


async def delete_doc(
    store: Store,
    cls: type[Document],
    doc_id: str,
    *,
    if_match: str | None = None,
    partition_key: str | None = None,
) -> None:
    """Delete a document of ``cls`` (``if_match`` makes it a CAS delete)."""
    try:
        await store.delete(cls.CONTAINER, doc_id, partition_key=partition_key, if_match=if_match)
    except InvalidRequest:
        raise NotFound(f"delete {cls.CONTAINER}: not a valid id") from None


async def update_doc[D: Document](
    store: Store,
    cls: type[D],
    doc_id: str,
    mutate: Callable[[D], D | None],
    *,
    partition_key: str | None = None,
    attempts: int = DEFAULT_CAS_ATTEMPTS,
) -> Stored[D]:
    """Read, ``mutate`` a copy, replace with If-Match; retry on 412 up to ``attempts`` times.

    ``mutate`` returns the new document, or ``None`` to leave it unchanged (the current
    :class:`Stored` is returned). It may raise (for example a
    :class:`~blogwriter.domain.states.TransitionError`); nothing is written then, and nothing
    is written when the result does not validate (:class:`InvalidDocumentWrite`). Server-side
    writers (jobs) use this; owner edits from the UI use :func:`replace_doc` with the etag the
    owner saw, so a concurrent change surfaces as a 412 conflict instead.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for _ in range(attempts):
        current = await read_doc(store, cls, doc_id, partition_key=partition_key)
        changed = mutate(current.doc.model_copy(deep=True))
        if changed is None:
            return current
        try:
            return await replace_doc(store, changed, if_match=current.etag)
        except PreconditionFailed:
            continue
    raise PreconditionFailed(f"update {cls.CONTAINER}/{doc_id}: lost {attempts} CAS rounds")


async def read_singleton[S: SingletonDocument](
    store: Store, cls: type[S], *, now: str
) -> tuple[S, str | None]:
    """The stored singleton and its etag, or ``(cls.default(now), None)`` if never saved."""
    try:
        stored = await read_doc(store, cls, cls.SINGLETON_ID)
    except NotFound:
        return cls.default(now), None
    return stored.doc, stored.etag


async def save_singleton[S: SingletonDocument](
    store: Store, doc: S, *, if_match: str | None
) -> Stored[S]:
    """Save a singleton: create it when ``if_match`` is ``None`` (the owner saw the default),
    else replace with If-Match. Losing either race raises :class:`PreconditionFailed`."""
    if if_match is not None:
        return await replace_doc(store, doc, if_match=if_match)
    try:
        return await create_doc(store, doc)
    except Conflict:
        raise PreconditionFailed(
            f"create {doc.CONTAINER}/{doc.id}: it was saved by someone else first"
        ) from None


async def update_singleton[S: SingletonDocument](
    store: Store,
    cls: type[S],
    mutate: Callable[[S], S | None],
    *,
    now: str,
    attempts: int = DEFAULT_CAS_ATTEMPTS,
) -> tuple[S, str | None]:
    """:func:`update_doc` for a singleton that may never have been saved.

    A missing singleton starts from ``cls.default(now)`` and is created (a lost race retries
    against the stored one). Returns the document and its etag, which is ``None`` when
    ``mutate`` left a never-saved singleton unchanged (nothing was written). For server-side
    writers (calibration, the LinkedIn connection, the backup status, the standards catalog).
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for _ in range(attempts):
        try:
            current = await read_doc(store, cls, cls.SINGLETON_ID)
        except NotFound:
            default = cls.default(now)
            changed = mutate(default.model_copy(deep=True))
            if changed is None:
                return default, None
            try:
                created = await create_doc(store, changed)
            except Conflict:
                continue
            return created.doc, created.etag
        changed = mutate(current.doc.model_copy(deep=True))
        if changed is None:
            return current.doc, current.etag
        try:
            replaced = await replace_doc(store, changed, if_match=current.etag)
        except PreconditionFailed:
            continue
        return replaced.doc, replaced.etag
    raise PreconditionFailed(f"update {cls.CONTAINER}/{cls.SINGLETON_ID}: lost {attempts} rounds")
