"""Embeddings (``Task.EMBED``): the OpenAI ``/embeddings`` call and ``AIRouter.embed``.

Plan: "AI layer" (embeddings ``text-embedding-3-large`` @1024, anonymized; one cost ledger and
the soft brake for background work) and the contract I-3 (an embeddings call on the OpenAI
provider; the route and price exist already). Wire-level: the real provider against replayed
HTTP traffic; nothing touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast, override

import httpx2
import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import AISettings, RouteSettings, RouteTarget
from blogwriter.ai.ledger import BrakeEngaged, MemoryLedger, UsageRecord
from blogwriter.ai.policy import PolicyViolation
from blogwriter.ai.providers import (
    AnthropicFoundryProvider,
    EmbeddingProvider,
    OpenAIResponsesProvider,
    ProviderRequestError,
    RateLimitedError,
    UnsupportedRequestError,
    create_anthropic_client,
    create_openai_client,
)
from blogwriter.ai.router import MAX_EMBED_CHARS, MAX_EMBED_INPUTS, AIRouter
from blogwriter.ai.types import Embeddings, Message, ModelSpec, Request, Sensitivity, Task, Usage

TOKEN = "entra-test-token"  # noqa: S105 - a fake token for the mock transport
BASE_URL = "https://foundry-bw.openai.azure.com/openai/v1/"
EMBEDDING = "text-embedding-3-large"
SPEC = ModelSpec("openai", "emb-deploy", EMBEDDING, None, 4096, supports_tools=False)
NOW = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
DIMS = 1024


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


class EmbeddingsAPI:
    """Answers ``POST /embeddings`` from a queue and records the request bodies."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []
        self.paths: list[str] = []
        self.queued: list[Callable[[list[str], int], httpx2.Response]] = []

    def queue(self, *answers: Callable[[list[str], int], httpx2.Response]) -> None:
        self.queued.extend(answers)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        raw: object = json.loads(request.content)
        assert isinstance(raw, dict)
        body = cast(dict[str, object], raw)
        self.bodies.append(body)
        inputs = body.get("input")
        assert isinstance(inputs, list)
        dims = body.get("dimensions", 3072)
        assert isinstance(dims, int)
        answer = self.queued.pop(0) if self.queued else vectors_answer
        return answer([str(item) for item in cast(list[object], inputs)], dims)


def vector(index: int, dims: int) -> list[float]:
    return [round((index + 1) / (position + 2), 6) for position in range(dims)]


def vectors_answer(inputs: list[str], dims: int, *, reverse: bool = True) -> httpx2.Response:
    data = [
        {"object": "embedding", "index": index, "embedding": vector(index, dims)}
        for index in range(len(inputs))
    ]
    if reverse:
        data.reverse()  # the service may answer out of order: index decides
    tokens = sum(len(text.split()) for text in inputs)
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "data": data,
            "model": EMBEDDING,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        },
    )


def rate_limited(_inputs: list[str], _dims: int) -> httpx2.Response:
    error = {"message": "Too many requests.", "type": "rate_limit", "code": "429"}
    return httpx2.Response(429, headers={"retry-after": "1"}, json={"error": error})


def short_answer(inputs: list[str], dims: int) -> httpx2.Response:
    return vectors_answer(inputs[:-1], dims)


def provider_for(api: EmbeddingsAPI) -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(
        create_openai_client(
            base_url=BASE_URL,
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
            max_retries=0,
        )
    )


def router_for(
    api: EmbeddingsAPI,
    *,
    ledger: MemoryLedger | None = None,
    settings: AISettings | None = None,
    clock: FakeClock | None = None,
) -> AIRouter:
    provider = provider_for(api)
    return AIRouter.from_settings(
        settings if settings is not None else ArgsOnlyAISettings(deployment_embedding="emb-deploy"),
        {"openai": provider, "anthropic": provider_stub()},
        ledger=ledger if ledger is not None else MemoryLedger(),
        clock=clock if clock is not None else FakeClock(),
        jitter=lambda: 0.5,
        now=lambda: NOW,
    )


def provider_stub() -> AnthropicFoundryProvider:
    """Claude stands in only so the routing table validates; it is never called here."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no Claude call expected")

    return AnthropicFoundryProvider(
        create_anthropic_client(
            resource="foundry-bw",
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)),
            max_retries=0,
        )
    )


# --------------------------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------------------------


async def test_the_provider_posts_embeddings_with_dimensions_and_floats() -> None:
    api = EmbeddingsAPI()
    provider = provider_for(api)
    assert isinstance(provider, EmbeddingProvider)
    try:
        result = await provider.embed(SPEC, ["agent identity", "token scopes"], dimensions=DIMS)
    finally:
        await provider.aclose()
    assert api.paths == ["/openai/v1/embeddings"]
    assert api.bodies == [
        {
            "model": "emb-deploy",
            "input": ["agent identity", "token scopes"],
            "dimensions": DIMS,
            "encoding_format": "float",
        }
    ]
    assert isinstance(result, Embeddings)
    assert result.dimensions == DIMS
    assert [list(v) for v in result.vectors] == [vector(0, DIMS), vector(1, DIMS)]  # by index
    assert result.usage == Usage(input=4)
    assert "agent" not in repr(result)


async def test_a_response_that_does_not_match_the_inputs_is_refused_with_its_usage() -> None:
    api = EmbeddingsAPI()
    api.queue(short_answer)
    provider = provider_for(api)
    try:
        with pytest.raises(ProviderRequestError) as caught:
            await provider.embed(SPEC, ["Contoso alpha", "beta", "gamma"], dimensions=8)
    finally:
        await provider.aclose()
    assert not caught.value.retryable
    assert caught.value.usage == Usage(input=3)
    assert "Contoso" not in str(caught.value)


async def test_wrong_dimensions_are_refused() -> None:
    api = EmbeddingsAPI()
    api.queue(lambda inputs, dims: vectors_answer(inputs, dims + 1))
    provider = provider_for(api)
    try:
        with pytest.raises(ProviderRequestError, match="wrong dimensions"):
            await provider.embed(SPEC, ["one"], dimensions=8)
    finally:
        await provider.aclose()


async def test_http_errors_map_like_chat_calls() -> None:
    api = EmbeddingsAPI()
    api.queue(rate_limited)
    provider = provider_for(api)
    try:
        with pytest.raises(RateLimitedError) as caught:
            await provider.embed(SPEC, ["one"], dimensions=8)
    finally:
        await provider.aclose()
    assert caught.value.retryable
    assert caught.value.status_code == 429


async def test_the_provider_refuses_a_foreign_spec_or_no_input() -> None:
    api = EmbeddingsAPI()
    provider = provider_for(api)
    claude = ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", None, 10, True)
    try:
        with pytest.raises(UnsupportedRequestError):
            await provider.embed(claude, ["one"])
        with pytest.raises(UnsupportedRequestError):
            await provider.embed(SPEC, [])
    finally:
        await provider.aclose()
    assert api.bodies == []


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


async def test_the_router_embeds_ledgers_and_prices_the_call() -> None:
    api = EmbeddingsAPI()
    ledger = MemoryLedger()
    router = router_for(api, ledger=ledger)
    try:
        result = await router.embed(
            ["agent identity in MCP servers"], origin="background", job_id="job-1", dimensions=DIMS
        )
    finally:
        await router.aclose()
    assert result.embeddings.dimensions == DIMS
    assert result.served_by.task is Task.EMBED
    assert (result.served_by.model, result.served_by.deployment) == (EMBEDDING, "emb-deploy")
    assert result.served_by.fallback_reason is None
    assert result.usage == Usage(input=5)
    assert result.cost_usd == Decimal("0.13") * 5 / 1_000_000
    (record,) = ledger.records
    assert isinstance(record, UsageRecord)
    assert (record.task, record.model, record.origin, record.outcome) == (
        Task.EMBED,
        EMBEDDING,
        "background",
        "completed",
    )
    assert record.job_id == "job-1"
    assert record.cost_usd == result.cost_usd


async def test_rate_limits_are_retried_through_the_limiter() -> None:
    api = EmbeddingsAPI()
    api.queue(rate_limited, vectors_answer)
    clock = FakeClock()
    router = router_for(api, clock=clock)
    try:
        result = await router.embed(["one two"], origin="owner", dimensions=8)
    finally:
        await router.aclose()
    assert len(api.bodies) == 2
    assert result.embeddings.dimensions == 8
    assert clock.sleeps
    assert clock.sleeps[0] >= 1.0  # Retry-After is a floor


async def test_a_malformed_answer_is_ledgered_as_failed_and_not_retried() -> None:
    api = EmbeddingsAPI()
    api.queue(short_answer)
    ledger = MemoryLedger()
    router = router_for(api, ledger=ledger)
    try:
        with pytest.raises(ProviderRequestError):
            await router.embed(["one", "two"], origin="owner", dimensions=8)
    finally:
        await router.aclose()
    assert len(api.bodies) == 1
    assert [record.outcome for record in ledger.records] == ["failed"]


async def test_background_embeddings_respect_the_brake() -> None:
    api = EmbeddingsAPI()
    ledger = MemoryLedger()
    settings = ArgsOnlyAISettings(deployment_embedding="emb-deploy", brake_daily_cap_usd=Decimal(0))
    await ledger.record(
        UsageRecord(
            task=Task.DRAFT,
            model="claude-opus-5-5",
            deployment="claude-opus-5-5",
            provider="anthropic",
            usage=Usage(input=10),
            cost_usd=Decimal("1"),
            at=NOW,
            origin="owner",
            outcome="completed",
        )
    )
    router = router_for(api, ledger=ledger, settings=settings)
    try:
        with pytest.raises(BrakeEngaged):
            await router.embed(["one"], origin="background")
        owner = await router.embed(["one"], origin="owner", dimensions=4)
    finally:
        await router.aclose()
    assert owner.embeddings.dimensions == 4
    assert len(api.bodies) == 1  # the braked call sent nothing


async def test_policy_and_input_checks_run_before_any_call() -> None:
    api = EmbeddingsAPI()
    router = router_for(api)
    strict = router_for(api, settings=ArgsOnlyAISettings(allow_private=False))
    try:
        with pytest.raises(PolicyViolation, match="above_task_max"):
            await router.embed(["raw interview"], origin="owner", sensitivity=Sensitivity.PRIVATE)
        with pytest.raises(ValueError, match="blank"):
            await router.embed(["fine", "   "], origin="owner")
        with pytest.raises(ValueError, match="1 to"):
            await router.embed([], origin="owner")
        with pytest.raises(ValueError, match="1 to"):
            await router.embed(["x"] * (MAX_EMBED_INPUTS + 1), origin="owner")
        with pytest.raises(ValueError, match="longer than"):
            await router.embed(["x" * (MAX_EMBED_CHARS + 1)], origin="owner")
        with pytest.raises(TypeError):
            await router.embed("one string", origin="owner")  # pyright: ignore[reportArgumentType]
        with pytest.raises(ValueError, match="dimensions"):
            await router.embed(["one"], origin="owner", dimensions=0)
        with pytest.raises(PolicyViolation):
            await strict.embed(["raw"], origin="owner", sensitivity=Sensitivity.PRIVATE)
    finally:
        await router.aclose()
        await strict.aclose()
    assert api.bodies == []


async def test_only_the_primary_serves_even_with_a_fallback_configured() -> None:
    api = EmbeddingsAPI()
    api.queue(short_answer)
    settings = ArgsOnlyAISettings(
        deployment_embedding="emb-deploy",
        route_overrides={
            Task.EMBED: RouteSettings(
                primary=RouteTarget(model=EMBEDDING),
                fallback=RouteTarget(model="gpt-6.2-sol"),
                max_sensitivity=Sensitivity.ANONYMIZED,
            )
        },
    )
    router = router_for(api, settings=settings)
    try:
        with pytest.raises(ProviderRequestError):
            await router.embed(["one", "two"], origin="owner")
    finally:
        await router.aclose()
    assert [body["model"] for body in api.bodies] == ["emb-deploy"]  # no fallback model


async def test_a_provider_without_embeddings_is_refused() -> None:
    class ChatOnly:
        name = "openai"

        async def aclose(self) -> None:
            return None

    api = EmbeddingsAPI()
    router = AIRouter.from_settings(
        ArgsOnlyAISettings(),
        {"openai": ChatOnly(), "anthropic": provider_stub()},  # pyright: ignore[reportArgumentType]
        ledger=MemoryLedger(),
    )
    try:
        with pytest.raises(UnsupportedRequestError, match="serves no embeddings"):
            await router.embed(["one"], origin="owner")
    finally:
        await router.aclose()
    assert api.bodies == []


async def test_run_and_stream_still_refuse_the_embed_task() -> None:
    api = EmbeddingsAPI()
    router = router_for(api)
    request = Request(
        system="", messages=(Message.user("one"),), sensitivity=Sensitivity.ANONYMIZED
    )
    try:
        with pytest.raises(UnsupportedRequestError, match="embed"):
            await router.run(Task.EMBED, request, origin="owner")
    finally:
        await router.aclose()


def test_the_router_exposes_prices_and_the_brake_read_only() -> None:
    api = EmbeddingsAPI()
    router = router_for(api)
    assert router.prices.price(EMBEDDING).input == Decimal("0.13")
    assert router.brake is not None


def _texts(count: int) -> Sequence[str]:
    return [f"text {index}" for index in range(count)]


async def test_inputs_up_to_the_cap_go_in_one_call() -> None:
    api = EmbeddingsAPI()
    router = router_for(api)
    try:
        result = await router.embed(_texts(MAX_EMBED_INPUTS), origin="owner", dimensions=2)
    finally:
        await router.aclose()
    assert len(result.vectors) == MAX_EMBED_INPUTS
    assert len(api.bodies) == 1
