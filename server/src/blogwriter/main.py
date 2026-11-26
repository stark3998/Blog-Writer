"""Production wiring: the one module that composes every package into the running app.

Run it with::

    uvicorn blogwriter.main:app --factory --no-access-log --no-server-header

Composition (start order; shutdown is the reverse):

1. ``db``: opens the Cosmos store (``BW_COSMOS_*``) and verifies every container exists.
2. ``jobs``: the crash-only runner, the scheduler and the app-wide SSE stream
   (``GET /api/events``). A quarantined job raises an out-of-band ``alert_event``.
3. ``ai``: the router, built only when ``BW_AI_*`` endpoints are configured. Its ledger writes
   to the ``usage`` container so the spend brake survives restarts.

Packages never import each other's internals; this module is the only place they meet.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from fastapi import FastAPI

from blogwriter import db, jobs
from blogwriter.ai import AIRouter, AISettings, UsageRecord, create_providers
from blogwriter.ai.providers.auth import entra_token_provider
from blogwriter.core.app import create_app
from blogwriter.core.settings import CoreSettings
from blogwriter.db import Filter, Store
from blogwriter.jobs import JobsLifecycle, QuarantineNotice
from blogwriter.notify import emit_alert

__all__ = ["AILifecycle", "CosmosLedgerSink", "app", "build_app", "quiet_third_party_loggers"]

log = logging.getLogger(__name__)

#: Third-party loggers that log URLs, request options or prompts below WARNING.
QUIET_LOGGERS: Final = (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity",
    "openai",
    "anthropic",
    "httpx",
    "httpx2",
    "httpcore",
)


def quiet_third_party_loggers() -> None:
    """Cap chatty SDK loggers at WARNING (they log URLs and, at DEBUG, full prompts)."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class CosmosLedgerSink:
    """:class:`blogwriter.ai.LedgerSink` over the ``usage`` container (partition key ``/month``).

    ``record`` upserts by ``record_id``, so a retried write never double counts.
    ``spend_between`` sums ``costUsd`` over ``start <= at < end`` across month partitions;
    ``at`` is fixed-width UTC, so string comparison is time comparison.
    """

    CONTAINER: Final = "usage"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def record(self, record: UsageRecord) -> None:
        await self._store.upsert(self.CONTAINER, dict(record.to_document()))

    async def spend_between(self, start: datetime, end: datetime) -> Decimal:
        docs = await self._store.find(
            self.CONTAINER,
            [Filter("at", ">=", _iso(start)), Filter("at", "<", _iso(end))],
        )
        total = Decimal(0)
        for doc in docs:
            cost = doc.body.get("costUsd")
            if isinstance(cost, (int, float)):
                total += Decimal(str(cost))
        return total


def _iso(moment: datetime) -> str:
    """The ledger's fixed-width UTC timestamp format (matches ``UsageRecord.to_document``)."""
    if moment.tzinfo is None:
        raise ValueError("ledger range bounds must be timezone-aware")
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


class AILifecycle:
    """Builds :class:`AIRouter` on ``app.state.ai`` when ``BW_AI_*`` is configured.

    Unconfigured (local development, tests): ``app.state.ai`` is ``None`` and nothing calls a
    model. The router needs ``app.state.store`` for its ledger, so start it after ``db``.
    """

    def __init__(self, settings: AISettings | None = None) -> None:
        self._settings = settings
        self._credential: object | None = None

    async def startup(self, app: FastAPI) -> None:
        settings = self._settings if self._settings is not None else AISettings()
        if not settings.configured:
            app.state.ai = None
            log.info("ai_not_configured")
            return
        store: object = getattr(app.state, "store", None)
        if not isinstance(store, Store):
            raise RuntimeError("blogwriter.ai needs app.state.store; start blogwriter.db first")
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
        self._credential = credential
        providers = create_providers(settings, entra_token_provider(credential))
        app.state.ai = AIRouter.from_settings(settings, providers, ledger=CosmosLedgerSink(store))

    async def shutdown(self, app: FastAPI) -> None:
        router: object = getattr(app.state, "ai", None)
        if isinstance(router, AIRouter):
            await router.aclose()
        credential = self._credential
        self._credential = None
        if credential is not None:
            close = getattr(credential, "close", None)
            if close is not None:
                await close()


async def _on_quarantine(notice: QuarantineNotice) -> None:
    """Out-of-band alert (Azure Monitor emails the owner); ids only, never job content."""
    emit_alert("quarantined", notice.job_id)


def build_app(settings: CoreSettings | None = None) -> FastAPI:
    """The production app: core plus db, jobs (with SSE) and the AI router."""
    quiet_third_party_loggers()
    return create_app(
        settings,
        routers=[jobs.get_router()],
        lifecycles=[db, JobsLifecycle(on_quarantine=_on_quarantine), AILifecycle()],
    )


def app() -> FastAPI:
    """Zero-argument factory for ``uvicorn blogwriter.main:app --factory``."""
    return build_app()
