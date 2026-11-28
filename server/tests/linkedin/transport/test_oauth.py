"""Connecting LinkedIn: constant scopes, a single-use state, a callback that reflects nothing,
the token stored only as vault ciphertext and never logged or returned."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import NotFound
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.docstore import read_doc, read_singleton, save_singleton
from blogwriter.domain.ephemeral import OAuthState
from blogwriter.domain.settings import LINKEDIN_SCOPES, LinkedInConnection
from blogwriter.domain.states import ConnectionStatus
from blogwriter.domain.testing import FakeServices
from blogwriter.linkedin.transport.client import INTROSPECT_URL, TOKEN_URL, USERINFO_URL
from blogwriter.linkedin.transport.oauth import authorize_url
from blogwriter.linkedin.transport.testing import (
    CLIENT_SECRET,
    MEMBER_TOKEN,
    MEMBER_URN,
    FakeLinkedIn,
    make_linkedin_settings,
)
from blogwriter.linkedin.transport.updates import revalidated

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
CALLBACK = "/api/linkedin/oauth/callback"
RETURN = "/settings/integrations"
CODE = "AQTcode-canary-0d4c"


async def start(http: httpx.AsyncClient) -> str:
    response = await http.post("/api/linkedin/oauth/start", headers=WRITE)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(str(response.json()["authorizeUrl"])).query)
    return query["state"][0]


async def connection(services: FakeServices) -> LinkedInConnection:
    conn, _etag = await read_singleton(
        services.store, LinkedInConnection, now=to_iso(services.clock.now())
    )
    return conn


async def test_start_asks_for_the_constant_scopes_with_a_hashed_state(
    http: httpx.AsyncClient, services: FakeServices
) -> None:
    response = await http.post("/api/linkedin/oauth/start", headers=WRITE)
    assert response.status_code == 200
    url = urlsplit(str(response.json()["authorizeUrl"]))
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "www.linkedin.com",
        "/oauth/v2/authorization",
    )
    query = parse_qs(url.query)
    assert query["scope"] == ["openid profile w_member_social"] == [" ".join(LINKEDIN_SCOPES)]
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["testclient123"]
    assert query["redirect_uri"] == ["https://writer.test/api/linkedin/oauth/callback"]
    state = query["state"][0]
    assert len(state) >= 32
    stored = await read_doc(services.store, OAuthState, OAuthState.id_for(state))
    assert stored.doc.id == f"oauth-{sha256_hex(state)}"
    assert stored.doc.expires_at == to_iso(services.clock.now() + timedelta(minutes=10))
    assert state not in json.dumps(stored.doc.to_document())  # only its hash is stored


def test_the_authorize_url_never_carries_other_scopes() -> None:
    url = authorize_url(
        client_id="c", redirect_uri="https://x.test/api/linkedin/oauth/callback", state="s" * 20
    )
    assert parse_qs(urlsplit(url).query)["scope"] == ["openid profile w_member_social"]
    assert "%20" in url  # spaces, not '+'


async def test_start_needs_the_app_credentials_and_the_vault(
    app: FastAPI, http: httpx.AsyncClient, services: FakeServices
) -> None:
    app.state.linkedin_settings = make_linkedin_settings(client_id=None, client_secret=None)
    response = await http.post("/api/linkedin/oauth/start", headers=WRITE)
    assert (response.status_code, response.json()["code"]) == (503, "linkedin_not_configured")
    app.state.linkedin_settings = make_linkedin_settings()
    services.vault = None
    response = await http.post("/api/linkedin/oauth/start", headers=WRITE)
    assert (response.status_code, response.json()["code"]) == (503, "vault_unavailable")


async def test_the_callback_connects_and_stores_only_ciphertext(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    state = await start(http)
    linkedin.token_exchange()
    linkedin.userinfo()
    response = await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    assert response.status_code == 303
    assert response.headers["location"] == RETURN
    conn = await connection(services)
    assert conn.status is ConnectionStatus.CONNECTED
    assert conn.member_urn == MEMBER_URN
    assert conn.access_token is not None
    assert conn.access_token.startswith("v1.")
    assert services.vault is not None
    assert services.vault.decrypt(conn.access_token, context="linkedin-connection") == MEMBER_TOKEN
    raw = await services.store.read("settings", "linkedin-connection")
    assert MEMBER_TOKEN not in json.dumps(raw.body)
    assert conn.connected_at == to_iso(services.clock.now())
    assert conn.expires_at == to_iso(services.clock.now() + timedelta(seconds=5_184_000))
    exchange = linkedin.calls("POST", TOKEN_URL)[0]
    assert dict(httpx.QueryParams(exchange.content.decode()))["code"] == CODE
    assert (
        linkedin.calls("GET", USERINFO_URL)[0].headers["Authorization"] == f"Bearer {MEMBER_TOKEN}"
    )
    with pytest.raises(NotFound):
        await read_doc(services.store, OAuthState, OAuthState.id_for(state))  # consumed
    view = await http.get("/api/linkedin/connection", headers=READ)
    assert view.status_code == 200
    body = view.json()
    assert body["status"] == "connected"
    assert body["memberUrn"] == MEMBER_URN
    assert body["tokenDay"] == 0
    assert body["bannerDue"] is False
    assert body["scopes"] == list(LINKEDIN_SCOPES)
    assert body["configured"] is True
    assert MEMBER_TOKEN not in view.text
    assert "v1." not in view.text


async def test_the_callback_reflects_nothing(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    junk = {
        "code": "<script>alert(1)</script>",
        "state": "evil-state-value-1234567890",
        "error_description": "<b>owned</b>",
        "next": "https://evil.test/",
    }
    response = await http.get(CALLBACK, params=junk, headers=READ)
    assert response.status_code == 303
    assert response.headers["location"] == RETURN
    assert response.content == b""
    echoed = " ".join(f"{k}: {v}" for k, v in response.headers.items())
    for value in junk.values():
        assert value not in echoed
    assert linkedin.requests == []
    # A state this app never issued: nothing is written (a GET changes no state).
    assert await services.store.find("settings") == []


async def test_a_callback_without_a_valid_state_writes_nothing(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    await save_singleton(
        services.store,
        revalidated(
            LinkedInConnection.default(to_iso(services.clock.now())),
            last_error="token_inactive",
        ),
        if_match=None,
    )
    before = await services.store.read("settings", "linkedin-connection")
    for params in ({}, {"state": "never-issued-state-0123456789"}, {"code": CODE}):
        response = await http.get(CALLBACK, params=params, headers=READ)
        assert (response.status_code, response.headers["location"]) == (303, RETURN)
    after = await services.store.read("settings", "linkedin-connection")
    assert after.etag == before.etag  # the earlier error the owner should see is kept
    assert (await connection(services)).last_error == "token_inactive"
    assert linkedin.requests == []


async def test_a_state_is_consumed_exactly_once(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    state = await start(http)
    linkedin.token_exchange()
    linkedin.userinfo()
    first = await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    again = await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    assert first.headers["location"] == again.headers["location"] == RETURN
    assert len(linkedin.calls("POST", TOKEN_URL)) == 1  # the replay was refused
    conn = await connection(services)
    assert conn.status is ConnectionStatus.CONNECTED
    assert conn.last_error is None  # and wrote nothing


async def test_an_expired_state_is_refused(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    state = await start(http)
    services.clock.advance(timedelta(minutes=10, seconds=1))
    await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    assert linkedin.requests == []
    assert (await connection(services)).last_error == "oauth_state_invalid"


async def test_head_never_consumes_a_state(http: httpx.AsyncClient, services: FakeServices) -> None:
    state = await start(http)
    response = await http.head(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    assert response.status_code == 405
    await read_doc(services.store, OAuthState, OAuthState.id_for(state))  # still there


@pytest.mark.parametrize(
    ("script", "params", "code"),
    [
        ("none", {"error": "user_cancelled_authorize"}, "oauth_denied"),
        ("none", {}, "oauth_code_missing"),
        ("exchange_400", {"code": CODE}, "oauth_exchange_failed"),
        ("scope", {"code": CODE}, "oauth_scope_missing"),
        ("userinfo_500", {"code": CODE}, "oauth_userinfo_failed"),
    ],
)
async def test_failures_land_in_last_error(
    http: httpx.AsyncClient,
    services: FakeServices,
    linkedin: FakeLinkedIn,
    script: str,
    params: dict[str, str],
    code: str,
) -> None:
    match script:
        case "exchange_400":
            linkedin.script("POST", TOKEN_URL, httpx.Response(400, json={"error": "invalid"}))
        case "scope":
            linkedin.token_exchange(scope="openid,profile")
        case "userinfo_500":
            linkedin.token_exchange()
            linkedin.script("GET", USERINFO_URL, httpx.Response(500))
        case _:
            pass
    state = await start(http)
    response = await http.get(CALLBACK, params={**params, "state": state}, headers=READ)
    assert (response.status_code, response.headers["location"]) == (303, RETURN)
    conn = await connection(services)
    assert conn.last_error == code
    assert conn.status is ConnectionStatus.DISCONNECTED
    assert conn.access_token is None
    view = (await http.get("/api/linkedin/connection", headers=READ)).json()
    assert view["lastError"] == code


async def test_the_callback_redirects_even_when_nothing_is_wired(
    app: FastAPI, http: httpx.AsyncClient
) -> None:
    app.state.services = None
    response = await http.get(CALLBACK, params={"code": CODE, "state": "x" * 40}, headers=READ)
    assert (response.status_code, response.headers["location"]) == (303, RETURN)


async def test_reconnecting_resets_token_day_alerts_but_keeps_api_warnings(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    old = revalidated(
        LinkedInConnection.default(to_iso(services.clock.now())),
        alerts_sent={
            "53": "2026-10-01T00:00:00.000000Z",
            "api-202611": "2026-10-01T00:00:00.000000Z",
        },
    )
    await save_singleton(services.store, old, if_match=None)
    state = await start(http)
    linkedin.token_exchange()
    linkedin.userinfo()
    await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    conn = await connection(services)
    assert conn.alerts_sent == {"api-202611": "2026-10-01T00:00:00.000000Z"}


async def test_disconnect_forgets_the_token(
    http: httpx.AsyncClient, services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    state = await start(http)
    linkedin.token_exchange()
    linkedin.userinfo()
    await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    response = await http.post("/api/linkedin/disconnect", headers=WRITE)
    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"
    assert response.json()["memberUrn"] is None
    conn = await connection(services)
    assert (conn.status, conn.access_token, conn.member_urn) == (
        ConnectionStatus.DISCONNECTED,
        None,
        None,
    )
    again = await http.post("/api/linkedin/disconnect", headers=WRITE)
    assert again.status_code == 200


async def test_codes_states_and_tokens_are_never_logged(
    http: httpx.AsyncClient,
    linkedin: FakeLinkedIn,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    state = await start(http)
    linkedin.token_exchange()
    linkedin.userinfo()
    await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)
    await http.get(CALLBACK, params={"code": CODE, "state": state}, headers=READ)  # a replay
    linkedin.script("POST", INTROSPECT_URL, httpx.Response(500))
    introspected = await http.post("/api/linkedin/introspect", headers=WRITE)
    assert introspected.json()["lastError"] == "introspect_failed"
    logged = " ".join(repr(record.__dict__) for record in caplog.records)
    logged += capsys.readouterr().out
    assert "linkedin_oauth_failed" in logged
    for secret in (MEMBER_TOKEN, CODE, state, CLIENT_SECRET):
        assert secret not in logged
