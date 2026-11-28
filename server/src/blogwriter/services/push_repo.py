"""Web Push subscriptions in Cosmos: :class:`CosmosSubscriptionRepo` (contract I-3).

The ``notify.SubscriptionRepo`` protocol over the ``settings`` container, one document per
browser, ``kind: "push_subscription"``::

    {"id": "push-<32 hex>", "kind": "push_subscription", "subscriptionId": "<32 hex>",
     "endpoint": "https://fcm.googleapis.com/...", "p256dh": "...", "auth": "...",
     "createdAt": "...", "expiredAt": null, "updatedAt": "..."}

The subscription id is derived from the endpoint (``notify.subscriptions.subscription_id_for``),
so subscribing the same browser again overwrites one document and re-activates it. The
document id carries a ``push-`` prefix so it can never collide with a settings singleton, and
every read and delete checks ``kind`` (a delete is a CAS on the etag it checked), so this repo
never touches another document.

The endpoint and keys are capabilities: they are stored (they must be, to push) but never
logged; log lines carry the subscription id only. A stored body that no longer validates is
skipped with a warning, never pushed to.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError

from blogwriter.db import Filter, NotFound, PreconditionFailed, Store, StoreUnavailable
from blogwriter.db.base import InvalidRequest
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.notify.subscriptions import PushSubscription
from blogwriter.services._logging import logger

__all__ = [
    "PUSH_SUBSCRIPTION_KIND",
    "SUBSCRIPTIONS_CONTAINER",
    "CosmosSubscriptionRepo",
    "subscription_doc_id",
]

SUBSCRIPTIONS_CONTAINER: Final = "settings"
PUSH_SUBSCRIPTION_KIND: Final = "push_subscription"
DEFAULT_STORE_TIMEOUT: Final = 10.0
_DOC_PREFIX: Final = "push-"
_ID_RE: Final = re.compile(r"[0-9a-f]{32}")
_CAS_ROUNDS: Final = 5


def subscription_doc_id(subscription_id: str) -> str:
    """The ``settings`` document id of a subscription id (``push-<32 hex>``)."""
    return f"{_DOC_PREFIX}{subscription_id}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _bounded[T](awaitable: Awaitable[T], seconds: float) -> T:
    async with asyncio.timeout(seconds):
        return await awaitable


class CosmosSubscriptionRepo:
    """:class:`blogwriter.notify.SubscriptionRepo` over a :class:`blogwriter.db.Store`."""

    def __init__(
        self,
        store: Store,
        *,
        clock: Callable[[], datetime] = _utc_now,
        store_timeout: float = DEFAULT_STORE_TIMEOUT,
    ) -> None:
        if store_timeout <= 0:
            raise ValueError("store_timeout must be positive")
        self._store = store
        self._clock = clock
        self._timeout = store_timeout

    # Mapping ---------------------------------------------------------------------------------

    def _body(self, subscription: PushSubscription) -> dict[str, Any]:
        return {
            "id": subscription_doc_id(subscription.id),
            "kind": PUSH_SUBSCRIPTION_KIND,
            "subscriptionId": subscription.id,
            "endpoint": subscription.endpoint,
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
            "createdAt": to_iso(subscription.created_at),
            "expiredAt": (
                to_iso(subscription.expired_at) if subscription.expired_at is not None else None
            ),
            "updatedAt": to_iso(self._clock()),
        }

    @staticmethod
    def _parse(body: Mapping[str, Any]) -> PushSubscription | None:
        """The subscription in a stored body, or ``None`` for another kind or a bad body."""
        if body.get("kind") != PUSH_SUBSCRIPTION_KIND:
            return None
        try:
            created: object = body.get("createdAt")
            expired: object = body.get("expiredAt")
            return PushSubscription.model_validate(
                {
                    "id": body.get("subscriptionId"),
                    "endpoint": body.get("endpoint"),
                    "p256dh": body.get("p256dh"),
                    "auth": body.get("auth"),
                    "created_at": parse_iso(created) if isinstance(created, str) else created,
                    "expired_at": parse_iso(expired) if isinstance(expired, str) else expired,
                }
            )
        except (TypeError, ValueError, ValidationError):
            logger.warning(
                "push_subscription_invalid", extra={"subscription_id": body.get("subscriptionId")}
            )
            return None

    @staticmethod
    def _valid_id(subscription_id: str) -> bool:
        return bool(_ID_RE.fullmatch(subscription_id))

    # SubscriptionRepo ------------------------------------------------------------------------

    async def upsert(self, subscription: PushSubscription) -> None:
        """Insert or replace by id (re-subscribing a browser re-activates it)."""
        await _bounded(
            self._store.upsert(SUBSCRIPTIONS_CONTAINER, self._body(subscription)), self._timeout
        )

    async def get(self, subscription_id: str) -> PushSubscription | None:
        if not self._valid_id(subscription_id):
            return None
        try:
            doc = await _bounded(
                self._store.read(SUBSCRIPTIONS_CONTAINER, subscription_doc_id(subscription_id)),
                self._timeout,
            )
        except (NotFound, InvalidRequest):
            return None
        return self._parse(doc.body)

    async def list_active(self) -> list[PushSubscription]:
        """Subscriptions without ``expiredAt``, oldest first."""
        docs = await _bounded(
            self._store.find(
                SUBSCRIPTIONS_CONTAINER, [Filter("kind", "==", PUSH_SUBSCRIPTION_KIND)]
            ),
            self._timeout,
        )
        found = [self._parse(doc.body) for doc in docs]
        active = [item for item in found if item is not None and item.active]
        return sorted(active, key=lambda item: (item.created_at, item.id))

    async def mark_expired(self, subscription_id: str, *, at: datetime) -> bool:
        """Set ``expiredAt`` (kept if already set). ``False`` if the id is unknown."""
        if not self._valid_id(subscription_id):
            return False
        doc_id = subscription_doc_id(subscription_id)
        for _ in range(_CAS_ROUNDS):
            try:
                doc = await _bounded(
                    self._store.read(SUBSCRIPTIONS_CONTAINER, doc_id), self._timeout
                )
            except NotFound:
                return False
            current = self._parse(doc.body)
            if current is None:
                return False
            if current.expired_at is not None:
                return True
            expired = PushSubscription.model_validate(
                {**current.model_dump(), "expired_at": at.astimezone(UTC)}
            )
            try:
                await _bounded(
                    self._store.replace(
                        SUBSCRIPTIONS_CONTAINER, self._body(expired), if_match=doc.etag
                    ),
                    self._timeout,
                )
            except PreconditionFailed:
                continue  # re-subscribed or expired concurrently: look again
            except NotFound:
                return False
            return True
        raise StoreUnavailable(f"{SUBSCRIPTIONS_CONTAINER}/{doc_id}: too many conflicting writes")

    async def delete(self, subscription_id: str) -> bool:
        """Remove a subscription. ``False`` if the id is unknown."""
        if not self._valid_id(subscription_id):
            return False
        doc_id = subscription_doc_id(subscription_id)
        for _ in range(_CAS_ROUNDS):
            try:
                doc = await _bounded(
                    self._store.read(SUBSCRIPTIONS_CONTAINER, doc_id), self._timeout
                )
            except NotFound:
                return False
            if doc.body.get("kind") != PUSH_SUBSCRIPTION_KIND:
                return False
            try:
                await _bounded(
                    self._store.delete(SUBSCRIPTIONS_CONTAINER, doc_id, if_match=doc.etag),
                    self._timeout,
                )
            except PreconditionFailed:
                continue
            except NotFound:
                return False
            return True
        raise StoreUnavailable(f"{SUBSCRIPTIONS_CONTAINER}/{doc_id}: too many conflicting writes")
