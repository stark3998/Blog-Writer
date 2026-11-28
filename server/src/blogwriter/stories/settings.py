"""Stories settings (env prefix ``BW_STORIES_``; contract section 1.10).

``BW_STORIES_INTAKE_MAX_CHARS`` (8000)
    The longest raw text the intake routes accept (``redact``, ``check``).
``BW_STORIES_SEED_MAX_CHARS`` (60000)
    The longest resume or portfolio text ``POST /api/stories/seed`` accepts (at most the
    ``SeedText`` document limit).
"""

from __future__ import annotations

import functools
from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.domain.ephemeral import MAX_SEED_CHARS

__all__ = ["INTAKE_HARD_MAX_CHARS", "StoriesSettings", "get_settings"]

INTAKE_HARD_MAX_CHARS: Final = 20_000
"""The ceiling for ``intake_max_chars`` (one anonymizer call stays small)."""


class StoriesSettings(BaseSettings):
    """Settings for ``blogwriter.stories``. The defaults are the production values."""

    model_config = SettingsConfigDict(
        env_prefix="BW_STORIES_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    intake_max_chars: int = Field(default=8000, ge=200, le=INTAKE_HARD_MAX_CHARS)
    seed_max_chars: int = Field(default=MAX_SEED_CHARS, ge=1000, le=MAX_SEED_CHARS)


@functools.cache
def get_settings() -> StoriesSettings:
    """The process-wide settings, read from the environment once."""
    return StoriesSettings()
