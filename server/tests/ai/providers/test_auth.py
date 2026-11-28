"""Entra ID tokens for Foundry: the right scope, cached, and no API keys.

Plan: "Facts that constrain the design" > Models (Claude uses AnthropicFoundry with Entra scope
https://ai.azure.com/.default) and "Owner action items" (no key auth anywhere).
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Self

import pytest
from azure.core.credentials import AccessToken

from blogwriter.ai.providers.auth import (
    FOUNDRY_SCOPE,
    entra_token_provider,
    validate_resource_name,
)


class FakeCredential:
    """An ``AsyncTokenCredential`` that records the scopes it was asked for."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, ...]] = []

    async def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        self.requests.append(scopes)
        return AccessToken(f"entra-token-{len(self.requests)}", int(time.time()) + 3600)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        return None


def test_the_foundry_scope_is_the_ai_azure_com_default_scope() -> None:
    assert FOUNDRY_SCOPE == "https://ai.azure.com/.default"


async def test_token_provider_requests_the_foundry_scope_and_caches() -> None:
    credential = FakeCredential()
    provider = entra_token_provider(credential)
    assert await provider() == "entra-token-1"
    assert await provider() == "entra-token-1"  # cached until close to expiry
    assert credential.requests == [(FOUNDRY_SCOPE,)]


async def test_token_provider_scope_can_be_overridden() -> None:
    credential = FakeCredential()
    provider = entra_token_provider(
        credential, scope="https://cognitiveservices.azure.com/.default"
    )
    await provider()
    assert credential.requests == [("https://cognitiveservices.azure.com/.default",)]


@pytest.mark.parametrize("name", ["foundry-bw-v2", "ab", "A1", "x" * 64])
def test_resource_names_accept_azure_subdomains(name: str) -> None:
    assert validate_resource_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "a", "-lead", "trail-", "with.dot", "evil.com/x", "a b", "x" * 65, "res?q=1", "res#f"],
)
def test_resource_names_reject_anything_that_could_redirect_the_host(name: str) -> None:
    with pytest.raises(ValueError, match="not a valid Azure resource name"):
        validate_resource_name(name)
