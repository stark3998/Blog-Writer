"""Security headers on every HTTP response.

* SPA responses (every path outside ``/api``) get the strict SPA Content-Security-Policy
  (:data:`SPA_CSP`): no inline scripts or styles, no third-party origins, no framing.
* API responses get :data:`API_CSP` (``default-src 'none'``) and ``Cache-Control: no-store``.
* All responses get ``X-Content-Type-Options: nosniff``, ``Referrer-Policy: no-referrer``,
  ``Permissions-Policy`` (powerful features off), ``X-Frame-Options: DENY`` and
  ``Cross-Origin-Resource-Policy: same-origin``; ``env='prod'`` adds HSTS.

Headers a route sets itself win (for example a preview route with its own CSP, or an SSE
route with its own ``Cache-Control``).
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "API_CSP",
    "HSTS",
    "PERMISSIONS_POLICY",
    "SPA_CSP",
    "SecurityHeadersMiddleware",
    "is_api_path",
]

SPA_CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'; worker-src 'self'; manifest-src 'self'"
)
API_CSP: Final = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
PERMISSIONS_POLICY: Final = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), geolocation=(), "
    "gyroscope=(), magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=()"
)
HSTS: Final = "max-age=63072000; includeSubDomains"

_COMMON: Final = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
_SPA: Final = (("Content-Security-Policy", SPA_CSP),)
_API: Final = (("Content-Security-Policy", API_CSP), ("Cache-Control", "no-store"))


def is_api_path(path: str) -> bool:
    """True for ``/api`` and everything below it."""
    return path == "/api" or path.startswith("/api/")


class SecurityHeadersMiddleware:
    """Adds the security headers to each ``http.response.start`` (missing ones only)."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        specific = _API if is_api_path(str(scope.get("path", ""))) else _SPA

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in (*_COMMON, *specific):
                    headers.setdefault(name, value)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
