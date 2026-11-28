"""Azure OpenAI image generation (``gpt-image-2``) through the v1 images API.

The client is ``openai.AsyncOpenAI`` pointed at the v1 endpoint of the **separate** Foundry
resource that hosts ``gpt-image-2`` (East US 2), authenticated with the same Entra token
provider as the Responses provider (``create_openai_client``). Every call is one
``POST /images/generations`` with ``model`` (the deployment), ``prompt``, ``size``,
``quality``, ``output_format="png"`` and ``n=1``; GPT image models always answer with base64
data (no URL the service would host, no ``response_format``).

Refusals are results, not errors: an HTTP 400 whose code (or inner error code) is a content
filter or moderation code (``content_filter``, ``contentFilter``, ``content_policy_violation``,
``moderation_blocked``, ``ResponsibleAIPolicyViolation``), or a 200 whose only item was filtered
(``content_filter_results`` with ``filtered: true`` and no image), becomes an
:class:`~blogwriter.ai.types.ImageGeneration` with a ``content_filter``
:class:`~blogwriter.ai.types.Refusal`. Anything else maps to a :class:`ProviderError` (429/5xx
retryable, the router retries; other 4xx final). The provider never logs and never keeps the
prompt or the image.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Final, cast

import httpx2
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI, OpenAIError

from blogwriter.ai.providers.base import (
    ProviderError,
    ProviderRequestError,
    ProviderUnavailableError,
    UnsupportedRequestError,
    count_attr,
    error_for_status,
)
from blogwriter.ai.types import (
    MAX_GENERATED_IMAGE_BYTES,
    GeneratedImage,
    ImageGeneration,
    ImageRequest,
    ModelSpec,
    ProviderName,
    Refusal,
    Usage,
    png_size,
)

__all__ = ["CONTENT_FILTER_CODES", "OpenAIImagesProvider"]

_PROVIDER: Final[ProviderName] = "openai"
CONTENT_FILTER_CODES: Final = frozenset(
    {
        "content_filter",
        "contentFilter",
        "content_policy_violation",
        "moderation_blocked",
        "ResponsibleAIPolicyViolation",
    }
)
"""Error codes Azure and OpenAI use when a safety system refused a prompt or an image."""
_RETRYABLE_CODES: Final = frozenset({"server_error", "rate_limit_exceeded"})


def _codes(exc: APIStatusError) -> set[str]:
    """The error's code and its inner error's code (Azure nests the filter's reason there)."""
    codes = {exc.code} if exc.code else set[str]()
    body = exc.body
    if isinstance(body, Mapping):
        fields = cast(Mapping[str, object], body)
        for key in ("inner_error", "innererror"):
            inner = fields.get(key)
            if isinstance(inner, Mapping):
                code = cast(Mapping[str, object], inner).get("code")
                if isinstance(code, str):
                    codes.add(code)
    return codes


def _refusal(spec: ModelSpec, usage: Usage | None = None) -> ImageGeneration:
    return ImageGeneration(
        image=None,
        usage=usage if usage is not None else Usage(),
        model=spec.deployment,
        refusal=Refusal("content_filter", None),
    )


def _map_error(exc: OpenAIError | httpx2.TransportError) -> ProviderError:
    if isinstance(exc, APIStatusError):
        return error_for_status(
            _PROVIDER,
            exc.status_code,
            code=exc.code,
            request_id=exc.request_id,
            headers=exc.response.headers,
        )
    if isinstance(exc, APIConnectionError | httpx2.TransportError):
        return ProviderUnavailableError(
            f"openai images connection failed ({type(exc).__name__})",
            provider=_PROVIDER,
            retryable=True,
        )
    if isinstance(exc, APIError):
        return ProviderUnavailableError(
            f"openai images call failed (code={exc.code})",
            provider=_PROVIDER,
            retryable=exc.code is None or exc.code in _RETRYABLE_CODES,
            code=exc.code,
        )
    return ProviderRequestError(
        f"openai images call failed ({type(exc).__name__})", provider=_PROVIDER, retryable=False
    )


def _filtered(item: object) -> bool:
    """Whether a response item carries a content filter result that filtered it (Azure)."""
    results: object = getattr(item, "content_filter_results", None)
    if not isinstance(results, Mapping):
        return False
    for value in cast(Mapping[str, object], results).values():
        if isinstance(value, Mapping) and cast(Mapping[str, object], value).get("filtered") is True:
            return True
    return False


class OpenAIImagesProvider:
    """``gpt-image-2`` on its Azure OpenAI (Foundry) resource. Satisfies ``ImageProvider``."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @property
    def name(self) -> ProviderName:
        return _PROVIDER

    async def aclose(self) -> None:
        await self._client.close()

    async def generate(self, spec: ModelSpec, request: ImageRequest) -> ImageGeneration:
        """One ``POST /images/generations`` (``n=1``, PNG). A content filter refusal is the
        result's ``refusal``; every other failure raises :class:`ProviderError`."""
        if spec.provider != _PROVIDER:
            raise UnsupportedRequestError(f"spec for {spec.provider} sent to the image provider")
        try:
            response = await self._client.images.generate(
                model=spec.deployment,
                prompt=request.prompt,
                size=request.size,
                quality=request.quality,
                output_format="png",
                n=1,
            )
        except APIStatusError as exc:
            if exc.status_code == 400 and _codes(exc) & CONTENT_FILTER_CODES:
                return _refusal(spec)
            raise _map_error(exc) from exc
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc) from exc
        tokens = Usage(
            input=count_attr(response, "usage", "input_tokens") or 0,
            output=count_attr(response, "usage", "output_tokens") or 0,
        )
        items = list(response.data or ())
        usage = tokens + Usage(images=len(items))  # what was generated (and billed)

        def unexpected(problem: str, *, billed: bool = True) -> ProviderRequestError:
            return ProviderRequestError(
                f"openai images response {problem}",
                provider=_PROVIDER,
                retryable=False,
                usage=usage if billed else tokens,
            )

        if len(items) != 1:
            if not items:
                raise unexpected("holds no image", billed=False)
            raise unexpected("holds more than one image")
        item = items[0]
        encoded = item.b64_json
        if not encoded:
            if _filtered(item):
                return _refusal(spec, tokens)
            raise unexpected("holds no image data", billed=False)
        if len(encoded) > (MAX_GENERATED_IMAGE_BYTES * 4) // 3 + 4:
            raise unexpected("is too large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise unexpected("holds invalid base64") from None
        size = png_size(data)
        if size is None:
            raise unexpected("is not a PNG")
        try:
            image = GeneratedImage(data=data, width=size[0], height=size[1])
        except ValueError:
            raise unexpected("holds an unusable image") from None
        return ImageGeneration(image=image, usage=usage, model=spec.deployment)
