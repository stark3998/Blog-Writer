"""The app factory: core middleware, core routes, package routers and lifecycles in one place.

Middleware, outermost first::

    request id -> access log -> security headers -> error boundary -> auth -> routes

* request id: a fresh id per request (log context + ``X-Request-Id``).
* access log: one JSON line per request (method, route template, status, duration).
* security headers: CSP, nosniff, Referrer-Policy, Permissions-Policy, HSTS in prod.
* error boundary: unexpected exceptions become a generic 500 problem+json (logged without
  their message), still wrapped by the security headers and the access log.
* auth: Easy Auth principal / ``__Host-bw`` cookie, owner check, CSRF. Every route except
  ``GET /api/health`` is behind it.

Routes: ``/api/health`` and ``/api/me`` (:mod:`blogwriter.api`), then each package router in
order, then the SPA catch-all when ``BW_SPA_DIR`` is set. OpenAPI is served at
``/api/openapi.json`` (authenticated); the Swagger/ReDoc pages are off (they load scripts from
a CDN the CSP forbids).

Lifecycles start in order after the loop watchdog and stop in reverse order before it. A
failed startup stops the lifecycles already started and aborts the boot. Shutdown errors and
timeouts are logged and do not stop the remaining shutdowns (the app is crash-only anyway).

Run it with ``uvicorn blogwriter.core.app:app --factory --no-access-log --no-server-header``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import types
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final, Protocol

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.middleware import Middleware

from blogwriter.api import health, me
from blogwriter.core.auth import Authenticator, AuthMiddleware, CookieSigner
from blogwriter.core.errors import ErrorBoundaryMiddleware, exception_handlers
from blogwriter.core.logging import (
    AccessLogMiddleware,
    RequestIdMiddleware,
    configure_logging,
    describe_exception,
)
from blogwriter.core.security_headers import SecurityHeadersMiddleware
from blogwriter.core.settings import ACA_MARKER_ENV, MIN_COOKIE_KEY_BYTES, CoreSettings
from blogwriter.core.spa import SpaFiles
from blogwriter.core.watchdog import LoopWatchdog

__all__ = ["OPENAPI_URL", "SHUTDOWN_TIMEOUT_S", "Lifecycle", "app", "create_app"]

OPENAPI_URL: Final = "/api/openapi.json"
#: Upper bound for each lifecycle's ``shutdown``.
SHUTDOWN_TIMEOUT_S: Final = 10.0

_logger: Final = logging.getLogger("blogwriter.core.app")


class Lifecycle(Protocol):
    """Startup/shutdown hooks of a package (a module with these functions also qualifies)."""

    async def startup(self, app: FastAPI) -> None: ...

    async def shutdown(self, app: FastAPI) -> None: ...


def _lifecycle_name(lifecycle: Lifecycle) -> str:
    if isinstance(lifecycle, types.ModuleType):
        return lifecycle.__name__
    return type(lifecycle).__qualname__


def _require_prod_invariants(settings: CoreSettings) -> None:
    # CoreSettings validates these too; this catches settings built with model_construct().
    if settings.env != "prod":
        if os.environ.get(ACA_MARKER_ENV):
            # The guard that keeps BW_ENV=local (and so the dev principal) out of a deployment.
            raise RuntimeError(
                f"BW_ENV must be 'prod' inside Azure Container Apps ({ACA_MARKER_ENV} is set); "
                "refusing to start"
            )
        return
    if settings.owner_oid is None or settings.cookie_key is None or settings.public_origin is None:
        raise RuntimeError(
            "env='prod' needs BW_OWNER_OID, BW_COOKIE_KEY and BW_PUBLIC_ORIGIN; refusing to start"
        )
    if not settings.public_origin.startswith("https://"):
        raise RuntimeError("env='prod' needs an https BW_PUBLIC_ORIGIN; refusing to start")


def _cookie_key(settings: CoreSettings) -> bytes:
    if settings.cookie_key is not None:
        return settings.cookie_key.get_secret_value().encode()
    # Outside prod only: a per-process key, so cookies do not survive a restart.
    _logger.info("cookie_key_ephemeral", extra={"env": settings.env})
    return secrets.token_bytes(MIN_COOKIE_KEY_BYTES)


def _check_unique_routes(routers: Sequence[APIRouter]) -> None:
    seen: set[tuple[str, str]] = set()
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods or ()):
                key = (method, route.path)
                if key in seen:
                    raise ValueError(f"duplicate route: {method} {route.path}")
                seen.add(key)


async def _shutdown(app: FastAPI, lifecycle: Lifecycle) -> None:
    try:
        async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
            await lifecycle.shutdown(app)
    except Exception as exc:
        _logger.error(
            "lifecycle_shutdown_failed",
            extra={"lifecycle": _lifecycle_name(lifecycle), **describe_exception(exc)},
        )


def _lifespan(
    settings: CoreSettings, watchdog: LoopWatchdog, lifecycles: Sequence[Lifecycle]
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        watchdog.start()
        started: list[Lifecycle] = []
        try:
            for lifecycle in lifecycles:
                try:
                    await lifecycle.startup(app)
                except Exception as exc:
                    _logger.error(
                        "lifecycle_startup_failed",
                        extra={"lifecycle": _lifecycle_name(lifecycle), **describe_exception(exc)},
                    )
                    raise
                started.append(lifecycle)
            _logger.info(
                "app_started",
                extra={"env": settings.env, "sha": settings.build_sha, "count": len(started)},
            )
            yield
        finally:
            for lifecycle in reversed(started):
                await _shutdown(app, lifecycle)
            await watchdog.stop()
            _logger.info("app_stopped", extra={"sha": settings.build_sha})

    return lifespan


def create_app(
    settings: CoreSettings | None = None,
    *,
    routers: Sequence[APIRouter] = (),
    lifecycles: Sequence[Lifecycle] = (),
) -> FastAPI:
    """Build the FastAPI app. ``settings`` default to ``CoreSettings()`` (the environment).

    ``routers`` are included in order after the core routes; each must carry its full paths
    (``APIRouter(prefix="/api/...")``). ``lifecycles`` are started in order and stopped in
    reverse order.
    """
    if settings is None:
        settings = CoreSettings()
    _require_prod_invariants(settings)
    configure_logging()
    if settings.dev_principal_oid is not None and settings.effective_dev_principal_oid is None:
        _logger.warning("dev_principal_ignored", extra={"env": settings.env})

    signer = CookieSigner(_cookie_key(settings), ttl_seconds=settings.cookie_ttl_seconds)
    watchdog = LoopWatchdog(threshold_ms=settings.watchdog_threshold_ms)
    spa = SpaFiles(settings.spa_dir) if settings.spa_dir is not None else None
    all_routers = [health.get_router(), me.get_router(), *routers]
    _check_unique_routes(all_routers)

    application = FastAPI(
        title="Blog Writer",
        version="2",
        openapi_url=OPENAPI_URL,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
        lifespan=_lifespan(settings, watchdog, tuple(lifecycles)),
        middleware=[
            Middleware(RequestIdMiddleware),
            Middleware(AccessLogMiddleware),
            Middleware(SecurityHeadersMiddleware, hsts=settings.is_prod),
            Middleware(ErrorBoundaryMiddleware),
            Middleware(AuthMiddleware, authenticator=Authenticator(settings, signer)),
        ],
        exception_handlers=exception_handlers(),
        # Native OpenTelemetry would record exception messages and request details.
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )
    application.state.settings = settings
    application.state.watchdog = watchdog
    for router in all_routers:
        application.include_router(router)
    if spa is not None:
        application.include_router(spa.router())
    return application


def app() -> FastAPI:
    """Zero-argument factory for ``uvicorn blogwriter.core.app:app --factory``."""
    return create_app()
