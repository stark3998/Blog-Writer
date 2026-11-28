"""``gpt-image-2`` on the wire: the v1 images API on its own Foundry resource (no network).

The real ``OpenAIImagesProvider`` runs against replayed HTTP traffic (``httpx2.MockTransport``):
the tests read the JSON the SDK sends, the Entra bearer token it attaches, and how Azure's
answers map to an image, a typed content-filter refusal or a provider error.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable

import httpx2
import pytest
from PIL import Image

from blogwriter.ai.providers import (
    OpenAIImagesProvider,
    ProviderAuthError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitedError,
    UnsupportedRequestError,
    create_openai_client,
)
from blogwriter.ai.types import (
    GeneratedImage,
    ImageGeneration,
    ImageRequest,
    ModelSpec,
    Sensitivity,
    Usage,
    png_size,
)

TOKEN = "entra-test-token"  # noqa: S105 - a fake token for the mock transport
IMAGE_URL = "https://img-bw.openai.azure.com/openai/v1/"
SPEC = ModelSpec("openai", "gpt-image-2-prod", "gpt-image-2", None, 4096, supports_tools=False)
PROMPT = "A lighthouse of stacked glass planes over calm teal water, soft light."


async def token() -> str:
    return TOKEN


def png(width: int = 48, height: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#0E2A24").save(buffer, format="PNG")
    return buffer.getvalue()


class Recorder:
    """Answers every request with ``respond`` and keeps the requests."""

    def __init__(self, respond: Callable[[], httpx2.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond()

    @property
    def bodies(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for request in self.requests:
            body: object = json.loads(request.content)
            assert isinstance(body, dict)
            out.append(body)  # pyright: ignore[reportUnknownArgumentType]
        return out


def image_answer(
    data: bytes | None = None, *, extra: dict[str, object] | None = None
) -> httpx2.Response:
    item: dict[str, object] = {"b64_json": base64.b64encode(data or png()).decode()}
    item.update(extra or {})
    body: dict[str, object] = {
        "created": 1764150000,
        "data": [item],
        "output_format": "png",
        "quality": "high",
        "size": "1536x1024",
        "usage": {
            "input_tokens": 60,
            "input_tokens_details": {"text_tokens": 60, "image_tokens": 0},
            "output_tokens": 6240,
            "total_tokens": 6300,
        },
    }
    return httpx2.Response(200, json=body)


def error_answer(
    status: int,
    code: str | None,
    *,
    inner: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    error: dict[str, object] = {"code": code, "message": "rejected", "type": "invalid_request"}
    if inner is not None:
        error["inner_error"] = {"code": inner, "content_filter_results": {}}
    return httpx2.Response(status, json={"error": error}, headers=headers or {})


def provider(recorder: Recorder) -> OpenAIImagesProvider:
    return OpenAIImagesProvider(
        create_openai_client(
            base_url=IMAGE_URL,
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
            max_retries=0,
        )
    )


def request(size: str = "1536x1024") -> ImageRequest:
    return ImageRequest(prompt=PROMPT, sensitivity=Sensitivity.PUBLIC, size=size)  # pyright: ignore[reportArgumentType]


async def generate(
    recorder: Recorder, image_request: ImageRequest | None = None
) -> ImageGeneration:
    images = provider(recorder)
    try:
        return await images.generate(SPEC, image_request or request())
    finally:
        await images.aclose()


async def test_one_png_is_asked_for_with_the_deployment_size_and_high_quality() -> None:
    recorder = Recorder(image_answer)
    result = await generate(recorder)
    (sent,) = recorder.requests
    assert sent.method == "POST"
    assert str(sent.url) == IMAGE_URL + "images/generations"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    (body,) = recorder.bodies
    assert body == {
        "model": "gpt-image-2-prod",
        "prompt": PROMPT,
        "size": "1536x1024",
        "quality": "high",
        "output_format": "png",
        "n": 1,
    }
    assert "response_format" not in body  # GPT image models always answer with base64
    image = result.image
    assert isinstance(image, GeneratedImage)
    assert image.data == png()
    assert (image.width, image.height) == (48, 32)
    assert result.refusal is None
    assert (result.usage.input, result.usage.output, result.usage.images) == (60, 6240, 1)
    assert result.model == "gpt-image-2-prod"


async def test_the_square_size_is_sent_as_asked() -> None:
    recorder = Recorder(image_answer)
    await generate(recorder, request("1024x1024"))
    assert recorder.bodies[0]["size"] == "1024x1024"


@pytest.mark.parametrize(
    ("code", "inner"),
    [
        ("content_policy_violation", "ResponsibleAIPolicyViolation"),
        ("content_filter", None),
        ("contentFilter", None),
        ("moderation_blocked", None),
        (None, "ResponsibleAIPolicyViolation"),
    ],
)
async def test_a_content_filter_is_a_typed_refusal_not_an_error(
    code: str | None, inner: str | None
) -> None:
    recorder = Recorder(lambda: error_answer(400, code, inner=inner))
    result = await generate(recorder)
    assert result.refused
    assert result.image is None
    refusal = result.refusal
    assert refusal is not None
    assert refusal.category == "content_filter"
    assert result.usage.images == 0
    assert len(recorder.requests) == 1


async def test_a_filtered_image_in_a_200_answer_is_a_refusal() -> None:
    def filtered() -> httpx2.Response:
        body = {
            "created": 1764150000,
            "data": [
                {"content_filter_results": {"violence": {"filtered": True, "severity": "high"}}}
            ],
        }
        return httpx2.Response(200, json=body)

    result = await generate(Recorder(filtered))
    assert result.refused
    assert result.usage.images == 0


async def test_other_errors_map_to_provider_errors_with_retry_hints() -> None:
    with pytest.raises(ProviderRequestError) as bad:
        await generate(Recorder(lambda: error_answer(400, "invalid_size")))
    assert not bad.value.retryable
    assert bad.value.status_code == 400
    with pytest.raises(RateLimitedError) as limited:
        await generate(
            Recorder(lambda: error_answer(429, "rate_limit_exceeded", headers={"retry-after": "7"}))
        )
    assert limited.value.retryable
    assert limited.value.retry_after == 7
    with pytest.raises(ProviderUnavailableError) as down:
        await generate(Recorder(lambda: error_answer(503, "server_error")))
    assert down.value.retryable
    with pytest.raises(ProviderAuthError):
        await generate(Recorder(lambda: error_answer(403, "PermissionDenied")))


@pytest.mark.parametrize(
    "answer",
    [
        lambda: httpx2.Response(200, json={"created": 1, "data": []}),
        lambda: httpx2.Response(200, json={"created": 1, "data": [{"b64_json": "!!not base64"}]}),
        lambda: image_answer(b"GIF89a not a png at all"),
        lambda: httpx2.Response(
            200,
            json={
                "created": 1,
                "data": [
                    {"b64_json": base64.b64encode(png()).decode()},
                    {"b64_json": base64.b64encode(png()).decode()},
                ],
            },
        ),
    ],
)
async def test_unusable_answers_are_request_errors(answer: Callable[[], httpx2.Response]) -> None:
    with pytest.raises(ProviderRequestError) as caught:
        await generate(Recorder(answer))
    assert not caught.value.retryable
    assert "b64" not in str(caught.value)  # codes and ids only, never content


async def test_a_spec_for_another_provider_is_refused_before_anything_is_sent() -> None:
    recorder = Recorder(image_answer)
    images = provider(recorder)
    claude = ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", "high", 4096, True)
    try:
        with pytest.raises(UnsupportedRequestError):
            await images.generate(claude, request())
    finally:
        await images.aclose()
    assert recorder.requests == []


def test_requests_and_images_validate_themselves() -> None:
    assert request().width == 1536
    assert request("1024x1024").height == 1024
    assert "lighthouse" not in repr(request())
    with pytest.raises(ValueError, match="prompt"):
        ImageRequest(prompt="  ", sensitivity=Sensitivity.PUBLIC)
    with pytest.raises(ValueError, match="size"):
        ImageRequest(prompt=PROMPT, sensitivity=Sensitivity.PUBLIC, size="512x512")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="quality"):
        ImageRequest(prompt=PROMPT, sensitivity=Sensitivity.PUBLIC, quality="hd")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="at most"):
        ImageRequest(prompt="x" * 4001, sensitivity=Sensitivity.PUBLIC)
    with pytest.raises(TypeError):
        ImageRequest(prompt=PROMPT, sensitivity="public")  # pyright: ignore[reportArgumentType]
    data = png(20, 10)
    assert png_size(data) == (20, 10)
    assert png_size(b"GIF89a") is None
    with pytest.raises(ValueError, match="stated size"):
        GeneratedImage(data=data, width=21, height=10)
    assert "data" not in repr(GeneratedImage(data=data, width=20, height=10))
    with pytest.raises(ValueError, match="exactly one"):
        ImageGeneration(image=None, usage=Usage(), model="m")
