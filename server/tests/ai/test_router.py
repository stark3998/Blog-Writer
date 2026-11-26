"""The AI router: routing, policy before any call, retries, fallback, served_by, repair, ledger.

Plan: "AI layer" (one entry point; per-task routing with a served_by record on every step;
structured output re-validated with one repair turn, then fallback; on a refusal, partial output
is discarded and never checkpointed; no forced tool_choice; per-model semaphores with Opus = 1;
async rate limiters; sensitivity enforcement; cost ledger and brake), "Article pipeline" (drafts
produced by a fallback model are review-only) and "Security model" > Logging.

The providers here are fakes implementing the provider interface, except in the wire-level
test, which runs the real providers against replayed HTTP traffic. Nothing touches the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import override

import httpx2
import pytest
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.ai.config import AISettings, NoRouteError, RouteSettings, RouteTarget
from blogwriter.ai.ledger import BrakeEngaged, MemoryLedger, Origin, PriceTable, UsageRecord
from blogwriter.ai.policy import PolicyViolation
from blogwriter.ai.providers import (
    AnthropicFoundryProvider,
    OpenAIResponsesProvider,
    Provider,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    UnsupportedRequestError,
    collect,
    create_anthropic_client,
    create_openai_client,
)
from blogwriter.ai.providers.base import error_for_status
from blogwriter.ai.router import (
    DISCARD_EVENTS,
    REVIEW_ONLY_TASKS,
    AIRouter,
    Fallback,
    Finished,
    Repair,
    Retry,
    RouterEvent,
    RouterResult,
    ServedBy,
    create_providers,
)
from blogwriter.ai.schema import SchemaCompileError, StructuredOutputError
from blogwriter.ai.types import (
    Completion,
    Done,
    FinishReason,
    FunctionTool,
    Message,
    ModelSpec,
    ProviderName,
    Refusal,
    Request,
    Sensitivity,
    StreamEvent,
    Task,
    TextDelta,
    ThinkingSummary,
    Tool,
    ToolCall,
    Usage,
    UsageEvent,
    WebSearchTool,
)
from blogwriter.core.logging import JsonFormatter

OPUS = "claude-opus-5-5"
SOL = "gpt-6.2-sol"
ASTRA = "gpt-6.2-astra"
LUNA = "gpt-6-luna"
EMBEDDING = "text-embedding-3-large"
NOW = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
USAGE = Usage(input=1_000, output=200)
TOKEN = "entra-test-token"  # noqa: S105 - a fake token for the mock transport
PROMPT = "Draft the post from the brief about the Contoso migration."


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
    """Manual time: ``sleep`` records the wait, moves the clock forward and yields."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


type Script = Sequence[StreamEvent | BaseException]
"""What one call does: yield the events in order; an exception is raised where it stands."""


class FakeProvider:
    """A scripted provider. Each call takes the next script queued for its deployment."""

    def __init__(self, name: ProviderName) -> None:
        self._name: ProviderName = name
        self.scripts: dict[str, list[Script]] = {}
        self.calls: list[tuple[ModelSpec, Request]] = []
        self.in_flight = 0
        self.peak = 0
        self.gate: asyncio.Event | None = None
        self.closed = False

    @property
    def name(self) -> ProviderName:
        return self._name

    def queue(self, deployment: str, *scripts: Script) -> None:
        self.scripts.setdefault(deployment, []).extend(scripts)

    def requests_to(self, deployment: str) -> list[Request]:
        return [request for spec, request in self.calls if spec.deployment == deployment]

    async def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        self.calls.append((spec, request))
        queued = self.scripts.get(spec.deployment)
        assert queued, f"unexpected call to {spec.deployment}"
        script = queued.pop(0)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.gate is not None:
                await self.gate.wait()
            for step in script:
                await asyncio.sleep(0)
                if isinstance(step, BaseException):
                    raise step
                yield step
        finally:
            self.in_flight -= 1

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        return await collect(self.stream(spec, request), provider=self._name)

    async def aclose(self) -> None:
        self.closed = True


def reply(
    *pieces: str,
    usage: Usage = USAGE,
    finish: FinishReason = "stop",
    tool_calls: tuple[ToolCall, ...] = (),
) -> list[StreamEvent]:
    """A successful call that streams ``pieces``."""
    text = "".join(pieces)
    completion = Completion(
        text=text,
        finish_reason=finish,
        usage=usage,
        model="served-model",
        response_id="resp_ok",
        tool_calls=tool_calls,
        message=Message.assistant(text) if text.strip() else None,
    )
    return [
        *(TextDelta(piece) for piece in pieces),
        *tool_calls,
        UsageEvent(usage),
        Done(finish, completion),
    ]


def refusal(*pieces: str, category: str = "cyber", usage: Usage = USAGE) -> list[StreamEvent]:
    """A call that streams ``pieces`` and then refuses (as Claude can)."""
    refused = Refusal(category, None)
    completion = Completion(
        text="",
        finish_reason="refusal",
        usage=usage,
        model="served-model",
        response_id="resp_refused",
        refusal=refused,
    )
    return [
        *(TextDelta(piece) for piece in pieces),
        refused,
        UsageEvent(usage),
        Done("refusal", completion),
    ]


def http_error(status: int, code: str | None = None, **headers: str) -> ProviderError:
    return error_for_status("anthropic", status, code=code, request_id="req_1", headers=headers)


def overloaded_mid_stream() -> ProviderError:
    return error_for_status(
        "anthropic", 200, code="overloaded_error", request_id=None, headers={}, during_stream=True
    )


def ask(
    sensitivity: Sensitivity = Sensitivity.ANONYMIZED,
    *,
    schema: type[BaseModel] | None = None,
    tools: tuple[Tool, ...] = (),
    background: bool = False,
) -> Request:
    return Request(
        system="You write in the owner's voice.",
        messages=(Message.user(PROMPT),),
        sensitivity=sensitivity,
        output_schema=schema,
        tools=tools,
        background=background,
    )


class Outline(BaseModel):
    title: str
    sections: list[str] = Field(min_length=2)
    note: str = "none"


class FetchArgs(BaseModel):
    url: str


FETCH = FunctionTool("fetch_page", "Fetch one page returned by search.", FetchArgs)
SEARCH = WebSearchTool(allowed_domains=("nist.gov",))
VALID = '{"title": "Passkeys", "sections": ["Why", "How"], "note": null}'


@dataclass
class Harness:
    router: AIRouter
    openai: FakeProvider
    anthropic: FakeProvider
    ledger: MemoryLedger
    clock: FakeClock

    def fake(self, provider: ProviderName) -> FakeProvider:
        return self.openai if provider == "openai" else self.anthropic

    async def run(
        self, task: Task, request: Request, *, origin: Origin = "background"
    ) -> RouterResult:
        return await self.router.run(task, request, origin=origin, job_id="job-1")

    async def events(
        self, task: Task, request: Request, *, origin: Origin = "background"
    ) -> list[RouterEvent]:
        stream = self.router.stream(task, request, origin=origin, job_id="job-1")
        async with aclosing(stream) as events:
            return [event async for event in events]


def harness(
    settings: AISettings | None = None,
    *,
    ledger: MemoryLedger | None = None,
    jitter: Callable[[], float] = lambda: 0.5,
) -> Harness:
    openai = FakeProvider("openai")
    anthropic = FakeProvider("anthropic")
    providers: dict[ProviderName, Provider] = {"openai": openai, "anthropic": anthropic}
    ledger = ledger if ledger is not None else MemoryLedger()
    clock = FakeClock()
    router = AIRouter.from_settings(
        settings if settings is not None else ArgsOnlyAISettings(),
        providers,
        ledger=ledger,
        clock=clock,
        jitter=jitter,
        now=lambda: NOW,
    )
    return Harness(router, openai, anthropic, ledger, clock)


def final(events: Sequence[RouterEvent]) -> RouterResult:
    last = events[-1]
    assert isinstance(last, Finished)
    assert not any(isinstance(event, Finished) for event in events[:-1])
    return last.result


# --------------------------------------------------------------------------------------------
# Routing table and settings
# --------------------------------------------------------------------------------------------


def test_the_default_routing_table_matches_the_plan() -> None:
    anonymized, public = Sensitivity.ANONYMIZED, Sensitivity.PUBLIC
    expected = {
        Task.DRAFT: (OPUS, "high", SOL, "high", anonymized, False),
        Task.REVISE: (OPUS, "high", SOL, "high", anonymized, False),
        Task.OUTLINE: (SOL, "high", ASTRA, None, anonymized, False),
        Task.PLAN: (SOL, "high", ASTRA, None, anonymized, False),
        Task.INTERVIEW: (SOL, "high", ASTRA, None, anonymized, False),
        Task.SYNTHESIS: (SOL, "high", ASTRA, None, anonymized, False),
        Task.CRITIQUE: (ASTRA, None, SOL, "xhigh", anonymized, False),
        Task.FACT_CHECK: (SOL, None, ASTRA, None, anonymized, False),
        Task.ANONYMIZE: (SOL, None, ASTRA, None, Sensitivity.PRIVATE, False),
        Task.RESEARCH: (SOL, None, ASTRA, None, public, True),
        Task.QUICK_SEARCH: (SOL, "low", ASTRA, "low", public, True),
        Task.TRIAGE: (LUNA, None, None, None, public, False),
        Task.ENGAGEMENT: (SOL, "low", None, None, anonymized, False),
        Task.EMBED: (EMBEDDING, None, None, None, anonymized, False),
    }
    routing = ArgsOnlyAISettings().routing()
    actual = {
        task: (
            route.primary.family,
            route.primary.effort,
            route.fallback.family if route.fallback else None,
            route.fallback.effort if route.fallback else None,
            route.policy.max_sensitivity,
            route.policy.tools,
        )
        for task, route in routing.items()
    }
    assert actual == expected
    assert routing[Task.DRAFT].primary.provider == "anthropic"
    assert all(spec.provider == "openai" for spec in routing[Task.ANONYMIZE].chain)
    assert not any(spec.thinking_summary for spec in routing[Task.ANONYMIZE].chain)
    opus = routing.limits[OPUS]
    assert (
        opus.concurrency,
        opus.requests_per_minute,
        opus.input_tokens_per_minute,
        opus.output_tokens_per_minute,
    ) == (1, 40, 40_000, 8_000)
    assert routing.limits[SOL].concurrency is None
    assert frozenset({Task.DRAFT, Task.REVISE, Task.CRITIQUE}) == REVIEW_ONLY_TASKS
    with pytest.raises(NoRouteError, match="image"):
        routing.route(Task.IMAGE)


def test_settings_read_the_bw_ai_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("BW_AI_")]:
        monkeypatch.delenv(name)
    research = {
        "primary": {"model": "gpt-5.6-sol", "max_output_tokens": 16_000},
        "fallback": {"model": SOL},
        "max_sensitivity": "public",
        "tools": True,
    }
    values = {
        "BW_AI_OPENAI_BASE_URL": "https://foundry-bw.openai.azure.com/openai/v1",
        "BW_AI_ANTHROPIC_RESOURCE": "foundry-bw",
        "BW_AI_DEPLOYMENT_OPUS": "claude-prod",
        "BW_AI_MODEL_OVERRIDES": json.dumps(
            {"gpt-5.6-sol": {"provider": "openai", "deployment": "gpt-5.6-sol"}}
        ),
        "BW_AI_ROUTE_OVERRIDES": json.dumps({"research": research}),
        "BW_AI_PRICE_OVERRIDES": json.dumps({"gpt-5.6-sol": {"input": "1.25", "output": "10"}}),
        "BW_AI_WEB_SEARCH_USD_PER_1K": "12",
        "BW_AI_ALLOW_PRIVATE": "false",
        "BW_AI_RETRY_MAX_ATTEMPTS": "2",
        "BW_AI_BRAKE_DAILY_CAP_USD": "40",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = AISettings()
    assert settings.openai_base_url == "https://foundry-bw.openai.azure.com/openai/v1/"
    assert settings.anthropic_resource == "foundry-bw"
    assert settings.configured
    routing = settings.routing()
    assert routing[Task.DRAFT].primary.deployment == "claude-prod"
    assert routing[Task.DRAFT].primary.family == OPUS
    assert [spec.family for spec in routing[Task.RESEARCH].chain] == ["gpt-5.6-sol", SOL]
    assert routing[Task.OUTLINE].primary.family == SOL  # untouched routes keep their defaults
    prices = settings.price_table()
    assert prices.price("gpt-5.6-sol").input == Decimal("1.25")
    assert prices.web_search_per_1k == Decimal(12)
    assert settings.sensitivity_policy().allow_private is False
    assert settings.retry_policy().max_attempts == 2
    assert settings.brake_daily_cap_usd == Decimal(40)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"openai_base_url": "http://foundry-bw.openai.azure.com/openai/v1/"}, "v1 endpoint"),
        ({"openai_base_url": "https://foundry-bw.openai.azure.com/openai/"}, "v1 endpoint"),
        ({"anthropic_resource": "bad name!"}, "resource name"),
        ({"deployment_opus": "-dash"}, "deployment"),
        (
            {
                "model_overrides": {"gpt-5.6-sol": {"provider": "openai", "deployment": "x"}},
                "route_overrides": {
                    "research": {"primary": {"model": "gpt-5.6-sol"}, "max_sensitivity": "public"}
                },
            },
            "no price",
        ),
        (
            {
                "route_overrides": {
                    "triage": {"primary": {"model": "gpt-9"}, "max_sensitivity": "public"}
                }
            },
            "unknown model",
        ),
        (
            {
                "route_overrides": {
                    "draft": {
                        "primary": {"model": OPUS, "effort": "minimal"},
                        "max_sensitivity": "anonymized",
                    }
                }
            },
            "not supported by anthropic",
        ),
        (
            {"model_overrides": {"bad name": {"provider": "openai", "deployment": "x"}}},
            "model name",
        ),
        ({"retry_base_delay_s": 10.0, "retry_max_delay_s": 1.0}, "delays"),
    ],
)
def test_bad_settings_fail_at_startup(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ArgsOnlyAISettings.model_validate(overrides)


# --------------------------------------------------------------------------------------------
# Serving, served_by and the ledger
# --------------------------------------------------------------------------------------------


async def test_the_primary_serves_and_every_call_is_ledgered() -> None:
    h = harness()
    h.openai.queue(SOL, [ThinkingSummary("Plan the outline."), *reply("An ", "outline.")])
    request = ask()
    events = await h.events(Task.OUTLINE, request)
    assert events[:-1] == [
        ThinkingSummary("Plan the outline."),
        TextDelta("An "),
        TextDelta("outline."),
    ]
    result = final(events)
    assert result.text == "An outline."
    assert result.served_by == ServedBy(Task.OUTLINE, SOL, SOL, "openai", "high")
    assert not result.served_by.fallback
    assert not result.review_only
    assert not result.refused
    assert result.refusals == ()
    assert result.value is None
    expected = Decimal("0.004")  # 1K in x $2 + 200 out x $10, per 1M
    assert (result.usage, result.cost_usd) == (USAGE, expected)
    [spec, sent] = h.openai.calls[0]
    assert sent is request
    assert (spec.deployment, spec.effort, spec.max_output_tokens) == (SOL, "high", 16_000)
    [record] = h.ledger.records
    assert (
        record.task,
        record.model,
        record.deployment,
        record.provider,
        record.origin,
        record.outcome,
        record.job_id,
        record.response_id,
        record.cost_usd,
        record.at,
    ) == (
        Task.OUTLINE,
        SOL,
        SOL,
        "openai",
        "background",
        "completed",
        "job-1",
        "resp_ok",
        expected,
        NOW,
    )


async def test_a_refusal_falls_back_and_discards_partial_output() -> None:
    h = harness()
    h.anthropic.queue(OPUS, refusal("Here is the partial ", "draft"))
    h.openai.queue(SOL, reply("Clean ", "draft."))
    request = ask()
    events = await h.events(Task.DRAFT, request)
    assert events[:-1] == [
        TextDelta("Here is the partial "),
        TextDelta("draft"),
        Refusal("cyber", None),
        Fallback(OPUS, SOL, "refusal"),
        TextDelta("Clean "),
        TextDelta("draft."),
    ]
    result = final(events)
    assert result.text == "Clean draft."
    assert "partial" not in result.text
    assert result.completion.message == Message.assistant("Clean draft.")
    assert result.served_by == ServedBy(
        Task.DRAFT, SOL, SOL, "openai", "high", fallback_reason="refusal", review_only=True
    )
    assert result.review_only
    assert result.refusals == (Refusal("cyber", None),)
    assert h.openai.requests_to(SOL) == [request]  # the original request, no partial text
    assert [(item.deployment, item.outcome) for item in h.ledger.records] == [
        (OPUS, "refused"),
        (SOL, "completed"),
    ]
    assert result.usage == USAGE + USAGE
    assert result.cost_usd == sum(item.cost_usd for item in h.ledger.records) == Decimal("0.012")


@pytest.mark.parametrize(
    ("task", "primary", "fallback", "effort", "review_only"),
    [
        (Task.DRAFT, ("anthropic", OPUS), ("openai", SOL), "high", True),
        (Task.REVISE, ("anthropic", OPUS), ("openai", SOL), "high", True),
        (Task.CRITIQUE, ("openai", ASTRA), ("openai", SOL), "xhigh", True),
        (Task.OUTLINE, ("openai", SOL), ("openai", ASTRA), None, False),
        (Task.SYNTHESIS, ("openai", SOL), ("openai", ASTRA), None, False),
        (Task.FACT_CHECK, ("openai", SOL), ("openai", ASTRA), None, False),
        (Task.ANONYMIZE, ("openai", SOL), ("openai", ASTRA), None, False),
    ],
)
async def test_review_only_when_a_fallback_serves_drafting_or_critique(
    task: Task,
    primary: tuple[ProviderName, str],
    fallback: tuple[ProviderName, str],
    effort: str | None,
    review_only: bool,
) -> None:
    h = harness()
    h.fake(primary[0]).queue(primary[1], refusal())
    h.fake(fallback[0]).queue(fallback[1], reply("Served by the fallback."))
    result = await h.run(task, ask())
    assert result.served_by.deployment == fallback[1]
    assert result.served_by.provider == fallback[0]
    assert result.served_by.effort == effort
    assert result.served_by.fallback_reason == "refusal"
    assert result.review_only is review_only


async def test_the_primary_serving_a_draft_is_not_review_only() -> None:
    h = harness()
    h.anthropic.queue(OPUS, reply("A draft."))
    result = await h.run(Task.DRAFT, ask())
    assert result.served_by == ServedBy(Task.DRAFT, OPUS, OPUS, "anthropic", "high")
    assert not result.review_only
    assert h.openai.calls == []


def kept_text(events: Sequence[RouterEvent]) -> str:
    """The text a consumer applying the discard rule still holds when the stream ends."""
    text = ""
    for event in events:
        if isinstance(event, DISCARD_EVENTS):
            text = ""
        elif isinstance(event, TextDelta):
            text += event.text
    return text


async def test_a_consumer_following_the_discard_rule_never_keeps_refused_text() -> None:
    h = harness()
    h.openai.queue(SOL, refusal("Some partial ", "engagement reply"))
    events = await h.events(Task.ENGAGEMENT, ask())  # no fallback: the refusal is final
    assert not any(isinstance(event, Fallback) for event in events)
    assert TextDelta("Some partial ") in events
    assert kept_text(events) == ""
    assert final(events).text == ""
    h.anthropic.queue(OPUS, refusal("Here is the partial ", "draft"))
    h.openai.queue(SOL, reply("Clean ", "draft."))
    events = await h.events(Task.DRAFT, ask())
    assert kept_text(events) == final(events).text == "Clean draft."


async def test_a_refusal_without_a_fallback_is_returned_as_refused() -> None:
    h = harness()
    h.openai.queue(SOL, refusal("Some "))
    events = await h.events(Task.ENGAGEMENT, ask())
    assert not any(isinstance(event, Fallback) for event in events)
    result = final(events)
    assert result.refused
    assert result.text == ""
    assert result.served_by == ServedBy(Task.ENGAGEMENT, SOL, SOL, "openai", "low")
    assert result.refusals == (Refusal("cyber", None),)
    assert [item.outcome for item in h.ledger.records] == ["refused"]


# --------------------------------------------------------------------------------------------
# Retries and quota exhaustion
# --------------------------------------------------------------------------------------------


async def test_quota_exhaustion_retries_with_backoff_then_falls_back() -> None:
    h = harness()
    h.anthropic.queue(OPUS, *([http_error(429, "rate_limit_error")] for _ in range(4)))
    h.openai.queue(SOL, reply("Fallback draft."))
    events = await h.events(Task.DRAFT, ask())
    assert [event for event in events if isinstance(event, Retry | Fallback)] == [
        Retry(OPUS, 2, 0.75, "rate_limited"),
        Retry(OPUS, 3, 1.5, "rate_limited"),
        Retry(OPUS, 4, 3.0, "rate_limited"),
        Fallback(OPUS, SOL, "rate_limited"),
    ]
    assert h.clock.sleeps == [0.75, 1.5, 3.0]
    result = final(events)
    assert result.text == "Fallback draft."
    assert result.served_by.fallback_reason == "rate_limited"
    assert result.review_only
    assert (len(h.anthropic.calls), len(h.openai.calls)) == (4, 1)
    assert [item.deployment for item in h.ledger.records] == [SOL]  # 429s reported no usage


async def test_server_errors_exhausted_fall_back_as_unavailable() -> None:
    h = harness()
    h.openai.queue(ASTRA, *([http_error(503)] for _ in range(4)))
    h.openai.queue(SOL, reply("Critique."))
    result = await h.run(Task.CRITIQUE, ask())
    assert result.served_by.deployment == SOL
    assert result.served_by.effort == "xhigh"
    assert result.served_by.fallback_reason == "unavailable"
    assert result.review_only


async def test_a_retry_discards_partial_output_and_the_same_model_serves() -> None:
    h = harness()
    call = ToolCall("call_1", "fetch_page", '{"url": "https://nist.gov/a"}')
    h.openai.queue(
        SOL,
        [TextDelta("Half an "), call, overloaded_mid_stream()],
        reply("A whole answer."),
    )
    events = await h.events(Task.RESEARCH, ask(Sensitivity.PUBLIC, tools=(SEARCH, FETCH)))
    assert events[:-1] == [
        TextDelta("Half an "),
        call,
        Retry(SOL, 2, 0.75, "unavailable"),
        TextDelta("A whole answer."),
    ]
    result = final(events)
    assert result.text == "A whole answer."
    assert result.completion.tool_calls == ()  # the void attempt's tool call is not in the result
    assert result.served_by == ServedBy(Task.RESEARCH, SOL, SOL, "openai", None)


@pytest.mark.parametrize(
    "error",
    [
        http_error(429, "rate_limit_error"),
        http_error(500, "api_error"),
        http_error(502),
        http_error(503),
        http_error(529, "overloaded_error"),
        overloaded_mid_stream(),
        ProviderUnavailableError("connection failed", provider="anthropic", retryable=True),
    ],
    ids=["429", "500", "502", "503", "529", "overloaded-mid-stream", "no-response"],
)
async def test_rate_limits_and_server_errors_are_retried(error: ProviderError) -> None:
    h = harness()
    h.anthropic.queue(OPUS, [error], reply("Second try."))
    result = await h.run(Task.DRAFT, ask())
    assert result.served_by.deployment == OPUS
    assert result.served_by.fallback_reason is None
    assert len(h.anthropic.calls) == 2
    assert h.clock.sleeps == [0.75]
    assert h.openai.calls == []


@pytest.mark.parametrize(
    "error",
    [
        http_error(400, "invalid_request_error"),
        http_error(401),
        http_error(403),
        http_error(404, "not_found_error"),
        http_error(408),
        http_error(409),
        http_error(413),
        http_error(422),
        http_error(429, "rate_limit_error", **{"x-should-retry": "false"}),
        http_error(503, **{"x-should-retry": "false"}),
        UnsupportedRequestError("not servable"),
    ],
    ids=[
        "400",
        "401",
        "403",
        "404",
        "408",
        "409",
        "413",
        "422",
        "429-no-retry",
        "503-no-retry",
        "unsupported",
    ],
)
async def test_other_errors_are_neither_retried_nor_fallen_back_from(error: Exception) -> None:
    h = harness()
    h.anthropic.queue(OPUS, [error])
    with pytest.raises(type(error)):
        await h.run(Task.DRAFT, ask())
    assert len(h.anthropic.calls) == 1
    assert h.openai.calls == []
    assert h.clock.sleeps == []


async def test_a_long_retry_after_gives_up_at_once_and_cools_the_model_down() -> None:
    h = harness()
    h.anthropic.queue(OPUS, [http_error(429, "rate_limit_error", **{"retry-after": "600"})])
    h.openai.queue(SOL, reply("Fallback."), reply("Later fallback."))
    result = await h.run(Task.DRAFT, ask())
    assert result.served_by.fallback_reason == "rate_limited"
    assert h.clock.sleeps == []  # no point waiting ten minutes: the fallback serves now
    assert len(h.anthropic.calls) == 1
    # Every later Opus caller, on any task, falls back at once too: waiting the cooldown out
    # would hold the call, and Opus's only slot, for the full ten minutes.
    later = await h.run(Task.REVISE, ask())
    assert later.text == "Later fallback."
    assert later.served_by.fallback_reason == "rate_limited"
    assert later.review_only
    assert len(h.anthropic.calls) == 1  # Opus was not called again during its cooldown
    assert h.clock.sleeps == []
    # Once the cooldown is over, Opus serves again.
    h.clock.now += 600.0
    h.anthropic.queue(OPUS, reply("Opus again."))
    again = await h.run(Task.REVISE, ask())
    assert again.served_by.deployment == OPUS
    assert not again.served_by.fallback


async def test_callers_queued_for_the_slot_fall_back_from_a_long_cooldown_too() -> None:
    h = harness()
    gate = asyncio.Event()
    h.anthropic.gate = gate
    h.anthropic.queue(OPUS, [http_error(429, "rate_limit_error", **{"retry-after": "600"})])
    h.openai.queue(SOL, reply("First."), reply("Second."))
    first = asyncio.create_task(h.run(Task.DRAFT, ask()))
    for _ in range(20):
        await asyncio.sleep(0)
    second = asyncio.create_task(h.run(Task.REVISE, ask()))
    for _ in range(20):
        await asyncio.sleep(0)
    assert h.router.limiter(OPUS).in_flight == 1  # the second caller waits for the only slot
    gate.set()
    results = await asyncio.gather(first, second)
    # The cooldown started while the second caller was queued: it gets the slot, sees ten
    # minutes left, and falls back instead of sleeping on the slot.
    assert [result.text for result in results] == ["First.", "Second."]
    assert [result.served_by.fallback_reason for result in results] == ["rate_limited"] * 2
    assert len(h.anthropic.calls) == 1
    assert h.clock.sleeps == []
    assert h.router.limiter(OPUS).in_flight == 0


async def test_a_cooled_down_model_without_a_fallback_raises_at_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = harness()
    h.openai.queue(SOL, [http_error(429, "rate_limit_error", **{"retry-after": "3600"})])
    with pytest.raises(RateLimitedError):
        await h.run(Task.ENGAGEMENT, ask())
    # An owner click during the hour-long cooldown gets the rate limit now, not in an hour.
    with (
        caplog.at_level(logging.INFO, logger="blogwriter.ai.router"),
        pytest.raises(RateLimitedError) as limited,
    ):
        await h.run(Task.ENGAGEMENT, ask(), origin="owner")
    assert limited.value.retryable
    assert limited.value.retry_after == pytest.approx(3600.0)
    assert len(h.openai.calls) == 1
    assert h.clock.sleeps == []
    assert h.ledger.records == ()
    [line] = [json.loads(JsonFormatter().format(item)) for item in caplog.records]
    assert line["event"] == "ai_model_cooling_down"
    assert (line["deployment"], line["delay_ms"], line["job_id"]) == (SOL, 3_600_000, "job-1")
    assert "dropped_fields" not in line
    assert PROMPT not in json.dumps(line)


async def test_a_short_retry_after_is_honoured() -> None:
    h = harness()
    h.anthropic.queue(
        OPUS, [http_error(429, "rate_limit_error", **{"retry-after": "7"})], reply("Done.")
    )
    result = await h.run(Task.DRAFT, ask())
    assert result.served_by.deployment == OPUS
    assert h.clock.sleeps == [7.0]  # the cooldown ends with the backoff: no second wait


async def test_exhausted_retries_without_a_fallback_raise() -> None:
    h = harness()
    h.openai.queue(SOL, *([http_error(503)] for _ in range(4)))
    with pytest.raises(ProviderUnavailableError):
        await h.run(Task.ENGAGEMENT, ask())
    assert len(h.openai.calls) == 4
    assert h.clock.sleeps == [0.75, 1.5, 3.0]


async def test_failed_calls_that_report_usage_are_still_billed() -> None:
    h = harness()
    failed = ProviderUnavailableError(
        "server_error", provider="openai", retryable=True, status_code=500, usage=USAGE
    )
    h.openai.queue(SOL, [failed], reply("Outline."))
    result = await h.run(Task.OUTLINE, ask())
    assert [item.outcome for item in h.ledger.records] == ["failed", "completed"]
    assert result.usage == USAGE + USAGE
    assert result.cost_usd == Decimal("0.008")


# --------------------------------------------------------------------------------------------
# Structured output
# --------------------------------------------------------------------------------------------


async def test_structured_output_is_revalidated_and_repaired_once() -> None:
    h = harness()
    invalid = '{"title": "Passkeys", "sections": ["Why"], "note": null}'
    h.openai.queue(SOL, reply(invalid), reply(VALID))
    events = await h.events(Task.OUTLINE, ask(schema=Outline))
    assert [event for event in events if isinstance(event, Repair)] == [Repair(SOL, 1)]
    result = final(events)
    # The OpenAI schema is strict, so the optional field comes back as null: read as absent.
    assert result.output(Outline) == Outline(title="Passkeys", sections=["Why", "How"])
    assert result.served_by.repaired
    assert not result.served_by.fallback
    first, repair = h.openai.requests_to(SOL)
    assert len(first.messages) == 1
    assert [message.role for message in repair.messages] == ["user", "assistant", "user"]
    assert repair.messages[1].text == invalid
    assert "$.sections" in repair.messages[2].text
    assert len(h.ledger.records) == 2


async def test_output_still_invalid_after_the_repair_falls_back() -> None:
    h = harness()
    h.anthropic.queue(OPUS, reply('{"title": "Passkeys"}'), reply("not json"))
    h.openai.queue(SOL, reply(VALID))
    events = await h.events(Task.DRAFT, ask(schema=Outline))
    assert [event for event in events if isinstance(event, Repair | Fallback)] == [
        Repair(OPUS, 1),
        Fallback(OPUS, SOL, "invalid_output"),
    ]
    result = final(events)
    assert result.output(Outline).sections == ["Why", "How"]
    assert result.served_by.fallback_reason == "invalid_output"
    assert result.review_only
    assert not result.served_by.repaired


async def test_invalid_output_without_a_fallback_raises() -> None:
    h = harness()
    h.openai.queue(SOL, reply('{"title": 1}'), reply('{"title": 2}'))
    with pytest.raises(StructuredOutputError) as failed:
        await h.run(Task.ENGAGEMENT, ask(schema=Outline))
    assert failed.value.reason == "invalid"
    assert failed.value.problems
    assert len(h.ledger.records) == 2


async def test_truncated_structured_output_falls_back_without_a_repair_turn() -> None:
    h = harness()
    h.anthropic.queue(OPUS, reply('{"title": "Pass', finish="length"))
    h.openai.queue(SOL, reply(VALID))
    events = await h.events(Task.DRAFT, ask(schema=Outline))
    assert not any(isinstance(event, Repair) for event in events)
    assert Fallback(OPUS, SOL, "incomplete_output") in events
    assert final(events).output(Outline).title == "Passkeys"
    h.openai.queue(SOL, reply('{"title": "Pass', finish="length"))
    with pytest.raises(StructuredOutputError) as failed:
        await h.run(Task.ENGAGEMENT, ask(schema=Outline))
    assert failed.value.reason == "incomplete"


async def test_a_refused_repair_turn_falls_back() -> None:
    h = harness()
    h.openai.queue(SOL, reply('{"title": "x"}'), refusal())
    h.openai.queue(ASTRA, reply(VALID))
    result = await h.run(Task.OUTLINE, ask(schema=Outline))
    assert result.served_by.deployment == ASTRA
    assert result.served_by.fallback_reason == "refusal"
    assert result.refusals == (Refusal("cyber", None),)


async def test_tool_calls_come_back_without_validation() -> None:
    h = harness()
    call = ToolCall("call_1", "fetch_page", '{"url": "https://nist.gov/a"}')
    h.openai.queue(SOL, reply(finish="tool_calls", tool_calls=(call,)))
    request = ask(Sensitivity.PUBLIC, schema=Outline, tools=(SEARCH, FETCH))
    events = await h.events(Task.RESEARCH, request)
    result = final(events)
    assert result.value is None
    assert result.completion.tool_calls == (call,)
    assert not any(isinstance(event, Repair) for event in events)
    with pytest.raises(TypeError, match="NoneType"):
        result.output(Outline)


async def test_schemas_that_cannot_compile_fail_before_any_call() -> None:
    class Loose(BaseModel):
        data: dict[str, int]

    h = harness()
    with pytest.raises(ValueError, match="dict"):
        await h.run(Task.OUTLINE, ask(schema=Loose))
    assert h.openai.calls == []


# --------------------------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------------------------


async def test_opus_serves_one_call_at_a_time() -> None:
    h = harness()
    gate = asyncio.Event()
    h.anthropic.gate = gate
    h.anthropic.queue(OPUS, *(reply("Draft.") for _ in range(4)))
    tasks = (Task.DRAFT, Task.REVISE, Task.DRAFT, Task.REVISE)  # both share the Opus limiter
    running = [asyncio.create_task(h.run(task, ask())) for task in tasks]
    for _ in range(20):
        await asyncio.sleep(0)
    assert h.anthropic.in_flight == 1
    assert h.router.limiter(OPUS).in_flight == 1
    gate.set()
    results = await asyncio.gather(*running)
    assert h.anthropic.peak == 1
    assert [result.served_by.deployment for result in results] == [OPUS] * 4
    assert h.router.limiter(OPUS).in_flight == 0

    # Sol has no semaphore: the same load runs side by side.
    sol_gate = asyncio.Event()
    h.openai.gate = sol_gate
    h.openai.queue(SOL, *(reply("Outline.") for _ in range(4)))
    running = [asyncio.create_task(h.run(Task.OUTLINE, ask())) for _ in range(4)]
    for _ in range(20):
        await asyncio.sleep(0)
    assert h.openai.in_flight == 4
    sol_gate.set()
    await asyncio.gather(*running)
    assert h.openai.peak == 4


async def test_an_abandoned_stream_releases_the_model_slot() -> None:
    h = harness()
    h.anthropic.queue(OPUS, reply("First ", "draft."), reply("Second."))
    stream = h.router.stream(Task.DRAFT, ask(), origin="owner")
    async with aclosing(stream) as events:
        async for event in events:
            assert event == TextDelta("First ")
            break
    assert h.router.limiter(OPUS).in_flight == 0
    assert h.anthropic.in_flight == 0
    result = await h.run(Task.DRAFT, ask())
    assert result.text == "Second."


# --------------------------------------------------------------------------------------------
# Checks before any call: policy, routes, brake
# --------------------------------------------------------------------------------------------


def forged_tools_request(sensitivity: Sensitivity) -> Request:
    built = ask(Sensitivity.PUBLIC, tools=(SEARCH,))
    object.__setattr__(built, "sensitivity", sensitivity)
    return built


@pytest.mark.parametrize(
    ("task", "request_factory", "rule"),
    [
        (Task.RESEARCH, lambda: ask(Sensitivity.ANONYMIZED), "above_task_max"),
        (Task.DRAFT, lambda: ask(Sensitivity.PRIVATE), "above_task_max"),
        (Task.TRIAGE, lambda: ask(Sensitivity.ANONYMIZED), "above_task_max"),
        (Task.ENGAGEMENT, lambda: ask(Sensitivity.PUBLIC, tools=(FETCH,)), "tools_not_allowed"),
        (Task.DRAFT, lambda: ask(Sensitivity.PUBLIC, tools=(FETCH,)), "tools_not_allowed"),
        # Every tool-enabled task is public-only, so non-public tools fail on the class first.
        (Task.QUICK_SEARCH, lambda: forged_tools_request(Sensitivity.ANONYMIZED), "above_task_max"),
    ],
)
async def test_policy_violations_stop_everything_before_any_call(
    task: Task, request_factory: Callable[[], Request], rule: str
) -> None:
    h = harness()
    with pytest.raises(PolicyViolation) as violation:
        await h.run(task, request_factory())
    assert violation.value.rule == rule
    with pytest.raises(PolicyViolation):
        await h.events(task, request_factory())
    assert h.openai.calls == []
    assert h.anthropic.calls == []
    assert h.ledger.records == ()


async def test_private_data_goes_only_to_the_tool_free_anonymizer() -> None:
    h = harness()
    h.openai.queue(SOL, refusal())
    h.openai.queue(ASTRA, reply("Anonymized wording."))
    result = await h.run(Task.ANONYMIZE, ask(Sensitivity.PRIVATE), origin="owner")
    assert result.text == "Anonymized wording."
    assert h.anthropic.calls == []
    for spec, sent in h.openai.calls:
        assert spec.provider == "openai"
        assert not spec.thinking_summary
        assert sent.tools == ()
    strict = harness(ArgsOnlyAISettings(allow_private=False))
    with pytest.raises(PolicyViolation) as violation:
        await strict.run(Task.ANONYMIZE, ask(Sensitivity.PRIVATE), origin="owner")
    assert violation.value.rule == "private_disabled"
    assert strict.openai.calls == []


async def test_unsupported_and_unrouted_requests_are_refused_before_any_call() -> None:
    h = harness()
    with pytest.raises(UnsupportedRequestError, match="embed"):
        await h.run(Task.EMBED, ask())
    with pytest.raises(NoRouteError):
        await h.run(Task.IMAGE, ask(Sensitivity.PUBLIC))
    with pytest.raises(UnsupportedRequestError, match="background"):
        await h.run(Task.RESEARCH, ask(Sensitivity.PUBLIC, background=True))
    assert h.openai.calls == []


async def test_the_brake_pauses_background_work_but_never_the_owner() -> None:
    ledger = MemoryLedger()
    await ledger.record(
        UsageRecord(
            task=Task.RESEARCH,
            model=SOL,
            deployment=SOL,
            provider="openai",
            usage=USAGE,
            cost_usd=Decimal(30),
            at=NOW,
            origin="background",
            outcome="completed",
        )
    )
    h = harness(ledger=ledger)
    h.openai.queue(SOL, reply("Owner outline."))
    with pytest.raises(BrakeEngaged) as engaged:
        await h.run(Task.OUTLINE, ask())
    assert engaged.value.status is not None
    assert engaged.value.status.threshold_usd == Decimal(25)
    assert h.openai.calls == []
    result = await h.run(Task.OUTLINE, ask(), origin="owner")
    assert result.text == "Owner outline."
    assert h.ledger.records[-1].origin == "owner"


# --------------------------------------------------------------------------------------------
# Wire level: real providers, replayed HTTP
# --------------------------------------------------------------------------------------------


def sse(events: list[dict[str, object]]) -> httpx2.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def openai_answer(text: str) -> httpx2.Response:
    message: dict[str, object] = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
    }
    final_response: dict[str, object] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1764150000,
        "model": SOL,
        "status": "completed",
        "output": [message],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "store": False,
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 150,
        },
    }
    started: dict[str, object] = {**final_response, "status": "in_progress", "output": []}
    return sse(
        [
            {"type": "response.created", "sequence_number": 0, "response": started},
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": {**message, "status": "in_progress", "content": []},
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 2,
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": text,
                "logprobs": [],
            },
            {"type": "response.completed", "sequence_number": 3, "response": final_response},
        ]
    )


def claude_answer(text: str, *, stop_reason: str = "end_turn") -> httpx2.Response:
    details: dict[str, object] | None = (
        {"type": "refusal", "category": "cyber", "explanation": "Exploit development."}
        if stop_reason == "refusal"
        else None
    )
    usage = {
        "input_tokens": 40,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    return sse(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": OPUS,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": usage,
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                    "stop_details": details,
                },
                "usage": {"output_tokens": 50},
            },
            {"type": "message_stop"},
        ]
    )


async def test_no_forced_tool_choice_in_any_outgoing_request() -> None:
    bodies: list[dict[str, object]] = []
    claude_replies = [claude_answer("Here is how ", stop_reason="refusal"), claude_answer("Draft.")]

    def record(request: httpx2.Request) -> None:
        body: object = json.loads(request.content)
        assert isinstance(body, dict)
        bodies.append(body)  # pyright: ignore[reportUnknownArgumentType]

    def openai_handler(request: httpx2.Request) -> httpx2.Response:
        record(request)
        return openai_answer("Answer.")

    def claude_handler(request: httpx2.Request) -> httpx2.Response:
        record(request)
        return claude_replies.pop(0)

    openai = OpenAIResponsesProvider(
        create_openai_client(
            base_url="https://foundry-bw.openai.azure.com/openai/v1/",
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(openai_handler)),
            max_retries=0,
        )
    )
    claude = AnthropicFoundryProvider(
        create_anthropic_client(
            resource="foundry-bw",
            token_provider=token,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(claude_handler)),
            max_retries=0,
        )
    )
    router = AIRouter.from_settings(
        ArgsOnlyAISettings(),
        {"openai": openai, "anthropic": claude},
        ledger=MemoryLedger(),
        clock=FakeClock(),
        now=lambda: NOW,
    )
    try:
        research = await router.run(
            Task.RESEARCH, ask(Sensitivity.PUBLIC, tools=(SEARCH, FETCH)), origin="owner"
        )
        quick = await router.run(
            Task.QUICK_SEARCH, ask(Sensitivity.PUBLIC, tools=(SEARCH,)), origin="owner"
        )
        draft = await router.run(Task.DRAFT, ask(), origin="owner")  # Claude refuses: Sol serves
        opus = await router.run(Task.REVISE, ask(), origin="owner")
    finally:
        await router.aclose()
    assert (research.text, quick.text, draft.text, opus.text) == (
        "Answer.",
        "Answer.",
        "Answer.",
        "Draft.",
    )
    assert draft.served_by.fallback_reason == "refusal"
    assert draft.refusals == (Refusal("cyber", "Exploit development."),)
    assert draft.review_only
    assert len(bodies) == 5
    assert sum("tools" in body for body in bodies) == 2  # the two tool-enabled calls
    for body in bodies:
        assert "tool_choice" not in body
        assert "temperature" not in body


# --------------------------------------------------------------------------------------------
# Logging, ledger failures, wiring
# --------------------------------------------------------------------------------------------


async def test_logs_carry_counts_and_costs_never_content(caplog: pytest.LogCaptureFixture) -> None:
    h = harness()
    h.anthropic.queue(OPUS, refusal("Secret partial text"))
    h.openai.queue(SOL, reply("Final draft body."))
    with caplog.at_level(logging.INFO, logger="blogwriter.ai"):
        await h.run(Task.DRAFT, ask())
    formatter = JsonFormatter()
    lines = [json.loads(formatter.format(item)) for item in caplog.records]
    assert [line["event"] for line in lines] == ["ai_model_call", "ai_fallback", "ai_model_call"]
    for line in lines:
        assert "dropped_fields" not in line
        text = json.dumps(line)
        for content in (PROMPT, "Secret partial text", "Final draft body.", "owner's voice"):
            assert content not in text
    assert lines[0]["outcome"] == "refused"
    assert lines[0]["cost_usd"] == 0.008
    assert lines[1]["fallback_reason"] == "refusal"
    assert lines[2]["input_tokens"] == 1_000


async def test_a_failing_ledger_never_loses_the_result(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenLedger(MemoryLedger):
        @override
        async def record(self, record: UsageRecord) -> None:
            raise ConnectionError("store down")

    h = harness(ledger=BrokenLedger())
    h.openai.queue(SOL, reply("Outline."))
    with caplog.at_level(logging.ERROR, logger="blogwriter.ai.router"):
        result = await h.run(Task.OUTLINE, ask(), origin="owner")
    assert result.text == "Outline."
    assert result.cost_usd == Decimal("0.004")
    assert [item.msg for item in caplog.records] == ["ai_ledger_write_failed"]


async def test_spend_the_ledger_failed_to_store_still_brakes_background_work() -> None:
    class WriteFails(MemoryLedger):
        @override
        async def record(self, record: UsageRecord) -> None:
            raise ConnectionError("store throttled")

    settings = ArgsOnlyAISettings(brake_daily_cap_usd=Decimal("0.005"))
    h = harness(settings, ledger=WriteFails())
    h.openai.queue(SOL, reply("One."), reply("Two."), reply("Owner."))
    await h.run(Task.OUTLINE, ask())  # $0.004, not stored
    await h.run(Task.OUTLINE, ask())  # $0.008 today: over the $0.005 cap
    with pytest.raises(BrakeEngaged) as engaged:
        await h.run(Task.OUTLINE, ask())
    assert engaged.value.status is not None
    assert engaged.value.status.spend_usd == Decimal("0.008")
    assert len(h.openai.calls) == 2
    owner = await h.run(Task.OUTLINE, ask(), origin="owner")  # the owner is never braked
    assert owner.text == "Owner."
    assert h.ledger.records == ()


async def test_tool_schemas_compile_for_every_model_of_the_chain_before_any_call() -> None:
    class Link(BaseModel):
        value: int
        next: Link  # required recursion: OpenAI can express it, Anthropic cannot

    class WalkArgs(BaseModel):
        start: Link

    research = RouteSettings(
        primary=RouteTarget(model=SOL),
        fallback=RouteTarget(model=OPUS),
        max_sensitivity=Sensitivity.PUBLIC,
        tools=True,
    )
    h = harness(ArgsOnlyAISettings(route_overrides={Task.RESEARCH: research}))
    walk = FunctionTool("walk", "Walk a linked chain.", WalkArgs)
    with pytest.raises(SchemaCompileError, match="recursive"):
        await h.run(Task.RESEARCH, ask(Sensitivity.PUBLIC, tools=(walk,)))
    assert h.openai.calls == []  # Sol was not paid for before the fallback could fail
    assert h.anthropic.calls == []


async def test_router_wiring_is_checked_at_construction() -> None:
    settings = ArgsOnlyAISettings(deployment_opus="claude-prod")
    openai = FakeProvider("openai")
    claude = FakeProvider("anthropic")
    router = AIRouter.from_settings(
        settings, {"openai": openai, "anthropic": claude}, ledger=MemoryLedger(), clock=FakeClock()
    )
    claude.queue("claude-prod", reply("Draft."))
    result = await router.run(Task.DRAFT, ask(), origin="owner")
    assert (result.served_by.model, result.served_by.deployment) == (OPUS, "claude-prod")
    assert router.routing is not None
    await router.aclose()
    assert openai.closed
    assert claude.closed

    with pytest.raises(ValueError, match="no anthropic provider"):
        AIRouter.from_settings(settings, {"openai": openai}, ledger=MemoryLedger())
    with pytest.raises(ValueError, match="registered as"):
        AIRouter.from_settings(
            settings, {"openai": claude, "anthropic": openai}, ledger=MemoryLedger()
        )
    with pytest.raises(ValueError, match="no price"):
        AIRouter(
            settings.routing(),
            {"openai": openai, "anthropic": claude},
            prices=PriceTable({}, web_search_per_1k=Decimal(14)),
            ledger=MemoryLedger(),
        )


async def test_create_providers_turns_sdk_retries_off() -> None:
    settings = ArgsOnlyAISettings(
        openai_base_url="https://foundry-bw.openai.azure.com/openai/v1/",
        anthropic_resource="foundry-bw",
    )
    providers = create_providers(settings, token, timeout=30.0)
    openai = providers["openai"]
    claude = providers["anthropic"]
    assert isinstance(openai, OpenAIResponsesProvider)
    assert isinstance(claude, AnthropicFoundryProvider)
    assert openai._client.max_retries == 0  # pyright: ignore[reportPrivateUsage]
    assert claude._client.max_retries == 0  # pyright: ignore[reportPrivateUsage]
    for provider in providers.values():
        await provider.aclose()
    assert create_providers(ArgsOnlyAISettings(), token) == {}
    assert not ArgsOnlyAISettings().configured


def test_served_by_and_result_helpers() -> None:
    served = ServedBy(
        Task.CRITIQUE, SOL, "sol-prod", "openai", "xhigh", "rate_limited", review_only=True
    )
    assert served.fallback
    assert served.to_dict() == {
        "task": "critique",
        "model": SOL,
        "deployment": "sol-prod",
        "provider": "openai",
        "effort": "xhigh",
        "fallbackReason": "rate_limited",
        "reviewOnly": True,
        "repaired": False,
    }
    completion = Completion(
        text=VALID, finish_reason="stop", usage=USAGE, model=SOL, response_id=None
    )
    outline = Outline(title="Passkeys", sections=["Why", "How"])
    result = RouterResult(completion, served, USAGE, Decimal(1), value=outline)
    assert result.output(Outline) is outline
    assert result.review_only
    with pytest.raises(TypeError, match="Outline, not FetchArgs"):
        result.output(FetchArgs)
    assert (Refusal, Retry, Repair, Fallback) == DISCARD_EVENTS
