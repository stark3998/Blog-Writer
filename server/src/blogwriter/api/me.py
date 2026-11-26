"""``GET /api/me``: the signed-in owner (object ID and display name).

``name`` comes from the Easy Auth principal and is ``null`` when the request was
authenticated by the ``__Host-bw`` cookie alone (principal headers missing, bug #1815).
A successful call also refreshes the app cookie, so the SPA can call it to recover from a
``csrf_failed`` 403 before retrying.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from blogwriter.core.auth import OwnerPrincipal

__all__ = ["MeResponse", "get_router"]


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oid: str
    name: str | None


async def me(principal: OwnerPrincipal) -> MeResponse:
    return MeResponse(oid=principal.oid, name=principal.name)


def get_router() -> APIRouter:
    router = APIRouter(tags=["system"])
    router.add_api_route(
        "/api/me",
        me,
        methods=["GET"],
        response_model=MeResponse,
        summary="The signed-in owner",
    )
    return router
