"""Signals settings (environment prefix ``BW_SIGNALS_``).

``BW_SIGNALS_MAX_ITEMS_PER_FEED`` (50)
    Items read from one feed per crawl (newest first).
``BW_SIGNALS_MAX_AGE_DAYS`` (14)
    Dated feed items older than this are dropped before triage (undated items are kept).
``BW_SIGNALS_TRIAGE_BATCH`` (20)
    Items per TRIAGE call.
``BW_SIGNALS_MIN_RELEVANCE`` (0.5)
    Items triaged below this relevance never become ideas.
``BW_SIGNALS_FAILING_AFTER`` (5)
    Consecutive crawl errors after which a feed source turns ``failing``.

Every value has a safe default; nothing here is a secret. Build the settings once with
:func:`get_settings` (cached); tests construct :class:`SignalsSettings` themselves.
"""

from __future__ import annotations

import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SignalsSettings", "get_settings"]


class SignalsSettings(BaseSettings):
    """Settings for ``blogwriter.signals``."""

    model_config = SettingsConfigDict(
        env_prefix="BW_SIGNALS_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    max_items_per_feed: int = Field(default=50, ge=1, le=200)
    max_age_days: int = Field(default=14, ge=1, le=365)
    triage_batch: int = Field(default=20, ge=1, le=50)
    min_relevance: float = Field(default=0.5, ge=0, le=1)
    failing_after: int = Field(default=5, ge=1, le=100)


@functools.cache
def get_settings() -> SignalsSettings:
    """The process-wide settings (read from the environment once)."""
    return SignalsSettings()
