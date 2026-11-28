"""blogwriter.db: document storage over Cosmos DB (Entra ID only), with an in-memory twin.

Public API::

    from blogwriter.db import Store, Doc, Filter, OrderBy, NotFound, Conflict, PreconditionFailed

    doc = await store.read("jobs", job_id)
    body = {**doc.body, "leaseUntil": until}
    try:
        doc = await store.replace("jobs", body, if_match=doc.etag)
    except PreconditionFailed:
        ...  # someone else wrote first: the claim is lost

    queued = await store.find(
        "jobs",
        [Filter("state", "==", "queued"), Filter("leaseUntil", "<", now_iso)],
        order_by=OrderBy("leaseUntil"),
        limit=10,
    )

See :mod:`blogwriter.db.base` for the document rules and :mod:`blogwriter.db.query` for query
semantics. ``startup``/``shutdown``/``get_store`` wire the store into the app.
"""

from blogwriter.db.base import (
    CONTAINERS,
    Conflict,
    ContainerCheckFailed,
    Doc,
    Filter,
    FilterOp,
    InvalidRequest,
    JsonScalar,
    NotFound,
    OrderBy,
    PreconditionFailed,
    Store,
    StoreError,
    StoreUnavailable,
)
from blogwriter.db.cosmos import CosmosStore
from blogwriter.db.lifecycle import get_store, shutdown, startup
from blogwriter.db.memory import MemoryStore
from blogwriter.db.settings import DEFAULT_DATABASE, CosmosSettings

__all__ = [
    "CONTAINERS",
    "DEFAULT_DATABASE",
    "Conflict",
    "ContainerCheckFailed",
    "CosmosSettings",
    "CosmosStore",
    "Doc",
    "Filter",
    "FilterOp",
    "InvalidRequest",
    "JsonScalar",
    "MemoryStore",
    "NotFound",
    "OrderBy",
    "PreconditionFailed",
    "Store",
    "StoreError",
    "StoreUnavailable",
    "get_store",
    "shutdown",
    "startup",
]
