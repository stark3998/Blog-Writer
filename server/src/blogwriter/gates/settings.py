"""Gates settings (environment prefix ``BW_GATES_``).

``BW_GATES_MAX_LINKS_CHECKED`` (25)
    Gate 5 resolves at most this many distinct https links per article (egress GET); links
    beyond the cap are an overridable blocking finding (``links-unchecked``).
``BW_GATES_LINK_TIMEOUT_SECONDS`` (10)
    Per link.
``BW_GATES_NOVELTY_CORPUS`` (50)
    Gate 4 compares the article's embedding with the last N live articles.
``BW_GATES_ATLAS_URL`` (unset)
    https URL of the MITRE ATLAS data file (``ATLAS.yaml``), fetched through egress by
    ``gates.standards-refresh``. Unset: the shipped seed list only.
``BW_GATES_SITE_ORIGIN`` (unset)
    The owner's site (``https://example.com``). Unset: links to the site are judged like any
    other domain in gate 7. Set it to the same value as ``BW_PUBLISH_SITE_ORIGIN``.
"""

from __future__ import annotations

import functools
from typing import Final
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.domain.base import check_https_url

__all__ = ["DEFAULT_LINK_TIMEOUT_SECONDS", "GatesSettings", "gates_settings"]

DEFAULT_LINK_TIMEOUT_SECONDS: Final = 10.0


class GatesSettings(BaseSettings):
    """Settings for ``blogwriter.gates``. Defaults are safe: every gate still runs."""

    model_config = SettingsConfigDict(
        env_prefix="BW_GATES_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    max_links_checked: int = Field(default=25, ge=0, le=200)
    link_timeout_seconds: float = Field(default=DEFAULT_LINK_TIMEOUT_SECONDS, gt=0, le=60)
    novelty_corpus: int = Field(default=50, ge=1, le=200)
    atlas_url: str | None = None
    site_origin: str | None = None

    @field_validator("atlas_url")
    @classmethod
    def _https_atlas(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return check_https_url(value.strip())

    @field_validator("site_origin")
    @classmethod
    def _origin_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = check_https_url(value.strip().rstrip("/"))
        parts = urlsplit(text)
        if parts.path or parts.query or parts.fragment:
            raise ValueError("the site origin is scheme and host only, e.g. https://example.com")
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
        return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"

    @property
    def site_host(self) -> str | None:
        """The site origin's host name (lowercase), if configured."""
        if self.site_origin is None:
            return None
        return urlsplit(self.site_origin).hostname


@functools.cache
def gates_settings() -> GatesSettings:
    """The process-wide settings, read from the environment once (tests: ``cache_clear()``)."""
    return GatesSettings()
