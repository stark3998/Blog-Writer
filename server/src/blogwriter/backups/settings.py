"""Backup settings (env prefix ``BW_BACKUPS_``).

``BW_BACKUPS_PAGE_SIZE``
    Documents read per store query while dumping a container (default 200). Dumps page with a
    keyset on ``id``, so the page size bounds memory and request size, not correctness.

The v1 migration CLI (:mod:`blogwriter.backups.migrate_v1`) has its own settings
(``BW_MIGRATE_V1_*``) so the app never reads the v1 account's key.
"""

from __future__ import annotations

import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["BackupsSettings", "backups_settings"]


class BackupsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BW_BACKUPS_", frozen=True)

    page_size: int = Field(default=200, ge=1, le=1000)


@functools.cache
def backups_settings() -> BackupsSettings:
    """The process-wide settings from the environment (built once, on first use)."""
    return BackupsSettings()
