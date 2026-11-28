"""FastAPI dependencies of the transport routes: settings and the installed LinkedIn client."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from blogwriter.core.errors import ProblemError
from blogwriter.core.settings import CoreSettings, settings_from_request
from blogwriter.linkedin.settings import LinkedInSettings, linkedin_settings
from blogwriter.linkedin.transport.client import LinkedInClient, current_client

__all__ = [
    "CoreSettingsDep",
    "LinkedInClientDep",
    "LinkedInSettingsDep",
    "get_linkedin_client",
    "get_linkedin_settings",
    "not_configured",
]


async def get_linkedin_settings(request: Request) -> LinkedInSettings:
    """``app.state.linkedin_settings`` (set by the transport lifecycle or a test), else the
    environment's."""
    value: object = getattr(request.app.state, "linkedin_settings", None)
    if isinstance(value, LinkedInSettings):
        return value
    return linkedin_settings()


async def get_linkedin_client() -> LinkedInClient:
    """The installed client; 503 ``linkedin_unavailable`` before the lifecycle ran."""
    client = current_client()
    if client is None:
        raise ProblemError(
            503,
            code="linkedin_unavailable",
            detail="LinkedIn is not running yet. Try again in a moment.",
            headers={"Retry-After": "5"},
        )
    return client


def not_configured() -> ProblemError:
    """503 ``linkedin_not_configured``: the LinkedIn app credentials are not set."""
    return ProblemError(
        503,
        code="linkedin_not_configured",
        detail="LinkedIn is not set up on the server. Use the ready-to-post kit meanwhile.",
        headers={"Retry-After": "5"},
    )


CoreSettingsDep = Annotated[CoreSettings, Depends(settings_from_request)]
LinkedInSettingsDep = Annotated[LinkedInSettings, Depends(get_linkedin_settings)]
LinkedInClientDep = Annotated[LinkedInClient, Depends(get_linkedin_client)]
