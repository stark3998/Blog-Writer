"""Tests for blogwriter.core.egress: the SSRF-safe egress client (no network anywhere).

DNS is a :class:`FakeResolver`; HTTP is ``httpx.MockTransport`` behind a recording transport
factory, or, for the pinned path, the real :func:`pinned_transport` over a fake ``httpcore``
network backend that records what it is asked to dial.
"""

from __future__ import annotations

import asyncio
import functools
import gzip
import ipaddress
import json
import logging
import socket
import ssl
import zlib
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
)
from typing import override

import httpcore
import httpx
import pytest

from blogwriter.core.egress import (
    DEFAULT_MAX_BYTES,
    DEFAULT_USER_AGENT,
    EgressBlockedError,
    EgressRedirectError,
    EgressResponseError,
    EgressTimeoutError,
    EgressTooLargeError,
    EgressTransportError,
    IPAddress,
    PinnedTarget,
    SafeHttpClient,
    SystemResolver,
    blocked_reason,
    pinned_transport,
)
from blogwriter.core.logging import JsonFormatter, allowed_log_fields

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_B = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"

DNS: dict[str, list[str]] = {
    "example.com": [PUBLIC_V4],
    "cdn.example.net": [PUBLIC_V4_B],
    "v6.example.org": [PUBLIC_V6],
    "dual.example.org": [PUBLIC_V4, PUBLIC_V4_B],
    "mapped-public.example.org": [f"::ffff:{PUBLIC_V4}", PUBLIC_V4],
    "internal.example.com": ["10.0.0.5"],
    "metadata.example.com": ["169.254.169.254"],
    "wire.example.com": ["168.63.129.16"],
    "mixed.example.com": [PUBLIC_V4, "127.0.0.1"],
    "mapped.example.com": ["::ffff:127.0.0.1"],
    "cgnat.example.com": ["100.64.0.10"],
    "ula.example.com": ["fd00::10"],
    "sixtofour.example.com": ["2002:a00:1::1"],
    "empty.example.com": [],
}


class FakeResolver:
    def __init__(self, table: Mapping[str, Sequence[str]] = DNS, *, delay: float = 0.0) -> None:
        self.table = {
            name: [ipaddress.ip_address(address) for address in addresses]
            for name, addresses in table.items()
        }
        self.delay = delay
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        self.calls.append((host, port))
        if self.delay:
            await asyncio.sleep(self.delay)
        if host not in self.table:
            raise EgressTransportError("dns_failed")
        return list(self.table[host])


type Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class Web:
    """A fake internet: ``handler`` behind ``httpx.MockTransport``, recording every hop."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.targets: list[PinnedTarget] = []
        self.requests: list[httpx.Request] = []

    def transport(self, target: PinnedTarget) -> httpx.AsyncBaseTransport:
        self.targets.append(target)

        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return await self.handler(request)

        return httpx.MockTransport(handle)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def serve(routes: Mapping[str, Callable[[], httpx.Response]]) -> Web:
    """A :class:`Web` answering exact URLs; anything else is a 404."""

    async def handler(request: httpx.Request) -> httpx.Response:
        factory = routes.get(str(request.url))
        return factory() if factory is not None else httpx.Response(404, content=b"missing")

    return Web(handler)


def ok(content: bytes = b"ok", **headers: str) -> Callable[[], httpx.Response]:
    # stream= keeps httpx from reading (and decoding) the body when the response is built.
    return lambda: httpx.Response(
        200,
        headers={"Content-Length": str(len(content)), **headers},
        stream=httpx.ByteStream(content),
    )


def redirect(location: str, status: int = 302) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(status, headers={"Location": location})


def make_client(
    web: Web,
    resolver: FakeResolver | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = 15.0,
    max_redirects: int = 5,
    allowed_ports: Collection[int] = (80, 443),
) -> SafeHttpClient:
    return SafeHttpClient(
        resolver=resolver if resolver is not None else FakeResolver(),
        transport_factory=web.transport,
        max_bytes=max_bytes,
        timeout=timeout,
        max_redirects=max_redirects,
        allowed_ports=allowed_ports,
    )


class ChunkedBody(httpx.AsyncByteStream):
    """A response body delivered in chunks, optionally slowly; records reads and closing."""

    def __init__(self, chunks: Iterable[bytes], *, delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.delivered = 0
        self.closed = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.delivered += 1
            yield chunk

    @override
    async def aclose(self) -> None:
        self.closed = True


# ----------------------------------------------------------------------------------------------
# Address classification
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        # Public unicast
        (PUBLIC_V4, None),
        ("8.8.8.8", None),
        ("1.1.1.1", None),
        (PUBLIC_V6, None),
        ("2606:4700:4700::1111", None),
        ("2a00:1450:4001:82a::200e", None),
        (f"::ffff:{PUBLIC_V4}", None),  # IPv4-mapped public address: judged as the IPv4
        # IPv4 special-purpose ranges
        ("0.0.0.0", "unspecified"),  # noqa: S104 - an address under test, not a bind
        ("0.1.2.3", "unspecified"),
        ("127.0.0.1", "loopback"),
        ("127.255.255.254", "loopback"),
        ("10.0.0.1", "private"),
        ("10.255.255.255", "private"),
        ("172.16.0.1", "private"),
        ("172.31.255.255", "private"),
        ("192.168.1.1", "private"),
        ("169.254.169.254", "link_local"),
        ("169.254.0.1", "link_local"),
        ("168.63.129.16", "azure_platform"),
        ("100.64.0.1", "cgnat"),
        ("100.127.255.254", "cgnat"),
        ("224.0.0.1", "multicast"),
        ("239.255.255.250", "multicast"),
        ("240.0.0.1", "reserved"),
        ("255.255.255.255", "reserved"),
        ("192.0.0.9", "reserved"),
        ("192.0.2.1", "reserved"),
        ("192.88.99.1", "reserved"),
        ("198.18.0.1", "reserved"),
        ("198.51.100.7", "reserved"),
        ("203.0.113.9", "reserved"),
        # IPv6 special-purpose ranges
        ("::", "unspecified"),
        ("::1", "loopback"),
        ("fc00::1", "unique_local"),
        ("fd12:3456:789a::1", "unique_local"),
        ("fe80::1", "link_local"),
        ("febf::1", "link_local"),
        ("fec0::1", "site_local"),
        ("ff02::1", "multicast"),
        ("ff0e::1", "multicast"),
        ("2001:db8::1", "reserved"),
        ("2001:2::1", "reserved"),
        ("3fff::1", "reserved"),
        ("100::1", "reserved"),
        ("4000::1", "reserved"),
        # IPv4-mapped forms of blocked IPv4 addresses
        ("::ffff:127.0.0.1", "loopback"),
        ("::ffff:10.1.2.3", "private"),
        ("::ffff:192.168.0.1", "private"),
        ("::ffff:169.254.169.254", "link_local"),
        ("::ffff:168.63.129.16", "azure_platform"),
        ("::ffff:100.64.0.1", "cgnat"),
        ("::ffff:0.0.0.0", "unspecified"),
        ("::ffff:224.0.0.1", "multicast"),
        # IPv6 forms that embed or tunnel to IPv4 are refused, whatever they embed
        ("::127.0.0.1", "ipv4_embedded"),  # IPv4-compatible
        ("::ffff:0:7f00:1", "ipv4_embedded"),  # IPv4-translated
        ("64:ff9b::7f00:1", "ipv4_embedded"),  # NAT64 of 127.0.0.1
        ("64:ff9b::808:808", "ipv4_embedded"),  # NAT64 of 8.8.8.8
        ("64:ff9b:1::1", "ipv4_embedded"),
        ("2002:7f00:1::1", "ipv4_embedded"),  # 6to4 of 127.0.0.1
        ("2002:a9fe:a9fe::1", "ipv4_embedded"),  # 6to4 of 169.254.169.254
        ("2002:a83f:8110::1", "ipv4_embedded"),  # 6to4 of 168.63.129.16
        ("2002:808:808::1", "ipv4_embedded"),  # 6to4 of 8.8.8.8
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "ipv4_embedded"),  # Teredo
    ],
)
def test_address_classification(address: str, expected: str | None) -> None:
    assert blocked_reason(ipaddress.ip_address(address)) == expected


def test_scoped_ipv6_addresses_are_refused() -> None:
    assert blocked_reason(ipaddress.IPv6Address(f"{PUBLIC_V6}%eth0")) == "scoped"


# ----------------------------------------------------------------------------------------------
# Successful fetches
# ----------------------------------------------------------------------------------------------


async def test_fetch_returns_final_url_status_content_type_and_bytes() -> None:
    web = serve(
        {
            "https://example.com/feed.xml": ok(
                b"<rss/>",
                **{
                    "Content-Type": "application/rss+xml; charset=UTF-8",
                    "ETag": '"v1"',
                    "Last-Modified": "Wed, 25 Nov 2026 10:00:00 GMT",
                },
            )
        }
    )
    response = await make_client(web).get("https://example.com/feed.xml#latest")
    assert response.url == "https://example.com/feed.xml"
    assert response.status == 200
    assert response.content_type == "application/rss+xml; charset=UTF-8"
    assert response.media_type == "application/rss+xml"
    assert response.charset == "utf-8"
    assert response.content == b"<rss/>"
    assert response.redirects == 0
    assert response.etag == '"v1"'
    assert response.last_modified == "Wed, 25 Nov 2026 10:00:00 GMT"
    assert "rss" not in repr(response).split("content_type")[0]  # body is not in the repr


async def test_request_carries_only_fixed_headers() -> None:
    web = serve({"https://example.com/": ok()})
    await make_client(web).get("https://example.com/")
    (request,) = web.requests
    assert request.method == "GET"
    assert dict(request.headers) == {
        "host": "example.com",
        "user-agent": DEFAULT_USER_AGENT,
        "accept": "*/*",
        "accept-encoding": "gzip, deflate",
    }


async def test_target_is_pinned_to_the_vetted_addresses() -> None:
    web = serve({"https://dual.example.org/": ok()})
    await make_client(web).get("https://dual.example.org/")
    (target,) = web.targets
    assert target == PinnedTarget(
        host="dual.example.org",
        port=443,
        addresses=(ipaddress.IPv4Address(PUBLIC_V4), ipaddress.IPv4Address(PUBLIC_V4_B)),
    )


async def test_ipv4_mapped_answers_are_dialled_as_ipv4_once() -> None:
    web = serve({"https://mapped-public.example.org/": ok()})
    await make_client(web).get("https://mapped-public.example.org/")
    assert web.targets[0].addresses == (ipaddress.IPv4Address(PUBLIC_V4),)


async def test_ip_literal_urls_skip_dns() -> None:
    resolver = FakeResolver()
    web = serve({f"https://{PUBLIC_V4}/": ok(), f"https://[{PUBLIC_V6}]/": ok()})
    client = make_client(web, resolver)
    await client.get(f"https://{PUBLIC_V4}/")
    await client.get(f"https://[{PUBLIC_V6}]/")
    assert resolver.calls == []
    assert [target.host for target in web.targets] == [PUBLIC_V4, PUBLIC_V6]


async def test_responses_already_read_by_the_transport_work() -> None:
    # Other packages' tests will use MockTransport with content=..., which httpx reads eagerly.
    web = serve({"https://example.com/": lambda: httpx.Response(200, content=b"eager")})
    assert (await make_client(web).get("https://example.com/")).content == b"eager"


async def test_error_statuses_are_returned_not_raised() -> None:
    web = serve({"https://example.com/gone": lambda: httpx.Response(410, content=b"gone")})
    response = await make_client(web).get("https://example.com/gone")
    assert (response.status, response.content) == (410, b"gone")


async def test_trailing_dot_hosts_resolve_the_same_name() -> None:
    resolver = FakeResolver()
    web = serve({"https://example.com./": ok()})
    await make_client(web, resolver).get("https://example.com./")
    assert resolver.calls == [("example.com", 443)]
    assert web.targets[0].host == "example.com."


# ----------------------------------------------------------------------------------------------
# URL and host policy
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/", "scheme_not_allowed"),
        ("ftp://example.com/file", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("data:text/plain,hello", "scheme_not_allowed"),
        ("//example.com/no-scheme", "scheme_not_allowed"),
        ("/relative/path", "scheme_not_allowed"),
        ("https:///no-host", "invalid_url"),
        ("https://exa\tmple.com/", "invalid_url"),
        ("https://[::1/", "invalid_url"),
        ("https://999.1.1.1/", "invalid_url"),
        ("https://example.com/" + "a" * 9000, "invalid_url"),
        ("https://user:pass@example.com/", "credentials_in_url"),
        ("https://user@example.com/", "credentials_in_url"),
        ("https://example.com\\@evil.example/", "credentials_in_url"),
        ("https://example.com:8443/", "port_not_allowed"),
        ("https://example.com:22/", "port_not_allowed"),
        ("https://example.com:0/", "port_not_allowed"),
        ("https://127.1/", "numeric_host"),
        ("https://2130706433/", "numeric_host"),
        ("https://0x7f000001/", "numeric_host"),
        ("https://0x7f.0.0.1/", "numeric_host"),
        ("https://017700000001/", "numeric_host"),
        ("https://1.2.3.4.5/", "numeric_host"),
        ("https://example.123/", "numeric_host"),
        ("https://localhost/", "local_host_name"),
        ("https://LOCALHOST./", "local_host_name"),
        ("https://api.localhost/", "local_host_name"),
        ("https://exa mple.com/", "invalid_host"),
        ("https://-bad.example.com/", "invalid_host"),
        ("https://bad-.example.com/", "invalid_host"),
        ("https://" + "a" * 64 + ".example.com/", "invalid_host"),
        ("https://[fe80::1%25eth0]/", "invalid_host"),
        ("https://127.0.0.1/", "address_loopback"),
        ("https://[::1]/", "address_loopback"),
        ("https://[::ffff:127.0.0.1]/", "address_loopback"),
        ("https://[::ffff:a9fe:a9fe]/latest/meta-data/", "address_link_local"),
        ("https://169.254.169.254/latest/meta-data/", "address_link_local"),
        ("https://168.63.129.16/machine?comp=goalstate", "address_azure_platform"),
        ("https://10.0.0.1/", "address_private"),
        ("https://192.168.0.1/", "address_private"),
        ("https://100.64.1.1/", "address_cgnat"),
        ("https://0.0.0.0/", "address_unspecified"),
        ("https://[fd00::1]/", "address_unique_local"),
        ("https://[2002:7f00:1::1]/", "address_ipv4_embedded"),
        ("https://[64:ff9b::a9fe:a9fe]/", "address_ipv4_embedded"),
        ("https://224.0.0.1/", "address_multicast"),
    ],
)
async def test_refused_urls_never_reach_dns_or_the_network(url: str, reason: str) -> None:
    resolver = FakeResolver()
    web = serve({})
    with pytest.raises(EgressBlockedError) as excinfo:
        await make_client(web, resolver).get(url)
    assert excinfo.value.reason == reason
    assert resolver.calls == []
    assert web.requests == []
    assert str(excinfo.value) == reason  # the message is the code, never the URL


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("internal.example.com", "address_private"),
        ("metadata.example.com", "address_link_local"),
        ("wire.example.com", "address_azure_platform"),
        ("mixed.example.com", "address_loopback"),  # one bad address taints the host
        ("mapped.example.com", "address_loopback"),
        ("cgnat.example.com", "address_cgnat"),
        ("ula.example.com", "address_unique_local"),
        ("sixtofour.example.com", "address_ipv4_embedded"),
    ],
)
async def test_names_resolving_to_blocked_addresses_are_refused(host: str, reason: str) -> None:
    resolver = FakeResolver()
    web = serve({})
    with pytest.raises(EgressBlockedError) as excinfo:
        await make_client(web, resolver).get(f"https://{host}/")
    assert excinfo.value.reason == reason
    assert resolver.calls == [(host, 443)]
    assert web.requests == []


@pytest.mark.parametrize("host", ["unknown.example.com", "empty.example.com"])
async def test_dns_failures(host: str) -> None:
    with pytest.raises(EgressTransportError) as excinfo:
        await make_client(serve({})).get(f"https://{host}/")
    assert excinfo.value.reason == "dns_failed"


async def test_http_is_opt_in_per_call() -> None:
    web = serve({"http://example.com/feed": ok(b"plain")})
    client = make_client(web)
    with pytest.raises(EgressBlockedError):
        await client.get("http://example.com/feed")
    response = await client.get("http://example.com/feed", allow_http=True)
    assert response.content == b"plain"
    assert web.targets[0].port == 80


async def test_allowed_ports_are_configurable() -> None:
    web = serve({"https://example.com:8443/": ok()})
    client = make_client(web, allowed_ports=(443, 8443))
    assert (await client.get("https://example.com:8443/")).status == 200
    assert web.targets[0].port == 8443


async def test_idn_hosts_are_resolved_in_ascii() -> None:
    resolver = FakeResolver({"xn--bcher-kva.example": [PUBLIC_V4]})
    web = serve({"https://xn--bcher-kva.example/": ok()})
    await make_client(web, resolver).get("https://bücher.example/")
    assert resolver.calls == [("xn--bcher-kva.example", 443)]
    assert web.requests[0].headers["host"] == "xn--bcher-kva.example"


# ----------------------------------------------------------------------------------------------
# Redirects
# ----------------------------------------------------------------------------------------------


async def test_relative_redirect_is_followed() -> None:
    web = serve({"https://example.com/a": redirect("/b", 301), "https://example.com/b": ok()})
    response = await make_client(web).get("https://example.com/a")
    assert response.url == "https://example.com/b"
    assert response.redirects == 1
    assert web.urls == ["https://example.com/a", "https://example.com/b"]


async def test_every_hop_is_resolved_and_vetted_again() -> None:
    resolver = FakeResolver()
    web = serve(
        {
            "https://example.com/start": redirect("https://cdn.example.net/next#frag"),
            "https://cdn.example.net/next": ok(b"done"),
        }
    )
    response = await make_client(web, resolver).get("https://example.com/start")
    assert response.url == "https://cdn.example.net/next"
    assert resolver.calls == [("example.com", 443), ("cdn.example.net", 443)]
    assert [target.addresses for target in web.targets] == [
        (ipaddress.IPv4Address(PUBLIC_V4),),
        (ipaddress.IPv4Address(PUBLIC_V4_B),),
    ]


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("https://internal.example.com/admin", "address_private"),
        ("https://metadata.example.com/latest/meta-data/", "address_link_local"),
        ("https://169.254.169.254/latest/meta-data/", "address_link_local"),
        ("https://[::ffff:127.0.0.1]/", "address_loopback"),
        ("https://[::1]:443/", "address_loopback"),
        ("https://localhost/", "local_host_name"),
        ("https://2130706433/", "numeric_host"),
        ("http://example.com/", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("https://user:pw@example.com/", "credentials_in_url"),
        ("https://example.com:6379/", "port_not_allowed"),
        ("https://" + "a" * 9000, "invalid_url"),
    ],
)
async def test_redirect_to_a_blocked_target_is_refused(location: str, reason: str) -> None:
    web = serve({"https://example.com/": redirect(location)})
    with pytest.raises(EgressBlockedError) as excinfo:
        await make_client(web).get("https://example.com/")
    assert excinfo.value.reason == reason
    assert web.urls == ["https://example.com/"]  # the blocked hop was never requested


async def test_redirect_to_a_private_ip_is_refused_even_with_http_allowed() -> None:
    web = serve({"http://example.com/": redirect("http://169.254.169.254/latest/meta-data/")})
    with pytest.raises(EgressBlockedError) as excinfo:
        await make_client(web).get("http://example.com/", allow_http=True)
    assert excinfo.value.reason == "address_link_local"
    assert len(web.requests) == 1


async def test_downgrade_to_http_needs_the_opt_in() -> None:
    routes = {
        "https://example.com/": redirect("http://example.com/plain"),
        "http://example.com/plain": ok(),
    }
    with pytest.raises(EgressBlockedError):
        await make_client(serve(routes)).get("https://example.com/")
    response = await make_client(serve(routes)).get("https://example.com/", allow_http=True)
    assert response.url == "http://example.com/plain"


def chain(depth: int) -> Web:
    """``/r0`` redirects to ``/r1`` ... ``/r{depth}``, which answers 200."""

    async def handler(request: httpx.Request) -> httpx.Response:
        step = int(request.url.path.removeprefix("/r"))
        if step < depth:
            return httpx.Response(302, headers={"Location": f"/r{step + 1}"})
        return httpx.Response(200, content=b"end")

    return Web(handler)


async def test_five_redirects_are_followed() -> None:
    web = chain(5)
    response = await make_client(web).get("https://example.com/r0")
    assert response.redirects == 5
    assert response.url == "https://example.com/r5"
    assert len(web.requests) == 6


async def test_six_redirects_are_too_many() -> None:
    web = chain(6)
    with pytest.raises(EgressRedirectError) as excinfo:
        await make_client(web).get("https://example.com/r0")
    assert excinfo.value.reason == "too_many_redirects"
    assert len(web.requests) == 6  # the sixth Location is never requested


async def test_redirect_loops_end() -> None:
    web = serve({"https://example.com/loop": redirect("/loop")})
    with pytest.raises(EgressRedirectError):
        await make_client(web).get("https://example.com/loop")
    assert len(web.requests) == 6


async def test_max_redirects_is_configurable() -> None:
    with pytest.raises(EgressRedirectError):
        await make_client(chain(1), max_redirects=0).get("https://example.com/r0")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_statuses_are_followed(status: int) -> None:
    web = serve(
        {"https://example.com/": redirect("/next", status), "https://example.com/next": ok()}
    )
    response = await make_client(web).get("https://example.com/")
    assert response.url == "https://example.com/next"
    assert all(request.method == "GET" for request in web.requests)


@pytest.mark.parametrize("status", [300, 304, 305, 399])
async def test_other_3xx_are_returned_not_followed(status: int) -> None:
    web = serve({"https://example.com/": redirect("https://evil.example/", status)})
    response = await make_client(web).get("https://example.com/")
    assert response.status == status
    assert len(web.requests) == 1


async def test_redirect_without_location_is_returned() -> None:
    web = serve({"https://example.com/": lambda: httpx.Response(302)})
    response = await make_client(web).get("https://example.com/")
    assert response.status == 302


async def test_no_cookies_are_kept_between_hops() -> None:
    web = serve(
        {
            "https://example.com/": lambda: httpx.Response(
                302, headers={"Location": "/next", "Set-Cookie": "session=abc"}
            ),
            "https://example.com/next": ok(),
        }
    )
    await make_client(web).get("https://example.com/")
    assert "cookie" not in web.requests[1].headers


async def test_caller_headers_are_sent_on_every_hop() -> None:
    web = serve({"https://example.com/": redirect("/next"), "https://example.com/next": ok()})
    await make_client(web).get(
        "https://example.com/",
        headers={"Accept": "application/rss+xml", "If-None-Match": '"v1"'},
    )
    for request in web.requests:
        assert request.headers["accept"] == "application/rss+xml"
        assert request.headers["if-none-match"] == '"v1"'


# ----------------------------------------------------------------------------------------------
# Size cap and decoding
# ----------------------------------------------------------------------------------------------


async def test_declared_length_over_the_cap_is_refused_before_reading() -> None:
    body = ChunkedBody([b"x" * 10])
    web = serve(
        {
            "https://example.com/": lambda: httpx.Response(
                200, headers={"Content-Length": "101"}, stream=body
            )
        }
    )
    with pytest.raises(EgressTooLargeError) as excinfo:
        await make_client(web, max_bytes=100).get("https://example.com/")
    assert excinfo.value.reason == "too_large"
    assert body.delivered == 0
    assert body.closed


async def test_streamed_body_over_the_cap_is_cut_off() -> None:
    body = ChunkedBody([b"x" * 60] * 10)
    web = serve({"https://example.com/": lambda: httpx.Response(200, stream=body)})
    with pytest.raises(EgressTooLargeError):
        await make_client(web, max_bytes=100).get("https://example.com/")
    assert body.delivered == 2  # stopped at the chunk that crossed the cap
    assert body.closed


async def test_body_exactly_at_the_cap_is_accepted() -> None:
    web = serve({"https://example.com/": ok(b"x" * 100)})
    response = await make_client(web, max_bytes=100).get("https://example.com/")
    assert len(response.content) == 100


async def test_per_call_cap_can_only_lower_the_client_cap() -> None:
    web = serve({"https://example.com/": ok(b"x" * 11)})
    client = make_client(web, max_bytes=100)
    with pytest.raises(EgressTooLargeError):
        await client.get("https://example.com/", max_bytes=10)
    assert len((await client.get("https://example.com/", max_bytes=11)).content) == 11
    with pytest.raises(ValueError, match="max_bytes"):
        await client.get("https://example.com/", max_bytes=101)
    with pytest.raises(ValueError, match="max_bytes"):
        await client.get("https://example.com/", max_bytes=0)


def compressed(encoding: str, data: bytes) -> bytes:
    match encoding:
        case "gzip" | "x-gzip":
            return gzip.compress(data)
        case "deflate":
            return zlib.compress(data)
        case "raw-deflate":
            compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
            return compressor.compress(data) + compressor.flush()
        case _:
            return data


@pytest.mark.parametrize(
    ("encoding", "header"),
    [
        ("gzip", "gzip"),
        ("x-gzip", "x-gzip"),
        ("deflate", "deflate"),
        ("raw-deflate", "deflate"),
        ("identity", "identity"),
        ("gzip", "GZIP"),
        ("gzip", "identity, gzip"),
    ],
)
async def test_bodies_are_decoded(encoding: str, header: str) -> None:
    data = b"<feed>" + b"item " * 500 + b"</feed>"
    web = serve(
        {"https://example.com/": ok(compressed(encoding, data), **{"Content-Encoding": header})}
    )
    response = await make_client(web).get("https://example.com/")
    assert response.content == data


async def test_compression_bomb_stops_at_the_cap() -> None:
    bomb = gzip.compress(b"\0" * 10_000_000)
    assert len(bomb) < 20_000
    web = serve({"https://example.com/": ok(bomb, **{"Content-Encoding": "gzip"})})
    with pytest.raises(EgressTooLargeError):
        await make_client(web, max_bytes=1_000_000).get("https://example.com/")


async def test_decoded_size_is_capped_across_chunks() -> None:
    payload = gzip.compress(b"a" * 5000)
    pieces = [payload[i : i + 64] for i in range(0, len(payload), 64)]
    web = serve(
        {
            "https://example.com/": lambda: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=ChunkedBody(pieces)
            )
        }
    )
    client = make_client(web, max_bytes=4999)
    with pytest.raises(EgressTooLargeError):
        await client.get("https://example.com/")
    response = await make_client(web, max_bytes=5000).get("https://example.com/")
    assert response.content == b"a" * 5000


async def test_wire_bytes_are_capped_even_if_they_decode_small() -> None:
    padded = gzip.compress(b"hi") + b"\0" * 200  # trailing junk after the gzip member
    web = serve({"https://example.com/": ok(padded, **{"Content-Encoding": "gzip"})})
    with pytest.raises(EgressTooLargeError):
        await make_client(web, max_bytes=100).get("https://example.com/")


@pytest.mark.parametrize("header", ["br", "zstd", "gzip, br", "compress", "gzip, gzip"])
async def test_unsupported_encodings_are_refused(header: str) -> None:
    web = serve({"https://example.com/": ok(b"data", **{"Content-Encoding": header})})
    with pytest.raises(EgressResponseError) as excinfo:
        await make_client(web).get("https://example.com/")
    assert excinfo.value.reason == "unsupported_encoding"


@pytest.mark.parametrize(
    ("header", "body"),
    [
        ("gzip", b"definitely not gzip"),
        ("gzip", gzip.compress(b"truncated payload " * 50)[:-12]),
        ("deflate", b"\xff\xfe not deflate at all"),
    ],
)
async def test_corrupt_bodies_are_refused(header: str, body: bytes) -> None:
    web = serve({"https://example.com/": ok(body, **{"Content-Encoding": header})})
    with pytest.raises(EgressResponseError) as excinfo:
        await make_client(web).get("https://example.com/")
    assert excinfo.value.reason == "bad_encoding"


async def test_empty_body_with_an_encoding_is_fine() -> None:
    web = serve(
        {"https://example.com/": lambda: httpx.Response(304, headers={"Content-Encoding": "gzip"})}
    )
    response = await make_client(web).get("https://example.com/")
    assert (response.status, response.content) == (304, b"")


# ----------------------------------------------------------------------------------------------
# Deadline and transport failures
# ----------------------------------------------------------------------------------------------


async def test_slow_server_hits_the_total_deadline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(EgressTimeoutError) as excinfo:
        await make_client(Web(handler), timeout=0.05).get("https://example.com/")
    assert excinfo.value.reason == "timeout"


async def test_slow_body_hits_the_total_deadline() -> None:
    body = ChunkedBody([b"a", b"b", b"c"], delay=0.04)
    web = serve({"https://example.com/": lambda: httpx.Response(200, stream=body)})
    with pytest.raises(EgressTimeoutError):
        await make_client(web, timeout=0.1).get("https://example.com/")
    assert body.closed


async def test_slow_dns_hits_the_total_deadline() -> None:
    with pytest.raises(EgressTimeoutError):
        await make_client(serve({}), FakeResolver(delay=5), timeout=0.05).get(
            "https://example.com/"
        )


async def test_the_deadline_covers_all_hops() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(302, headers={"Location": "/again"})

    with pytest.raises(EgressTimeoutError):
        await make_client(Web(handler), timeout=0.1, max_redirects=50).get("https://example.com/")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("refused"), (EgressTransportError, "connect_failed")),
        (httpx.RemoteProtocolError("garbage"), (EgressTransportError, "protocol_error")),
        (httpx.ReadError("reset"), (EgressTransportError, "transport_error")),
        (httpx.ReadTimeout("slow"), (EgressTimeoutError, "timeout")),
        (httpx.ConnectTimeout("slow"), (EgressTimeoutError, "timeout")),
    ],
)
async def test_transport_errors_are_mapped(
    error: Exception, expected: tuple[type[Exception], str]
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise error

    error_type, reason = expected
    with pytest.raises(error_type) as excinfo:
        await make_client(Web(handler)).get("https://example.com/")
    assert getattr(excinfo.value, "reason", None) == reason


# ----------------------------------------------------------------------------------------------
# Arguments
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Authorization", "Cookie", "Host", "User-Agent", "X-Forwarded-For"]
)
async def test_only_content_negotiation_headers_may_be_set(name: str) -> None:
    with pytest.raises(ValueError, match="cannot be set"):
        await make_client(serve({})).get("https://example.com/", headers={name: "x"})


@pytest.mark.parametrize("value", ["a\r\nX-Injected: 1", "\x00", "é", "x" * 1025])
async def test_header_values_must_be_plain(value: str) -> None:
    with pytest.raises(ValueError, match="printable ASCII"):
        await make_client(serve({})).get("https://example.com/", headers={"Accept": value})


@pytest.mark.parametrize(
    "options",
    [
        {"max_bytes": 0},
        {"timeout": 0},
        {"timeout": 301},
        {"max_redirects": -1},
        {"allowed_ports": ()},
        {"allowed_ports": (0,)},
        {"allowed_ports": (70_000,)},
        {"user_agent": "bad\r\nagent"},
        {"user_agent": ""},
    ],
)
def test_constructor_validates_options(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - each option has its own message
        SafeHttpClient(**options)  # pyright: ignore[reportArgumentType]


def test_default_client_builds_a_pinned_transport() -> None:
    client = SafeHttpClient()
    assert client.max_bytes == DEFAULT_MAX_BYTES


# ----------------------------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------------------------


def json_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    formatter = JsonFormatter()
    return [json.loads(formatter.format(record)) for record in caplog.records]


async def test_successful_fetch_logs_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="blogwriter.core.egress")
    web = serve({"https://example.com/secret-path?token=abc": ok(b"SECRET-CONTENT")})
    await make_client(web).get("https://example.com/secret-path?token=abc")
    (line,) = json_lines(caplog)
    assert line["event"] == "egress_fetch"
    assert line["status"] == 200
    assert line["redirects"] == 0
    assert line["size_bytes"] == len(b"SECRET-CONTENT")
    assert "duration_ms" in line
    assert "dropped_fields" not in line
    text = json.dumps(line)
    assert "example.com" not in text
    assert "secret" not in text.lower()


async def test_refused_fetch_logs_the_reason_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="blogwriter.core.egress")
    web = serve({"https://example.com/": redirect("https://internal.example.com/?key=SECRET")})
    with pytest.raises(EgressBlockedError):
        await make_client(web).get("https://example.com/")
    (line,) = json_lines(caplog)
    assert line["event"] == "egress_failed"
    assert line["level"] == "WARNING"
    assert line["reason"] == "address_private"
    assert line["redirects"] == 1
    assert "dropped_fields" not in line
    text = json.dumps(line)
    assert "internal" not in text
    assert "SECRET" not in text


def test_log_fields_are_registered() -> None:
    assert {"redirects", "size_bytes"} <= allowed_log_fields()


# ----------------------------------------------------------------------------------------------
# The pinned transport over a fake network backend
# ----------------------------------------------------------------------------------------------


class RecordingStream(httpcore.AsyncNetworkStream):
    def __init__(self, reply: bytes, *, fail_read: bool = False) -> None:
        self._reply = [reply] if reply else []
        self._fail_read = fail_read
        self.written = bytearray()
        self.tls_hostname: str | None = None
        self.closed = False

    @override
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._fail_read:
            raise httpcore.ReadError("connection reset")
        return self._reply.pop(0) if self._reply else b""

    @override
    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written += buffer

    @override
    async def aclose(self) -> None:
        self.closed = True

    @override
    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.tls_hostname = server_hostname
        return self

    @override
    def get_extra_info(self, info: str) -> object:
        return None


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Records every dial; ``refuse`` lists addresses that fail to connect."""

    def __init__(self, reply: bytes, *, refuse: Collection[str] = (), fail_read: bool = False):
        self.reply = reply
        self.refuse = set(refuse)
        self.fail_read = fail_read
        self.connects: list[tuple[str, int]] = []
        self.streams: list[RecordingStream] = []

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connects.append((host, port))
        if host in self.refuse:
            raise httpcore.ConnectError("refused")
        stream = RecordingStream(self.reply, fail_read=self.fail_read)
        self.streams.append(stream)
        return stream

    @override
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("unix sockets are never used")

    @override
    async def sleep(self, seconds: float) -> None:
        return None


HELLO = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello"


@pytest.fixture(scope="module")
def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def pinned_client(
    backend: RecordingBackend, tls_context: ssl.SSLContext, resolver: FakeResolver | None = None
) -> SafeHttpClient:
    return SafeHttpClient(
        resolver=resolver if resolver is not None else FakeResolver(),
        transport_factory=functools.partial(
            pinned_transport, ssl_context=tls_context, network_backend=backend
        ),
    )


async def test_pinned_path_dials_the_vetted_ip_with_hostname_sni_and_host(
    tls_context: ssl.SSLContext,
) -> None:
    backend = RecordingBackend(HELLO)
    response = await pinned_client(backend, tls_context).get("https://example.com/feed?x=1")
    assert (response.status, response.content, response.media_type) == (200, b"hello", "text/plain")
    assert backend.connects == [(PUBLIC_V4, 443)]  # the vetted address, not the name
    (stream,) = backend.streams
    assert stream.tls_hostname == "example.com"  # SNI and certificate checks use the name
    wire = bytes(stream.written)
    assert wire.startswith(b"GET /feed?x=1 HTTP/1.1\r\n")
    assert b"\r\nhost: example.com\r\n" in wire.lower()
    assert stream.closed  # the per-hop pool is closed


async def test_pinned_path_over_plain_http_skips_tls(tls_context: ssl.SSLContext) -> None:
    backend = RecordingBackend(HELLO)
    await pinned_client(backend, tls_context).get("http://example.com/", allow_http=True)
    assert backend.connects == [(PUBLIC_V4, 80)]
    assert backend.streams[0].tls_hostname is None


async def test_pinned_path_dials_ipv6(tls_context: ssl.SSLContext) -> None:
    backend = RecordingBackend(HELLO)
    await pinned_client(backend, tls_context).get("https://v6.example.org/")
    assert backend.connects == [(PUBLIC_V6, 443)]


async def test_pinned_path_falls_back_to_the_next_vetted_address(
    tls_context: ssl.SSLContext,
) -> None:
    backend = RecordingBackend(HELLO, refuse={PUBLIC_V4})
    response = await pinned_client(backend, tls_context).get("https://dual.example.org/")
    assert response.content == b"hello"
    assert backend.connects == [(PUBLIC_V4, 443), (PUBLIC_V4_B, 443)]


async def test_pinned_path_reports_connect_failures(tls_context: ssl.SSLContext) -> None:
    backend = RecordingBackend(HELLO, refuse={PUBLIC_V4, PUBLIC_V4_B})
    with pytest.raises(EgressTransportError) as excinfo:
        await pinned_client(backend, tls_context).get("https://dual.example.org/")
    assert excinfo.value.reason == "connect_failed"


async def test_pinned_path_reports_read_failures(tls_context: ssl.SSLContext) -> None:
    backend = RecordingBackend(HELLO, fail_read=True)
    with pytest.raises(EgressTransportError) as excinfo:
        await pinned_client(backend, tls_context).get("https://example.com/")
    assert excinfo.value.reason == "transport_error"


async def test_pinned_path_reports_protocol_errors(tls_context: ssl.SSLContext) -> None:
    backend = RecordingBackend(b"THIS IS NOT HTTP\r\n\r\n")
    with pytest.raises(EgressTransportError) as excinfo:
        await pinned_client(backend, tls_context).get("https://example.com/")
    assert excinfo.value.reason == "protocol_error"


async def test_pinned_path_follows_redirects_with_a_fresh_pin(
    tls_context: ssl.SSLContext,
) -> None:
    backend = RecordingBackend(
        b"HTTP/1.1 301 Moved\r\nLocation: https://cdn.example.net/x\r\nContent-Length: 0\r\n\r\n"
    )
    with pytest.raises(EgressRedirectError):  # the fake server always redirects
        await pinned_client(backend, tls_context).get("https://example.com/")
    assert backend.connects[:2] == [(PUBLIC_V4, 443), (PUBLIC_V4_B, 443)]
    assert all(stream.closed for stream in backend.streams)


async def test_pinned_path_enforces_the_size_cap(tls_context: ssl.SSLContext) -> None:
    big = b"HTTP/1.1 200 OK\r\nContent-Length: 6000000\r\n\r\n" + b"x" * 100
    with pytest.raises(EgressTooLargeError):
        await pinned_client(RecordingBackend(big), tls_context).get("https://example.com/")


async def test_pinned_path_decodes_gzip(tls_context: ssl.SSLContext) -> None:
    body = gzip.compress(b"compressed hello")
    reply = (
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    response = await pinned_client(RecordingBackend(reply), tls_context).get("https://example.com/")
    assert response.content == b"compressed hello"


@pytest.mark.parametrize("url", ["https://other.example/", "https://example.com:8443/"])
async def test_pinned_transport_refuses_any_other_host_or_port(
    url: str, tls_context: ssl.SSLContext
) -> None:
    backend = RecordingBackend(HELLO)
    target = PinnedTarget("example.com", 443, (ipaddress.IPv4Address(PUBLIC_V4),))
    transport = pinned_transport(target, ssl_context=tls_context, network_backend=backend)
    try:
        with pytest.raises(EgressBlockedError) as excinfo:
            await transport.handle_async_request(httpx.Request("GET", url))
    finally:
        await transport.aclose()
    assert excinfo.value.reason == "pin_mismatch"
    assert backend.connects == []


# ----------------------------------------------------------------------------------------------
# System resolver (getaddrinfo is replaced; nothing leaves the machine)
# ----------------------------------------------------------------------------------------------

type AddrInfo = tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]


def fake_getaddrinfo(
    results: list[AddrInfo] | Exception, calls: list[tuple[object, ...]]
) -> Callable[..., Awaitable[list[AddrInfo]]]:
    async def getaddrinfo(
        host: str, port: int, *, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0
    ) -> list[AddrInfo]:
        calls.append((host, port, family, type, proto, flags))
        if isinstance(results, Exception):
            raise results
        return results

    return getaddrinfo


async def test_system_resolver_queries_absolute_names_and_parses_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    answers: list[AddrInfo] = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC_V6, 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 443)),  # duplicate
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%en0", 443, 0, 4)),
    ]
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo(answers, calls))
    addresses = await SystemResolver().resolve("example.com", 443)
    assert addresses == [
        ipaddress.IPv4Address(PUBLIC_V4),
        ipaddress.IPv6Address(PUBLIC_V6),
        ipaddress.IPv6Address("fe80::1"),  # zone dropped; the link-local verdict still applies
    ]
    assert calls == [
        ("example.com.", 443, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AI_ADDRCONFIG)
    ]


@pytest.mark.parametrize(
    "failure",
    [socket.gaierror(socket.EAI_NONAME, "unknown"), UnicodeError("label too long")],
)
async def test_system_resolver_failures_are_dns_failed(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo(failure, []))
    with pytest.raises(EgressTransportError) as excinfo:
        await SystemResolver().resolve("example.com", 443)
    assert excinfo.value.reason == "dns_failed"


async def test_system_resolver_feeds_the_address_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[AddrInfo] = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo(answers, []))
    client = SafeHttpClient(transport_factory=serve({}).transport)
    with pytest.raises(EgressBlockedError) as excinfo:
        await client.get("https://rebind.example/")
    assert excinfo.value.reason == "address_loopback"


# ----------------------------------------------------------------------------------------------
# Parser differentials and DNS rebinding
# ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://0177.0.0.1/",  # octal dotted quad
        "https://0x7f.0x0.0x0.0x1/",
        "https://%31%32%37.0.0.1/",  # percent-encoded host
        "https://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",  # fullwidth digits
        "https://\u24db\u24de\u24d2\u24d0\u24db\u24d7\u24de\u24e2\u24e3/",  # enclosed letters
        "https://[::127.0.0.1]/",  # IPv4-compatible
        "https://[0:0:0:0:0:ffff:7f00:1]/",  # IPv4-mapped, long form
        "https://example.com%2F@127.0.0.1/",
        "https://example.com:443@127.0.0.1/",
        "https://127.0.0.1#.example.com",
    ],
)
async def test_parser_tricks_are_refused_before_dns(url: str) -> None:
    # Only the refusal is asserted: which check fires first may move between httpx versions.
    resolver = FakeResolver()
    web = serve({})
    with pytest.raises(EgressBlockedError):
        await make_client(web, resolver).get(url)
    assert resolver.calls == []
    assert web.requests == []


async def test_a_fragment_cannot_smuggle_another_host() -> None:
    web = serve({"https://example.com": ok(b"public")})
    response = await make_client(web).get("https://example.com#@127.0.0.1/")
    assert (response.content, response.url) == (b"public", "https://example.com")
    assert [target.host for target in web.targets] == ["example.com"]
    assert web.targets[0].addresses == (ipaddress.IPv4Address(PUBLIC_V4),)


@pytest.mark.parametrize("location", ["/\\127.0.0.1/", "https:\\\\127.0.0.1/", "\\\\127.0.0.1/"])
async def test_backslash_locations_stay_on_the_vetted_host(location: str) -> None:
    # Browsers read "\" as "/", httpx does not: what matters is that the host we check is the
    # host we dial, and both are example.com here.
    web = serve({"https://example.com/a/b": redirect(location)})
    response = await make_client(web).get("https://example.com/a/b")
    assert response.status == 404
    assert [target.host for target in web.targets] == ["example.com", "example.com"]
    assert all(target.addresses == (ipaddress.IPv4Address(PUBLIC_V4),) for target in web.targets)


class RebindingResolver(FakeResolver):
    """Answers with a public address first and a private one afterwards (DNS rebinding)."""

    def __init__(self) -> None:
        super().__init__()
        self.answers = [[ipaddress.ip_address(PUBLIC_V4)], [ipaddress.ip_address("10.0.0.5")]]

    @override
    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        self.calls.append((host, port))
        return list(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])


async def test_a_name_rebinding_between_hops_is_refused_on_the_next_hop() -> None:
    resolver = RebindingResolver()
    web = serve({"https://flip.example.com/start": redirect("/next")})
    with pytest.raises(EgressBlockedError) as excinfo:
        await make_client(web, resolver).get("https://flip.example.com/start")
    assert excinfo.value.reason == "address_private"
    assert resolver.calls == [("flip.example.com", 443), ("flip.example.com", 443)]
    assert web.urls == ["https://flip.example.com/start"]  # the rebound hop was never sent


async def test_the_pinned_path_dials_the_first_answer_even_if_dns_changes_later(
    tls_context: ssl.SSLContext,
) -> None:
    # One lookup per hop: the connection never asks DNS again, so a later private answer
    # (the rebinding attack) is never dialled.
    resolver = RebindingResolver()
    backend = RecordingBackend(HELLO)
    response = await pinned_client(backend, tls_context, resolver).get("https://flip.example.com/")
    assert response.status == 200
    assert resolver.calls == [("flip.example.com", 443)]
    assert backend.connects == [(PUBLIC_V4, 443)]
