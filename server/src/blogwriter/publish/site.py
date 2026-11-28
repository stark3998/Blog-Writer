"""The portfolio server in this replica (``BW_PUBLISH_SITE_UPSTREAM``): revalidate, read pages.

When the portfolio runs as a second container of the same Container App (``site_hosts`` in
``infra/v2``), a publish needs no GitHub build: the published-blogs document is the source the
site renders from. After the upsert the job asks the site to drop its cached copies
(``POST /_bw/revalidate``) and then reads the page back from the site directly until the new
version's ``bw-updated-at`` marker shows.

The upstream is loopback only (:func:`blogwriter.core.site.normalize_site_upstream`), so this
client bypasses the egress helper on purpose: the egress helper exists to keep fetches *off*
loopback, and this client can reach nothing else. Its own ``httpx.AsyncClient``: fixed base URL,
timeouts, no redirects, no proxies or environment settings.

Errors are :class:`blogwriter.publish.portfolio.PortfolioError` (``timeout`` and
``unreachable`` are transient, so the runner retries the step; an unexpected status is not).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Final, Self

import httpx

from blogwriter.core.site import normalize_site_upstream
from blogwriter.publish.portfolio import PortfolioError

if TYPE_CHECKING:
    from blogwriter.publish.settings import PublishSettings

__all__ = ["INTERNAL_HEADER", "REVALIDATE_PATH", "LocalSiteClient"]

REVALIDATE_PATH: Final = "/_bw/revalidate"
#: The portfolio answers its internal routes only with this header; the gateway never forwards
#: it (nor any other ``x-`` header) from the internet.
INTERNAL_HEADER: Final = ("x-bw-internal", "1")
DEFAULT_TIMEOUT_SECONDS: Final = 15.0
MAX_PAGE_BYTES: Final = 5 * 1024 * 1024

_logger: Final = logging.getLogger("blogwriter.publish.site")


class LocalSiteClient:
    """Calls to the portfolio server in this replica. Use it as an async context manager."""

    def __init__(
        self,
        upstream: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        base = normalize_site_upstream(upstream)
        if base is None:
            raise ValueError("the local site client needs an upstream")
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"User-Agent": "BlogWriter/2.0 (publish)"},
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls, settings: PublishSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> LocalSiteClient:
        """A client for ``BW_PUBLISH_SITE_UPSTREAM``; ``PortfolioError("not_configured")``
        without one."""
        if settings.site_upstream is None:
            raise PortfolioError("not_configured", transient=False)
        return cls(settings.site_upstream, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def revalidate(self, slugs: Sequence[str]) -> list[str]:
        """Drop the site's cached copies of these articles and of the lists that show them."""
        started = time.monotonic()
        name, value = INTERNAL_HEADER
        try:
            response = await self._client.post(
                REVALIDATE_PATH, json={"slugs": list(slugs)}, headers={name: value}
            )
        except httpx.TimeoutException:
            raise self._failed("revalidate", "timeout", True, None, started) from None
        except httpx.HTTPError:
            raise self._failed("revalidate", "unreachable", True, None, started) from None
        if response.status_code >= 500:
            raise self._failed("revalidate", "unavailable", True, response.status_code, started)
        if response.status_code != 200:
            raise self._failed(
                "revalidate", "unexpected_status", False, response.status_code, started
            )
        return list(slugs)

    async def page(self, path: str) -> tuple[int, bytes] | None:
        """``(status, body)`` of ``GET path`` on the site, or ``None`` when it cannot be
        reached (the caller keeps polling). Bodies over 5 MiB are cut there."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("page paths are site-relative")
        try:
            async with self._client.stream("GET", path) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_PAGE_BYTES:
                        break
                return response.status_code, bytes(body[:MAX_PAGE_BYTES])
        except httpx.HTTPError:
            return None

    def _failed(
        self, call: str, code: str, transient: bool, status: int | None, started: float
    ) -> PortfolioError:
        _logger.warning(
            "portfolio_call_failed",
            extra={
                "portfolio_call": call,
                "portfolio_code": code,
                "http_status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return PortfolioError(code, transient=transient, status=status)
