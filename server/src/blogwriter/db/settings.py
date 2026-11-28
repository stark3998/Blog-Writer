"""Cosmos DB settings (env prefix ``BW_COSMOS_``).

``BW_COSMOS_ENDPOINT``
    The account endpoint, for example
    ``https://cosmos-blogwriter-v2-7c3167.documents.azure.com:443/``.
    Unset by default: nothing connects anywhere until it is configured, and
    :func:`blogwriter.db.startup` refuses to start without it (no silent in-memory fallback).
``BW_COSMOS_DATABASE``
    The database name. Defaults to ``blog-writer``.

There is no key setting: the account has key auth disabled, so the store always authenticates
with Entra ID (``DefaultAzureCredential``; on Azure Container Apps the managed identity named by
``AZURE_CLIENT_ID``).
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.db.base import validate_id

__all__ = ["DEFAULT_DATABASE", "CosmosSettings", "validate_endpoint"]

DEFAULT_DATABASE: Final = "blog-writer"


def validate_endpoint(value: str) -> str:
    """Return the endpoint if it is an ``https://host[:port]/`` URL, else raise ``ValueError``."""
    endpoint = value.strip()
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("Cosmos endpoint must be an https:// URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Cosmos endpoint must not contain credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise ValueError("Cosmos endpoint must be the account URL without a path")
    return endpoint


class CosmosSettings(BaseSettings):
    """Where the store connects. Defaults are safe for local tests (nothing configured)."""

    model_config = SettingsConfigDict(env_prefix="BW_COSMOS_", frozen=True)

    endpoint: str | None = None
    database: str = DEFAULT_DATABASE

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_endpoint(value)

    @field_validator("database")
    @classmethod
    def _check_database(cls, value: str) -> str:
        return validate_id(value)
