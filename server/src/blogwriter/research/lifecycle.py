"""Startup and shutdown of the research fetcher (``app.state.fetcher``).

``startup`` always installs a :class:`~blogwriter.research.fetch.FallbackFetcher`:

- With ``BW_RESEARCH_WEBIQ_ENDPOINT`` and ``BW_RESEARCH_WEBIQ_SCOPE`` set, Web IQ Browse is tried
  first, with a managed-identity token (``DefaultAzureCredential``; on Azure Container Apps the
  user-assigned identity named by ``AZURE_CLIENT_ID``). The token is fetched lazily, on the first
  fetch, so startup never waits on the identity endpoint.
- Otherwise (or with an endpoint but no scope) only the egress fetcher is used, and
  ``research_webiq_not_configured`` is logged with the reason.

The egress fetcher reads ``app.state.egress`` at fetch time (set by the integrator's core
services lifecycle), so the lifecycle order between the two does not matter.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from fastapi import FastAPI

from blogwriter.domain.ports import HttpGetter, StateServices
from blogwriter.research.fetch import EgressFetcher, FallbackFetcher, WebIqFetcher
from blogwriter.research.settings import ResearchSettings, research_settings

__all__ = ["ResearchLifecycle", "webiq_state"]

_logger: Final = logging.getLogger("blogwriter.research.lifecycle")
WEBIQ_CONNECT_TIMEOUT_SECONDS: Final = 5.0


class ResearchLifecycle:
    """Puts the research fetcher on ``app.state.fetcher`` (and closes it on shutdown)."""

    def __init__(
        self,
        settings: ResearchSettings | None = None,
        *,
        credential: AsyncTokenCredential | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._transport = transport
        self._owned_credential: AsyncTokenCredential | None = None
        self._fetcher: FallbackFetcher | None = None

    async def startup(self, app: FastAPI) -> None:
        settings = self._settings if self._settings is not None else research_settings()

        def egress() -> HttpGetter:
            return StateServices(app).egress

        fallback = EgressFetcher(
            resolve=egress,
            timeout=settings.fetch_timeout_seconds,
            passage_max_chars=settings.passage_max_chars,
        )
        webiq = self._webiq(settings)
        fetcher = FallbackFetcher(webiq, fallback)
        self._fetcher = fetcher
        app.state.fetcher = fetcher

    def _webiq(self, settings: ResearchSettings) -> WebIqFetcher | None:
        browse_url = settings.browse_url
        scope = settings.webiq_scope
        if browse_url is None or scope is None:
            reason = "scope_missing" if browse_url is not None else "endpoint_missing"
            if browse_url is None and scope is None:
                reason = "unset"
            _logger.info("research_webiq_not_configured", extra={"reason": reason})
            return None
        from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

        credential = self._credential
        if credential is None:
            credential = DefaultAzureCredential()
            self._owned_credential = credential
        timeout = httpx.Timeout(
            settings.fetch_timeout_seconds,
            connect=min(WEBIQ_CONNECT_TIMEOUT_SECONDS, settings.fetch_timeout_seconds),
        )
        client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=self._transport
        )
        _logger.info("research_webiq_configured")
        return WebIqFetcher(
            browse_url=browse_url,
            token=get_bearer_token_provider(credential, scope),
            client=client,
            qps=settings.webiq_qps,
            timeout=settings.fetch_timeout_seconds,
            passage_max_chars=settings.passage_max_chars,
        )

    async def shutdown(self, app: FastAPI) -> None:
        if getattr(app.state, "fetcher", None) is self._fetcher:
            app.state.fetcher = None
        fetcher, self._fetcher = self._fetcher, None
        if fetcher is not None:
            await fetcher.aclose()
        credential, self._owned_credential = self._owned_credential, None
        if credential is not None:
            await credential.close()


def webiq_state(app: FastAPI) -> str:
    """``off``, ``on`` or ``degraded`` (circuit open) for ``/api/system/status``; ``off`` when
    the research lifecycle did not run."""
    fetcher: object = getattr(app.state, "fetcher", None)
    if isinstance(fetcher, FallbackFetcher):
        return fetcher.state
    return "off"
