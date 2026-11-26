"""Model providers: Azure OpenAI (Responses API, v1 endpoint) and Anthropic on Foundry.

Build clients with :func:`create_openai_client` / :func:`create_anthropic_client` and an Entra
token provider from :func:`entra_token_provider`, then wrap them in
:class:`OpenAIResponsesProvider` / :class:`AnthropicFoundryProvider`. Both satisfy
:class:`Provider`.
"""

from blogwriter.ai.providers.anthropic_foundry import (
    AnthropicFoundryProvider,
    create_anthropic_client,
    foundry_anthropic_base_url,
)
from blogwriter.ai.providers.auth import FOUNDRY_SCOPE, entra_token_provider
from blogwriter.ai.providers.base import (
    BackgroundPoll,
    BackgroundStatus,
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderStreamError,
    ProviderUnavailableError,
    RateLimitedError,
    TokenProvider,
    UnsupportedRequestError,
    collect,
)
from blogwriter.ai.providers.openai_responses import (
    OpenAIResponsesProvider,
    azure_openai_v1_base_url,
    create_openai_client,
)

__all__ = [
    "FOUNDRY_SCOPE",
    "AnthropicFoundryProvider",
    "BackgroundPoll",
    "BackgroundStatus",
    "OpenAIResponsesProvider",
    "Provider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderStreamError",
    "ProviderUnavailableError",
    "RateLimitedError",
    "TokenProvider",
    "UnsupportedRequestError",
    "azure_openai_v1_base_url",
    "collect",
    "create_anthropic_client",
    "create_openai_client",
    "entra_token_provider",
    "foundry_anthropic_base_url",
]
