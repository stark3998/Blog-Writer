"""Entra ID bearer tokens for the Foundry resource (no API keys anywhere).

Both clients authenticate with a token for :data:`FOUNDRY_SCOPE`: the OpenAI SDK takes the
provider as ``api_key`` (it awaits it before every request), and ``AsyncAnthropicFoundry`` takes
it as ``azure_ad_token_provider``. ``azure.identity.aio.get_bearer_token_provider`` caches the token
and refreshes it before expiry.
"""

from __future__ import annotations

import re
from typing import Final

from azure.core.credentials_async import AsyncTokenProvider
from azure.identity.aio import get_bearer_token_provider

from blogwriter.ai.providers.base import TokenProvider

__all__ = ["FOUNDRY_SCOPE", "entra_token_provider", "validate_resource_name"]

FOUNDRY_SCOPE: Final = "https://ai.azure.com/.default"

# Azure custom subdomain names: letters, digits and hyphens, 2-64 characters, no edge hyphen.
_RESOURCE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])")


def entra_token_provider(
    credential: AsyncTokenProvider, *, scope: str = FOUNDRY_SCOPE
) -> TokenProvider:
    """A cached bearer-token provider for ``scope`` backed by an async Azure credential."""
    return get_bearer_token_provider(credential, scope)


def validate_resource_name(resource: str) -> str:
    """Return ``resource`` if it is a valid Azure custom subdomain name, else raise."""
    if not _RESOURCE_NAME.fullmatch(resource):
        raise ValueError(f"{resource!r} is not a valid Azure resource name")
    return resource
