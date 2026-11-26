"""Out-of-band alert events.

:func:`emit_alert` writes exactly one structured log line::

    {"ts": "...", "level": "ERROR", "logger": "blogwriter.notify.alerts", "event": "alert_event",
     "alert_kind": "job_failed", "ref_id": "job-01JD3..."}

Azure Monitor log alert rules (``infra/v2``) query the Container Apps console log for
``event == "alert_event"`` and email the owner through the action group. That path does not
depend on the app's own channel (web push), so a broken push setup still reaches the owner.

Only allowlisted fields are written: the kind (:data:`ALERT_KINDS`) and an id. A ``ref_id``
that does not look like an id (it could be content) is dropped and ``reason`` says so. An
unknown kind is logged as ``unknown`` rather than lost. :func:`emit_alert` never raises, so it
is safe in ``except`` blocks.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Literal, get_args

from blogwriter.core.logging import register_log_fields

__all__ = ["ALERT_EVENT", "ALERT_KINDS", "AlertKind", "emit_alert"]

#: The ``event`` value Azure Monitor alert rules match.
ALERT_EVENT: Final = "alert_event"

type AlertKind = Literal[
    "job_failed",  # a job used up its attempts or failed permanently
    "publish_failed",  # an article or post did not publish (or could not be verified)
    "needs_attention",  # a workflow is waiting on the owner past its deadline
    "quarantined",  # a job or research run was quarantined (blocklist hit, crash loop)
    "brake_tripped",  # the AI runaway brake stopped model calls
    "notification_failed",  # a push delivery receipt failed
    "test",  # the System page's "send test alert"
]

#: Every valid :data:`AlertKind`.
ALERT_KINDS: Final[frozenset[str]] = frozenset(get_args(AlertKind.__value__))

_REF_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_logger: Final = logging.getLogger("blogwriter.notify.alerts")

register_log_fields("alert_kind", "ref_id")


def emit_alert(kind: AlertKind, ref_id: str | None = None) -> None:
    """Write one ``alert_event`` line (level ERROR) for Azure Monitor to email the owner.

    ``ref_id`` is the id of what the alert is about (job, post, subscription); at most 128
    characters of ``[A-Za-z0-9._:-]``. Never pass titles, URLs or any other content.
    Arguments of the wrong type (an ``int`` or ``UUID`` id, say) are treated like any other
    unacceptable value rather than raising: this runs inside ``except`` blocks.
    """
    _logger.error(ALERT_EVENT, extra=_alert_fields(kind, ref_id))


def _alert_fields(kind: object, ref_id: object) -> dict[str, object]:
    # ``object`` parameters: callers outside strict typing must not make emit_alert raise.
    known_kind = isinstance(kind, str) and kind in ALERT_KINDS
    fields: dict[str, object] = {"alert_kind": kind if known_kind else "unknown"}
    if ref_id is not None:
        if isinstance(ref_id, str) and _REF_ID_RE.fullmatch(ref_id):
            fields["ref_id"] = ref_id
        else:
            fields["reason"] = "ref_id_rejected"
    return fields
