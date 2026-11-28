"""Settings of the v1 migration CLI (env prefix ``BW_MIGRATE_V1_``; never read by the app).

``BW_MIGRATE_V1_ENDPOINT``
    The **v1** Cosmos account (a separate account from v2), ``https://<account>.documents.azure.com:443/``.
    Required.
``BW_MIGRATE_V1_KEY``
    Optional account key. Without it the tool signs in with ``DefaultAzureCredential`` (needs a
    data-plane reader role on the v1 account). Use the account's **read-only** key. The value is
    a ``SecretStr``: it is never logged, printed or put in an error message.
``BW_MIGRATE_V1_DATABASE``
    v1's database name (default ``blog-writer``).

The v2 side uses the app's own settings: ``BW_COSMOS_*`` for the store and ``BW_STORAGE_*`` for
Blob storage.
"""

from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.db.base import validate_id
from blogwriter.db.settings import validate_endpoint

__all__ = ["MigrateV1Settings"]


class MigrateV1Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BW_MIGRATE_V1_", frozen=True, hide_input_in_errors=True
    )

    endpoint: str | None = None
    key: SecretStr | None = None
    database: str = "blog-writer"

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_endpoint(value)

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        return value

    @field_validator("database")
    @classmethod
    def _check_database(cls, value: str) -> str:
        return validate_id(value)
