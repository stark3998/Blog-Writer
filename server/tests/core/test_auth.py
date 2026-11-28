"""Tests for blogwriter.core.auth: principal parsing, the __Host-bw cookie, CSRF and the
HTTP-level access rules (v2 plan, Security model > Auth)."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import pytest
from fastapi import APIRouter
from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Scope

from blogwriter.core.app import create_app
from blogwriter.core.auth import (
    CLEAR_COOKIE_HEADER,
    COOKIE_NAME,
    Authenticator,
    CookieSigner,
    OwnerPrincipal,
    Principal,
    PrincipalError,
    is_same_origin,
    parse_easy_auth_principal,
    read_cookie_values,
)
from blogwriter.core.settings import CoreSettings
from blogwriter.core.testing import (
    COOKIE_KEY,
    ORIGIN,
    OTHER_OID,
    OWNER_OID,
    asgi_client,
    encode_principal,
    make_settings,
    owner_cookie,
    principal_headers,
    write_headers,
)

OID_CLAIM = "http://schemas.microsoft.com/identity/claims/objectidentifier"
TTL = 12 * 3600


def probe_router() -> APIRouter:
    """GET and unsafe-method routes that echo the authenticated principal."""
    router = APIRouter()

    async def probe(principal: OwnerPrincipal) -> dict[str, str]:
        return {"oid": principal.oid, "source": principal.source}

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        router.add_api_route(
            "/api/probe", probe, methods=[method], operation_id=f"probe_{method.lower()}"
        )
    return router


def signer(clock: float | None = None) -> CookieSigner:
    if clock is None:
        return CookieSigner(COOKIE_KEY.encode(), ttl_seconds=TTL)
    return CookieSigner(COOKIE_KEY.encode(), ttl_seconds=TTL, clock=lambda: clock)


def principal_document(*claims: tuple[str, str], auth_typ: str = "aad") -> dict[str, object]:
    return {
        "auth_typ": auth_typ,
        "claims": [{"typ": typ, "val": val} for typ, val in claims],
        "name_typ": "name",
        "role_typ": "roles",
    }


def headers_for(document: object, **extra: str) -> Headers:
    encoded = base64.b64encode(json.dumps(document).encode()).decode()
    return Headers(headers={"x-ms-client-principal": encoded, **extra})


def parse_set_cookie(header: str) -> tuple[str, str, dict[str, str | bool]]:
    first, *attributes = (part.strip() for part in header.split(";"))
    name, _, value = first.partition("=")
    parsed: dict[str, str | bool] = {}
    for attribute in attributes:
        key, separator, attribute_value = attribute.partition("=")
        parsed[key.lower()] = attribute_value if separator else True
    return name, value, parsed


# ----------------------------------------------------------------------------------------------
# Easy Auth principal parsing
# ----------------------------------------------------------------------------------------------


def test_parses_the_easy_auth_principal() -> None:
    principal = parse_easy_auth_principal(Headers(headers=principal_headers(OWNER_OID)))
    assert principal == Principal(oid=OWNER_OID, name="Owner", source="easy_auth")


def test_principal_object_id_is_normalized_and_short_oid_claim_accepted() -> None:
    document = principal_document(("oid", OWNER_OID.upper()))
    principal = parse_easy_auth_principal(headers_for(document))
    assert principal is not None
    assert principal.oid == OWNER_OID
    assert principal.name is None


def test_name_falls_back_to_the_name_header() -> None:
    document = principal_document((OID_CLAIM, OWNER_OID))
    headers = headers_for(document, **{"x-ms-client-principal-name": "owner@example.com"})
    principal = parse_easy_auth_principal(headers)
    assert principal is not None
    assert principal.name == "owner@example.com"


def test_name_drops_control_characters() -> None:
    document = principal_document((OID_CLAIM, OWNER_OID), ("name", "Jay\x00\n Owner\x1b"))
    principal = parse_easy_auth_principal(headers_for(document))
    assert principal is not None
    assert principal.name == "Jay Owner"


def test_unpadded_urlsafe_base64_and_entra_idp_alias_are_accepted() -> None:
    document = principal_document((OID_CLAIM, OWNER_OID), auth_typ="AzureActiveDirectory")
    encoded = base64.urlsafe_b64encode(json.dumps(document).encode()).decode().rstrip("=")
    principal = parse_easy_auth_principal(Headers(headers={"x-ms-client-principal": encoded}))
    assert principal is not None
    assert principal.oid == OWNER_OID


def test_no_principal_header_means_no_principal_even_with_id_headers() -> None:
    headers = Headers(headers={"x-ms-client-principal-id": OWNER_OID})
    assert parse_easy_auth_principal(headers) is None


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"x-ms-client-principal": "not base64!"}, "bad_encoding"),
        ({"x-ms-client-principal": "abcde"}, "bad_encoding"),
        ({"x-ms-client-principal": base64.b64encode(b"not json").decode()}, "bad_encoding"),
        ({"x-ms-client-principal": encode_principal({"a": 1}) + "A" * 70_000}, "too_large"),
    ],
)
def test_undecodable_principal_is_rejected(headers: dict[str, str], reason: str) -> None:
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(Headers(headers=headers))
    assert reason in caught.value.reason


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (["not", "an", "object"], "bad_document"),
        ({"auth_typ": "aad"}, "bad_claims"),
        ({"auth_typ": "aad", "claims": "x"}, "bad_claims"),
        ({"auth_typ": "aad", "claims": ["x"]}, "bad_claims"),
        ({"auth_typ": "aad", "claims": [{"typ": OID_CLAIM, "val": 7}]}, "bad_claims"),
        (principal_document(("name", "Owner")), "bad_oid"),
        (principal_document((OID_CLAIM, "not-a-guid")), "bad_oid"),
        (principal_document((OID_CLAIM, OWNER_OID), ("oid", OTHER_OID)), "bad_oid"),
        ({"claims": [{"typ": OID_CLAIM, "val": OWNER_OID}]}, "unsupported_idp"),
        (principal_document((OID_CLAIM, OWNER_OID), auth_typ="google"), "unsupported_idp"),
    ],
)
def test_untrustworthy_principal_is_rejected(document: object, reason: str) -> None:
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(headers_for(document))
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "payload",
    [
        b"[" * 49_152,  # the most the 64 KiB header cap can carry
        b"[" * 24_000 + b"]" * 24_000,  # well-formed JSON, still too deep for the parser
        b'{"":' * 12_000,
    ],
)
def test_deeply_nested_principal_is_a_principal_error(payload: bytes) -> None:
    with pytest.raises(RecursionError):
        json.loads(payload)  # what the parser does with it: not a ValueError
    encoded = base64.b64encode(payload).decode()
    assert len(encoded) <= 64 * 1024  # inside the size cap, so the decoder must cope
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(Headers(headers={"x-ms-client-principal": encoded}))
    assert caught.value.reason == "bad_encoding"


async def test_deeply_nested_principal_is_401_not_500() -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    nested = base64.b64encode(b"[" * 49_152).decode()
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"X-MS-CLIENT-PRINCIPAL": nested})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_principal"


def test_principal_id_header_must_match_the_claims() -> None:
    document = principal_document((OID_CLAIM, OWNER_OID))
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(headers_for(document, **{"x-ms-client-principal-id": OTHER_OID}))
    assert caught.value.reason == "id_mismatch"


def test_principal_idp_header_must_be_entra() -> None:
    document = principal_document((OID_CLAIM, OWNER_OID))
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(headers_for(document, **{"x-ms-client-principal-idp": "github"}))
    assert caught.value.reason == "unsupported_idp"


def test_duplicate_principal_headers_are_rejected() -> None:
    encoded = encode_principal(principal_document((OID_CLAIM, OWNER_OID)))
    headers = Headers(
        raw=[(b"x-ms-client-principal", encoded.encode()), (b"x-ms-client-principal", b"x")]
    )
    with pytest.raises(PrincipalError) as caught:
        parse_easy_auth_principal(headers)
    assert caught.value.reason == "duplicate_header"


# ----------------------------------------------------------------------------------------------
# Cookie signing
# ----------------------------------------------------------------------------------------------


def test_cookie_round_trip_and_format() -> None:
    issued = signer(1_800_000_000).issue(OWNER_OID)
    assert issued.expires_at == 1_800_000_000 + TTL
    assert issued.max_age == TTL
    assert re.fullmatch(rf"v1\.{OWNER_OID}\.{issued.expires_at}\.[A-Za-z0-9_-]{{43}}", issued.value)
    assert signer(1_800_000_000).verify(issued.value, owner_oid=OWNER_OID) == issued.expires_at


def test_cookie_header_has_every_attribute() -> None:
    issued = signer().issue(OWNER_OID)
    name, value, attributes = parse_set_cookie(issued.set_cookie_header())
    assert name == COOKIE_NAME == "__Host-bw"
    assert value == issued.value
    assert attributes == {
        "max-age": str(TTL),
        "path": "/",
        "secure": True,
        "httponly": True,
        "samesite": "Strict",
    }


def test_tampered_cookie_is_rejected() -> None:
    issued = signer().issue(OWNER_OID)
    version, oid, expires, signature = issued.value.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = [
        f"v2.{oid}.{expires}.{signature}",  # version
        f"{version}.{OTHER_OID}.{expires}.{signature}",  # object ID
        f"{version}.{oid}.{int(expires) - 1}.{signature}",  # expiry
        f"{version}.{oid}.{expires}.{flipped}",  # signature
        f"{version}.{oid}.{expires}.{signature}A",  # signature length
    ]
    for value in tampered:
        assert signer().verify(value, owner_oid=OWNER_OID) is None, value
        assert signer().verify(value, owner_oid=OTHER_OID) is None, value


def test_cookie_for_another_object_id_is_rejected() -> None:
    issued = signer().issue(OTHER_OID)
    assert signer().verify(issued.value, owner_oid=OWNER_OID) is None
    assert signer().verify(issued.value, owner_oid=None) is None


def test_expired_cookie_is_rejected() -> None:
    issued = signer(1_800_000_000).issue(OWNER_OID)
    assert signer(issued.expires_at - 1).verify(issued.value, owner_oid=OWNER_OID) is not None
    assert signer(issued.expires_at).verify(issued.value, owner_oid=OWNER_OID) is None


def test_cookie_with_an_expiry_beyond_the_ttl_is_rejected() -> None:
    long_lived = CookieSigner(COOKIE_KEY.encode(), ttl_seconds=48 * 3600, clock=lambda: 1e9)
    issued = long_lived.issue(OWNER_OID)
    assert signer(1e9).verify(issued.value, owner_oid=OWNER_OID) is None


def test_cookie_signed_with_another_key_is_rejected() -> None:
    other = CookieSigner(b"another-key-another-key-another-key!", ttl_seconds=TTL)
    assert signer().verify(other.issue(OWNER_OID).value, owner_oid=OWNER_OID) is None


@pytest.mark.parametrize(
    "value",
    ["", "v1", f"v1.{OWNER_OID}.123", f"v2.{OWNER_OID}.123.{'A' * 43}", "garbage" * 20],
)
def test_malformed_cookie_is_rejected(value: str) -> None:
    assert signer().verify(value, owner_oid=OWNER_OID) is None


def test_signer_rejects_weak_configuration() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        CookieSigner(b"short", ttl_seconds=TTL)
    with pytest.raises(ValueError, match="positive"):
        CookieSigner(COOKIE_KEY.encode(), ttl_seconds=0)
    with pytest.raises(ValueError, match="GUID"):
        signer().issue("owner@example.com")


def test_read_cookie_values_reads_every_cookie_header() -> None:
    headers = Headers(
        raw=[
            (b"cookie", b"a=1; __Host-bw=first"),
            (b"cookie", b"__Host-bw=second; b=2"),
            (b"cookie", b"__Host-bwx=nope"),
        ]
    )
    assert read_cookie_values(headers) == ["first", "second"]
    assert read_cookie_values(Headers()) == []


def test_case_variants_of_the_cookie_name_are_ignored() -> None:
    # Older browsers enforce the __Host- prefix rules case-sensitively, so a sibling subdomain
    # can set "__HOST-bw" with a Domain attribute. Only the exact name is ever read.
    value = signer().issue(OWNER_OID).value
    for name in ("__host-bw", "__HOST-bw", "__Host-BW", "__Secure-bw", "bw"):
        headers = Headers(raw=[(b"cookie", f"{name}={value}".encode())])
        assert read_cookie_values(headers) == [], name


def test_clearing_header_meets_the_host_prefix_rules() -> None:
    # Browsers ignore a __Host- Set-Cookie without Secure and Path=/ (or with Domain), so a
    # clearing header missing them would leave a bad cookie in place.
    name, value, attributes = parse_set_cookie(CLEAR_COOKIE_HEADER)
    assert (name, value) == (COOKIE_NAME, "")
    assert attributes == {
        "max-age": "0",
        "path": "/",
        "secure": True,
        "httponly": True,
        "samesite": "Strict",
    }


# ----------------------------------------------------------------------------------------------
# Same-origin check
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"sec-fetch-site": "same-origin"}, True),
        ({"sec-fetch-site": "Same-Origin"}, True),
        ({"sec-fetch-site": "same-site"}, False),
        ({"sec-fetch-site": "cross-site"}, False),
        ({"sec-fetch-site": "none"}, False),
        # Sec-Fetch-Site decides when present, even if Origin matches.
        ({"sec-fetch-site": "cross-site", "origin": ORIGIN}, False),
        ({"origin": ORIGIN}, True),
        ({"origin": "HTTPS://WRITER.TEST:443"}, True),
        ({"origin": "https://evil.example"}, False),
        ({"origin": "null"}, False),
        ({}, False),
    ],
)
def test_is_same_origin(headers: dict[str, str], expected: bool) -> None:
    assert is_same_origin(Headers(headers=headers), ORIGIN) is expected


def test_origin_fallback_needs_a_public_origin() -> None:
    assert is_same_origin(Headers(headers={"origin": ORIGIN}), None) is False


# ----------------------------------------------------------------------------------------------
# HTTP rules
# ----------------------------------------------------------------------------------------------


async def test_health_is_exempt_and_minimal() -> None:
    app = create_app(make_settings(build_sha="abc1234"))
    async with asgi_client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "sha": "abc1234"}
    assert "set-cookie" not in response.headers


async def test_health_ignores_malformed_principal_headers() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/health", headers={"X-MS-CLIENT-PRINCIPAL": "!!"})
    assert response.status_code == 200


async def test_missing_principal_is_401() -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe")
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "unauthenticated"
    assert "set-cookie" not in response.headers


async def test_non_owner_is_403() -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=principal_headers(OTHER_OID))
    assert response.status_code == 403
    assert response.json()["code"] == "not_owner"
    assert "set-cookie" not in response.headers


async def test_owner_headers_are_accepted_and_set_the_cookie() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=principal_headers(OWNER_OID))
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "source": "easy_auth"}
    name, value, attributes = parse_set_cookie(response.headers["set-cookie"])
    assert name == "__Host-bw"
    assert attributes == {
        "max-age": "43200",
        "path": "/",
        "secure": True,
        "httponly": True,
        "samesite": "Strict",
    }
    assert "domain" not in attributes
    assert signer().verify(value, owner_oid=OWNER_OID) is not None


async def test_cookie_alone_is_accepted() -> None:
    # Easy Auth bug #1815: the principal headers go missing; the app cookie carries the session.
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"Cookie": owner_cookie(settings)})
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "source": "cookie"}
    assert "set-cookie" not in response.headers


async def test_tampered_cookie_is_401_and_cleared() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    cookie = owner_cookie(settings)
    tampered = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"Cookie": tampered})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert response.headers["set-cookie"] == CLEAR_COOKIE_HEADER


async def test_expired_cookie_is_401() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    stale = owner_cookie(settings, clock=lambda: time.time() - TTL - 60)
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"Cookie": stale})
    assert response.status_code == 401


async def test_cookie_from_another_key_is_401() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    other_settings = make_settings(cookie_key="a-completely-different-cookie-key-000")
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"Cookie": owner_cookie(other_settings)})
    assert response.status_code == 401


async def test_duplicate_cookies_are_not_trusted() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    cookie = owner_cookie(settings)
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers={"Cookie": f"{cookie}; {cookie}"})
    assert response.status_code == 401


async def test_non_owner_headers_beat_a_valid_owner_cookie() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    headers = {**principal_headers(OTHER_OID), "Cookie": owner_cookie(settings)}
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=headers)
    assert response.status_code == 403
    assert response.headers["set-cookie"] == CLEAR_COOKIE_HEADER


async def test_malformed_principal_is_401_even_with_a_valid_cookie() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    headers = {"X-MS-CLIENT-PRINCIPAL": "%%%", "Cookie": owner_cookie(settings)}
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_principal"


async def test_fresh_cookie_is_not_reissued_and_stale_cookie_is_refreshed() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    fresh = owner_cookie(settings, clock=lambda: time.time() - 3600)
    stale = owner_cookie(settings, clock=lambda: time.time() - 7 * 3600)
    async with asgi_client(app) as client:
        kept = await client.get(
            "/api/probe", headers={**principal_headers(OWNER_OID), "Cookie": fresh}
        )
        refreshed = await client.get(
            "/api/probe", headers={**principal_headers(OWNER_OID), "Cookie": stale}
        )
    assert kept.status_code == refreshed.status_code == 200
    assert "set-cookie" not in kept.headers
    _, value, _ = parse_set_cookie(refreshed.headers["set-cookie"])
    assert signer().verify(value, owner_oid=OWNER_OID) is not None


# ----------------------------------------------------------------------------------------------
# CSRF
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_unsafe_method_without_cookie_is_403(method: str) -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    headers = {**principal_headers(OWNER_OID), "Sec-Fetch-Site": "same-origin"}
    async with asgi_client(app) as client:
        response = await client.request(method, "/api/probe", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
    # A fresh cookie comes back, so the SPA can retry once.
    _, value, _ = parse_set_cookie(response.headers["set-cookie"])
    assert signer().verify(value, owner_oid=OWNER_OID) is not None


async def test_unsafe_method_without_any_credentials_is_401() -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.post("/api/probe", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "origin_headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site", "Origin": "https://jatinmadan.example"},
        {"Origin": "https://evil.example"},
        {},
    ],
)
async def test_cross_site_post_with_cookie_is_403(origin_headers: dict[str, str]) -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    headers = {**principal_headers(OWNER_OID), "Cookie": owner_cookie(settings), **origin_headers}
    async with asgi_client(app) as client:
        response = await client.post("/api/probe", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


@pytest.mark.parametrize("origin_headers", [{"Sec-Fetch-Site": "same-origin"}, {"Origin": ORIGIN}])
async def test_same_origin_post_with_cookie_is_accepted(origin_headers: dict[str, str]) -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    headers = {**principal_headers(OWNER_OID), "Cookie": owner_cookie(settings), **origin_headers}
    async with asgi_client(app) as client:
        response = await client.post("/api/probe", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "source": "easy_auth"}


async def test_same_origin_post_with_cookie_only_is_accepted() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.post("/api/probe", headers=write_headers(settings))
    assert response.status_code == 200
    assert response.json()["source"] == "cookie"


async def test_head_is_a_safe_method() -> None:
    app = create_app(make_settings(), routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.head("/api/probe", headers=principal_headers(OWNER_OID))
    # Not a CSRF failure: the route simply does not allow the method.
    assert response.status_code == 405


async def test_options_needs_the_cookie_and_same_origin_like_any_non_get() -> None:
    # The plan: every non-GET request needs __Host-bw plus Sec-Fetch-Site: same-origin. There is
    # no CORS, so OPTIONS has no legitimate cross-origin use either.
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        headers_only = await client.options("/api/probe", headers=principal_headers(OWNER_OID))
        cross_site = await client.options(
            "/api/probe", headers={"Cookie": owner_cookie(settings), "Sec-Fetch-Site": "cross-site"}
        )
        same_origin = await client.options("/api/probe", headers=write_headers(settings))
    assert (headers_only.status_code, headers_only.json()["code"]) == (403, "csrf_failed")
    assert (cross_site.status_code, cross_site.json()["code"]) == (403, "csrf_failed")
    assert same_origin.status_code == 405  # past auth; the route does not allow OPTIONS


async def raw_request(app: ASGIApp, method: str, headers: dict[str, str]) -> int:
    """Send one request with ``method`` exactly as given (httpx would upper-case it)."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": "/api/probe",
        "raw_path": b"/api/probe",
        "root_path": "",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "server": ("writer.test", 443),
        "client": ("127.0.0.1", 50000),
    }
    await app(scope, receive, send)
    (start,) = [message for message in sent if message["type"] == "http.response.start"]
    return int(start["status"])


@pytest.mark.parametrize("method", ["get", "Get", "head", "options", "post"])
async def test_methods_are_matched_case_sensitively(method: str) -> None:
    # Routing treats "get" as a different method from GET, so auth must not call it safe: a
    # mounted app that branched on `method == "GET"` would otherwise take its write path.
    settings = make_settings()
    app = create_app(settings, routers=[probe_router()])
    assert await raw_request(app, method, principal_headers(OWNER_OID)) == 403
    assert (
        await raw_request(app, method, {**principal_headers(OWNER_OID), **write_headers(settings)})
        == 405
    )


def test_authenticator_treats_only_get_and_head_as_safe() -> None:
    settings = make_settings()
    authenticator = Authenticator(settings, signer())
    owner = Headers(headers=principal_headers(OWNER_OID))
    for method in ("GET", "HEAD"):
        assert authenticator.decide(method, "/api/probe", owner).allowed, method
    for method in ("OPTIONS", "get", "head", "TRACE", "PROPFIND", "QUERY", ""):
        decision = authenticator.decide(method, "/api/probe", owner)
        assert (decision.allowed, decision.code) == (False, "csrf_failed"), method


# ----------------------------------------------------------------------------------------------
# Development principal and fail-closed configuration
# ----------------------------------------------------------------------------------------------


async def test_dev_principal_works_in_local() -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID)
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/me")
        probe = await client.get("/api/probe")
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "name": "Local developer"}
    assert "set-cookie" in response.headers
    assert probe.json() == {"oid": OWNER_OID, "source": "dev"}


async def test_dev_principal_is_still_subject_to_the_owner_check() -> None:
    settings = make_settings(env="local", dev_principal_oid=OTHER_OID)
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe")
    assert response.status_code == 403


async def test_dev_principal_does_not_override_real_headers() -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID)
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=principal_headers(OTHER_OID))
    assert response.status_code == 403


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:5173",  # the Vite proxy keeps the browser's Host
        "http://LOCALHOST:8000",
        "http://127.0.0.1:8000",
        "http://127.0.0.2",
        "http://[::1]:8000",
        "http://app.localhost:5173",
    ],
)
async def test_dev_principal_serves_loopback_hosts(base_url: str) -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID, public_origin=None)
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app, base_url=base_url) as client:
        response = await client.get("/api/probe")
    assert response.status_code == 200
    assert response.json() == {"oid": OWNER_OID, "source": "dev"}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://rebind.attacker.example:8000",  # DNS rebinding: the page's own host name
        "http://localhost.attacker.example:8000",
        "http://192.168.1.20:5173",  # another machine on the network
        "http://writer.test.attacker.example",
    ],
)
async def test_dev_principal_is_refused_to_other_hosts(base_url: str) -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID)  # public origin writer.test
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app, base_url=base_url) as client:
        response = await client.get("/api/probe")
        me = await client.get("/api/me")
    assert response.status_code == me.status_code == 401
    assert "set-cookie" not in response.headers  # no owner cookie for the foreign host


async def test_dev_principal_serves_the_public_origin_host() -> None:
    # For a phone on the LAN: BW_PUBLIC_ORIGIN names the address the browser uses.
    settings = make_settings(
        env="local", dev_principal_oid=OWNER_OID, public_origin="http://192.168.1.20:5173"
    )
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app, base_url="http://192.168.1.20:5173") as client:
        response = await client.get("/api/probe")
    assert response.json() == {"oid": OWNER_OID, "source": "dev"}


@pytest.mark.parametrize(
    "raw_hosts",
    [
        [],  # no Host at all
        [b"localhost", b"localhost"],
        [b"localhost:notaport"],
        [b"localhost:99999"],
        [b"attacker.example@localhost"],
        [b"localhost/path"],
        [b"localhost#x"],
        [b""],
    ],
)
def test_dev_principal_needs_one_well_formed_host(raw_hosts: list[bytes]) -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID, public_origin=None)
    decision = Authenticator(settings, signer()).decide(
        "GET", "/api/probe", Headers(raw=[(b"host", value) for value in raw_hosts])
    )
    assert (decision.allowed, decision.status) == (False, 401)


def test_dev_principal_host_check_keeps_ports_and_case_out_of_it() -> None:
    settings = make_settings(env="local", dev_principal_oid=OWNER_OID, public_origin=None)
    authenticator = Authenticator(settings, signer())
    for host in (b"localhost", b" Localhost:5173 ", b"[::1]", b"127.0.0.1:1"):
        decision = authenticator.decide("GET", "/api/probe", Headers(raw=[(b"host", host)]))
        assert decision.principal is not None, host
        assert decision.principal.source == "dev", host


@pytest.mark.parametrize("env", ["test", "prod"])
async def test_dev_principal_is_ignored_outside_local(env: str) -> None:
    settings = make_settings(env=env, dev_principal_oid=OWNER_OID)
    app = create_app(settings, routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe")
    assert response.status_code == 401


def prod_settings_without_owner() -> CoreSettings:
    # model_construct skips validation, as a misconfigured caller might.
    return CoreSettings.model_construct(
        env="prod", owner_oid=None, cookie_key=SecretStr(COOKIE_KEY), public_origin=ORIGIN
    )


def test_prod_without_owner_fails_closed_at_startup() -> None:
    with pytest.raises(RuntimeError, match="BW_OWNER_OID"):
        create_app(prod_settings_without_owner())


def test_prod_without_owner_denies_every_request() -> None:
    authenticator = Authenticator(prod_settings_without_owner(), signer())
    by_headers = authenticator.decide(
        "GET", "/api/me", Headers(headers=principal_headers(OWNER_OID))
    )
    assert (by_headers.allowed, by_headers.status, by_headers.code) == (
        False,
        403,
        "owner_not_configured",
    )
    cookie = f"{COOKIE_NAME}={signer().issue(OWNER_OID).value}"
    by_cookie = authenticator.decide("GET", "/api/me", Headers(headers={"cookie": cookie}))
    assert (by_cookie.allowed, by_cookie.status) == (False, 401)


async def test_without_an_owner_nobody_gets_in() -> None:
    app = create_app(make_settings(owner_oid=None), routers=[probe_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=principal_headers(OWNER_OID))
    assert response.status_code == 403
    assert response.json()["code"] == "owner_not_configured"


# ----------------------------------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------------------------------


async def test_every_route_except_health_requires_authentication(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    app = create_app(make_settings(spa_dir=tmp_path), routers=[probe_router()])
    schema_paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    requests: set[tuple[str, str]] = set()
    for template, operations in schema_paths.items():
        path = re.sub(r"\{[^}]+\}", "x", template)
        requests.update((method.upper(), path) for method in operations)
    requests.update(
        {
            ("GET", "/"),
            ("GET", "/studio/123"),
            ("GET", "/index.html"),
            ("GET", "/api/openapi.json"),
            ("GET", "/api/unknown"),
            ("POST", "/api/unknown"),
            ("GET", "/api/health/"),
            ("GET", "/api//health"),
        }
    )
    requests.discard(("GET", "/api/health"))
    assert ("GET", "/api/me") in requests
    assert ("DELETE", "/api/probe") in requests
    async with asgi_client(app) as client:
        for method, path in sorted(requests):
            response = await client.request(method, path)
            assert response.status_code == 401, (method, path)


async def test_websocket_connections_are_refused() -> None:
    app = create_app(make_settings())
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    headers = [
        (name.lower().encode(), value.encode())
        for name, value in principal_headers(OWNER_OID).items()
    ]
    scope: Scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "path": "/api/me",
        "raw_path": b"/api/me",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "server": ("writer.test", 443),
        "client": ("127.0.0.1", 50000),
        "subprotocols": [],
    }
    await app(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
