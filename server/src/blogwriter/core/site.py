"""The public site gateway: the portfolio's host names are served by the portfolio container.

With ``BW_SITE_HOSTS`` (``example.com,www.example.com``; the first one is canonical) and
``BW_SITE_UPSTREAM`` (``http://127.0.0.1:3000``, loopback only) a request whose ``Host`` is a
site host never reaches Blog Writer's routes, authentication or SPA:

* the canonical host is proxied to the upstream, the portfolio's Next.js server in the same
  replica (it listens on loopback only, so nothing else in the environment reaches it);
* every other site host answers ``301`` to the same path on the canonical host;
* ``/api/health`` stays the app's own health check on every host;
* ``/studio`` redirects (302) to Blog Writer's own origin (``BW_PUBLIC_ORIGIN``), the owner's
  way in from the public site. Blog Writer is never served under a site host.

The proxy is a read-only, cookie-free pass-through:

* ``GET`` and ``HEAD`` only (``405`` otherwise); a request body is never read or forwarded.
* Request headers come from an allowlist: content negotiation, cache validators, client hints,
  fetch metadata and the Next.js navigation headers. Cookies, ``Authorization``, Easy Auth's
  ``X-MS-*`` principal headers and every other ``X-*`` header (``x-middleware-subrequest``
  included) are dropped, so the site never sees the owner's session. ``X-Forwarded-Host`` and
  ``X-Forwarded-Proto`` are set by the gateway.
* ``/_bw`` and everything below it (the portfolio's internal health and revalidate routes) is
  ``404``: only the app itself calls those, directly on the upstream.
* The response streams back as is (still compressed) except that hop-by-hop headers,
  ``Set-Cookie``, ``Server`` and ``X-Powered-By`` are removed and missing security headers are
  added (:data:`SITE_SECURITY_HEADERS`, HSTS in prod). The site sets its own CSP.
* An unreachable upstream answers ``502`` and a slow one ``504``, both with a small page and
  ``Retry-After``; the failure is logged without the path's query string.

Every other host is Blog Writer as before. An unknown host is never treated as the site: it
gets the owner-only app, which fails closed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import httpx
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from blogwriter.core.logging import register_log_fields

__all__ = [
    "INTERNAL_PREFIX",
    "SITE_SECURITY_HEADERS",
    "SiteGateway",
    "SiteGatewayMiddleware",
    "normalize_site_hosts",
    "normalize_site_upstream",
    "request_host",
]

#: The portfolio's internal routes (health, revalidate): never reachable through the gateway.
INTERNAL_PREFIX: Final = "/_bw"
#: Served by the app on every host (probes may use any host name).
HEALTH_PATH: Final = "/api/health"
#: On a site host: a redirect to Blog Writer's own origin.
STUDIO_PATHS: Final = frozenset({"/studio", "/studio/"})
CONNECT_TIMEOUT_S: Final = 2.0
READ_TIMEOUT_S: Final = 30.0
MAX_CONNECTIONS: Final = 64
MAX_HOST_CHARS: Final = 253

_HOST_RE: Final = re.compile(r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS: Final = frozenset({"GET", "HEAD"})

#: Request headers passed to the site (lowercase). Prefixes in :data:`_FORWARD_PREFIXES`.
_FORWARD_HEADERS: Final = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "pragma",
        "if-none-match",
        "if-modified-since",
        "if-range",
        "range",
        "user-agent",
        "referer",
        "dnt",
        "upgrade-insecure-requests",
        "purpose",
        "sec-purpose",
        # Next.js App Router client navigation (RSC payload requests).
        "rsc",
        "next-router-state-tree",
        "next-router-prefetch",
        "next-router-segment-prefetch",
        "next-url",
    }
)
_FORWARD_PREFIXES: Final = ("sec-ch-", "sec-fetch-")

#: Response headers never passed back (hop-by-hop, cookies, implementation details).
_DROP_RESPONSE_HEADERS: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "set-cookie",
        "server",
        "x-powered-by",
    }
)

SITE_SECURITY_HEADERS: Final = (
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    (
        "permissions-policy",
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
        "microphone=(), payment=(), usb=(), interest-cohort=()",
    ),
    ("x-frame-options", "SAMEORIGIN"),
    ("cross-origin-opener-policy", "same-origin"),
)
_HSTS: Final = ("strict-transport-security", "max-age=63072000; includeSubDomains")

_ERROR_PAGE: Final = (
    "<!doctype html><html lang=en><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<meta name=robots content=noindex><title>{title}</title>"
    "<body style='font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:20vh auto;"
    "padding:0 1.5rem;color:#1f2937'><h1 style='font-size:1.5rem'>{title}</h1>"
    "<p>{detail}</p></body></html>"
)

_logger: Final = logging.getLogger("blogwriter.core.site")
register_log_fields("site_error", "site_status")


def normalize_site_hosts(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """``BW_SITE_HOSTS`` as lowercase host names (comma separated or a list); ``ValueError``
    for anything that is not a plain DNS name (no ports, IPs, wildcards or duplicates)."""
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    hosts: list[str] = []
    for item in items:
        host = item.strip().lower().rstrip(".")
        if not host:
            continue
        if len(host) > MAX_HOST_CHARS or not _HOST_RE.fullmatch(host):
            raise ValueError("site hosts are plain DNS names such as example.com")
        if host in hosts:
            raise ValueError("site hosts must be distinct")
        hosts.append(host)
    return tuple(hosts)


def normalize_site_upstream(value: str | None) -> str | None:
    """``BW_SITE_UPSTREAM``: ``http://<loopback>:<port>`` only (``ValueError`` otherwise)."""
    if value is None or not value.strip():
        return None
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if (
        parts.scheme != "http"
        or host not in _LOOPBACK_HOSTS
        or port is None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("the site upstream is http://127.0.0.1:<port> (loopback only)")
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return f"http://{netloc}"


def request_host(headers: Headers) -> str | None:
    """The request's ``Host`` as a lowercase DNS name without port or trailing dot."""
    raw = headers.get("host")
    if raw is None or len(raw) > MAX_HOST_CHARS + 6:
        return None
    host = raw.strip().lower()
    if host.startswith("["):  # IPv6 literal: never a site host
        return None
    host = host.partition(":")[0].rstrip(".")
    return host or None


def _is_internal(path: str) -> bool:
    lowered = path.lower()
    return lowered == INTERNAL_PREFIX or lowered.startswith(f"{INTERNAL_PREFIX}/")


@dataclass(frozen=True, slots=True)
class _Target:
    canonical: str
    hosts: frozenset[str]


class SiteGateway:
    """The site hosts, the upstream and the HTTP client used to reach it.

    Build it once per app (:func:`blogwriter.core.app.create_app`); :meth:`aclose` closes the
    client at shutdown.
    """

    def __init__(
        self,
        hosts: Iterable[str],
        upstream: str,
        *,
        studio_origin: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = normalize_site_hosts(list(hosts))
        if not normalized:
            raise ValueError("the site gateway needs at least one host")
        base = normalize_site_upstream(upstream)
        if base is None:
            raise ValueError("the site gateway needs an upstream")
        self.target = _Target(canonical=normalized[0], hosts=frozenset(normalized))
        self.upstream = base
        self.studio_origin = studio_origin
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def hosts(self) -> frozenset[str]:
        return self.target.hosts

    @property
    def canonical_host(self) -> str:
        return self.target.canonical

    def client(self) -> httpx.AsyncClient:
        """The upstream client (created on first use, inside the event loop)."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.upstream,
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=16),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


class SiteGatewayMiddleware:
    """Routes the site hosts to the portfolio (see the module docstring); the rest goes on.

    Place it inside the access log and outside the security headers, error boundary and auth:
    site traffic is logged like any request but gets the site's own headers, and Blog Writer's
    authentication never sees it.
    """

    def __init__(self, app: ASGIApp, *, gateway: SiteGateway, hsts: bool) -> None:
        self.app = app
        self.gateway = gateway
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = request_host(Headers(scope=scope))
        path = str(scope.get("path", ""))
        if host is None or host not in self.gateway.hosts or path == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        if host != self.gateway.canonical_host:
            await self._redirect(scope, send)
            return
        studio = self.gateway.studio_origin
        if path in STUDIO_PATHS and studio is not None:
            await self._studio(send, studio)
            return
        method = str(scope["method"])
        if method not in _SAFE_METHODS:
            await self._simple(send, 405, "Method not allowed", "", allow="GET, HEAD")
            return
        if _is_internal(path):
            await self._simple(send, 404, "Not found", "This page does not exist.")
            return
        await self._proxy(scope, send, method)

    # ------------------------------------------------------------------------------------

    def _security_headers(self, present: set[str]) -> list[tuple[bytes, bytes]]:
        extra = list(SITE_SECURITY_HEADERS)
        if self.hsts:
            extra.append(_HSTS)
        return [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in extra
            if name not in present
        ]

    async def _redirect(self, scope: Scope, send: Send) -> None:
        raw_path = scope.get("raw_path") or str(scope.get("path", "/")).encode("latin-1")
        if not isinstance(raw_path, bytes) or not raw_path.startswith(b"/"):
            raw_path = b"/"
        query = scope.get("query_string") or b""
        location = b"https://" + self.gateway.canonical_host.encode("ascii") + raw_path
        if query:
            location += b"?" + query
        headers = [
            (b"location", location),
            (b"cache-control", b"public, max-age=3600"),
            (b"content-length", b"0"),
            *self._security_headers(set()),
        ]
        await send({"type": "http.response.start", "status": 301, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    async def _studio(self, send: Send, origin: str) -> None:
        headers = [
            (b"location", f"{origin}/".encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"content-length", b"0"),
            (b"x-robots-tag", b"noindex"),
            *self._security_headers(set()),
        ]
        await send({"type": "http.response.start", "status": 302, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    async def _simple(
        self,
        send: Send,
        status: int,
        title: str,
        detail: str,
        *,
        allow: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        body = _ERROR_PAGE.format(title=title, detail=detail).encode()
        headers = [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
            (
                b"content-security-policy",
                b"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            ),
        ]
        if allow is not None:
            headers.append((b"allow", allow.encode()))
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode()))
        headers.extend(self._security_headers({"x-frame-options"}))
        headers.append((b"x-frame-options", b"DENY"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _forward_headers(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        forwarded: list[tuple[bytes, bytes]] = []
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").lower()
            if name in _FORWARD_HEADERS or name.startswith(_FORWARD_PREFIXES):
                forwarded.append((name.encode("latin-1"), raw_value))
        forwarded.append((b"x-forwarded-host", self.gateway.canonical_host.encode("ascii")))
        forwarded.append((b"x-forwarded-proto", b"https"))
        return forwarded

    async def _proxy(self, scope: Scope, send: Send, method: str) -> None:
        raw_path = scope.get("raw_path")
        if not isinstance(raw_path, bytes) or not raw_path.startswith(b"/"):
            raw_path = str(scope.get("path", "/")).encode("utf-8")
        if raw_path.startswith(b"//") or any(c in raw_path for c in (b"\r", b"\n", b"\x00")):
            await self._simple(send, 400, "Bad request", "This address is not valid.")
            return
        query = scope.get("query_string") or b""
        target = raw_path + (b"?" + query if query else b"")
        client = self.gateway.client()
        started = time.monotonic()
        try:
            request = client.build_request(
                method,
                httpx.URL(self.gateway.upstream).copy_with(raw_path=target),
                headers=self._forward_headers(scope),
            )
            response = await client.send(request, stream=True)
        except httpx.TimeoutException:
            self._log_failure("timeout", started)
            await self._simple(
                send,
                504,
                "Taking too long",
                "The site is slow to respond. Try again shortly.",
                retry_after=5,
            )
            return
        except httpx.HTTPError:
            self._log_failure("unreachable", started)
            await self._simple(
                send,
                502,
                "Back in a moment",
                "The site is restarting. Try again shortly.",
                retry_after=5,
            )
            return
        try:
            headers: list[tuple[bytes, bytes]] = []
            present: set[str] = set()
            for raw_name, raw_value in response.headers.raw:
                name = raw_name.decode("latin-1").lower()
                if name in _DROP_RESPONSE_HEADERS:
                    continue
                present.add(name)
                headers.append((name.encode("latin-1"), raw_value))
            headers.extend(self._security_headers(present))
            await send(
                {"type": "http.response.start", "status": response.status_code, "headers": headers}
            )
            if method == "HEAD":
                await send({"type": "http.response.body", "body": b""})
                return
            async for chunk in response.aiter_raw():
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        except httpx.HTTPError:
            # Headers are out: the client sees a truncated body; nothing else can be done.
            self._log_failure("interrupted", started)
        finally:
            await response.aclose()

    def _log_failure(self, kind: str, started: float) -> None:
        _logger.warning(
            "site_upstream_failed",
            extra={
                "site_error": kind,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
