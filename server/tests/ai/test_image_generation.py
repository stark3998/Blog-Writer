"""Image generation through the router (``Task.IMAGE``, ``gpt-image-2`` on its own resource).

Plan: "AI layer" (opt-in ``gpt-image-2``, public data only; per-model semaphores; retries only
on 429/5xx; a cost ledger and a brake) and "Facts that constrain the design" (``gpt-image-2`` is
only in East US 2, so images need a second Foundry resource). The image provider is the real
one over ``httpx2.MockTransport``; the chat providers are never called.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import override

import httpx2
import pytest
from PIL import Image
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai import (
    DEFAULT_PRICES,
    IMAGE,
    AIRouter,
    AISettings,
    BrakeEngaged,
    MemoryLedger,
    NoRouteError,
    PolicyViolation,
    UsageRecord,
    create_image_provider,
)
from blogwriter.ai.config import RouteSettings, RouteTarget
from blogwriter.ai.providers import (
    OpenAIImagesProvider,
    ProviderRequestError,
    UnsupportedRequestError,
)
from blogwriter.ai.types import (
    Completion,
    ImageRequest,
    Message,
    ModelSpec,
    ProviderName,
    Request,
    Sensitivity,
    StreamEvent,
    Task,
    Usage,
)

TOKEN = "entra-test-token"  # noqa: S105 - a fake token for the mock transport
IMAGE_URL = "https://img-bw.openai.azure.com/openai/v1/"
NOW = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
PROMPT = "A bridge of layered teal planes between two quiet islands, soft morning light."


async def token() -> str:
    return TOKEN


class ArgsOnlyAISettings(AISettings):
    """``AISettings`` that ignore the process environment."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class ChatUnused:
    """A chat provider that must never be called by image generation."""

    def __init__(self, name: ProviderName) -> None:
        self._name: ProviderName = name

    @property
    def name(self) -> ProviderName:
        return self._name

    def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        raise AssertionError("image generation never calls a chat model")

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        raise AssertionError("image generation never calls a chat model")

    async def aclose(self) -> None:
        return None


def png(width: int = 48, height: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#2E4A43").save(buffer, format="PNG")
    return buffer.getvalue()


def ok() -> httpx2.Response:
    body = {
        "created": 1764150000,
        "data": [{"b64_json": base64.b64encode(png()).decode()}],
        "usage": {
            "input_tokens": 60,
            "input_tokens_details": {"text_tokens": 60, "image_tokens": 0},
            "output_tokens": 6240,
            "total_tokens": 6300,
        },
    }
    return httpx2.Response(200, json=body)


def failure(status: int, code: str, **headers: str) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"code": code, "message": "no"}}, headers=headers)


type Handler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)


class Script:
    """Answers requests in order (the last answer repeats) and keeps them."""

    def __init__(self, *answers: Callable[[], httpx2.Response]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer()


def settings(**overrides: object) -> AISettings:
    values: dict[str, object] = {
        "openai_base_url": "https://foundry-bw.openai.azure.com/openai/v1/",
        "anthropic_resource": "foundry-bw",
        "image_base_url": IMAGE_URL,
    }
    values.update(overrides)
    return ArgsOnlyAISettings.model_validate(values)


def router(
    handler: Handler,
    *,
    config: AISettings | None = None,
    ledger: MemoryLedger | None = None,
    clock: FakeClock | None = None,
    with_images: bool = True,
) -> AIRouter:
    chosen = config or settings()
    images = (
        create_image_provider(
            chosen,
            token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        if with_images
        else None
    )
    return AIRouter.from_settings(
        chosen,
        {"openai": ChatUnused("openai"), "anthropic": ChatUnused("anthropic")},
        ledger=ledger or MemoryLedger(),
        clock=clock or FakeClock(),
        jitter=lambda: 0.0,
        now=lambda: NOW,
        images=images,
    )


def ask(size: str = "1536x1024", sensitivity: Sensitivity = Sensitivity.PUBLIC) -> ImageRequest:
    return ImageRequest(prompt=PROMPT, sensitivity=sensitivity, size=size)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


def test_images_are_off_until_the_image_resource_is_set() -> None:
    off = ArgsOnlyAISettings()
    assert not off.images_configured
    assert Task.IMAGE not in off.routing()
    assert create_image_provider(off, token) is None
    on = ArgsOnlyAISettings(image_base_url="https://img-bw.openai.azure.com/openai/v1")
    assert on.images_configured
    assert on.image_base_url == IMAGE_URL
    route = on.routing().route(Task.IMAGE)
    assert route.fallback is None
    assert route.primary.family == IMAGE
    assert route.primary.deployment == "gpt-image-2"
    assert route.primary.provider == "openai"
    assert route.policy.max_sensitivity is Sensitivity.PUBLIC
    assert route.policy.tools is False
    assert on.routing().limits[IMAGE].concurrency == 1  # one generation at a time
    renamed = ArgsOnlyAISettings(image_base_url=IMAGE_URL, deployment_image="images-prod")
    assert renamed.routing().route(Task.IMAGE).primary.deployment == "images-prod"


def test_image_prices_are_per_image_by_size_and_configurable() -> None:
    table = ArgsOnlyAISettings().price_table()
    assert table.image_cost(IMAGE, "1536x1024", 1) == Decimal("0.25")
    assert table.image_cost(IMAGE, "1024x1024", 1) == Decimal("0.17")
    assert table.image_cost(IMAGE, "1024x1024", 3) == Decimal("0.51")
    assert DEFAULT_PRICES[IMAGE].image_price("1536x1024") == Decimal("0.25")
    custom = ArgsOnlyAISettings(
        image_usd_1536x1024=Decimal("0.30"), image_usd_1024x1024=Decimal("0.20")
    ).price_table()
    assert custom.image_cost(IMAGE, "1536x1024", 1) == Decimal("0.30")
    assert custom.image_cost(IMAGE, "1024x1024", 2) == Decimal("0.40")
    assert custom.image_cost(IMAGE, "999x999", 1) == Decimal("0.30")  # unknown size: the base
    with pytest.raises(ValueError, match="negative"):
        custom.image_cost(IMAGE, "1024x1024", -1)


def test_the_environment_configures_the_image_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_AI_IMAGE_BASE_URL", "https://img-bw.openai.azure.com/openai/v1/")
    monkeypatch.setenv("BW_AI_DEPLOYMENT_IMAGE", "gpt-image-2-eus2")
    monkeypatch.setenv("BW_AI_IMAGE_USD_1536X1024", "0.31")
    monkeypatch.setenv("BW_AI_IMAGE_USD_1024X1024", "0.21")
    configured = AISettings()
    assert configured.image_base_url == IMAGE_URL
    assert configured.routing().route(Task.IMAGE).primary.deployment == "gpt-image-2-eus2"
    assert configured.price_table().image_cost(IMAGE, "1024x1024", 1) == Decimal("0.21")


@pytest.mark.parametrize(
    "overrides",
    [
        {"image_base_url": "http://img-bw.openai.azure.com/openai/v1/"},
        {"image_base_url": "https://img-bw.openai.azure.com/openai/"},
        {"deployment_image": "-bad"},
        {"image_usd_1536x1024": "-1"},
    ],
)
def test_bad_image_settings_fail_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ArgsOnlyAISettings.model_validate(overrides)


async def test_the_image_provider_has_sdk_retries_off() -> None:
    provider = create_image_provider(settings(), token, timeout=30.0)
    assert isinstance(provider, OpenAIImagesProvider)
    assert provider._client.max_retries == 0  # pyright: ignore[reportPrivateUsage]
    await provider.aclose()


# --------------------------------------------------------------------------------------------
# Serving and the ledger
# --------------------------------------------------------------------------------------------


async def test_an_image_is_generated_priced_per_size_and_ledgered() -> None:
    ledger = MemoryLedger()
    calls = Script(ok)
    images = router(calls, ledger=ledger)
    assert images.serves_images
    landscape = await images.generate_image(ask(), origin="owner", job_id="job-1")
    square = await images.generate_image(ask("1024x1024"), origin="owner", job_id="job-1")
    await images.aclose()
    assert landscape.image is not None
    assert landscape.image.data == png()
    assert not landscape.refused
    assert landscape.served_by.task is Task.IMAGE
    assert (landscape.served_by.model, landscape.served_by.deployment) == (IMAGE, "gpt-image-2")
    assert landscape.served_by.fallback_reason is None
    assert landscape.cost_usd == Decimal("0.25")
    assert square.cost_usd == Decimal("0.17")
    assert [json.loads(r.content)["size"] for r in calls.requests] == ["1536x1024", "1024x1024"]
    first, second = ledger.records
    assert (first.task, first.model, first.outcome, first.origin) == (
        Task.IMAGE,
        IMAGE,
        "completed",
        "owner",
    )
    assert first.usage.images == 1
    assert first.usage.output == 6240  # recorded, never billed on top of the image price
    assert (first.cost_usd, second.cost_usd) == (Decimal("0.25"), Decimal("0.17"))
    assert first.job_id == "job-1"
    assert first.to_document()["images"] == 1


async def test_a_content_filter_refusal_is_returned_and_costs_nothing() -> None:
    ledger = MemoryLedger()
    calls = Script(lambda: failure(400, "content_policy_violation"))
    images = router(calls, ledger=ledger)
    result = await images.generate_image(ask(), origin="owner")
    await images.aclose()
    assert result.refused
    assert result.image is None
    assert result.refusal is not None
    assert result.cost_usd == Decimal(0)
    assert len(calls.requests) == 1  # a refusal is final: no retry, no fallback
    (record,) = ledger.records
    assert (record.outcome, record.usage.images, record.cost_usd) == ("refused", 0, Decimal(0))


async def test_only_429_and_5xx_are_retried() -> None:
    clock = FakeClock()
    calls = Script(
        lambda: failure(429, "rate_limit_exceeded", **{"retry-after": "2"}),
        lambda: failure(503, "server_error"),
        ok,
    )
    images = router(calls, clock=clock)
    result = await images.generate_image(ask(), origin="owner")
    assert result.image is not None
    assert len(calls.requests) == 3
    assert clock.sleeps[0] >= 2  # Retry-After is a floor
    rejected = Script(lambda: failure(400, "invalid_request_error"))
    strict = router(rejected)
    with pytest.raises(ProviderRequestError):
        await strict.generate_image(ask(), origin="owner")
    assert len(rejected.requests) == 1
    await images.aclose()
    await strict.aclose()


async def test_retries_stop_at_the_policy_limit() -> None:
    calls = Script(lambda: failure(500, "server_error"))
    images = router(calls, config=settings(retry_max_attempts=2))
    with pytest.raises(Exception, match="unavailable"):
        await images.generate_image(ask(), origin="owner")
    assert len(calls.requests) == 2
    await images.aclose()


@pytest.mark.parametrize("sensitivity", [Sensitivity.ANONYMIZED, Sensitivity.PRIVATE])
async def test_only_public_prompts_reach_the_image_model(sensitivity: Sensitivity) -> None:
    calls = Script(ok)
    images = router(calls)
    with pytest.raises(PolicyViolation) as caught:
        await images.generate_image(ask(sensitivity=sensitivity), origin="owner")
    assert caught.value.rule == "above_task_max"
    assert PROMPT not in str(caught.value)
    assert calls.requests == []
    await images.aclose()


async def test_run_and_stream_never_serve_images() -> None:
    calls = Script(ok)
    images = router(calls)
    request = Request(
        system="Draw.", messages=(Message.user(PROMPT),), sensitivity=Sensitivity.PUBLIC
    )
    with pytest.raises(UnsupportedRequestError, match="image"):
        await images.run(Task.IMAGE, request, origin="owner")
    assert calls.requests == []
    await images.aclose()


async def test_without_the_resource_or_the_provider_nothing_is_served() -> None:
    calls = Script(ok)
    unrouted = router(calls, config=settings(image_base_url=None), with_images=False)
    assert not unrouted.serves_images
    with pytest.raises(NoRouteError):
        await unrouted.generate_image(ask(), origin="owner")
    misrouted = router(
        calls,
        config=settings(
            image_base_url=None,
            route_overrides={
                Task.IMAGE: RouteSettings(
                    primary=RouteTarget(model="gpt-6.2-sol"), max_sensitivity=Sensitivity.PUBLIC
                )
            },
        ),
        with_images=False,
    )
    assert not misrouted.serves_images  # a route without the image resource serves nothing
    with pytest.raises(UnsupportedRequestError):
        await misrouted.generate_image(ask(), origin="owner")
    assert calls.requests == []


async def test_one_generation_runs_at_a_time() -> None:
    in_flight = 0
    peak = 0

    async def slow(request: httpx2.Request) -> httpx2.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ok()

    images = router(slow)
    results = await asyncio.gather(
        *(images.generate_image(ask(), origin="owner") for _ in range(3))
    )
    await images.aclose()
    assert all(result.image is not None for result in results)
    assert peak == 1


async def test_background_image_work_is_braked_but_never_the_owner() -> None:
    ledger = MemoryLedger()
    await ledger.record(
        UsageRecord(
            task=Task.RESEARCH,
            model="gpt-6.2-sol",
            deployment="gpt-6.2-sol",
            provider="openai",
            usage=Usage(input=1, output=1),
            cost_usd=Decimal(30),
            at=NOW,
            origin="background",
            outcome="completed",
        )
    )
    calls = Script(ok)
    images = router(calls, ledger=ledger)
    with pytest.raises(BrakeEngaged):
        await images.generate_image(ask(), origin="background")
    assert calls.requests == []
    result = await images.generate_image(ask(), origin="owner")
    assert result.image is not None
    await images.aclose()


async def test_the_call_log_carries_counts_and_costs_never_the_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="blogwriter.ai.router")
    images = router(Script(ok))
    await images.generate_image(ask("1024x1024"), origin="owner", job_id="job-7")
    await images.aclose()
    (call,) = [record for record in caplog.records if record.getMessage() == "ai_model_call"]
    assert getattr(call, "images", None) == 1
    assert getattr(call, "image_size", None) == "1024x1024"
    assert getattr(call, "cost_usd", None) == pytest.approx(0.17)
    assert PROMPT not in caplog.text
