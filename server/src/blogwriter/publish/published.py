"""Reading and writing the published-blogs containers (``published-blogs``, ``...-staging``).

These documents follow ``contracts/published-blogs.v2.schema.json``, not a domain model, so
they go through :class:`blogwriter.db.Store` directly. **Writes fail closed**: every document
is validated with ``contract.validate_published_doc`` (in a worker thread) before any I/O, and
a document with any contract error is never written (:class:`ContractViolation`). A missing or
malformed contract (``ContractUnavailableError``) stops the write too.

Only the publish job writes here (contract section 1.3).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Literal

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Doc, InvalidRequest, NotFound, Store
from blogwriter.publish.contract import ContractError, validate_published_doc

__all__ = [
    "PUBLISHED_CONTAINER",
    "STAGING_CONTAINER",
    "ContractViolation",
    "PublishedContainer",
    "read_published",
    "validate_document",
    "write_published",
]

PUBLISHED_CONTAINER: Final = "published-blogs"
STAGING_CONTAINER: Final = "published-blogs-staging"
type PublishedContainer = Literal["published-blogs", "published-blogs-staging"]

_logger: Final = logging.getLogger("blogwriter.publish.published")
register_log_fields("contract_errors", "contract_paths")


class ContractViolation(ValueError):
    """A document breaks the published-blogs contract; nothing was written.

    ``codes`` and ``paths`` are safe to log (the messages may quote document values and are
    kept out of logs and job documents)."""

    def __init__(self, errors: list[ContractError]) -> None:
        self.codes: tuple[str, ...] = tuple(dict.fromkeys(error.code for error in errors))
        self.paths: tuple[str, ...] = tuple(dict.fromkeys(error.path for error in errors))
        super().__init__(
            "the document breaks the published-blogs contract: " + ", ".join(self.codes)
        )


async def validate_document(doc: Mapping[str, object], *, now: datetime) -> None:
    """Raise :class:`ContractViolation` unless ``doc`` passes the contract (in a thread)."""
    errors = await asyncio.to_thread(validate_published_doc, doc, now=now)
    if errors:
        violation = ContractViolation(errors)
        _logger.warning(
            "published_document_invalid",
            extra={
                "contract_errors": list(violation.codes)[:20],
                "contract_paths": list(violation.paths)[:20],
            },
        )
        raise violation


async def read_published(
    store: Store, container: PublishedContainer, slug: str
) -> dict[str, Any] | None:
    """The document stored under ``slug`` (its ``id``), or ``None``."""
    try:
        found: Doc = await store.read(container, slug)
    except (NotFound, InvalidRequest):
        return None
    return found.body


async def write_published(
    store: Store,
    container: PublishedContainer,
    doc: Mapping[str, object],
    *,
    now: datetime,
    mode: Literal["create", "upsert"],
) -> Doc:
    """Validate, then write ``doc``. ``create`` raises ``Conflict`` when the id exists (a slug
    this article does not own yet); ``upsert`` overwrites (only for slugs the article owns)."""
    await validate_document(doc, now=now)
    body = dict(doc)
    if mode == "create":
        return await store.create(container, body)
    return await store.upsert(container, body)
