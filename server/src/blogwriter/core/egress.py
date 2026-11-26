"""SSRF-safe outbound HTTP for feeds, owner-supplied URLs and images.

Every fetch of a URL that did not come from our own configuration goes through
:class:`SafeHttpClient`. For every hop (the first request and each redirect) it:

1. **Checks the URL.** ``https`` only, unless the call opts into ``http``; no credentials in the
   URL; allowed ports only (443 and 80 by default); the host must be a valid DNS name or a
   canonical IP literal. Numeric shorthands such as ``127.1``, ``2130706433`` or ``0x7f000001``
   and ``localhost`` names are refused before any lookup.
2. **Resolves the name asynchronously and vets every address** (:func:`blocked_reason`). If
   *any* address is not public the hop is refused: loopback, RFC 1918, link-local (including
   169.254.169.254), Azure's 168.63.129.16, CGNAT 100.64.0.0/10, ULA fc00::/7, fe80::/10,
   site-local, multicast, unspecified, reserved and documentation ranges, and IPv6 forms that
   embed or tunnel to IPv4 (IPv4-mapped addresses are judged as the IPv4 address they map;
   6to4, Teredo, NAT64 and IPv4-compatible addresses are refused outright).
3. **Connects only to the vetted addresses.** Each hop gets its own connection pool whose
   network backend is pinned to the vetted addresses of that host and port, so there is no
   second DNS lookup between the check and the connect (no DNS rebinding). TLS SNI,
   certificate verification and the ``Host`` header still use the hostname.
4. **Follows redirects itself** (301, 302, 303, 307 and 308; at most 5), repeating steps 1-3
   for every hop. 304 and other 3xx responses are returned as they are.
5. **Streams the body under caps:** 5 MiB on the wire *and* after decompression (gzip and
   deflate are decoded here with a bounded decoder, so a compression bomb stops at the cap),
   and one total deadline of 15 s covering DNS, every hop and the body.

Only ``GET`` exists. No cookies, credentials or proxies (``HTTP(S)_PROXY`` and ``.netrc`` are
ignored) ever reach the wire, and callers may set only a few content-negotiation headers.
Failures raise an :class:`EgressError` subclass with a stable ``reason``; messages never contain
the URL or any fetched content. Log lines carry only status, byte and hop counts, the reason and
the duration.

Build one :class:`SafeHttpClient` per process and share it: it holds no per-request state and
loads the CA bundle once. Tests inject a fake :class:`Resolver` and a ``transport_factory``
returning ``httpx.MockTransport``; :func:`pinned_transport` accepts a fake ``httpcore`` network
backend to exercise the pinned path without a network.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import logging
import re
import socket
import ssl
import time
import zlib
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Callable,
    Collection,
    Generator,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from typing import Final, Protocol, cast, override, runtime_checkable

import httpcore
import httpx

from blogwriter.core.logging import register_log_fields

__all__ = [
    "ALLOWED_REQUEST_HEADERS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_PORTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "REDIRECT_STATUSES",
    "EgressBlockedError",
    "EgressError",
    "EgressRedirectError",
    "EgressResponse",
    "EgressResponseError",
    "EgressTimeoutError",
    "EgressTooLargeError",
    "EgressTransportError",
    "IPAddress",
    "PinnedTarget",
    "Resolver",
    "SafeHttpClient",
    "SystemResolver",
    "TransportFactory",
    "blocked_reason",
    "pinned_transport",
]

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Body cap, on the wire and after decompression (5 MiB).
DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024
#: Total deadline for one fetch: DNS, every hop and the body.
DEFAULT_TIMEOUT_SECONDS: Final = 15.0
#: Redirects followed before :class:`EgressRedirectError`.
DEFAULT_MAX_REDIRECTS: Final = 5
#: Ports a URL may name (explicitly or by default).
DEFAULT_PORTS: Final = frozenset({80, 443})
DEFAULT_USER_AGENT: Final = "BlogWriter/2.0"
#: Statuses whose ``Location`` is followed. Others (including 300, 304 and 305) are returned.
REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
#: Request headers callers may set (lowercase). Everything else is fixed by the client.
ALLOWED_REQUEST_HEADERS: Final = frozenset(
    {"accept", "accept-language", "if-modified-since", "if-none-match"}
)
MAX_URL_CHARS: Final = 8192

_SCHEME_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}
_LABEL_RE: Final = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_NUMERIC_LABEL_RE: Final = re.compile(r"0x[0-9a-f]*|[0-9]+")
_HEADER_VALUE_RE: Final = re.compile(r"[\x20-\x7e]{0,1024}")
_CONTENT_LENGTH_RE: Final = re.compile(r"[0-9]{1,19}")

_logger: Final = logging.getLogger("blogwriter.core.egress")
register_log_fields("redirects", "size_bytes")


# ----------------------------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------------------------


class EgressError(Exception):
    """An outbound fetch failed or was refused. :attr:`reason` is a stable snake_case code.

    The message is the reason code: it never contains the URL, a hostname or fetched content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EgressBlockedError(EgressError):
    """The URL, a redirect target or a resolved address is not allowed. Never retry."""


class EgressRedirectError(EgressError):
    """The server redirected more often than allowed."""


class EgressTooLargeError(EgressError):
    """The body is larger than the cap (on the wire or after decompression)."""


class EgressTimeoutError(EgressError):
    """The total deadline passed."""


class EgressTransportError(EgressError):
    """DNS, connection, TLS or HTTP protocol failure. May be retried later."""


class EgressResponseError(EgressError):
    """The body cannot be decoded (unsupported or corrupt ``Content-Encoding``)."""


# ----------------------------------------------------------------------------------------------
# Address policy
# ----------------------------------------------------------------------------------------------

_BLOCKED_V4: Final[tuple[tuple[ipaddress.IPv4Network, str], ...]] = tuple(
    (ipaddress.IPv4Network(cidr), reason)
    for cidr, reason in (
        ("0.0.0.0/8", "unspecified"),  # "this network"; 0.0.0.0 reaches localhost on Linux
        ("10.0.0.0/8", "private"),
        ("100.64.0.0/10", "cgnat"),  # RFC 6598 shared address space
        ("127.0.0.0/8", "loopback"),
        ("168.63.129.16/32", "azure_platform"),  # Azure WireServer, DNS and health probes
        ("169.254.0.0/16", "link_local"),  # includes 169.254.169.254 (instance metadata)
        ("172.16.0.0/12", "private"),
        ("192.0.0.0/24", "reserved"),  # IETF protocol assignments
        ("192.0.2.0/24", "reserved"),  # TEST-NET-1
        ("192.88.99.0/24", "reserved"),  # deprecated 6to4 relay anycast
        ("192.168.0.0/16", "private"),
        ("198.18.0.0/15", "reserved"),  # benchmarking
        ("198.51.100.0/24", "reserved"),  # TEST-NET-2
        ("203.0.113.0/24", "reserved"),  # TEST-NET-3
        ("224.0.0.0/4", "multicast"),
        ("240.0.0.0/4", "reserved"),  # includes 255.255.255.255
    )
)

_BLOCKED_V6: Final[tuple[tuple[ipaddress.IPv6Network, str], ...]] = tuple(
    (ipaddress.IPv6Network(cidr), reason)
    for cidr, reason in (
        ("::/128", "unspecified"),
        ("::1/128", "loopback"),
        ("::/96", "ipv4_embedded"),  # IPv4-compatible (deprecated)
        ("::ffff:0:0:0/96", "ipv4_embedded"),  # IPv4-translated (SIIT)
        ("64:ff9b::/96", "ipv4_embedded"),  # NAT64 well-known prefix
        ("64:ff9b:1::/48", "ipv4_embedded"),  # NAT64 local use
        ("2001::/32", "ipv4_embedded"),  # Teredo
        ("2002::/16", "ipv4_embedded"),  # 6to4
        ("2001::/23", "reserved"),  # IETF protocol assignments
        ("2001:db8::/32", "reserved"),  # documentation
        ("3fff::/20", "reserved"),  # documentation (RFC 9637)
        ("fc00::/7", "unique_local"),
        ("fe80::/10", "link_local"),
        ("fec0::/10", "site_local"),
        ("ff00::/8", "multicast"),
    )
)

#: IANA's global unicast space; any other IPv6 address is special-purpose or unassigned.
_GLOBAL_UNICAST_V6: Final = ipaddress.IPv6Network("2000::/3")


def blocked_reason(address: IPAddress) -> str | None:
    """Why ``address`` may not be contacted, or ``None`` if it is a public unicast address.

    Reasons: ``unspecified``, ``loopback``, ``private``, ``cgnat``, ``link_local``,
    ``azure_platform``, ``unique_local``, ``site_local``, ``multicast``, ``ipv4_embedded``,
    ``scoped`` and ``reserved``. An IPv4-mapped IPv6 address gets the verdict of its IPv4 address.
    """
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return blocked_reason(mapped)
        if address.scope_id is not None:
            return "scoped"
        for network, reason in _BLOCKED_V6:
            if address in network:
                return reason
        if address not in _GLOBAL_UNICAST_V6:
            return "reserved"
    else:
        for network, reason in _BLOCKED_V4:
            if address in network:
                return reason
    if address.is_multicast:
        return "multicast"
    if address.is_reserved or not address.is_global:
        return "reserved"
    return None


def _connect_form(address: IPAddress) -> IPAddress:
    """The address to dial: IPv4-mapped IPv6 addresses are dialled as plain IPv4."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _parse_host(host: str) -> str | IPAddress:
    """The IP literal, or the DNS name (lowercase, no trailing dot), of a URL host."""
    if not host:
        raise EgressBlockedError("invalid_host")
    if ":" in host:  # IPv6 literal; httpx has already removed the brackets
        if "%" in host:  # a zone id: never a public address
            raise EgressBlockedError("invalid_host")
        try:
            return ipaddress.IPv6Address(host)
        except ValueError:
            raise EgressBlockedError("invalid_host") from None
    name = host.lower().removesuffix(".")
    labels = name.split(".")
    if len(name) > 253 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise EgressBlockedError("invalid_host")
    if _NUMERIC_LABEL_RE.fullmatch(labels[-1]):
        # No top-level domain is numeric: only a canonical dotted-quad literal is accepted, so
        # resolver quirks (inet_aton accepts 127.1, 0x7f.1 and 2130706433) never matter.
        try:
            return ipaddress.IPv4Address(name)
        except ValueError:
            raise EgressBlockedError("numeric_host") from None
    if labels[-1] == "localhost":
        raise EgressBlockedError("local_host_name")
    return name


def _parse_url(raw: str) -> httpx.URL:
    if len(raw) > MAX_URL_CHARS:
        raise EgressBlockedError("invalid_url")
    try:
        return httpx.URL(raw).copy_with(fragment=None)  # fragments never go on the wire
    except (httpx.InvalidURL, TypeError, ValueError):
        raise EgressBlockedError("invalid_url") from None


def _check_url(url: httpx.URL, *, allow_http: bool) -> None:
    if url.scheme not in _SCHEME_PORTS or (url.scheme == "http" and not allow_http):
        raise EgressBlockedError("scheme_not_allowed")
    if not url.is_absolute_url or not url.raw_host:
        raise EgressBlockedError("invalid_url")
    if url.userinfo:
        raise EgressBlockedError("credentials_in_url")


# ----------------------------------------------------------------------------------------------
# DNS
# ----------------------------------------------------------------------------------------------


class Resolver(Protocol):
    """Resolves a DNS name to every address it has (in preference order)."""

    async def resolve(self, host: str, port: int) -> Sequence[IPAddress]:
        """Raise :class:`EgressTransportError` (``dns_failed``) if the name does not resolve."""
        ...


class SystemResolver:
    """The operating system's resolver, run off the event loop (``loop.getaddrinfo``).

    Names are looked up as absolute names (a trailing dot is added), so ``resolv.conf`` search
    domains, such as Kubernetes' ``*.svc.cluster.local``, are never appended.
    """

    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(
                f"{host}.",
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_ADDRCONFIG,
            )
        except (OSError, UnicodeError) as exc:
            raise EgressTransportError("dns_failed") from exc
        addresses: list[IPAddress] = []
        for family, _kind, _proto, _canonical, sockaddr in infos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            text = str(sockaddr[0]).partition("%")[0]  # drop an IPv6 zone id
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                raise EgressTransportError("dns_failed") from None
            if address not in addresses:
                addresses.append(address)
        return addresses


# ----------------------------------------------------------------------------------------------
# Pinned transport
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PinnedTarget:
    """Where one hop may connect: the URL's host and port, and the vetted addresses to dial."""

    host: str
    """The host as it appears in the URL: lowercase ASCII (IDNA) name or bare IP literal."""
    port: int
    addresses: tuple[IPAddress, ...]


type TransportFactory = Callable[[PinnedTarget], httpx.AsyncBaseTransport]

_CORE_TO_HTTPX: Final[tuple[tuple[type[Exception], type[httpx.TransportError]], ...]] = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.TimeoutException, httpx.TimeoutException),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.NetworkError, httpx.NetworkError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore.ProtocolError, httpx.ProtocolError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
    (httpcore.ProxyError, httpx.ProxyError),
    (httpcore.ConnectionNotAvailable, httpx.TransportError),
)


@contextlib.contextmanager
def _httpcore_errors_as_httpx() -> Generator[None]:
    """Re-raise ``httpcore`` errors as the matching ``httpx`` errors (as httpx's own transport)."""
    try:
        yield
    except Exception as exc:
        for core_type, httpx_type in _CORE_TO_HTTPX:
            if isinstance(exc, core_type):
                raise httpx_type(type(exc).__name__) from exc
        raise


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Dials the vetted addresses of one host and port instead of resolving the name again."""

    def __init__(self, target: PinnedTarget, inner: httpcore.AsyncNetworkBackend) -> None:
        self._target = target
        self._inner = inner

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.lower() != self._target.host or port != self._target.port:
            raise EgressBlockedError("pin_mismatch")
        last_error: Exception | None = None
        for address in self._target.addresses:
            try:
                return await self._inner.connect_tcp(
                    address.compressed,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is None:
            raise httpcore.ConnectError("no vetted address to connect to")
        raise last_error

    @override
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise EgressBlockedError("unix_socket")

    @override
    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


@runtime_checkable
class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        with _httpcore_errors_as_httpx():
            async for part in self._stream:
                yield part

    @override
    async def aclose(self) -> None:
        if isinstance(self._stream, _AsyncClosable):
            await self._stream.aclose()


class _CoreTransport(httpx.AsyncBaseTransport):
    """An ``httpx`` transport over an ``httpcore`` pool we configure ourselves.

    ``httpx.AsyncHTTPTransport`` (0.28) cannot take a network backend, so this mirrors its
    request/response mapping using public ``httpcore`` APIs only.
    """

    def __init__(self, pool: httpcore.AsyncConnectionPool) -> None:
        self._pool = pool

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with _httpcore_errors_as_httpx():
            core_response = await self._pool.handle_async_request(core_request)
        body = core_response.stream
        if not isinstance(body, AsyncIterable):  # pragma: no cover - async pools stream async
            raise TypeError("httpcore returned a synchronous stream")
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_CoreResponseStream(body),
        )

    @override
    async def aclose(self) -> None:
        await self._pool.aclose()


def pinned_transport(
    target: PinnedTarget,
    *,
    ssl_context: ssl.SSLContext,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncBaseTransport:
    """An HTTP/1.1 transport that can only reach ``target``'s vetted addresses.

    Requests for any other host or port fail with ``EgressBlockedError('pin_mismatch')``. The
    pool keeps no idle connections; :class:`SafeHttpClient` closes it after its hop.
    """
    if network_backend is None:
        # httpcore declares a stand-in AnyIOBackend for installs without anyio; httpx requires
        # anyio, so this is always the real backend.
        network_backend = cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())
    pool = httpcore.AsyncConnectionPool(
        ssl_context=ssl_context,
        max_connections=1,
        max_keepalive_connections=0,
        http1=True,
        http2=False,
        retries=0,
        network_backend=_PinnedBackend(target, network_backend),
    )
    return _CoreTransport(pool)


# ----------------------------------------------------------------------------------------------
# Body decoding
# ----------------------------------------------------------------------------------------------


class _BodyDecoder(Protocol):
    def decode(self, data: bytes) -> bytes: ...

    def flush(self) -> bytes: ...


class _IdentityDecoder:
    def decode(self, data: bytes) -> bytes:
        return data

    def flush(self) -> bytes:
        return b""


class _ZlibDecoder:
    """gzip/deflate decoder that never produces more than ``limit`` bytes in total."""

    def __init__(self, wbits: int, limit: int, *, raw_deflate_fallback: bool = False) -> None:
        self._decompressor = zlib.decompressobj(wbits)
        self._remaining = limit
        self._raw_deflate_fallback = raw_deflate_fallback
        self._fed = False

    def decode(self, data: bytes) -> bytes:
        if not data:
            return b""
        first = not self._fed
        self._fed = True
        try:
            output = self._decompressor.decompress(data, self._remaining + 1)
        except zlib.error:
            if not (first and self._raw_deflate_fallback):
                raise EgressResponseError("bad_encoding") from None
            # Some servers send "deflate" without the zlib wrapper.
            self._decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                output = self._decompressor.decompress(data, self._remaining + 1)
            except zlib.error:
                raise EgressResponseError("bad_encoding") from None
        return self._account(output)

    def flush(self) -> bytes:
        if not self._fed:
            return b""  # an empty body (204, 304, HEAD-like replies) needs no decoding
        try:
            output = self._decompressor.flush()
        except zlib.error:  # pragma: no cover - flush after a clean decompress does not fail
            raise EgressResponseError("bad_encoding") from None
        if not self._decompressor.eof:
            raise EgressResponseError("bad_encoding")  # truncated stream
        return self._account(output)

    def _account(self, output: bytes) -> bytes:
        if len(output) > self._remaining or self._decompressor.unconsumed_tail:
            raise EgressTooLargeError("too_large")
        self._remaining -= len(output)
        return output


def _decoder_for(content_encoding: str | None, limit: int) -> _BodyDecoder:
    codings = [coding.strip().lower() for coding in (content_encoding or "").split(",")]
    codings = [coding for coding in codings if coding and coding != "identity"]
    if not codings:
        return _IdentityDecoder()
    if codings in (["gzip"], ["x-gzip"]):
        return _ZlibDecoder(zlib.MAX_WBITS | 16, limit)
    if codings == ["deflate"]:
        return _ZlibDecoder(zlib.MAX_WBITS, limit, raw_deflate_fallback=True)
    raise EgressResponseError("unsupported_encoding")


async def _read_body(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and _CONTENT_LENGTH_RE.fullmatch(declared) and int(declared) > limit:
        raise EgressTooLargeError("too_large")
    decoder = _decoder_for(response.headers.get("content-encoding"), limit)
    stream = response.stream
    if not isinstance(stream, httpx.AsyncByteStream):  # pragma: no cover - async transports only
        raise TypeError("the transport returned a synchronous body")
    # Iterate the raw stream rather than Response.aiter_raw(): it also works for responses a
    # transport has already read (httpx.MockTransport with content=...), and it is never
    # decoded by httpx, so decompression stays under the cap above.
    received = 0
    chunks: list[bytes] = []
    raw_chunks = aiter(stream)
    try:
        async for chunk in raw_chunks:
            received += len(chunk)
            if received > limit:
                raise EgressTooLargeError("too_large")
            chunks.append(decoder.decode(chunk))
    finally:
        if isinstance(raw_chunks, _AsyncClosable):
            await raw_chunks.aclose()
    chunks.append(decoder.flush())
    return b"".join(chunks)


# ----------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EgressResponse:
    """The final response of a fetch (after redirects)."""

    url: str
    """Final URL, without a fragment."""
    status: int
    content_type: str | None
    """The ``Content-Type`` header as sent, for example ``text/html; charset=utf-8``."""
    content: bytes = field(repr=False)
    """The decoded body (``Content-Encoding`` removed), at most the byte cap."""
    redirects: int = 0
    etag: str | None = None
    last_modified: str | None = None

    @property
    def media_type(self) -> str | None:
        """``type/subtype`` in lowercase, without parameters."""
        if self.content_type is None:
            return None
        media_type = self.content_type.split(";", 1)[0].strip().lower()
        return media_type or None

    @property
    def charset(self) -> str | None:
        """The ``charset`` parameter of ``Content-Type``, lowercase."""
        if self.content_type is None:
            return None
        for parameter in self.content_type.split(";")[1:]:
            name, _, value = parameter.partition("=")
            if name.strip().lower() == "charset":
                return value.strip().strip('"').lower() or None
        return None


@dataclass(slots=True)
class _Progress:
    redirects: int = 0


def _redirect_location(response: httpx.Response) -> str | None:
    if response.status_code not in REDIRECT_STATUSES:
        return None
    location = response.headers.get("location", "").strip()
    return location or None


def _follow_location(current: httpx.URL, location: str) -> httpx.URL:
    if len(location) > MAX_URL_CHARS:
        raise EgressBlockedError("invalid_url")
    try:
        target = current.join(location)
    except (httpx.InvalidURL, TypeError, ValueError):
        raise EgressBlockedError("invalid_url") from None
    return target.copy_with(fragment=None)


def _transport_reason(error: httpx.TransportError) -> str:
    if isinstance(error, httpx.ConnectError):
        return "connect_failed"
    if isinstance(error, httpx.ProtocolError | httpx.UnsupportedProtocol):
        return "protocol_error"
    return "transport_error"


class SafeHttpClient:
    """The one way to fetch an untrusted URL (feeds, owner-supplied URLs, images).

    See the module documentation for the policy. Instances are safe to share between tasks.
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        transport_factory: TransportFactory | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        allowed_ports: Collection[int] = DEFAULT_PORTS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if not 0 < timeout <= 300:
            raise ValueError("timeout must be more than 0 and at most 300 seconds")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        ports = frozenset(allowed_ports)
        if not ports or not all(0 < port < 65536 for port in ports):
            raise ValueError("allowed_ports must be TCP ports")
        if not user_agent or not _HEADER_VALUE_RE.fullmatch(user_agent):
            raise ValueError("user_agent must be printable ASCII")
        if transport_factory is None:
            # One CA bundle per client; trust_env=False ignores SSL_CERT_FILE/SSL_CERT_DIR.
            transport_factory = functools.partial(
                pinned_transport, ssl_context=httpx.create_ssl_context(trust_env=False)
            )
        self._resolver: Resolver = resolver if resolver is not None else SystemResolver()
        self._transport_factory: TransportFactory = transport_factory
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._allowed_ports = ports
        self._user_agent = user_agent

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        """Fetch ``url`` with ``GET`` under the egress policy and return the final response.

        ``allow_http`` permits ``http`` URLs (on the first hop and on redirects).
        ``headers`` may set only :data:`ALLOWED_REQUEST_HEADERS`. ``max_bytes`` may lower, never
        raise, the client's cap. Any status is returned; only policy, size, time, transport and
        decoding failures raise (:class:`EgressError` subclasses).
        """
        limit = self._limit(max_bytes)
        request_headers = self._request_headers(headers)
        started = time.perf_counter()
        progress = _Progress()
        try:
            try:
                async with asyncio.timeout(self._timeout):
                    response = await self._follow(
                        url,
                        allow_http=allow_http,
                        headers=request_headers,
                        limit=limit,
                        progress=progress,
                    )
            except (TimeoutError, httpx.TimeoutException) as exc:
                raise EgressTimeoutError("timeout") from exc
            except httpx.TransportError as exc:
                raise EgressTransportError(_transport_reason(exc)) from exc
        except EgressError as exc:
            _logger.warning(
                "egress_failed",
                extra={
                    "reason": exc.reason,
                    "redirects": progress.redirects,
                    "duration_ms": _elapsed_ms(started),
                },
            )
            raise
        _logger.info(
            "egress_fetch",
            extra={
                "status": response.status,
                "redirects": response.redirects,
                "size_bytes": len(response.content),
                "duration_ms": _elapsed_ms(started),
            },
        )
        return response

    def _limit(self, max_bytes: int | None) -> int:
        if max_bytes is None:
            return self._max_bytes
        if not 0 < max_bytes <= self._max_bytes:
            raise ValueError(f"max_bytes must be between 1 and {self._max_bytes}")
        return max_bytes

    def _request_headers(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        headers = {
            "user-agent": self._user_agent,
            "accept": "*/*",
            "accept-encoding": "gzip, deflate",
        }
        for name, value in (extra or {}).items():
            key = name.lower()
            if key not in ALLOWED_REQUEST_HEADERS:
                raise ValueError(f"header {name!r} cannot be set on egress requests")
            if not _HEADER_VALUE_RE.fullmatch(value):
                raise ValueError(f"header {name!r} must be printable ASCII (at most 1024 chars)")
            headers[key] = value
        return headers

    async def _follow(
        self,
        url: str,
        *,
        allow_http: bool,
        headers: Mapping[str, str],
        limit: int,
        progress: _Progress,
    ) -> EgressResponse:
        current = _parse_url(url)
        timeouts = dict.fromkeys(("connect", "read", "write", "pool"), self._timeout)
        while True:
            _check_url(current, allow_http=allow_http)
            target = await self._vet(current)
            transport = self._transport_factory(target)
            try:
                request = httpx.Request(
                    "GET", current, headers=headers, extensions={"timeout": timeouts}
                )
                response = await transport.handle_async_request(request)
                response.request = request
                try:
                    location = _redirect_location(response)
                    if location is None:
                        content = await _read_body(response, limit)
                        return EgressResponse(
                            url=str(current.copy_with(fragment=None)),
                            status=response.status_code,
                            content_type=response.headers.get("content-type"),
                            content=content,
                            redirects=progress.redirects,
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                        )
                finally:
                    await response.aclose()
            finally:
                await transport.aclose()
            progress.redirects += 1
            if progress.redirects > self._max_redirects:
                raise EgressRedirectError("too_many_redirects")
            current = _follow_location(current, location)

    async def _vet(self, url: httpx.URL) -> PinnedTarget:
        try:
            raw_host = url.raw_host.decode("ascii").lower()
        except UnicodeDecodeError:  # pragma: no cover - httpx stores hosts as IDNA ASCII
            raise EgressBlockedError("invalid_host") from None
        host = _parse_host(raw_host)
        port = url.port if url.port is not None else _SCHEME_PORTS[url.scheme]
        if port not in self._allowed_ports:
            raise EgressBlockedError("port_not_allowed")
        if isinstance(host, str):
            addresses = tuple(await self._resolver.resolve(host, port))
            if not addresses:
                raise EgressTransportError("dns_failed")
        else:
            addresses = (host,)
        for address in addresses:
            reason = blocked_reason(address)
            if reason is not None:
                raise EgressBlockedError(f"address_{reason}")
        dial = tuple(dict.fromkeys(_connect_form(address) for address in addresses))
        return PinnedTarget(host=raw_host, port=port, addresses=dial)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
