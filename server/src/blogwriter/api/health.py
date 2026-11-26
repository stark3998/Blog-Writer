"""``GET /api/health``: the only unauthenticated route. Returns status and build SHA only.

It checks the process and nothing else (no store, model or network calls), so the
readiness/startup probes never depend on downstream services.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from blogwriter.core.auth import EXEMPT_PATH
from blogwriter.core.settings import CoreSettings, settings_from_request

__all__ = ["HealthResponse", "get_router"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]
    sha: str


async def health(
    settings: Annotated[CoreSettings, Depends(settings_from_request)],
) -> HealthResponse:
    return HealthResponse(status="ok", sha=settings.build_sha)


def get_router() -> APIRouter:
    router = APIRouter(tags=["system"])
    router.add_api_route(
        EXEMPT_PATH,
        health,
        methods=["GET"],
        response_model=HealthResponse,
        summary="Liveness: status and build SHA (no authentication)",
    )
    return router
