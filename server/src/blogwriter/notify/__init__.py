"""Notifications: Web Push (title + link only) and out-of-band alert events.

* :func:`emit_alert` (``notify.alerts``): one ``alert_event`` log line that Azure Monitor log
  alerts email to the owner.
* :class:`NotificationPayload` (``notify.payload``): title, same-origin path and tag; nothing
  else can be pushed, so draft text never reaches a push service.
* :class:`PushSubscription`, :class:`SubscriptionRepo`, :class:`MemorySubscriptionRepo`
  (``notify.subscriptions``).
* :class:`NotifySettings` (``notify.settings``, env prefix ``BW_NOTIFY_``).
* ``WebPushNotifier`` and ``DeliveryReceipt`` live in :mod:`blogwriter.notify.webpush`, which
  imports ``pywebpush``; this package root stays light so any package can import
  :func:`emit_alert` cheaply.

See ``docs/v2/phase1/egress-crypto-notify.md``.
"""

from blogwriter.notify.alerts import ALERT_EVENT, ALERT_KINDS, AlertKind, emit_alert
from blogwriter.notify.payload import NotificationPayload
from blogwriter.notify.settings import NotifyConfigError, NotifySettings, load_vapid_private_key
from blogwriter.notify.subscriptions import (
    PUSH_SERVICE_HOSTS,
    MemorySubscriptionRepo,
    PushSubscription,
    SubscriptionRepo,
)

__all__ = [
    "ALERT_EVENT",
    "ALERT_KINDS",
    "PUSH_SERVICE_HOSTS",
    "AlertKind",
    "MemorySubscriptionRepo",
    "NotificationPayload",
    "NotifyConfigError",
    "NotifySettings",
    "PushSubscription",
    "SubscriptionRepo",
    "emit_alert",
    "load_vapid_private_key",
]
