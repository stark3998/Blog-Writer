"""The connection and its token lifecycle: access, vault failures, introspection, the banner,
the day 53/57/59 alerts, the API version sunset warning, and every write through
``update_singleton``."""

from __future__ import annotations

import contextvars
import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from blogwriter.core.crypto import TokenVault
from blogwriter.db import Doc, MemoryStore
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_singleton, save_singleton
from blogwriter.domain.settings import LinkedInConnection
from blogwriter.domain.states import ConnectionStatus
from blogwriter.domain.testing import FakeServices, make_services
from blogwriter.jobs.testing import DelegatingStore, FakeClock
from blogwriter.linkedin.transport import tokens
from blogwriter.linkedin.transport.client import INTROSPECT_URL, LinkedInClient
from blogwriter.linkedin.transport.testing import (
    MEMBER_TOKEN,
    MEMBER_URN,
    FakeLinkedIn,
    connect_member,
    fake_client,
    make_linkedin_settings,
)
from blogwriter.linkedin.transport.tokens import (
    ReconnectRequired,
    access_token,
    api_version_sunset,
    connection_view,
    disconnect,
    introspect_connection,
    mark_needs_reconnect,
    read_connection,
    record_connection_error,
    token_alerts,
)
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


async def connected(services: FakeServices, *, at: datetime = T0) -> None:
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=at)


async def current(services: FakeServices) -> LinkedInConnection:
    return await read_connection(services.store, services.clock.now())


def alerts(caplog: pytest.LogCaptureFixture) -> list[tuple[object, object]]:
    return [
        (getattr(r, "alert_kind", None), getattr(r, "ref_id", None))
        for r in caplog.records
        if r.getMessage() == "alert_event"
    ]


async def test_access_needs_a_connection(services: FakeServices) -> None:
    with pytest.raises(ReconnectRequired) as caught:
        await access_token(services)
    assert caught.value.code == "not_connected"
    assert await services.store.find("settings") == []  # nothing written


async def test_access_returns_the_token_and_member(services: FakeServices) -> None:
    await connected(services)
    assert await access_token(services) == (MEMBER_TOKEN, MEMBER_URN)


async def test_an_expired_token_needs_a_reconnect(services: FakeServices) -> None:
    await connected(services)
    services.clock.advance(timedelta(days=60, seconds=1))
    with pytest.raises(ReconnectRequired) as caught:
        await access_token(services)
    assert caught.value.code == "token_expired"
    conn = await current(services)
    assert (conn.status, conn.last_error) == (ConnectionStatus.NEEDS_RECONNECT, "token_expired")
    with pytest.raises(ReconnectRequired, match="needs_reconnect"):
        await access_token(services)


def other_vault() -> TokenVault:
    return TokenVault({"other": hashlib.sha256(b"another-test-key").digest()}, "other")


@pytest.mark.parametrize("damage", ["key_left_the_ring", "relabelled", "tampered"])
async def test_a_vault_decrypt_failure_needs_a_reconnect(
    services: FakeServices, damage: str
) -> None:
    await connected(services)
    match damage:
        case "key_left_the_ring":
            services.vault = other_vault()
        case "relabelled":
            assert services.vault is not None
            moved = services.vault.encrypt(MEMBER_TOKEN, context="another-document")
            conn, etag = await read_singleton(services.store, LinkedInConnection, now=to_iso(T0))
            await save_singleton(
                services.store, revalidated(conn, access_token=moved), if_match=etag
            )
        case _:
            conn, etag = await read_singleton(services.store, LinkedInConnection, now=to_iso(T0))
            assert conn.access_token is not None
            flipped = conn.access_token[:-2] + ("AA" if conn.access_token[-2:] != "AA" else "BA")
            await save_singleton(
                services.store, revalidated(conn, access_token=flipped), if_match=etag
            )
    with pytest.raises(ReconnectRequired) as caught:
        await access_token(services)
    assert caught.value.code == "token_unreadable"
    conn = await current(services)
    assert (conn.status, conn.last_error) == (ConnectionStatus.NEEDS_RECONNECT, "token_unreadable")


async def test_no_vault_is_not_a_reconnect(services: FakeServices) -> None:
    await connected(services)
    services.vault = None
    with pytest.raises(ReconnectRequired) as caught:
        await access_token(services)
    assert caught.value.code == "vault_unavailable"
    assert (await current(services)).status is ConnectionStatus.CONNECTED


async def test_the_view_shows_the_token_day_and_the_banner_from_day_50(
    services: FakeServices,
) -> None:
    await connected(services)
    conn = await current(services)
    day49 = connection_view(conn, T0 + timedelta(days=49, hours=23), configured=True)
    assert (day49.token_day, day49.banner_due) == (49, False)
    day50 = connection_view(conn, T0 + timedelta(days=50), configured=True)
    assert (day50.token_day, day50.banner_due) == (50, True)
    expired = connection_view(conn, T0 + timedelta(days=61), configured=True)
    assert expired.status is ConnectionStatus.NEEDS_RECONNECT
    assert "accessToken" not in expired.model_dump(by_alias=True)


async def test_introspection_records_an_active_token(
    services: FakeServices, linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    await connected(services)
    services.clock.advance(timedelta(days=3))
    expires = T0 + timedelta(days=59, hours=23)
    linkedin.introspection(active=True, expires_at=expires)
    result = await introspect_connection(services, li_client, now=services.clock.now())
    assert result.outcome == "active"
    conn = result.connection
    assert conn.introspection_active is True
    assert conn.expires_at == to_iso(expires)
    assert conn.last_introspected_at == to_iso(services.clock.now())
    (request,) = linkedin.calls("POST", INTROSPECT_URL)
    assert dict(httpx.QueryParams(request.content.decode()))["token"] == MEMBER_TOKEN


async def test_a_revoked_token_needs_a_reconnect(
    services: FakeServices, linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    await connected(services)
    linkedin.introspection(active=False)
    result = await introspect_connection(services, li_client, now=services.clock.now())
    assert (result.outcome, result.lost) == ("inactive", True)
    conn = result.connection
    assert (conn.status, conn.introspection_active, conn.last_error) == (
        ConnectionStatus.NEEDS_RECONNECT,
        False,
        "token_inactive",
    )


async def test_an_unreachable_linkedin_is_not_a_verdict(
    services: FakeServices, linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    await connected(services)
    linkedin.script("POST", INTROSPECT_URL, httpx.Response(502))
    result = await introspect_connection(services, li_client, now=services.clock.now())
    assert result.outcome == "failed"
    assert (result.connection.status, result.connection.last_error) == (
        ConnectionStatus.CONNECTED,
        "introspect_failed",
    )
    linkedin.introspection(active=True)
    cleared = await introspect_connection(services, li_client, now=services.clock.now())
    assert cleared.connection.last_error is None


async def test_introspection_without_credentials_or_connection_asks_nothing(
    services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    client = fake_client(linkedin, make_linkedin_settings(client_id=None, client_secret=None))
    try:
        idle = await introspect_connection(services, client, now=T0)
        assert idle.outcome == "not_connected"
        await connected(services)
        unconfigured = await introspect_connection(services, client, now=T0)
        assert unconfigured.outcome == "linkedin_not_configured"
    finally:
        await client.aclose()
    assert linkedin.requests == []


async def test_token_day_alerts_fire_once_each_on_days_53_57_59(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    await connected(services)
    sent: list[list[str]] = []
    for day in range(45, 61):
        services.clock.set(T0 + timedelta(days=day, hours=8, minutes=30))
        sent.append(await token_alerts(services, now=services.clock.now()))
    fired = {45 + index: keys for index, keys in enumerate(sent) if keys}
    assert fired == {53: ["53"], 57: ["57"], 59: ["59"]}
    titles = [payload.title for payload, _urgency in services.notifier.sent]
    assert titles == [
        "LinkedIn sign-in ends in 7 days: reconnect",
        "LinkedIn sign-in ends in 3 days: reconnect",
        "LinkedIn sign-in ends in 1 day: reconnect",
    ]
    assert all(p.url == "/settings/integrations" for p, _u in services.notifier.sent)
    assert alerts(caplog) == [("needs_attention", "linkedin-connection")] * 3
    assert set((await current(services)).alerts_sent) == {"53", "57", "59"}


async def test_missed_days_collapse_into_one_alert(services: FakeServices) -> None:
    await connected(services)
    services.clock.set(T0 + timedelta(days=58))
    assert await token_alerts(services, now=services.clock.now()) == ["53", "57"]
    assert len(services.notifier.sent) == 1


async def test_the_api_version_sunset_warning(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    sunset = api_version_sunset()
    assert sunset.isoformat() == "2027-11-15"
    assert api_version_sunset("202601").isoformat() == "2027-01-15"  # LinkedIn's published date
    before = datetime(2027, 9, 15, 9, tzinfo=UTC)  # 61 days before
    assert await token_alerts(services, now=before) == []  # never connected: nothing to warn
    await connected(services, at=before - timedelta(days=1))
    assert await token_alerts(services, now=before) == []
    due = datetime(2027, 9, 16, 9, tzinfo=UTC)
    assert await token_alerts(services, now=due) == ["api-202611"]
    assert await token_alerts(services, now=due + timedelta(days=1)) == []
    assert services.notifier.sent[0][0].title == "LinkedIn API 202611 retires 2027-11-15: update"
    assert ("needs_attention", "linkedin-api-version") in alerts(caplog)


async def test_mark_and_record_leave_a_disconnected_connection_alone(
    services: FakeServices,
) -> None:
    conn = await mark_needs_reconnect(services.store, now=T0, code="unauthorized")
    assert conn.status is ConnectionStatus.DISCONNECTED
    recorded = await record_connection_error(services.store, now=T0, code="oauth_denied")
    assert (recorded.status, recorded.last_error) == (ConnectionStatus.DISCONNECTED, "oauth_denied")


# --------------------------------------------------------------------------------------------
# Every connection write goes through update_singleton
# --------------------------------------------------------------------------------------------

_inside: contextvars.ContextVar[bool] = contextvars.ContextVar("inside", default=False)


class GuardedStore(DelegatingStore):
    """Fails any write of the connection made outside ``update_singleton``."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.writes = 0

    def _check(self, container: str, doc_id: object) -> None:
        if container == "settings" and doc_id == "linkedin-connection":
            assert _inside.get(), "connection written outside update_singleton"
            self.writes += 1

    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._check(container, body.get("id"))
        return await super().create(container, body)

    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        self._check(container, body.get("id"))
        return await super().replace(container, body, if_match=if_match)

    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._check(container, body.get("id"))
        return await super().upsert(container, body)


async def test_connection_writes_use_update_singleton(
    monkeypatch: pytest.MonkeyPatch, linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    real = tokens.update_singleton

    async def spy(*args: Any, **kwargs: Any) -> Any:
        token = _inside.set(True)
        try:
            return await real(*args, **kwargs)
        finally:
            _inside.reset(token)

    monkeypatch.setattr(tokens, "update_singleton", spy)
    store = GuardedStore()
    services = make_services(store=store, clock=FakeClock(T0))
    steps: list[Callable[[], Any]] = [
        lambda: connected(services),
        lambda: record_connection_error(store, now=T0, code="oauth_denied"),
        lambda: token_alerts(services, now=T0 + timedelta(days=53)),
        lambda: introspect_connection(services, li_client, now=T0),
        lambda: mark_needs_reconnect(store, now=T0, code="unauthorized"),
        lambda: disconnect(store, now=T0),
    ]
    linkedin.introspection(active=True)
    for step in steps:
        await step()
    assert store.writes >= len(steps)
