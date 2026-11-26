"""Owner-only access: Easy Auth principal, owner-OID check, the ``__Host-bw`` cookie and CSRF.

Every request except ``GET /api/health`` goes through :class:`AuthMiddleware`, which applies
:meth:`Authenticator.decide`:

1. **Principal.** Easy Auth injects ``X-MS-CLIENT-PRINCIPAL`` (base64 JSON with the claims)
   plus ``X-MS-CLIENT-PRINCIPAL-ID``/``-NAME``/``-IDP``. The object ID comes from the claims;
   when the ``-ID`` or ``-IDP`` headers are present they must agree with it. Present but
   malformed or inconsistent headers fail closed with **401** (a cookie does not rescue them).
   With ``env='local'`` and ``BW_DEV_PRINCIPAL_OID`` set, a request without principal headers
   gets that development principal instead, but only when its ``Host`` is a loopback name or
   address or the host of ``BW_PUBLIC_ORIGIN`` (so a DNS-rebinding page cannot borrow it).
   Every other environment ignores the setting.
2. **Owner.** A principal whose object ID is not ``BW_OWNER_OID`` gets **403** (and the app
   cookie is cleared). With no owner configured nobody is the owner.
3. **App cookie.** Without principal headers a valid ``__Host-bw`` cookie authenticates the
   owner. This rides out Easy Auth bug #1815, where the principal headers go missing. Neither
   headers nor a valid cookie: **401**.
4. **CSRF.** Every method except ``GET`` and ``HEAD`` (exact case; ``OPTIONS`` included) also
   needs a valid ``__Host-bw`` cookie and a same-origin request: ``Sec-Fetch-Site:
   same-origin`` or, when the browser sends no ``Sec-Fetch-Site``, ``Origin`` equal to
   ``BW_PUBLIC_ORIGIN``. Otherwise **403** (``code: csrf_failed``). When the cookie was missing
   or stale but the principal headers are valid, that 403 carries a fresh cookie, so the SPA
   can retry once.

On a request authenticated by principal headers the app (re)issues the cookie when it is
missing, invalid or past half its lifetime. The cookie is
``__Host-bw=v1.<oid>.<expiry>.<sig>; Max-Age=...; Path=/; Secure; HttpOnly; SameSite=Strict``
with ``sig = base64url(HMAC-SHA256(k, "v1|<oid>|<expiry>"))`` and ``k`` derived from
``BW_COOKIE_KEY``. It is bound to the owner's object ID and expires after
``BW_COOKIE_TTL_HOURS`` (12 by default).

There is no switch that turns any of this off.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast
from urllib.parse import urlsplit

from fastapi import Depends
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from blogwriter.core.errors import ProblemError, problem_response
from blogwriter.core.settings import CoreSettings, normalize_oid, normalize_origin

__all__ = [
    "CLEAR_COOKIE_HEADER",
    "COOKIE_NAME",
    "EXEMPT_PATH",
    "AuthDecision",
    "AuthMiddleware",
    "Authenticator",
    "CookieSigner",
    "IssuedCookie",
    "OwnerPrincipal",
    "Principal",
    "PrincipalError",
    "current_principal",
    "is_same_origin",
    "parse_easy_auth_principal",
    "read_cookie_values",
]

COOKIE_NAME: Final = "__Host-bw"
COOKIE_VERSION: Final = "v1"
_COOKIE_ATTRIBUTES: Final = "Path=/; Secure; HttpOnly; SameSite=Strict"
CLEAR_COOKIE_HEADER: Final = f"{COOKIE_NAME}=; Max-Age=0; {_COOKIE_ATTRIBUTES}"

#: The only route that needs no authentication. It returns status and build SHA only.
EXEMPT_PATH: Final = "/api/health"
#: Methods exempt from the CSRF check, matched case-sensitively (RFC 9110 methods are). Every
#: other method, ``OPTIONS`` and a lowercase ``get`` included, needs the cookie and same origin.
SAFE_METHODS: Final = frozenset({"GET", "HEAD"})

PRINCIPAL_HEADER: Final = "x-ms-client-principal"
PRINCIPAL_ID_HEADER: Final = "x-ms-client-principal-id"
PRINCIPAL_NAME_HEADER: Final = "x-ms-client-principal-name"
PRINCIPAL_IDP_HEADER: Final = "x-ms-client-principal-idp"
#: Identity-provider names Easy Auth uses for Microsoft Entra ID.
ACCEPTED_IDPS: Final = frozenset({"aad", "azureactivedirectory"})
OID_CLAIM_TYPES: Final = frozenset(
    {"http://schemas.microsoft.com/identity/claims/objectidentifier", "oid"}
)
NAME_CLAIM_TYPE: Final = "name"
MAX_PRINCIPAL_HEADER_CHARS: Final = 64 * 1024
DEV_PRINCIPAL_NAME: Final = "Local developer"

#: ASGI scope key holding the authenticated :class:`Principal`.
PRINCIPAL_SCOPE_KEY: Final = "blogwriter.principal"

_KEY_LABEL: Final = b"blogwriter/__Host-bw/v1"
_MIN_KEY_BYTES: Final = 32
_MAX_CLOCK_SKEW_S: Final = 60
_MAX_NAME_CHARS: Final = 256
_BASE64_RE: Final = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")
_COOKIE_RE: Final = re.compile(
    r"v1\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.([1-9][0-9]{0,11})\.([A-Za-z0-9_-]{43})"
)
_METHOD_RE: Final = re.compile(r"[A-Z]{1,16}")
_logger: Final = logging.getLogger("blogwriter.core.auth")

type PrincipalSource = Literal["easy_auth", "cookie", "dev"]


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated owner. ``name`` is unknown (``None``) for cookie-only requests."""

    oid: str
    name: str | None
    source: PrincipalSource


class PrincipalError(ValueError):
    """Easy Auth principal headers are present but malformed or inconsistent."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _single_header(headers: Headers, name: str) -> str | None:
    values = headers.getlist(name)
    if len(values) > 1:
        raise PrincipalError("duplicate_header")
    return values[0] if values else None


def _decode_principal(encoded: str) -> dict[str, object]:
    text = encoded.strip()
    if len(text) > MAX_PRINCIPAL_HEADER_CHARS:
        raise PrincipalError("principal_too_large")
    if not _BASE64_RE.fullmatch(text):
        raise PrincipalError("bad_encoding")
    standard = text.rstrip("=").translate(str.maketrans("-_", "+/"))
    try:
        raw = base64.b64decode(standard + "=" * (-len(standard) % 4), validate=True)
        document: object = json.loads(raw)
    except (binascii.Error, ValueError, RecursionError):
        # RecursionError: deeply nested arrays or objects fit well inside the size cap.
        raise PrincipalError("bad_encoding") from None
    if not isinstance(document, dict):
        raise PrincipalError("bad_document")
    return cast(dict[str, object], document)


def _claims(document: dict[str, object]) -> list[tuple[str, str]]:
    raw_claims = document.get("claims")
    if not isinstance(raw_claims, list):
        raise PrincipalError("bad_claims")
    claims: list[tuple[str, str]] = []
    for item in cast(list[object], raw_claims):
        if not isinstance(item, dict):
            raise PrincipalError("bad_claims")
        claim = cast(dict[str, object], item)
        claim_type, value = claim.get("typ"), claim.get("val")
        if not isinstance(claim_type, str) or not isinstance(value, str):
            raise PrincipalError("bad_claims")
        claims.append((claim_type, value))
    return claims


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()[:_MAX_NAME_CHARS]
    return cleaned or None


def parse_easy_auth_principal(headers: Headers) -> Principal | None:
    """Read the Easy Auth principal, or ``None`` when ``X-MS-CLIENT-PRINCIPAL`` is absent.

    Raises :class:`PrincipalError` when the headers are present but cannot be trusted.
    Without ``X-MS-CLIENT-PRINCIPAL`` the other principal headers are ignored: they carry no
    claims to check them against.
    """
    encoded = _single_header(headers, PRINCIPAL_HEADER)
    if encoded is None:
        return None
    document = _decode_principal(encoded)
    auth_type = document.get("auth_typ")
    if not isinstance(auth_type, str) or auth_type.strip().lower() not in ACCEPTED_IDPS:
        raise PrincipalError("unsupported_idp")
    idp_header = _single_header(headers, PRINCIPAL_IDP_HEADER)
    if idp_header is not None and idp_header.strip().lower() not in ACCEPTED_IDPS:
        raise PrincipalError("unsupported_idp")
    claims = _claims(document)
    oids = {normalize_oid(value) for claim_type, value in claims if claim_type in OID_CLAIM_TYPES}
    oid = oids.pop() if len(oids) == 1 else None
    if oid is None:
        raise PrincipalError("bad_oid")
    id_header = _single_header(headers, PRINCIPAL_ID_HEADER)
    if id_header is not None and normalize_oid(id_header) != oid:
        raise PrincipalError("id_mismatch")
    name_claim = next((value for kind, value in claims if kind == NAME_CLAIM_TYPE), None)
    name = _clean_name(name_claim) or _clean_name(_single_header(headers, PRINCIPAL_NAME_HEADER))
    return Principal(oid=oid, name=name, source="easy_auth")


@dataclass(frozen=True, slots=True)
class IssuedCookie:
    """A freshly signed ``__Host-bw`` value and its expiry (Unix seconds)."""

    value: str
    expires_at: int
    max_age: int

    def set_cookie_header(self) -> str:
        return f"{COOKIE_NAME}={self.value}; Max-Age={self.max_age}; {_COOKIE_ATTRIBUTES}"


class CookieSigner:
    """Issues and verifies ``__Host-bw`` values (HMAC-SHA256 over ``version|oid|expiry``)."""

    def __init__(
        self, key: bytes, *, ttl_seconds: int, clock: Callable[[], float] = time.time
    ) -> None:
        if len(key) < _MIN_KEY_BYTES:
            raise ValueError(f"cookie key must be at least {_MIN_KEY_BYTES} bytes")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        # A purpose-bound subkey: the configured key is never used directly.
        self._key = hmac.new(key, _KEY_LABEL, hashlib.sha256).digest()
        self._ttl = ttl_seconds
        self._clock = clock

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def now(self) -> float:
        return self._clock()

    def _signature(self, oid: str, expires_at: str) -> str:
        message = f"{COOKIE_VERSION}|{oid}|{expires_at}".encode()
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def issue(self, oid: str) -> IssuedCookie:
        """Sign a cookie for ``oid`` (a normalized object ID) valid for the configured TTL."""
        canonical = normalize_oid(oid)
        if canonical is None:
            raise ValueError("oid must be an Entra object ID (a GUID)")
        expires_at = int(self._clock()) + self._ttl
        signature = self._signature(canonical, str(expires_at))
        value = f"{COOKIE_VERSION}.{canonical}.{expires_at}.{signature}"
        return IssuedCookie(value=value, expires_at=expires_at, max_age=self._ttl)

    def verify(self, value: str, *, owner_oid: str | None) -> int | None:
        """The cookie's expiry if it is authentic, unexpired and bound to ``owner_oid``."""
        match = _COOKIE_RE.fullmatch(value)
        if match is None or owner_oid is None:
            return None
        oid, expires_text, signature = match.groups()
        if not hmac.compare_digest(self._signature(oid, expires_text), signature):
            return None
        expires_at = int(expires_text)
        now = self._clock()
        if expires_at <= now or expires_at > now + self._ttl + _MAX_CLOCK_SKEW_S:
            return None
        if not hmac.compare_digest(oid, owner_oid):
            return None
        return expires_at


def read_cookie_values(headers: Headers) -> list[str]:
    """Every ``__Host-bw`` value across all ``Cookie`` headers."""
    values: list[str] = []
    for header in headers.getlist("cookie"):
        for chunk in header.split(";"):
            name, separator, value = chunk.strip().partition("=")
            if separator and name.strip() == COOKIE_NAME:
                values.append(value.strip())
    return values


def is_same_origin(headers: Headers, public_origin: str | None) -> bool:
    """True for a same-origin browser request.

    ``Sec-Fetch-Site`` decides when present (it must be ``same-origin``). Browsers that do not
    send it are checked by ``Origin``, which must equal ``public_origin``.
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.strip().lower() == "same-origin"
    origin = headers.get("origin")
    if origin is None or public_origin is None:
        return False
    return normalize_origin(origin) == public_origin


def _request_host_name(headers: Headers) -> str | None:
    """The lowercase host name in ``Host`` (no port; IPv6 without brackets), else ``None``."""
    values = headers.getlist("host")
    if len(values) != 1:
        return None
    try:
        parts = urlsplit(f"//{values[0].strip()}")
        _ = parts.port  # a malformed port raises ValueError
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return parts.hostname or None


def _is_loopback_host(name: str) -> bool:
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class AuthDecision:
    """Outcome of :meth:`Authenticator.decide`."""

    allowed: bool
    principal: Principal | None = None
    status: int = 200
    code: str | None = None
    reason: str | None = None
    set_cookie: str | None = None
    """A ``Set-Cookie`` header value to send (a fresh cookie, or one that clears it)."""


_DENIAL_DETAILS: Final = {
    "unauthenticated": "Sign in to continue.",
    "invalid_principal": "The sign-in headers are malformed.",
    "not_owner": "This account is not allowed to use this app.",
    "owner_not_configured": "The app has no owner configured.",
    "csrf_failed": "State-changing requests need the app cookie and must come from this origin.",
}


def _deny(
    status: int,
    code: str,
    *,
    reason: str | None = None,
    principal: Principal | None = None,
    set_cookie: str | None = None,
) -> AuthDecision:
    return AuthDecision(
        allowed=False,
        principal=principal,
        status=status,
        code=code,
        reason=reason or code,
        set_cookie=set_cookie,
    )


class Authenticator:
    """The access decision for one request (pure: no I/O)."""

    def __init__(self, settings: CoreSettings, signer: CookieSigner) -> None:
        self._owner_oid = settings.owner_oid
        self._dev_oid = settings.effective_dev_principal_oid
        self._public_origin = settings.public_origin
        self._public_host = (
            urlsplit(settings.public_origin).hostname if settings.public_origin else None
        )
        self._signer = signer

    def decide(self, method: str, path: str, headers: Headers) -> AuthDecision:
        """Decide one request. ``method`` is compared case-sensitively, as HTTP defines it."""
        if path == EXEMPT_PATH:
            return AuthDecision(allowed=True)
        try:
            principal = parse_easy_auth_principal(headers)
        except PrincipalError as exc:
            return _deny(401, "invalid_principal", reason=exc.reason)
        if principal is None and self._dev_oid is not None and self._dev_host_allowed(headers):
            principal = Principal(oid=self._dev_oid, name=DEV_PRINCIPAL_NAME, source="dev")

        cookies = read_cookie_values(headers)
        cookie_expiry: int | None = None
        if len(cookies) == 1:  # duplicates are never trusted
            cookie_expiry = self._signer.verify(cookies[0], owner_oid=self._owner_oid)
        bad_cookie = CLEAR_COOKIE_HEADER if cookies and cookie_expiry is None else None

        if principal is not None:
            if self._owner_oid is None:
                return _deny(403, "owner_not_configured", principal=principal)
            if not hmac.compare_digest(principal.oid, self._owner_oid):
                clear = CLEAR_COOKIE_HEADER if cookies else None
                return _deny(403, "not_owner", principal=principal, set_cookie=clear)
        elif cookie_expiry is not None and self._owner_oid is not None:
            principal = Principal(oid=self._owner_oid, name=None, source="cookie")
        else:
            return _deny(401, "unauthenticated", set_cookie=bad_cookie)

        # From here the principal is the owner.
        refresh = self._refreshed_cookie(principal, cookie_expiry)
        if method not in SAFE_METHODS:
            if cookie_expiry is None:
                return _deny(403, "csrf_failed", reason="csrf_cookie", set_cookie=refresh)
            if not is_same_origin(headers, self._public_origin):
                return _deny(403, "csrf_failed", reason="csrf_origin", set_cookie=refresh)
        return AuthDecision(allowed=True, principal=principal, set_cookie=refresh)

    def _dev_host_allowed(self, headers: Headers) -> bool:
        """The development principal serves a browser on this machine or the configured origin.

        A DNS-rebinding page reaches a local server under its own host name, which it cannot
        change, so checking ``Host`` keeps such a page from reading the app as the owner.
        """
        host = _request_host_name(headers)
        if host is None:
            return False
        return host == self._public_host or _is_loopback_host(host)

    def _refreshed_cookie(self, principal: Principal, cookie_expiry: int | None) -> str | None:
        """A fresh cookie for header-authenticated requests whose cookie is missing or stale."""
        if principal.source == "cookie":
            return None
        if cookie_expiry is not None:
            remaining = cookie_expiry - self._signer.now()
            if remaining >= self._signer.ttl_seconds / 2:
                return None
        return self._signer.issue(principal.oid).set_cookie_header()


class AuthMiddleware:
    """Applies :class:`Authenticator` to every HTTP request; WebSockets are refused."""

    def __init__(self, app: ASGIApp, *, authenticator: Authenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # The app has no WebSocket routes (SSE is the push channel): fail closed.
            _logger.warning("auth_denied", extra={"status": 403, "reason": "websocket"})
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Not upper-cased: routing matches methods case-sensitively, so "get" is not GET here.
        method = str(scope["method"])
        decision = self.authenticator.decide(method, str(scope["path"]), Headers(scope=scope))
        if not decision.allowed:
            extra: dict[str, object] = {
                "status": decision.status,
                "reason": decision.reason,
                "method": method if _METHOD_RE.fullmatch(method) else "OTHER",
            }
            if decision.code == "not_owner" and decision.principal is not None:
                extra["oid"] = decision.principal.oid
            _logger.warning("auth_denied", extra=extra)
            headers = {"Set-Cookie": decision.set_cookie} if decision.set_cookie else None
            response = problem_response(
                decision.status,
                code=decision.code,
                detail=_DENIAL_DETAILS.get(decision.code or ""),
                headers=headers,
            )
            await response(scope, receive, send)
            return

        if decision.principal is not None:
            scope[PRINCIPAL_SCOPE_KEY] = decision.principal
        set_cookie = decision.set_cookie
        if set_cookie is None:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("Set-Cookie", set_cookie)
            await send(message)

        await self.app(scope, receive, send_with_cookie)


async def current_principal(request: Request) -> Principal:
    """FastAPI dependency: the authenticated owner (fails closed if auth did not run).

    ``async`` so FastAPI resolves it on the event loop instead of a worker thread.
    """
    principal = request.scope.get(PRINCIPAL_SCOPE_KEY)
    if not isinstance(principal, Principal):
        raise ProblemError(401, code="unauthenticated", detail=_DENIAL_DETAILS["unauthenticated"])
    return principal


#: Route parameter annotation for the authenticated owner.
OwnerPrincipal = Annotated[Principal, Depends(current_principal)]
