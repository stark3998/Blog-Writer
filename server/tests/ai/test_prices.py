"""``AIRouter.prices`` (contract I-11): the price table every call is costed with, read-only.

kb's models view shows it (``tests/kb/test_cadence_models.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from types import MappingProxyType
from typing import override

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai import DEFAULT_PRICES, AIRouter, AISettings, MemoryLedger, ModelPrice, PriceTable
from blogwriter.ai.providers import Provider, collect
from blogwriter.ai.types import (
    Completion,
    Done,
    Message,
    ModelSpec,
    ProviderName,
    Request,
    Sensitivity,
    StreamEvent,
    Task,
    TextDelta,
    Usage,
    UsageEvent,
)

OPUS = "claude-opus-5-5"
USAGE = Usage(input=10_000, cached_input=4_000, cache_write=1_000, output=2_000)


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


class OneAnswer:
    """A provider that answers every call with one short text and a fixed usage."""

    def __init__(self, name: ProviderName) -> None:
        self._name: ProviderName = name

    @property
    def name(self) -> ProviderName:
        return self._name

    async def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        completion = Completion(
            text="Done.",
            finish_reason="stop",
            usage=USAGE,
            model=spec.deployment,
            response_id="resp_1",
            message=Message.assistant("Done."),
        )
        yield TextDelta("Done.")
        yield UsageEvent(USAGE)
        yield Done("stop", completion)

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        return await collect(self.stream(spec, request), provider=self._name)

    async def aclose(self) -> None:
        return None


def providers() -> dict[ProviderName, Provider]:
    return {"openai": OneAnswer("openai"), "anthropic": OneAnswer("anthropic")}


def test_the_router_exposes_the_table_it_was_given() -> None:
    settings = ArgsOnlyAISettings()
    table = PriceTable(dict(DEFAULT_PRICES), web_search_per_1k=Decimal("9.5"))
    router = AIRouter(settings.routing(), providers(), prices=table, ledger=MemoryLedger())
    assert router.prices is table
    assert router.prices.web_search_per_1k == Decimal("9.5")


def test_from_settings_prices_are_the_settings_table_with_overrides() -> None:
    opus = ModelPrice(input=Decimal("3"), output=Decimal("15"), cached_input=Decimal("0.3"))
    settings = ArgsOnlyAISettings(price_overrides={OPUS: opus}, web_search_usd_per_1k=Decimal("12"))
    router = AIRouter.from_settings(settings, providers(), ledger=MemoryLedger())
    assert router.prices.price(OPUS) == opus
    assert router.prices.web_search_per_1k == Decimal("12")
    assert dict(router.prices.models) == dict(settings.price_table().models)
    assert set(router.routing.limits) <= set(router.prices.models)


def test_the_prices_are_read_only() -> None:
    router = AIRouter.from_settings(ArgsOnlyAISettings(), providers(), ledger=MemoryLedger())
    with pytest.raises(AttributeError):
        setattr(router, "prices", PriceTable({}, web_search_per_1k=Decimal(0)))  # noqa: B010
    assert isinstance(router.prices.models, MappingProxyType)
    with pytest.raises(TypeError):
        router.prices.models[OPUS] = ModelPrice(input=Decimal(0))  # pyright: ignore[reportIndexIssue]


async def test_every_call_is_costed_with_these_prices() -> None:
    opus = ModelPrice(
        input=Decimal("5"),
        output=Decimal("25"),
        cached_input=Decimal("1"),
        cache_write=Decimal("7"),
    )
    ledger = MemoryLedger()
    router = AIRouter.from_settings(
        ArgsOnlyAISettings(price_overrides={OPUS: opus}), providers(), ledger=ledger
    )
    request = Request(
        system="You write in the owner's voice.",
        messages=(Message.user("Draft it."),),
        sensitivity=Sensitivity.ANONYMIZED,
    )
    result = await router.run(Task.DRAFT, request, origin="owner")
    assert result.served_by.model == OPUS
    expected = router.prices.cost(OPUS, USAGE)
    assert expected == (5_000 * 5 + 4_000 * 1 + 1_000 * 7 + 2_000 * 25) / Decimal(1_000_000)
    assert result.cost_usd == expected
    assert [record.cost_usd for record in ledger.records] == [expected]
