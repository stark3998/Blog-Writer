"""The ``Notifier`` port (contract I-3): web push to every subscribed device, or nothing.

- :class:`PushNotifier` adapts :class:`blogwriter.notify.webpush.WebPushNotifier` (VAPID web
  push) to :class:`blogwriter.domain.ports.Notifier`: one push per active subscription, and a
  :class:`~blogwriter.domain.ports.NotifyResult` counting the receipts (``delivered`` = push
  services that answered 201/202, the veto notice's delivery receipt). It also gives the push
  routes the VAPID public key and the subscription repository.
- :class:`NullNotifier` is what ``app.state.notifier`` holds when push is not configured
  (``BW_NOTIFY_VAPID_*`` unset): it sends nothing and reports nothing delivered, so whatever
  waits for a receipt (the auto-publish veto notice) holds instead of assuming delivery.

``notify`` never raises: a repository failure is logged, raises the ``notification_failed``
out-of-band alert and is reported as one failed delivery (callers treat it as "no receipt").
Payloads are :class:`~blogwriter.notify.payload.NotificationPayload` (a title and a same-origin
path); nothing else can be pushed.
"""

from __future__ import annotations

from typing import Final

from blogwriter.core.logging import describe_exception
from blogwriter.domain.ports import NotifyResult, Urgency
from blogwriter.notify.alerts import emit_alert
from blogwriter.notify.payload import NotificationPayload
from blogwriter.notify.subscriptions import SubscriptionRepo
from blogwriter.notify.webpush import DeliveryReceipt, WebPushNotifier
from blogwriter.services._logging import logger

__all__ = ["NullNotifier", "PushNotifier", "notify_result"]

_NO_RECEIPT: Final = NotifyResult(failed=1)


def notify_result(receipts: list[DeliveryReceipt]) -> NotifyResult:
    """Count the receipts of one ``notify_all`` by outcome."""
    return NotifyResult(
        delivered=sum(receipt.outcome == "delivered" for receipt in receipts),
        failed=sum(receipt.outcome == "failed" for receipt in receipts),
        expired=sum(receipt.outcome == "expired" for receipt in receipts),
    )


class PushNotifier:
    """:class:`~blogwriter.domain.ports.Notifier` over web push (see the module docs)."""

    def __init__(self, sender: WebPushNotifier, subscriptions: SubscriptionRepo) -> None:
        self._sender = sender
        self._subscriptions = subscriptions

    @property
    def application_server_key(self) -> str:
        """The VAPID public key (base64url) the browser subscribes with."""
        return self._sender.application_server_key

    @property
    def subscriptions(self) -> SubscriptionRepo:
        return self._subscriptions

    async def notify(
        self, payload: NotificationPayload, *, urgency: Urgency = "normal"
    ) -> NotifyResult:
        """Push ``payload`` to every active subscription; never raises."""
        try:
            receipts = await self._sender.notify_all(payload, urgency=urgency)
        except Exception as exc:  # the subscription repository failed; nothing was pushed
            logger.error("push_notify_failed", extra=describe_exception(exc))
            emit_alert("notification_failed")
            return _NO_RECEIPT
        result = notify_result(receipts)
        logger.info(
            "push_notified",
            extra={"count": len(receipts), "delivered": result.delivered},
        )
        return result


class NullNotifier:
    """The notifier when push is off: nothing is sent and nothing is reported delivered."""

    async def notify(
        self, payload: NotificationPayload, *, urgency: Urgency = "normal"
    ) -> NotifyResult:
        logger.info("push_not_configured")
        return NotifyResult()
