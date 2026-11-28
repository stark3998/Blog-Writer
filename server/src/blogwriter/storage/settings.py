"""Blob storage settings (env prefix ``BW_STORAGE_``).

``BW_STORAGE_BLOB_ENDPOINT``
    The account's Blob endpoint, ``https://<account>.blob.core.windows.net/``. Unset by default:
    storage is off, ``app.state.blobs`` is ``None`` and every route that needs it answers 503
    ``storage_unavailable``. There is no key or SAS setting: the account has shared-key auth
    disabled, so the store always authenticates with Entra ID (``DefaultAzureCredential``; on
    Azure Container Apps the managed identity in ``AZURE_CLIENT_ID``).
``BW_STORAGE_CONTAINER_MEDIA`` / ``_PUBLISHED`` / ``_BACKUPS``
    The real container names behind the logical containers ``media``, ``published`` and
    ``backups`` (defaults: the same names). Terraform creates them; the app never does.
``BW_STORAGE_MAX_UPLOAD_BYTES``
    Upload cap for ``POST /api/media`` (default 10 MiB).
``BW_STORAGE_MAX_IMAGE_PIXELS``
    Uploaded images above this many pixels are refused (default 12 MP).
"""

from __future__ import annotations

import functools
import re
from typing import Final
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.domain.common import BlobContainer

__all__ = [
    "DEFAULT_MAX_IMAGE_PIXELS",
    "DEFAULT_MAX_UPLOAD_BYTES",
    "StorageSettings",
    "storage_settings",
    "validate_blob_endpoint",
]

DEFAULT_MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS: Final = 12_000_000
_CONTAINER_NAME: Final = re.compile(r"^(?=.{3,63}$)[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))*$")
"""Azure's container naming rule: 3-63 characters, lowercase letters, digits and single
hyphens, starting and ending with a letter or digit."""


def validate_blob_endpoint(value: str) -> str:
    """Return the endpoint if it is an ``https://host[:port]/`` URL, else raise ``ValueError``."""
    endpoint = value.strip()
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("the Blob endpoint must be an https:// URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the Blob endpoint must not contain credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise ValueError("the Blob endpoint must be the account URL without a path")
    return endpoint


class StorageSettings(BaseSettings):
    """Where blobs live and the upload limits. Defaults are safe: nothing is configured."""

    model_config = SettingsConfigDict(env_prefix="BW_STORAGE_", frozen=True)

    blob_endpoint: str | None = None
    container_media: str = "media"
    container_published: str = "published"
    container_backups: str = "backups"
    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=1024, le=100 * 1024 * 1024)
    max_image_pixels: int = Field(default=DEFAULT_MAX_IMAGE_PIXELS, ge=1, le=100_000_000)

    @field_validator("blob_endpoint")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_blob_endpoint(value)

    @field_validator("container_media", "container_published", "container_backups")
    @classmethod
    def _check_container(cls, value: str) -> str:
        if not _CONTAINER_NAME.fullmatch(value):
            raise ValueError("container names are 3-63 lowercase letters, digits and hyphens")
        return value

    def container_name(self, container: BlobContainer) -> str:
        """The real container name behind a logical one."""
        match container:
            case "media":
                return self.container_media
            case "published":
                return self.container_published
            case "backups":
                return self.container_backups

    @property
    def configured(self) -> bool:
        return self.blob_endpoint is not None


@functools.cache
def storage_settings() -> StorageSettings:
    """The process-wide settings from the environment (built once, on first use)."""
    return StorageSettings()
