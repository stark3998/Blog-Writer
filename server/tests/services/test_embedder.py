"""The ``Embedder`` port over ``AIRouter.embed`` (contract I-3): 1024 dimensions, batches,
fail closed; end to end through the real OpenAI provider on replayed HTTP traffic."""

from __future__ import annotations

import json
from typing import cast, override

import httpx2
import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import AISettings
from blogwriter.ai.ledger import MemoryLedger
from blogwriter.ai.providers import (
    AnthropicFoundryProvider,
    OpenAIResponsesProvider,
    create_anthropic_client,
    create_openai_client,
)
from blogwriter.ai.router import AIRouter
from blogwriter.ai.types import Task
from blogwriter.domain.ports import Embedder
from blogwriter.services.embedder import EMBEDDING_DIMENSIONS, RouterEmbedder
from blogwriter.services.testing import FakeEmbeddingRouter


async def token() -> str:
    return "entra-test-token"


class ArgsOnlyAISettings(AISettings):
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


async def test_texts_go_in_batches_with_the_plan_dimensions() -> None:
    router = FakeEmbeddingRouter()
    embedder: Embedder = RouterEmbedder(router, batch_size=2)
    texts = [f"article {n}" for n in range(5)]
    vectors = await embedder.embed(texts, origin="background", job_id="job-9")
    assert embedder.dimensions == EMBEDDING_DIMENSIONS == 1024
    assert len(vectors) == 5
    assert all(len(vector) == 1024 for vector in vectors)
    assert [len(call[0]) for call in router.calls] == [2, 2, 1]
    assert {call[1:] for call in router.calls} == {("background", "job-9", 1024)}
    again = await embedder.embed(texts[:1], origin="background")
    assert again[0] == vectors[0]


async def test_no_texts_make_no_call() -> None:
    router = FakeEmbeddingRouter()
    assert await RouterEmbedder(router).embed([], origin="owner") == []
    assert router.calls == []


async def test_a_short_result_fails_closed() -> None:
    embedder = RouterEmbedder(FakeEmbeddingRouter(short_by=1))
    with pytest.raises(ValueError, match="does not match"):
        await embedder.embed(["one", "two"], origin="owner")


def test_settings_are_checked() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        RouterEmbedder(FakeEmbeddingRouter(), dimensions=0)
    with pytest.raises(ValueError, match="batch_size"):
        RouterEmbedder(FakeEmbeddingRouter(), batch_size=0)


async def test_end_to_end_through_the_router_and_the_openai_provider() -> None:
    bodies: list[dict[str, object]] = []

    def embeddings(request: httpx2.Request) -> httpx2.Response:
        body = cast(dict[str, object], json.loads(request.content))
        bodies.append(body)
        inputs = cast(list[str], body["input"])
        dims = cast(int, body["dimensions"])
        data = [
            {"object": "embedding", "index": i, "embedding": [0.5] * dims}
            for i in range(len(inputs))
        ]
        usage = {"prompt_tokens": 7, "total_tokens": 7}
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-large",
                "usage": usage,
            },
        )

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no Claude call expected")

    openai = OpenAIResponsesProvider(
        create_openai_client(
            base_url="https://foundry-bw.openai.azure.com/openai/v1/",
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(embeddings)),
            max_retries=0,
        )
    )
    claude = AnthropicFoundryProvider(
        create_anthropic_client(
            resource="foundry-bw",
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)),
            max_retries=0,
        )
    )
    ledger = MemoryLedger()
    router = AIRouter.from_settings(
        ArgsOnlyAISettings(), {"openai": openai, "anthropic": claude}, ledger=ledger
    )
    try:
        vectors = await RouterEmbedder(router).embed(
            ["Agents need their own identities."], origin="background", job_id="job-1"
        )
    finally:
        await router.aclose()
    assert len(vectors[0]) == 1024
    (body,) = bodies
    assert (body["model"], body["dimensions"], body["encoding_format"]) == (
        "text-embedding-3-large",
        1024,
        "float",
    )
    (record,) = ledger.records
    assert (record.task, record.usage.input, record.job_id) == (Task.EMBED, 7, "job-1")
    assert record.cost_usd > 0
