"""The LinkedIn connection: the encrypted member token, access for uploads and the publisher,
introspection and the token lifecycle (banner from day 50, alerts on days 53, 57 and 59, the
API version sunset warning).

The token is stored only as vault ciphertext (``TokenVault``, context = the connection document
id ``linkedin-connection``) and is decrypted in memory right before a call. It is never logged,
returned by a route or put in a job event. **Every write of the connection goes through**
:func:`blogwriter.domain.docstore.update_singleton` (:func:`update_connection`): server-side
writes of a singleton that may never have been saved.

Any vault failure (a key that left the ring, a tampered or relabelled ciphertext) means
``needs_reconnect``: the owner reconnects and a fresh token is encrypted under the current key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal

from blogwriter.core.crypto import TokenVault, VaultError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.docstore import read_singleton, update_singleton
from blogwriter.domain.ports import Notifier, Services
from blogwriter.domain.settings import (
    LINKEDIN_API_VERSION,
    LINKEDIN_SCOPES,
    TOKEN_LIFETIME_DAYS,
    LinkedInConnection,
)
from blogwriter.domain.states import CONNECTION_MACHINE, ConnectionStatus
from blogwriter.linkedin.transport.client import LinkedInClient, LinkedInError, TokenGrant
from blogwriter.linkedin.transport.updates import revalidated
from blogwriter.notify import NotificationPayload, emit_alert

__all__ = [
    "ALERT_REF",
    "API_ALERT_PREFIX",
    "API_SUNSET_WARNING",
    "API_VERSION_ALERT_REF",
    "CONNECTION_ID",
    "INTEGRATIONS_PATH",
    "MAX_TOKEN_LIFETIME",
    "ConnectionView",
    "IntrospectResult",
    "ReconnectRequired",
    "access_token",
    "api_version_sunset",
    "connection_usable",
    "connection_view",
    "disconnect",
    "effective_status",
    "introspect_connection",
    "mark_needs_reconnect",
    "notify_owner",
    "read_connection",
    "record_connection_error",
    "store_connection",
    "token_alerts",
    "update_connection",
]

CONNECTION_ID: Final = LinkedInConnection.SINGLETON_ID
"""The connection document id, also the vault context of its token."""
ALERT_REF: Final = "linkedin-connection"
API_VERSION_ALERT_REF: Final = "linkedin-api-version"
API_ALERT_PREFIX: Final = "api-"
"""``alerts_sent`` keys of API version warnings (kept across reconnects)."""
API_SUNSET_WARNING: Final = timedelta(days=60)
INTEGRATIONS_PATH: Final = "/settings/integrations"
MAX_TOKEN_LIFETIME: Final = timedelta(days=366)
"""Longest ``expires_in`` taken at face value (LinkedIn issues 60-day member tokens)."""

_logger: Final = logging.getLogger("blogwriter.linkedin.tokens")
register_log_fields("connection_status", "error_code", "days")


class ReconnectRequired(Exception):
    """No usable LinkedIn connection. ``code`` says why: ``not_connected``,
    ``needs_reconnect``, ``token_expired``, ``token_unreadable`` (a vault failure; the
    connection is now ``needs_reconnect``), ``unauthorized`` (LinkedIn answered 401) or
    ``vault_unavailable`` (the vault is not configured; the connection is left alone)."""

    def __init__(self, code: str = "needs_reconnect") -> None:
        super().__init__(code)
        self.code = code


def api_version_sunset(version: str = LINKEDIN_API_VERSION) -> date:
    """When a ``Linkedin-Version`` stops working: LinkedIn supports each monthly version for one
    year (``202601`` sunsets on 2027-01-15), so ``YYYYMM`` sunsets on the 15th of that month a
    year later. Check LinkedIn's versioning page when the version changes."""
    year, month = int(version[:4]), int(version[4:6])
    return date(year + 1, month, 15)


# --------------------------------------------------------------------------------------------
# Reading and the view
# --------------------------------------------------------------------------------------------


def _expired(conn: LinkedInConnection, now: datetime) -> bool:
    return conn.expires_at is not None and parse_iso(conn.expires_at) <= now


def effective_status(conn: LinkedInConnection, now: datetime) -> ConnectionStatus:
    """The stored status, except that a connection whose token has expired reads as
    ``needs_reconnect`` (without a write)."""
    if conn.status is ConnectionStatus.CONNECTED and _expired(conn, now):
        return ConnectionStatus.NEEDS_RECONNECT
    return conn.status


def connection_usable(conn: LinkedInConnection, now: datetime) -> bool:
    """Connected, with a token and a member URN, not past its expiry (decryption not tried)."""
    return (
        effective_status(conn, now) is ConnectionStatus.CONNECTED
        and conn.access_token is not None
        and conn.member_urn is not None
    )


class ConnectionView(ApiModel):
    """``GET /api/linkedin/connection``: everything about the connection except the token."""

    status: ConnectionStatus
    member_urn: str | None = None
    connected_at: str | None = None
    expires_at: str | None = None
    token_day: int | None = None
    banner_due: bool = False
    last_introspected_at: str | None = None
    last_error: str | None = None
    scopes: list[str]
    configured: bool = False
    """The LinkedIn app credentials are set (connecting is possible)."""
    api_version: str = LINKEDIN_API_VERSION


def connection_view(conn: LinkedInConnection, now: datetime, *, configured: bool) -> ConnectionView:
    status = effective_status(conn, now)
    connected = status is not ConnectionStatus.DISCONNECTED
    return ConnectionView(
        status=status,
        member_urn=conn.member_urn if connected else None,
        connected_at=conn.connected_at if connected else None,
        expires_at=conn.expires_at if connected else None,
        token_day=conn.token_day(now) if connected else None,
        banner_due=connected and conn.banner_due(now),
        last_introspected_at=conn.last_introspected_at,
        last_error=conn.last_error,
        scopes=list(LINKEDIN_SCOPES),
        configured=configured,
        api_version=LINKEDIN_API_VERSION,
    )


async def read_connection(store: Store, now: datetime) -> LinkedInConnection:
    """The stored connection, or the never-saved default (disconnected)."""
    conn, _etag = await read_singleton(store, LinkedInConnection, now=to_iso(now))
    return conn


# --------------------------------------------------------------------------------------------
# Writes (all through update_singleton)
# --------------------------------------------------------------------------------------------


def _changed(conn: LinkedInConnection, stamp: str, **changes: object) -> LinkedInConnection:
    """A validated copy with ``changes`` and ``updated_at`` (the store write re-checks too)."""
    return revalidated(conn, **changes, updated_at=stamp)


def _status_change(
    conn: LinkedInConnection, target: ConnectionStatus, stamp: str
) -> dict[str, object]:
    """``status``/``status_changed_at`` for a move to ``target`` (``TransitionError`` if the
    machine refuses it); nothing when the status stays."""
    if conn.status is target:
        return {}
    CONNECTION_MACHINE.transition(conn.status, target)
    return {"status": target, "status_changed_at": stamp}


async def update_connection(
    store: Store,
    mutate: Callable[[LinkedInConnection], LinkedInConnection | None],
    *,
    now: datetime,
) -> LinkedInConnection:
    """Apply ``mutate`` to the connection with :func:`update_singleton` (CAS, retried)."""
    conn, _etag = await update_singleton(store, LinkedInConnection, mutate, now=to_iso(now))
    return conn


async def store_connection(
    store: Store, vault: TokenVault, *, grant: TokenGrant, member_urn: str, now: datetime
) -> LinkedInConnection:
    """Encrypt the new token and mark the connection ``connected`` (token day 0, token-day
    alerts reset; API version warnings kept)."""
    stamp = to_iso(now)
    ciphertext = vault.encrypt(grant.access_token, context=CONNECTION_ID)
    lifetime = min(timedelta(seconds=grant.expires_in), MAX_TOKEN_LIFETIME)

    def mutate(conn: LinkedInConnection) -> LinkedInConnection:
        kept = {k: v for k, v in conn.alerts_sent.items() if k.startswith(API_ALERT_PREFIX)}
        return _changed(
            conn,
            stamp,
            **_status_change(conn, ConnectionStatus.CONNECTED, stamp),
            member_urn=member_urn,
            access_token=ciphertext,
            connected_at=stamp,
            expires_at=to_iso(now + lifetime),
            last_introspected_at=None,
            introspection_active=None,
            alerts_sent=kept,
            api_version=LINKEDIN_API_VERSION,
            last_error=None,
        )

    connected = await update_connection(store, mutate, now=now)
    _logger.info("linkedin_connected", extra={"connection_status": connected.status.value})
    return connected


async def disconnect(store: Store, *, now: datetime) -> LinkedInConnection:
    """Forget the token and the member (``disconnected``). Idempotent."""
    stamp = to_iso(now)

    def mutate(conn: LinkedInConnection) -> LinkedInConnection | None:
        if conn.status is ConnectionStatus.DISCONNECTED and conn.access_token is None:
            return None
        kept = {k: v for k, v in conn.alerts_sent.items() if k.startswith(API_ALERT_PREFIX)}
        return _changed(
            conn,
            stamp,
            **_status_change(conn, ConnectionStatus.DISCONNECTED, stamp),
            member_urn=None,
            access_token=None,
            connected_at=None,
            expires_at=None,
            last_introspected_at=None,
            introspection_active=None,
            alerts_sent=kept,
            last_error=None,
        )

    return await update_connection(store, mutate, now=now)


async def mark_needs_reconnect(store: Store, *, now: datetime, code: str) -> LinkedInConnection:
    """``connected -> needs_reconnect`` with ``last_error = code`` (a disconnected connection
    stays disconnected)."""
    stamp = to_iso(now)

    def mutate(conn: LinkedInConnection) -> LinkedInConnection | None:
        if conn.status is ConnectionStatus.DISCONNECTED:
            return None
        if conn.status is ConnectionStatus.NEEDS_RECONNECT and conn.last_error == code:
            return None
        return _changed(
            conn,
            stamp,
            **_status_change(conn, ConnectionStatus.NEEDS_RECONNECT, stamp),
            last_error=code,
        )

    updated = await update_connection(store, mutate, now=now)
    _logger.warning(
        "linkedin_needs_reconnect",
        extra={"connection_status": updated.status.value, "error_code": code},
    )
    return updated


async def record_connection_error(store: Store, *, now: datetime, code: str) -> LinkedInConnection:
    """Record ``last_error`` (OAuth callback and introspection failures) without a status
    change."""
    stamp = to_iso(now)

    def mutate(conn: LinkedInConnection) -> LinkedInConnection | None:
        if conn.last_error == code:
            return None
        return _changed(conn, stamp, last_error=code)

    return await update_connection(store, mutate, now=now)


# --------------------------------------------------------------------------------------------
# Using the token
# --------------------------------------------------------------------------------------------


async def access_token(services: Services) -> tuple[str, str]:
    """``(token, member URN)`` of a usable connection, else :class:`ReconnectRequired`.

    An expired token or a vault decrypt failure also moves the connection to
    ``needs_reconnect`` before raising. The plain token lives only in the caller's memory.
    """
    store = services.store
    now = services.clock.now()
    conn = await read_connection(store, now)
    if conn.status is ConnectionStatus.DISCONNECTED or conn.access_token is None:
        raise ReconnectRequired("not_connected")
    if conn.status is not ConnectionStatus.CONNECTED or conn.member_urn is None:
        raise ReconnectRequired("needs_reconnect")
    if _expired(conn, now):
        await mark_needs_reconnect(store, now=now, code="token_expired")
        raise ReconnectRequired("token_expired")
    vault = services.vault
    if vault is None:
        raise ReconnectRequired("vault_unavailable")
    try:
        token = vault.decrypt(conn.access_token, context=CONNECTION_ID)
    except VaultError:
        await mark_needs_reconnect(store, now=now, code="token_unreadable")
        raise ReconnectRequired("token_unreadable") from None
    return token, conn.member_urn


async def notify_owner(
    notifier: Notifier | None,
    *,
    title: str,
    url: str,
    tag: str,
    urgency: Literal["normal", "high"] = "high",
) -> bool:
    """Best-effort push (title and same-origin link only); a failure is logged, never raised."""
    if notifier is None:
        return False
    try:
        result = await notifier.notify(
            NotificationPayload(title=title, url=url, tag=tag), urgency=urgency
        )
    except Exception as exc:  # a push problem must not undo what already happened
        _logger.warning("linkedin_push_failed", extra={"error_type": type(exc).__name__})
        return False
    return result.any_delivered


# --------------------------------------------------------------------------------------------
# Introspection and alerts (the linkedin.introspect job and POST /api/linkedin/introspect)
# --------------------------------------------------------------------------------------------

type IntrospectOutcome = Literal[
    "active",
    "inactive",
    "expired",
    "token_unreadable",
    "failed",
    "not_connected",
    "vault_unavailable",
    "linkedin_not_configured",
]


@dataclass(frozen=True, slots=True)
class IntrospectResult:
    outcome: IntrospectOutcome
    connection: LinkedInConnection
    lost: bool = False
    """The connection went from ``connected`` to ``needs_reconnect`` in this run."""


async def introspect_connection(
    services: Services, client: LinkedInClient | None, *, now: datetime
) -> IntrospectResult:
    """Ask LinkedIn whether the token still works and record the answer.

    Active: ``introspectionActive``, ``expiresAt`` and ``lastIntrospectedAt`` are updated.
    Inactive (revoked, expired) or locally expired: ``needs_reconnect``. A vault failure:
    ``needs_reconnect``. LinkedIn unreachable: ``lastError = introspect_failed`` and the
    status stays (a failed check is not a verdict).
    """
    store = services.store
    conn = await read_connection(store, now)
    if conn.status is ConnectionStatus.DISCONNECTED or conn.access_token is None:
        return IntrospectResult("not_connected", conn)
    was_connected = conn.status is ConnectionStatus.CONNECTED
    if _expired(conn, now):
        updated = await mark_needs_reconnect(store, now=now, code="token_expired")
        return IntrospectResult("expired", updated, lost=was_connected)
    vault = services.vault
    if vault is None:
        return IntrospectResult("vault_unavailable", conn)
    try:
        token = vault.decrypt(conn.access_token, context=CONNECTION_ID)
    except VaultError:
        updated = await mark_needs_reconnect(store, now=now, code="token_unreadable")
        return IntrospectResult("token_unreadable", updated, lost=was_connected)
    if client is None or not client.settings.configured:
        return IntrospectResult("linkedin_not_configured", conn)
    try:
        verdict = await client.introspect(token)
    except LinkedInError:
        updated = await record_connection_error(store, now=now, code="introspect_failed")
        return IntrospectResult("failed", updated)
    stamp = to_iso(now)
    checked = conn.access_token  # the verdict is about this token only
    if not verdict.active:

        def inactive(current: LinkedInConnection) -> LinkedInConnection | None:
            if current.access_token != checked:
                return None  # reconnected or disconnected meanwhile
            return _changed(
                current,
                stamp,
                **_status_change(current, ConnectionStatus.NEEDS_RECONNECT, stamp),
                introspection_active=False,
                last_introspected_at=stamp,
                last_error="token_inactive",
            )

        updated = await update_connection(store, inactive, now=now)
        _logger.warning(
            "linkedin_token_inactive", extra={"connection_status": updated.status.value}
        )
        lost = was_connected and updated.status is ConnectionStatus.NEEDS_RECONNECT
        return IntrospectResult("inactive", updated, lost=lost)

    expires_at = (
        to_iso(min(verdict.expires_at, now + MAX_TOKEN_LIFETIME))
        if verdict.expires_at is not None
        else conn.expires_at
    )

    def active(current: LinkedInConnection) -> LinkedInConnection | None:
        if current.access_token != checked:
            return None
        last_error = current.last_error
        if last_error in ("introspect_failed", "token_inactive"):
            last_error = None
        return _changed(
            current,
            stamp,
            introspection_active=True,
            last_introspected_at=stamp,
            expires_at=expires_at,
            last_error=last_error,
        )

    updated = await update_connection(store, active, now=now)
    return IntrospectResult("active", updated)


async def token_alerts(services: Services, *, now: datetime) -> list[str]:
    """Send the token-day alerts that are due (days 53, 57, 59, once each per token) and the
    API version sunset warning (60 days before), then record them in ``alertsSent``.

    The push goes out before the record is written, so a crash in between repeats a push
    rather than losing one. Returns the keys recorded.
    """
    store = services.store
    conn = await read_connection(store, now)
    keys: list[str] = []
    if effective_status(conn, now) is ConnectionStatus.CONNECTED:
        due = conn.alerts_due(now)
        day = conn.token_day(now)
        if due and day is not None:
            left = max(0, TOKEN_LIFETIME_DAYS - day)
            await notify_owner(
                services.notifier,
                title=f"LinkedIn sign-in ends in {left} day{'' if left == 1 else 's'}: reconnect",
                url=INTEGRATIONS_PATH,
                tag="linkedin-token",
            )
            emit_alert("needs_attention", ALERT_REF)
            _logger.warning("linkedin_token_alert", extra={"days": day})
            keys.extend(str(d) for d in due)
    sunset = api_version_sunset(LINKEDIN_API_VERSION)
    api_key = f"{API_ALERT_PREFIX}{LINKEDIN_API_VERSION}"
    if (
        conn.status is not ConnectionStatus.DISCONNECTED
        and now.date() >= sunset - API_SUNSET_WARNING
        and api_key not in conn.alerts_sent
    ):
        await notify_owner(
            services.notifier,
            title=f"LinkedIn API {LINKEDIN_API_VERSION} retires {sunset.isoformat()}: update",
            url=INTEGRATIONS_PATH,
            tag="linkedin-api",
        )
        emit_alert("needs_attention", API_VERSION_ALERT_REF)
        keys.append(api_key)
    if keys:
        stamp = to_iso(now)

        def record(current: LinkedInConnection) -> LinkedInConnection:
            sent = dict(current.alerts_sent)
            sent.update(dict.fromkeys(keys, stamp))
            return _changed(current, stamp, alerts_sent=sent)

        await update_connection(store, record, now=now)
    return keys
