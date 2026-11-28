"""Analytics settings (environment prefix ``BW_ANALYTICS_``).

``BW_ANALYTICS_IMPORT_MAX_BYTES``
    The largest LinkedIn export upload ``POST /api/analytics/imports`` accepts (default 5 MiB).
``BW_ANALYTICS_IMPORT_BETA``
    The LinkedIn export import is a **beta** feature and is off by default (``false``): the
    parser follows an assumed layout (``blogwriter.analytics.imports``) until a real export
    sample confirms it, so the upload route answers 503 ``import_unavailable`` until this is
    ``true``. Listing earlier imports and the snapshot form always work.
"""

from __future__ import annotations

import functools
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DEFAULT_IMPORT_MAX_BYTES", "AnalyticsSettings", "analytics_settings"]

DEFAULT_IMPORT_MAX_BYTES: Final = 5_242_880


class AnalyticsSettings(BaseSettings):
    """Settings for ``blogwriter.analytics``. Defaults are safe: the beta import is off."""

    model_config = SettingsConfigDict(
        env_prefix="BW_ANALYTICS_", env_ignore_empty=True, extra="forbid", frozen=True
    )

    import_max_bytes: int = Field(default=DEFAULT_IMPORT_MAX_BYTES, ge=1024, le=50 * 1024 * 1024)
    """Upload cap for one export file, in bytes."""
    import_beta: bool = False
    """Turn on the beta LinkedIn export import (assumed format; see ``analytics.imports``)."""


@functools.cache
def analytics_settings() -> AnalyticsSettings:
    """The process's settings, read once (tests build their own or call ``cache_clear()``)."""
    return AnalyticsSettings()
