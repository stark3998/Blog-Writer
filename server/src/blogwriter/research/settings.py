"""Research settings (environment prefix ``BW_RESEARCH_``).

``BW_RESEARCH_WEBIQ_ENDPOINT``
    Base URL of the Web IQ runtime API. Microsoft documents one public endpoint,
    ``https://api.microsoft.ai`` (the Browse call is ``POST <base>/v3/browse``; a base that
    already ends in ``/v3`` or ``/v3/browse`` is accepted as well). Unset: Web IQ is off and every
    fetch uses the egress fallback.
``BW_RESEARCH_WEBIQ_SCOPE``
    The Entra scope of the managed-identity token, ``https://api.microsoft.ai/.default``. Required
    with the endpoint: an endpoint without a scope leaves Web IQ off (logged at startup) rather
    than failing the boot, because the egress fallback is fully functional.
``BW_RESEARCH_WEBIQ_QPS``
    Client-side rate limit for Browse calls (the AppID's default limit is 15 queries per second).
``BW_RESEARCH_MAX_SOURCES``
    Sources fetched and extracted per run (owner URLs first, then allowlisted ones).
``BW_RESEARCH_MAX_TOOL_CALLS``
    ``max_tool_calls`` of each ``web_search`` pass.
``BW_RESEARCH_FETCH_TIMEOUT_SECONDS``
    Budget of one fetch (Web IQ, including a pending live crawl; the egress fetch gets its own).
``BW_RESEARCH_PASSAGE_MAX_CHARS``
    The passage size kept in memory for one source (Web IQ Browse returns at most 3,000).
``BW_RESEARCH_QUICK_TIMEOUT_SECONDS``
    Deadline of a synchronous quick search (the platform cuts requests at about 240 s).

Nothing here is a secret: Web IQ authenticates with the app's managed identity (no API key).
"""

from __future__ import annotations

import functools
import re
from typing import Final
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DEFAULT_WEBIQ_SCOPE", "ResearchSettings", "research_settings"]

DEFAULT_WEBIQ_SCOPE: Final = "https://api.microsoft.ai/.default"
"""The documented Entra scope for Web IQ runtime calls (for the owner's configuration notes)."""

_SCOPE_RE: Final = re.compile(
    r"^(?:https|api)://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{1,500}/\.default$"
)


class ResearchSettings(BaseSettings):
    """Settings for ``blogwriter.research``. Defaults are safe: Web IQ off, egress only."""

    model_config = SettingsConfigDict(
        env_prefix="BW_RESEARCH_", env_ignore_empty=True, extra="forbid", frozen=True
    )

    webiq_endpoint: str | None = None
    webiq_scope: str | None = None
    webiq_qps: float = Field(default=15.0, gt=0, le=1000)
    max_sources: int = Field(default=12, ge=1, le=40)
    max_tool_calls: int = Field(default=8, ge=1, le=50)
    fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    passage_max_chars: int = Field(default=3000, ge=200, le=20_000)
    quick_timeout_seconds: float = Field(default=90.0, gt=0, le=230)

    @field_validator("webiq_endpoint")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        parts = urlsplit(text)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or any(char.isspace() for char in text)
        ):
            raise ValueError("webiq_endpoint must be an https base URL without query or fragment")
        return text.rstrip("/")

    @field_validator("webiq_scope")
    @classmethod
    def _check_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _SCOPE_RE.fullmatch(text):
            raise ValueError("webiq_scope must be an Entra scope ending in /.default")
        return text

    @property
    def webiq_configured(self) -> bool:
        """Both the endpoint and the scope are set (Web IQ is used, with egress as fallback)."""
        return self.webiq_endpoint is not None and self.webiq_scope is not None

    @property
    def browse_url(self) -> str | None:
        """The full ``POST`` URL of the Browse API, or ``None`` when Web IQ is off."""
        if self.webiq_endpoint is None:
            return None
        base = self.webiq_endpoint
        if base.endswith("/v3/browse"):
            return base
        if base.endswith("/v3"):
            return f"{base}/browse"
        return f"{base}/v3/browse"


@functools.cache
def research_settings() -> ResearchSettings:
    """The process-wide settings (read once; tests call ``research_settings.cache_clear()``)."""
    return ResearchSettings()
