"""FastAPI dependencies and error mapping shared by every package router.

Every route is owner-only (core's auth middleware runs first; declare
``principal: OwnerPrincipal`` where the handler needs the owner) and every non-GET passes
core's CSRF check. Handlers take services through these dependencies::

    @router.get("/api/articles/{article_id}")
    async def get_article(article_id: str, store: StoreDep) -> Versioned[Article]:
        with problems():
            stored = await read_doc(store, Article, article_id)
        return versioned(stored, response)

Unavailable services answer 503 with ``code: <name>_unavailable``; store errors, transition
errors, the who-writes rule, the confidentiality checks and malformed cursors map to problems in
:func:`problem_from`.
"""

from __future__ import annotations

import functools
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any, cast

from fastapi import Depends, Header, Request, Response

from blogwriter.core.crypto import TokenVault
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.db import Conflict, NotFound, PreconditionFailed, Store, StoreUnavailable
from blogwriter.db.base import InvalidRequest
from blogwriter.domain.api import InvalidCursor, Versioned
from blogwriter.domain.base import Document, SingletonDocument, WrongKind
from blogwriter.domain.compose import MetaIncomplete
from blogwriter.domain.confidentiality import (
    AiUseNotConfirmed,
    BlocklistNotConfigured,
    ConfidentialText,
)
from blogwriter.domain.docstore import InvalidStoredDocument, Stored
from blogwriter.domain.ports import (
    BlobStore,
    BlobStoreUnavailable,
    ConfidentialityClassifier,
    Embedder,
    HttpGetter,
    JobQueue,
    ModelRunner,
    Notifier,
    PageFetcher,
    RenderService,
    ScheduleSeeder,
    Services,
    ServiceUnavailable,
    SlopChecker,
)
from blogwriter.domain.states import TransitionError, TransitionRefused, WriterConflict
from blogwriter.jobs.clock import Clock

__all__ = [
    "AIDep",
    "BlobsDep",
    "ClassifierDep",
    "ClockDep",
    "EgressDep",
    "EmbedderDep",
    "FetcherDep",
    "IfMatch",
    "JobsDep",
    "NotifierDep",
    "OptionalIfMatch",
    "RenderDep",
    "SchedulesDep",
    "ServicesDep",
    "SingletonPrecondition",
    "SlopDep",
    "StoreDep",
    "VaultDep",
    "etag_mismatch",
    "get_services",
    "problem_from",
    "problems",
    "versioned",
    "versioned_singleton",
]


def _unavailable(name: str) -> ProblemError:
    return ProblemError(
        503,
        code=f"{name}_unavailable",
        detail="This feature is not configured or not running.",
        headers={"Retry-After": "5"},
    )


async def get_services(request: Request) -> Services:
    """The app's :class:`Services` (``app.state.services``); 503 when not wired."""
    services: object = getattr(request.app.state, "services", None)
    if services is None:
        raise _unavailable("services")
    return cast(Services, services)


ServicesDep = Annotated[Services, Depends(get_services)]


async def _store(services: ServicesDep) -> Store:
    try:
        return services.store
    except ServiceUnavailable:
        raise _unavailable("store") from None


async def _jobs(services: ServicesDep) -> JobQueue:
    try:
        return services.jobs
    except ServiceUnavailable:
        raise _unavailable("jobs") from None


async def _clock(services: ServicesDep) -> Clock:
    try:
        return services.clock
    except ServiceUnavailable:
        raise _unavailable("clock") from None


async def _notifier(services: ServicesDep) -> Notifier:
    try:
        return services.notifier
    except ServiceUnavailable:
        raise _unavailable("notifier") from None


async def _egress(services: ServicesDep) -> HttpGetter:
    try:
        return services.egress
    except ServiceUnavailable:
        raise _unavailable("egress") from None


async def _ai(services: ServicesDep) -> ModelRunner:
    runner = services.ai
    if runner is None:
        raise _unavailable("ai")
    return runner


async def _embedder(services: ServicesDep) -> Embedder:
    embedder = services.embedder
    if embedder is None:
        raise _unavailable("embeddings")
    return embedder


async def _blobs(services: ServicesDep) -> BlobStore:
    blobs = services.blobs
    if blobs is None:
        raise _unavailable("storage")
    return blobs


async def _fetcher(services: ServicesDep) -> PageFetcher:
    fetcher = services.fetcher
    if fetcher is None:
        raise _unavailable("fetcher")
    return fetcher


async def _render(services: ServicesDep) -> RenderService:
    render = services.render
    if render is None:
        raise _unavailable("render")
    return render


async def _vault(services: ServicesDep) -> TokenVault:
    vault = services.vault
    if vault is None:
        raise _unavailable("vault")
    return vault


async def _slop(services: ServicesDep) -> SlopChecker:
    slop = services.slop
    if slop is None:
        raise _unavailable("slop")
    return slop


async def _classifier(services: ServicesDep) -> ConfidentialityClassifier:
    classifier = services.classifier
    if classifier is None:
        raise _unavailable("classifier")
    return classifier


async def _schedules(services: ServicesDep) -> ScheduleSeeder:
    schedules = services.schedules
    if schedules is None:
        raise _unavailable("schedules")
    return schedules


StoreDep = Annotated[Store, Depends(_store)]
JobsDep = Annotated[JobQueue, Depends(_jobs)]
ClockDep = Annotated[Clock, Depends(_clock)]
NotifierDep = Annotated[Notifier, Depends(_notifier)]
EgressDep = Annotated[HttpGetter, Depends(_egress)]
AIDep = Annotated[ModelRunner, Depends(_ai)]
EmbedderDep = Annotated[Embedder, Depends(_embedder)]
BlobsDep = Annotated[BlobStore, Depends(_blobs)]
FetcherDep = Annotated[PageFetcher, Depends(_fetcher)]
RenderDep = Annotated[RenderService, Depends(_render)]
VaultDep = Annotated[TokenVault, Depends(_vault)]
SlopDep = Annotated[SlopChecker, Depends(_slop)]
ClassifierDep = Annotated[ConfidentialityClassifier, Depends(_classifier)]
SchedulesDep = Annotated[ScheduleSeeder, Depends(_schedules)]


# --------------------------------------------------------------------------------------------
# Conditional requests
# --------------------------------------------------------------------------------------------


async def _require_if_match(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> str:
    if not if_match:
        raise ProblemError(
            428,
            code="if_match_required",
            detail="Send the If-Match header with the etag you last read.",
        )
    return if_match


async def _optional_if_match(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> str | None:
    return if_match or None


async def _singleton_precondition(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> str | None:
    """``If-Match: <etag>`` to replace, ``If-None-Match: *`` to create a never-saved singleton
    (``None``); anything else is 428."""
    if if_match:
        return if_match
    if if_none_match == "*":
        return None
    raise ProblemError(
        428,
        code="if_match_required",
        detail="Send If-Match with the etag you read, or If-None-Match: * when etag was null.",
    )


IfMatch = Annotated[str, Depends(_require_if_match)]
OptionalIfMatch = Annotated[str | None, Depends(_optional_if_match)]
SingletonPrecondition = Annotated[str | None, Depends(_singleton_precondition)]


# --------------------------------------------------------------------------------------------
# Responses and errors
# --------------------------------------------------------------------------------------------


@functools.cache
def _versioned_model(doc_type: type[Document]) -> type[Versioned[Any]]:
    # The concrete ``Versioned[Article]`` class (pydantic caches parametrizations), so the
    # envelope serializes every field of the document and matches the route's response model.
    return cast(type[Versioned[Any]], Versioned.__class_getitem__(doc_type))


def versioned[D: Document](stored: Stored[D], response: Response | None = None) -> Versioned[D]:
    """The ``{item, etag}`` envelope; also sets the ``ETag`` header when given the response."""
    if response is not None:
        response.headers["ETag"] = stored.etag
    model = _versioned_model(type(stored.doc))
    return cast(Versioned[D], model(item=stored.doc, etag=stored.etag))


def versioned_singleton[S: SingletonDocument](
    doc: S, etag: str | None, response: Response | None = None
) -> Versioned[S]:
    """The envelope of a singleton as :func:`~blogwriter.domain.docstore.read_singleton` or
    ``update_singleton`` return it: ``etag`` is ``None`` (JSON ``null``, no ``ETag`` header)
    while the owner sees the defaults of a never-saved singleton."""
    if response is not None and etag is not None:
        response.headers["ETag"] = etag
    model = _versioned_model(type(doc))
    return cast(Versioned[S], model(item=doc, etag=etag))


def etag_mismatch(current: Stored[Document] | None = None) -> ProblemError:
    """The 412 problem. With ``current``, the UI's conflict dialog can say who changed what:
    ``current: {etag, updatedAt, updatedBy?, version?, status?}``."""
    extensions: dict[str, JsonValue] = {}
    if current is not None:
        info: dict[str, JsonValue] = {
            "etag": current.etag,
            "updatedAt": current.doc.updated_at,
        }
        for name in ("updated_by", "version", "status"):
            value: object = getattr(current.doc, name, None)
            if isinstance(value, str | int):
                info[name.replace("_by", "By")] = value
        extensions["current"] = info
    return ProblemError(
        412,
        code="etag_mismatch",
        detail="It changed since you loaded it. Reload, compare and save again.",
        extensions=extensions or None,
    )


def problem_from(exc: Exception) -> ProblemError:
    """Map store, transition, confidentiality and service errors to problems (never with
    content: blocklist findings carry term ids, never terms)."""
    match exc:
        case ProblemError():
            return exc
        case PreconditionFailed():
            return etag_mismatch()
        case NotFound() | WrongKind() | InvalidRequest():
            # InvalidRequest left over after docstore: an id that cannot exist (a "#" in a path).
            return ProblemError(404, code="not_found")
        case InvalidCursor():
            return ProblemError(422, code="invalid_cursor", detail="The page cursor is invalid.")
        case ConfidentialText():
            findings: list[JsonValue] = [
                {"field": field, **finding.model_dump(mode="json", exclude_none=True)}
                for field, items in exc.findings.items()
                for finding in items
            ]
            return ProblemError(
                422,
                code=ConfidentialText.code,
                detail="Some text matches the blocklist. Rephrase it and try again.",
                extensions={"findings": findings},
            )
        case BlocklistNotConfigured():
            return ProblemError(
                409,
                code=BlocklistNotConfigured.code,
                detail="Save the blocklist once in Settings > Policy first.",
            )
        case AiUseNotConfirmed():
            return ProblemError(
                409,
                code=AiUseNotConfirmed.code,
                detail="Confirm the employer AI-use policy in Settings > Policy first.",
            )
        case MetaIncomplete():
            return ProblemError(
                409,
                code=MetaIncomplete.code,
                detail="Fill in the article details first.",
                extensions={"missing": list(exc.missing)},
            )
        case TransitionRefused():
            return ProblemError(409, code=exc.code, detail=exc.detail)
        case Conflict():
            return ProblemError(409, code="conflict", detail="It already exists.")
        case StoreUnavailable():
            return ProblemError(503, code="store_unavailable", headers={"Retry-After": "2"})
        case InvalidStoredDocument():
            return ProblemError(500, code="invalid_document")
        case WriterConflict():
            # ``state``, not ``status``: ``status`` is the problem's reserved HTTP status member.
            return ProblemError(
                409,
                code=WriterConflict.code,
                detail=f"Read-only while {exc.status.value}: the {exc.writer} writes now.",
                extensions={"state": exc.status.value, "writer": exc.writer},
            )
        case TransitionError():
            return ProblemError(
                409,
                code=TransitionError.code,
                detail=f"Cannot go from {exc.current} to {exc.target}.",
                extensions={
                    "machine": exc.machine,
                    "from": exc.current,
                    "to": exc.target,
                    "allowed": list(exc.allowed),
                },
            )
        case ServiceUnavailable():
            return _unavailable(exc.name)
        case BlobStoreUnavailable():
            return _unavailable("storage")
        case _:
            return ProblemError(500, code="internal_error")


_MAPPED = (
    ProblemError,
    PreconditionFailed,
    NotFound,
    WrongKind,
    InvalidRequest,
    InvalidCursor,
    ConfidentialText,
    BlocklistNotConfigured,
    AiUseNotConfirmed,
    MetaIncomplete,
    TransitionRefused,
    Conflict,
    StoreUnavailable,
    InvalidStoredDocument,
    WriterConflict,
    TransitionError,
    ServiceUnavailable,
    BlobStoreUnavailable,
)


@contextmanager
def problems() -> Generator[None]:
    """Turn the mapped errors raised inside the block into problem responses."""
    try:
        yield
    except _MAPPED as exc:
        raise problem_from(exc) from None
