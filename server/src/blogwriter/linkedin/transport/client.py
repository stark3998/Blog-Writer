"""The LinkedIn REST client: one ``httpx.AsyncClient``, fixed hosts, no redirects, no retries.

Every REST call carries ``Authorization: Bearer``, ``Linkedin-Version`` (:data:`API_VERSION`)
and ``X-Restli-Protocol-Version: 2.0.0``. The hosts are constants; the only URL LinkedIn hands
back (an upload URL) must be ``https`` on :data:`UPLOAD_HOSTS` or it is refused before the
token goes anywhere. Redirects are never followed, proxies and ``.netrc`` are ignored
(``trust_env=False``) and the transport never retries (``retries=0``).

**Never retried.** No retry or backoff helper wraps this client. :meth:`LinkedInClient.create_post`
makes exactly one attempt and reports what happened as a :class:`CreateOutcome` instead of
raising: a 5xx or a timeout may still have created the post, so only the owner can decide what
comes next (``publishing -> needs_attention``).

Logs carry the operation name, the HTTP status and the duration only: never URLs (upload URLs
are capabilities), headers, tokens, codes or bodies.

The process-wide client is installed by the transport lifecycle (:func:`install_client`) and
read by routes and job steps with :func:`current_client`; tests install one built on
``httpx.MockTransport`` with :func:`client_installed`.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast
from urllib.parse import quote, unquote, urlsplit

import httpx

from blogwriter.core.logging import register_log_fields
from blogwriter.domain.posts import PublishOutcome
from blogwriter.domain.settings import LINKEDIN_API_VERSION
from blogwriter.linkedin.settings import LinkedInSettings

__all__ = [
    "API_BASE",
    "API_VERSION",
    "AUTHORIZE_URL",
    "INTROSPECT_URL",
    "TOKEN_URL",
    "UPLOAD_HOSTS",
    "USERINFO_URL",
    "CreateOutcome",
    "Introspection",
    "LinkedInClient",
    "LinkedInError",
    "MediaKind",
    "TokenGrant",
    "client_installed",
    "current_client",
    "install_client",
    "uninstall_client",
]

API_BASE: Final = "https://api.linkedin.com/rest"
API_VERSION: Final = LINKEDIN_API_VERSION
USERINFO_URL: Final = "https://api.linkedin.com/v2/userinfo"
AUTHORIZE_URL: Final = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL: Final = "https://www.linkedin.com/oauth/v2/accessToken"  # noqa: S105 - a URL
INTROSPECT_URL: Final = "https://www.linkedin.com/oauth/v2/introspectToken"
UPLOAD_HOSTS: Final = frozenset({"api.linkedin.com", "www.linkedin.com"})
"""Hosts an ``uploadUrl`` from ``initializeUpload`` may point at (it receives the token)."""
RESTLI_PROTOCOL_VERSION: Final = "2.0.0"

_POST_URN_RE: Final = re.compile(r"^urn:li:(?:share|ugcPost):[0-9]{1,30}$")
_MEDIA_URN_RE: Final = {
    "document": re.compile(r"^urn:li:document:[A-Za-z0-9_-]{1,100}$"),
    "image": re.compile(r"^urn:li:image:[A-Za-z0-9_-]{1,100}$"),
}
_SUB_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_STATUS_RE: Final = re.compile(r"^[A-Z][A-Z_]{0,39}$")
_SCOPE_SPLIT_RE: Final = re.compile(r"[,\s]+")
_MAX_TOKEN_CHARS: Final = 4096

type MediaKind = Literal["document", "image"]

_logger: Final = logging.getLogger("blogwriter.linkedin.client")
register_log_fields("linkedin_op", "linkedin_urn")


class LinkedInError(Exception):
    """A LinkedIn call failed. ``code`` is stable and content-free: ``http_<status>``,
    ``timeout``, ``network_error``, ``bad_response``, ``not_configured``, ``upload_url_refused``.

    ``status`` is the HTTP status when LinkedIn answered.
    """

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status

    @property
    def unauthorized(self) -> bool:
        """LinkedIn refused the token (401): the connection needs a reconnect."""
        return self.status == 401

    @property
    def transient(self) -> bool:
        """No answer, a 5xx or a rate limit: worth trying again later (by the owner)."""
        return self.status is None or self.status >= 500 or self.status == 429


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    """How the single ``POST /rest/posts`` ended.

    ``status`` is ``None`` when no answer arrived (timeout, network error); ``urn`` is set only
    for a 201 that named the post; ``error_code`` is ``None`` for a clean 201.
    """

    status: int | None
    urn: str | None
    error_code: str | None

    @property
    def outcome(self) -> PublishOutcome:
        """``posted`` (201 + URN), ``rejected`` (a definite 4xx: nothing was created) or
        ``unknown`` (5xx, timeout, network error, 408, any other answer)."""
        if self.status == 201 and self.urn is not None:
            return "posted"
        if self.status is not None and 400 <= self.status < 500 and self.status != 408:
            return "rejected"
        return "unknown"


@dataclass(frozen=True, slots=True)
class Introspection:
    """LinkedIn's view of a token (``introspectToken``)."""

    active: bool
    status: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TokenGrant:
    """A fresh member token from the authorization-code exchange (never logged or returned)."""

    access_token: str = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...]


def _scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(part for part in _SCOPE_SPLIT_RE.split(value.strip()) if part)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body: object = response.json()
    except ValueError:
        raise LinkedInError("bad_response", response.status_code) from None
    if not isinstance(body, dict):
        raise LinkedInError("bad_response", response.status_code)
    return cast(dict[str, Any], body)


def _check_upload_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        raise LinkedInError("upload_url_refused") from None
    if (
        parts.scheme != "https"
        or host not in UPLOAD_HOSTS
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or len(url) > 4096
        or any(ch.isspace() for ch in url)
    ):
        raise LinkedInError("upload_url_refused")
    return url


def _urn_path(urn: str) -> str:
    return quote(urn, safe="")


class LinkedInClient:
    """Typed LinkedIn calls over one ``httpx.AsyncClient`` (see the module docs)."""

    def __init__(
        self,
        settings: LinkedInSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings if settings is not None else LinkedInSettings()
        timeout = self.settings.timeout_seconds
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            # retries=0: never re-send anything, not even after a failed connect.
            transport=transport if transport is not None else httpx.AsyncHTTPTransport(retries=0),
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ----------------------------------------------------------------------------------------
    # Plumbing
    # ----------------------------------------------------------------------------------------

    @staticmethod
    def _rest_headers(token: str, *, method: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": API_VERSION,
            "X-Restli-Protocol-Version": RESTLI_PROTOCOL_VERSION,
        }
        if method is not None:
            headers["X-RestLi-Method"] = method
        return headers

    async def _send(
        self,
        op: str,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object = None,
        content: bytes | None = None,
        form: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """One request, no retries. Timeouts and network errors become :class:`LinkedInError`."""
        started = time.monotonic()
        try:
            if json_body is not None:
                response = await self._http.request(method, url, headers=headers, json=json_body)
            elif form is not None:
                response = await self._http.request(method, url, headers=headers, data=form)
            else:
                response = await self._http.request(method, url, headers=headers, content=content)
        except httpx.TimeoutException:
            self._log_failure(op, "timeout", started)
            raise LinkedInError("timeout") from None
        except httpx.HTTPError:
            self._log_failure(op, "network_error", started)
            raise LinkedInError("network_error") from None
        _logger.info(
            "linkedin_call",
            extra={
                "linkedin_op": op,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        return response

    @staticmethod
    def _log_failure(op: str, reason: str, started: float) -> None:
        _logger.warning(
            "linkedin_call_failed",
            extra={
                "linkedin_op": op,
                "reason": reason,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )

    def _credentials(self) -> tuple[str, str]:
        client_id = self.settings.client_id
        secret = self.settings.client_secret
        if client_id is None or secret is None:
            raise LinkedInError("not_configured")
        return client_id, secret.get_secret_value()

    # ----------------------------------------------------------------------------------------
    # Posts
    # ----------------------------------------------------------------------------------------

    async def create_post(self, token: str, body: Mapping[str, object]) -> CreateOutcome:
        """``POST /rest/posts`` exactly once. Never raises for LinkedIn's answer or for a lost
        one: the outcome says what happened (see :class:`CreateOutcome`)."""
        started = time.monotonic()
        try:
            response = await self._http.post(
                f"{API_BASE}/posts", headers=self._rest_headers(token), json=dict(body)
            )
        except httpx.TimeoutException:
            self._log_failure("create_post", "timeout", started)
            return CreateOutcome(status=None, urn=None, error_code="timeout")
        except httpx.HTTPError:
            self._log_failure("create_post", "network_error", started)
            return CreateOutcome(status=None, urn=None, error_code="network_error")
        status = response.status_code
        urn: str | None = None
        if status == 201:
            raw = response.headers.get("x-restli-id") or response.headers.get("x-linkedin-id")
            candidate = unquote(raw.strip()) if raw is not None else None
            if candidate is not None and _POST_URN_RE.fullmatch(candidate):
                urn = candidate
        _logger.info(
            "linkedin_call",
            extra={
                "linkedin_op": "create_post",
                "status": status,
                "linkedin_urn": urn,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        if status == 201:
            return CreateOutcome(
                status=201, urn=urn, error_code=None if urn is not None else "urn_missing"
            )
        return CreateOutcome(status=status, urn=None, error_code=f"linkedin_{status}")

    async def update_commentary(self, token: str, urn: str, commentary: str) -> None:
        """``PARTIAL_UPDATE`` of a live post's commentary (one attempt; a POST is never
        retried)."""
        response = await self._send(
            "update_commentary",
            "POST",
            f"{API_BASE}/posts/{_urn_path(urn)}",
            headers=self._rest_headers(token, method="PARTIAL_UPDATE"),
            json_body={"patch": {"$set": {"commentary": commentary}}},
        )
        if response.status_code not in (200, 204):
            raise LinkedInError(f"http_{response.status_code}", response.status_code)

    async def delete_post(self, token: str, urn: str) -> None:
        """``DELETE /rest/posts/{urn}``; a post that is already gone (404, 410) counts as
        deleted."""
        response = await self._send(
            "delete_post",
            "DELETE",
            f"{API_BASE}/posts/{_urn_path(urn)}",
            headers=self._rest_headers(token, method="DELETE"),
        )
        if response.status_code not in (200, 204, 404, 410):
            raise LinkedInError(f"http_{response.status_code}", response.status_code)

    # ----------------------------------------------------------------------------------------
    # Documents and images
    # ----------------------------------------------------------------------------------------

    async def initialize_upload(
        self, token: str, kind: MediaKind, owner_urn: str
    ) -> tuple[str, str]:
        """``POST /rest/{documents|images}?action=initializeUpload``: ``(upload_url, urn)``."""
        response = await self._send(
            "initialize_upload",
            "POST",
            f"{API_BASE}/{kind}s?action=initializeUpload",
            headers=self._rest_headers(token),
            json_body={"initializeUploadRequest": {"owner": owner_urn}},
        )
        if response.status_code != 200:
            raise LinkedInError(f"http_{response.status_code}", response.status_code)
        value = _json_object(response).get("value")
        if not isinstance(value, dict):
            raise LinkedInError("bad_response", response.status_code)
        fields = cast(dict[str, object], value)
        upload_url = fields.get("uploadUrl")
        urn = fields.get(kind)
        if not isinstance(upload_url, str) or not isinstance(urn, str):
            raise LinkedInError("bad_response", response.status_code)
        if not _MEDIA_URN_RE[kind].fullmatch(urn):
            raise LinkedInError("bad_response", response.status_code)
        return _check_upload_url(upload_url), urn

    async def upload_bytes(
        self, upload_url: str, data: bytes, content_type: str, *, token: str | None = None
    ) -> None:
        """``PUT`` the bytes to the upload URL (checked against :data:`UPLOAD_HOSTS` first).

        LinkedIn's upload endpoint expects the member token as well; it is sent only to the
        allowlisted LinkedIn hosts.
        """
        url = _check_upload_url(upload_url)
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        response = await self._send("upload", "PUT", url, headers=headers, content=data)
        if response.status_code not in (200, 201):
            raise LinkedInError(f"http_{response.status_code}", response.status_code)

    async def media_status(self, token: str, kind: MediaKind, urn: str) -> str:
        """The upload's processing status (``AVAILABLE``, ``PROCESSING``, ``WAITING_UPLOAD``,
        ``PROCESSING_FAILED``...). ``UNREADABLE`` when the token may not read it (403): the
        caller then falls back to a fixed wait."""
        response = await self._send(
            "media_status",
            "GET",
            f"{API_BASE}/{kind}s/{_urn_path(urn)}",
            headers=self._rest_headers(token),
        )
        if response.status_code == 403:
            return "UNREADABLE"
        if response.status_code != 200:
            raise LinkedInError(f"http_{response.status_code}", response.status_code)
        status = _json_object(response).get("status")
        if not isinstance(status, str) or not _STATUS_RE.fullmatch(status.upper()):
            raise LinkedInError("bad_response", response.status_code)
        return status.upper()

    # ----------------------------------------------------------------------------------------
    # OAuth and the member
    # ----------------------------------------------------------------------------------------

    async def userinfo(self, token: str) -> str:
        """The member URN (``urn:li:person:<sub>``) from the OpenID userinfo endpoint."""
        response = await self._send(
            "userinfo", "GET", USERINFO_URL, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code != 200:
            raise LinkedInError(f"http_{response.status_code}", response.status_code)
        sub = _json_object(response).get("sub")
        if not isinstance(sub, str) or not _SUB_RE.fullmatch(sub):
            raise LinkedInError("bad_response", response.status_code)
        return f"urn:li:person:{sub}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenGrant:
        """Exchange an authorization code for a member token (``accessToken``)."""
        client_id, secret = self._credentials()
        response = await self._send(
            "token",
            "POST",
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        if response.status_code != 200:
            raise LinkedInError(f"http_{response.status_code}", response.status_code)
        body = _json_object(response)
        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > _MAX_TOKEN_CHARS
            or any(ch.isspace() for ch in token)
            or not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise LinkedInError("bad_response", response.status_code)
        return TokenGrant(
            access_token=token, expires_in=expires_in, scopes=_scopes(body.get("scope"))
        )

    async def introspect(self, token: str) -> Introspection:
        """LinkedIn's verdict on the member token (``introspectToken``)."""
        client_id, secret = self._credentials()
        response = await self._send(
            "introspect",
            "POST",
            INTROSPECT_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            form={"client_id": client_id, "client_secret": secret, "token": token},
        )
        if response.status_code != 200:
            raise LinkedInError(f"http_{response.status_code}", response.status_code)
        body = _json_object(response)
        active = body.get("active")
        if not isinstance(active, bool):
            raise LinkedInError("bad_response", response.status_code)
        status = body.get("status")
        expires = body.get("expires_at")
        expires_at: datetime | None = None
        if isinstance(expires, int) and not isinstance(expires, bool) and expires > 0:
            try:
                expires_at = datetime.fromtimestamp(expires, tz=UTC)
            except (OverflowError, OSError, ValueError):
                expires_at = None
        return Introspection(
            active=active,
            status=status if isinstance(status, str) and len(status) <= 40 else None,
            expires_at=expires_at,
            scopes=_scopes(body.get("scope")),
        )


# --------------------------------------------------------------------------------------------
# The process-wide client
# --------------------------------------------------------------------------------------------

_installed: list[LinkedInClient] = []


def install_client(client: LinkedInClient) -> None:
    """Make ``client`` what :func:`current_client` returns (the transport lifecycle)."""
    _installed[:] = [client]


def uninstall_client(client: LinkedInClient | None = None) -> None:
    """Forget the installed client (only ``client``, when given)."""
    if client is None or (_installed and _installed[0] is client):
        _installed.clear()


def current_client() -> LinkedInClient | None:
    """The installed client, or ``None`` when the transport lifecycle has not run."""
    return _installed[0] if _installed else None


@contextmanager
def client_installed(client: LinkedInClient) -> Generator[LinkedInClient]:
    """Install ``client`` for a ``with`` block (tests)."""
    previous = list(_installed)
    install_client(client)
    try:
        yield client
    finally:
        _installed[:] = previous
