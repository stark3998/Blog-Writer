"""Articles settings (environment prefix ``BW_ARTICLES_``).

``BW_ARTICLES_KEEP_VERSIONS``
    How many stored versions (``article_version`` documents) an article keeps, newest first
    (default 50). Pinned versions are kept on top of these: the pipeline's gated version
    (calibration), the version of the current gate report and the last published version.
"""

from __future__ import annotations

import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ArticlesSettings", "get_settings"]


class ArticlesSettings(BaseSettings):
    """Settings for ``blogwriter.articles``. Defaults are the production values."""

    model_config = SettingsConfigDict(
        env_prefix="BW_ARTICLES_", env_ignore_empty=True, extra="forbid", frozen=True
    )

    keep_versions: int = Field(default=50, ge=1, le=1000)
    """Stored versions kept per article, newest first, plus the pinned ones."""


@functools.cache
def get_settings() -> ArticlesSettings:
    """The process's settings, read once (tests call ``get_settings.cache_clear()``)."""
    return ArticlesSettings()
