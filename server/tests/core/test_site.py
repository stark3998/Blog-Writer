"""Tests for blogwriter.core.site: the public site gateway in front of Blog Writer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from blogwriter.core.app import create_app
from blogwriter.core.site import (
    SITE_SECURITY_HEADERS,
    SiteGateway,
    normalize_site_hosts,
    normalize_site_upstream,
)
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    owner_cookie,
    principal_headers,
)

SITE = "https://example.com"
WWW = "https://www.example.com"
UPSTREAM = "http://127.0.0.1:3000"


class Upstream:
    """A fake portfolio server: records requests, answers with a fixed streamed page."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail: type[httpx.TransportError] | None = None
        self.headers: dict[str, str] = {
            "content-type": "text/html; charset=utf-8",
            "set-cookie": "session=leak",
            "x-powered-by": "Next.js",
            "date": "Sat, 28 Nov 2026 09:00:00 GMT",
            "cache-control": "s-maxage=60",
        }

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail("upstream trouble")

        async def chunks() -> AsyncIterator[bytes]:
            yield b"<!doctype html><title>Site</title>"
            yield b"<main>page</main>"

        return httpx.Response(200, headers=self.headers, content=chunks())


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def app(upstream: Upstream) -> FastAPI:
    settings = make_settings(site_hosts="example.com,www.example.com", site_upstream=UPSTREAM)
    return create_app(settings, site_transport=httpx.MockTransport(upstream.handler))


async def test_site_host_is_proxied_with_path_and_query(app: FastAPI, upstream: Upstream) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/blog/a%20b?page=2&x=%2F")
    assert response.status_code == 200
    assert response.text == "<!doctype html><title>Site</title><main>page</main>"
    sent = upstream.requests[0]
    assert sent.url.raw_path == b"/blog/a%20b?page=2&x=%2F"
    assert sent.url.host == "127.0.0.1"
    assert sent.url.port == 3000


async def test_only_allowlisted_request_headers_reach_the_site(
    app: FastAPI, upstream: Upstream
) -> None:
    headers = {
        **principal_headers(OWNER_OID),
        "Cookie": owner_cookie(make_settings()),
        "Authorization": "Bearer secret",
        "X-Middleware-Subrequest": "middleware",
        "X-Bw-Internal": "1",
        "X-Forwarded-Host": "evil.test",
        "Accept": "text/html",
        "RSC": "1",
        "Next-Router-State-Tree": "%5B%22%22%5D",
        "Sec-Fetch-Mode": "navigate",
        "If-None-Match": '"abc"',
    }
    async with asgi_client(app, base_url=SITE) as client:
        await client.get("/", headers=headers)
    sent = upstream.requests[0].headers
    for dropped in (
        "cookie",
        "authorization",
        "x-ms-client-principal",
        "x-ms-client-principal-id",
        "x-middleware-subrequest",
        "x-bw-internal",
    ):
        assert dropped not in sent
    assert sent["x-forwarded-host"] == "example.com"
    assert sent["x-forwarded-proto"] == "https"
    assert sent["accept"] == "text/html"
    assert sent["rsc"] == "1"
    assert sent["next-router-state-tree"] == "%5B%22%22%5D"
    assert sent["sec-fetch-mode"] == "navigate"
    assert sent["if-none-match"] == '"abc"'


async def test_response_drops_cookies_and_adds_security_headers(app: FastAPI) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/")
    assert "set-cookie" not in response.headers
    assert "x-powered-by" not in response.headers
    assert response.headers["cache-control"] == "s-maxage=60"
    for name, value in SITE_SECURITY_HEADERS:
        assert response.headers[name] == value
    # Blog Writer's own SPA policy never applies to the site.
    assert "content-security-policy" not in response.headers


async def test_head_is_forwarded_without_a_body(app: FastAPI, upstream: Upstream) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.head("/blog")
    assert response.status_code == 200
    assert response.content == b""
    assert upstream.requests[0].method == "HEAD"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def test_writes_are_refused_on_site_hosts(
    app: FastAPI, upstream: Upstream, method: str
) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.request(method, "/api/posts", content=b"{}")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert upstream.requests == []


@pytest.mark.parametrize("path", ["/_bw", "/_bw/revalidate", "/_BW/health", "/%5Fbw/revalidate"])
async def test_internal_routes_are_never_forwarded(
    app: FastAPI, upstream: Upstream, path: str
) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get(path)
    assert response.status_code == 404
    assert upstream.requests == []


async def test_writer_routes_are_not_served_on_site_hosts(app: FastAPI, upstream: Upstream) -> None:
    """``/api/me`` on the site host is the site's path, never Blog Writer's API."""
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/api/me", headers=principal_headers(OWNER_OID))
    assert response.status_code == 200
    assert upstream.requests[0].url.raw_path == b"/api/me"
    assert "x-ms-client-principal" not in upstream.requests[0].headers


async def test_health_is_the_app_on_every_host(app: FastAPI, upstream: Upstream) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["sha"] == "test-sha"
    assert upstream.requests == []


async def test_other_site_hosts_redirect_to_the_canonical_host(
    app: FastAPI, upstream: Upstream
) -> None:
    async with asgi_client(app, base_url=WWW) as client:
        response = await client.get("/blog/x?y=1")
    assert response.status_code == 301
    assert response.headers["location"] == "https://example.com/blog/x?y=1"
    assert upstream.requests == []


async def test_studio_redirects_to_blog_writer(app: FastAPI, upstream: Upstream) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/studio")
    assert response.status_code == 302
    assert response.headers["location"] == "https://writer.test/"
    assert upstream.requests == []


async def test_host_header_port_and_case_are_normalised(app: FastAPI, upstream: Upstream) -> None:
    async with asgi_client(app, base_url="https://EXAMPLE.com:443") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert len(upstream.requests) == 1


async def test_writer_and_unknown_hosts_keep_owner_only_access(
    app: FastAPI, upstream: Upstream
) -> None:
    for base in ("https://writer.test", "https://unknown.test", "https://example.com.evil.test"):
        async with asgi_client(app, base_url=base) as client:
            response = await client.get("/api/me")
        assert response.status_code == 401, base
    assert upstream.requests == []


async def test_writer_still_works_for_the_owner(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        response = await client.get("/api/me", headers=principal_headers(OWNER_OID))
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("failure", "status"), [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)]
)
async def test_upstream_failures_answer_a_small_page(
    app: FastAPI, upstream: Upstream, failure: type[httpx.TransportError], status: int
) -> None:
    upstream.fail = failure
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/")
    assert response.status_code == status
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"
    assert "noindex" in response.text


async def test_no_gateway_without_site_hosts(upstream: Upstream) -> None:
    app = create_app(make_settings(), site_transport=httpx.MockTransport(upstream.handler))
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/")
    assert response.status_code == 401
    assert upstream.requests == []


async def test_gateway_client_is_closed_at_shutdown(upstream: Upstream) -> None:
    gateway = SiteGateway(
        ["example.com"], UPSTREAM, transport=httpx.MockTransport(upstream.handler)
    )
    client = gateway.client()
    await gateway.aclose()
    assert client.is_closed


# ---------------------------------------------------------------------------- settings


def test_site_hosts_are_normalised() -> None:
    assert normalize_site_hosts(" Example.COM., www.example.com ,") == (
        "example.com",
        "www.example.com",
    )


@pytest.mark.parametrize(
    "value", ["example.com:443", "127.0.0.1", "*.example.com", "a.com,a.com", "exa mple.com"]
)
def test_bad_site_hosts_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="site hosts"):
        normalize_site_hosts(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:3000", "http://127.0.0.1:3000"),
        ("http://localhost:3000/", "http://localhost:3000"),
        ("http://[::1]:3000", "http://[::1]:3000"),
    ],
)
def test_upstream_is_loopback_only(value: str, expected: str) -> None:
    assert normalize_site_upstream(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:3000",
        "http://10.0.0.4:3000",
        "http://example.com:3000",
        "http://127.0.0.1",
        "http://127.0.0.1:3000/app",
        "http://user:pw@127.0.0.1:3000",
    ],
)
def test_non_loopback_upstreams_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        normalize_site_upstream(value)


def test_settings_need_hosts_and_upstream_together() -> None:
    with pytest.raises(ValidationError, match="BW_SITE_HOSTS and BW_SITE_UPSTREAM"):
        make_settings(site_hosts="example.com")
    with pytest.raises(ValidationError, match="BW_SITE_HOSTS and BW_SITE_UPSTREAM"):
        make_settings(site_upstream=UPSTREAM)


def test_blog_writer_origin_cannot_be_a_site_host() -> None:
    with pytest.raises(ValidationError, match="public origin"):
        make_settings(site_hosts="writer.test", site_upstream=UPSTREAM)


def test_settings_expose_the_host_list() -> None:
    settings = make_settings(site_hosts="Example.com,www.example.com", site_upstream=UPSTREAM)
    assert settings.site_host_list == ("example.com", "www.example.com")
    assert json.loads(json.dumps(settings.site_host_list)) == ["example.com", "www.example.com"]


# ---------------------------------------------------------------------------- sign-in redirect


@pytest.fixture
def prod_app(upstream: Upstream, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    settings = make_settings(
        env="prod",
        site_hosts="example.com,www.example.com",
        site_upstream=UPSTREAM,
    )
    return create_app(settings, site_transport=httpx.MockTransport(upstream.handler))


NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/today", "/.auth/login/aad?post_login_redirect_uri=/today"),
        (
            "/studio/art-1?tab=draft",
            "/.auth/login/aad?post_login_redirect_uri=/studio/art-1%3Ftab%3Ddraft",
        ),
        ("/", "/.auth/login/aad?post_login_redirect_uri=/"),
    ],
)
async def test_unauthenticated_navigation_goes_to_sign_in(
    prod_app: FastAPI, path: str, expected: str
) -> None:
    async with asgi_client(prod_app) as client:
        response = await client.get(path, headers=NAVIGATE)
    assert response.status_code == 302
    assert response.headers["location"] == expected
    assert response.headers["cache-control"] == "no-store"


async def test_navigation_without_fetch_metadata_uses_accept(prod_app: FastAPI) -> None:
    async with asgi_client(prod_app) as client:
        response = await client.get("/calendar", headers={"Accept": "text/html,*/*"})
    assert response.status_code == 302


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/api/me", NAVIGATE),
        ("GET", "/today", {"Sec-Fetch-Mode": "cors", "Accept": "text/html"}),
        ("GET", "/today", {"Accept": "application/json"}),
        ("POST", "/today", NAVIGATE),
    ],
)
async def test_other_unauthenticated_requests_stay_401(
    prod_app: FastAPI, method: str, path: str, headers: dict[str, str]
) -> None:
    async with asgi_client(prod_app) as client:
        response = await client.request(method, path, headers=headers)
    assert response.status_code in (401, 403)
    assert "location" not in response.headers


async def test_non_owner_is_not_redirected(prod_app: FastAPI) -> None:
    from blogwriter.core.testing import OTHER_OID

    async with asgi_client(prod_app) as client:
        response = await client.get("/today", headers={**NAVIGATE, **principal_headers(OTHER_OID)})
    assert response.status_code == 403


async def test_no_redirect_outside_prod(app: FastAPI) -> None:
    async with asgi_client(app) as client:
        response = await client.get("/today", headers=NAVIGATE)
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "query", "target"),
    [
        ("//evil.test/x", b"", "/"),
        ("/a\\b", b"", "/"),
        ("/x", b"a=1&b=%2F", "/x%3Fa%3D1%26b%3D%252F"),
        ("//evil.test", b"next=1", "/"),
    ],
)
def test_login_location_only_returns_to_site_paths(path: str, query: bytes, target: str) -> None:
    from blogwriter.core.auth import login_location

    assert login_location(path, query) == f"/.auth/login/aad?post_login_redirect_uri={target}"


async def test_no_compression_unless_the_client_asks(app: FastAPI, upstream: Upstream) -> None:
    """httpx adds "gzip, deflate" by default; the site must not compress for a client that did
    not ask, because the gateway passes the body through untouched."""
    async with asgi_client(app, base_url=SITE) as client:
        del client.headers["accept-encoding"]  # no header at all
        await client.get("/")
        await client.get("/", headers={"Accept-Encoding": ""})
        await client.get("/", headers={"Accept-Encoding": "br, gzip"})
    sent = [request.headers.get_list("accept-encoding") for request in upstream.requests]
    assert sent == [["identity"], ["identity"], ["br, gzip"]]


async def test_the_site_date_header_is_not_duplicated(app: FastAPI) -> None:
    async with asgi_client(app, base_url=SITE) as client:
        response = await client.get("/")
    assert "Sat, 28 Nov 2026 09:00:00 GMT" not in response.headers.get_list("date")
