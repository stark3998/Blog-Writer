"""The Anthropic-on-Foundry provider against replayed HTTP traffic (no network).

Plan: "Facts" > Models (AnthropicFoundry with Entra scope https://ai.azure.com/.default; no
temperature; thinking always on; no forced tool_choice; refusals on cyber topics), "AI layer"
(normalized events, structured output, refusal handling) and "Security model" (Claude only ever
sees anonymized or public data, tool-free for anonymized).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

import httpx2
import pytest
from pydantic import BaseModel, Field

from blogwriter.ai.limits import retry_reason
from blogwriter.ai.providers.anthropic_foundry import (
    AnthropicFoundryProvider,
    create_anthropic_client,
    foundry_anthropic_base_url,
)
from blogwriter.ai.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderStreamError,
    ProviderUnavailableError,
    RateLimitedError,
    UnsupportedRequestError,
)
from blogwriter.ai.schema import compile_schema
from blogwriter.ai.types import (
    Done,
    FunctionTool,
    Message,
    ModelSpec,
    NativeItems,
    Refusal,
    Request,
    Sensitivity,
    SensitivityError,
    StreamEvent,
    TextDelta,
    TextPart,
    ThinkingSummary,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
    Usage,
    UsageEvent,
    WebSearchTool,
)

TOKEN = "entra-test-token"  # noqa: S105 - a fake token for the mock transport
RESOURCE = "foundry-bw"
OPUS = ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", "high", 16000, True)
CACHE_1H = {"type": "ephemeral", "ttl": "1h"}


async def token() -> str:
    return TOKEN


class Draft(BaseModel):
    """A draft."""

    title: str = Field(max_length=80)
    body: str


class FetchArgs(BaseModel):
    url: str


FETCH = FunctionTool("fetch_page", "Fetch one page.", FetchArgs)


# --------------------------------------------------------------------------------------------
# Replay harness
# --------------------------------------------------------------------------------------------


class MockAPI:
    """Serves queued responses in order and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.queued: list[httpx2.Response | Exception] = []

    def queue(self, *responses: httpx2.Response | Exception) -> None:
        self.queued.extend(responses)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert self.queued, f"unexpected request {request.method} {request.url}"
        response = self.queued.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = -1) -> dict[str, object]:
        data: object = json.loads(self.requests[index].content)
        assert isinstance(data, dict)
        return data  # pyright: ignore[reportUnknownVariableType]


class Broken(httpx2.AsyncByteStream):
    """A response body that dies after its first chunk."""

    def __init__(self, first: bytes) -> None:
        self.first = first

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        raise httpx2.ReadError("connection reset by peer")

    async def aclose(self) -> None:
        return None


def sse_bytes(events: list[dict[str, object]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def sse(events: list[dict[str, object]]) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream", "request-id": "req_stream"},
        content=sse_bytes(events),
    )


def error_response(
    status: int, kind: str, headers: dict[str, str] | None = None
) -> httpx2.Response:
    body = {"type": "error", "error": {"type": kind, "message": "Something failed."}}
    return httpx2.Response(status, headers={"request-id": "req_err", **(headers or {})}, json=body)


def make_provider(api: MockAPI, *, max_retries: int = 0) -> AnthropicFoundryProvider:
    client = create_anthropic_client(
        resource=RESOURCE,
        token_provider=token,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
        max_retries=max_retries,
    )
    return AnthropicFoundryProvider(client)


@pytest.fixture
def api() -> MockAPI:
    return MockAPI()


@pytest.fixture
async def provider(api: MockAPI) -> AsyncIterator[AnthropicFoundryProvider]:
    instance = make_provider(api)
    yield instance
    await instance.aclose()


def request(**overrides: object) -> Request:
    fields: dict[str, object] = {
        "system": "You write in the owner's voice.",
        "messages": (Message.user("Draft the post from this brief."),),
        "sensitivity": Sensitivity.ANONYMIZED,
    }
    fields.update(overrides)
    return Request(**fields)  # pyright: ignore[reportArgumentType]


def sent_message(api: MockAPI, index: int) -> object:
    messages = api.body()["messages"]
    assert isinstance(messages, list)
    return messages[index]  # pyright: ignore[reportUnknownVariableType]


async def drain(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


async def drain_into(received: list[StreamEvent], stream: AsyncIterator[StreamEvent]) -> None:
    """Collect events until the stream ends or raises (to inspect what came before an error)."""
    async for event in stream:
        received.append(event)


# --------------------------------------------------------------------------------------------
# Realistic Messages API streams (synthetic data)
# --------------------------------------------------------------------------------------------


def message_start(*, cache_write: int = 2048, cache_read: int = 0) -> dict[str, object]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-opus-5-5",
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 40,
                "output_tokens": 1,
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": cache_write,
                },
            },
        },
    }


def thinking_block(index: int, *pieces: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        }
    ]
    events.extend(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "thinking_delta", "thinking": piece},
        }
        for piece in pieces
    )
    events.append(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "signature_delta", "signature": "EqQBCgIYAhIM-sig"},
        }
    )
    events.append({"type": "content_block_stop", "index": index})
    return events


def text_block(index: int, *pieces: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
    ]
    events.extend(
        {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": p}}
        for p in pieces
    )
    events.append({"type": "content_block_stop", "index": index})
    return events


def tool_block(index: int, *pieces: str) -> list[dict[str, object]]:
    start: dict[str, object] = {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "tool_use", "id": "toolu_01", "name": "fetch_page", "input": {}},
    }
    deltas: list[dict[str, object]] = [
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": piece},
        }
        for piece in pieces
    ]
    return [start, *deltas, {"type": "content_block_stop", "index": index}]


def finish(
    stop_reason: str, *, output: int = 512, details: dict[str, object] | None = None
) -> list[dict[str, object]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": details},
            "usage": {"output_tokens": output, "output_tokens_details": {"thinking_tokens": 200}},
        },
        {"type": "message_stop"},
    ]


PING: dict[str, object] = {"type": "ping"}


def draft_stream() -> list[dict[str, object]]:
    return [
        message_start(),
        PING,
        *thinking_block(0, "Outline: hook, ", "three points."),
        *text_block(1, "Passkeys ", "are here."),
        *finish("end_turn"),
    ]


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


async def test_calls_go_to_the_foundry_endpoint_with_an_entra_bearer_token(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()))
    await provider.complete(OPUS, request())
    (sent,) = api.requests
    assert sent.method == "POST"
    assert str(sent.url) == "https://foundry-bw.services.ai.azure.com/anthropic/v1/messages"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert "x-api-key" not in sent.headers
    assert "api-key" not in sent.headers
    assert sent.headers["anthropic-version"]


async def test_environment_variables_cannot_redirect_or_add_keys(
    api: MockAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "https://evil.example/anthropic/")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "evil")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "leaked-foundry-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked-anthropic-key")
    provider = make_provider(api)
    api.queue(sse(draft_stream()))
    await provider.complete(OPUS, request())
    await provider.aclose()
    (sent,) = api.requests
    assert sent.url.host == "foundry-bw.services.ai.azure.com"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert not {"x-api-key", "api-key"} & set(sent.headers)
    assert "leaked" not in str(sent.headers)


def test_base_url_comes_from_a_validated_resource_name() -> None:
    assert foundry_anthropic_base_url(RESOURCE) == (
        "https://foundry-bw.services.ai.azure.com/anthropic/"
    )
    with pytest.raises(ValueError, match="resource name"):
        create_anthropic_client(resource="evil.example/x", token_provider=token)


# --------------------------------------------------------------------------------------------
# Request shape
# --------------------------------------------------------------------------------------------


async def test_request_body_thinks_caches_and_never_samples(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()))
    await provider.complete(OPUS, request())
    body = api.body()
    assert body == {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Draft the post from this brief."}],
            }
        ],
        "system": [
            {"type": "text", "text": "You write in the owner's voice.", "cache_control": CACHE_1H}
        ],
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "high"},
        "stream": True,
    }
    for forbidden in ("temperature", "top_k", "top_p", "tool_choice"):
        assert forbidden not in body


async def test_thinking_display_and_effort_follow_the_spec(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()), sse(draft_stream()))
    quiet = ModelSpec("anthropic", "claude-opus-5-5", "opus", "max", 100, False, False)
    await provider.complete(quiet, request(system=""))
    body = api.body()
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["output_config"] == {"effort": "max"}
    assert "system" not in body
    await provider.complete(ModelSpec("anthropic", "opus", "opus", None, 100, False), request())
    assert "output_config" not in api.body()


async def test_structured_output_uses_the_compiled_schema(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    answer = json.dumps({"title": "Passkeys", "body": "They are here."})
    api.queue(sse([message_start(), *text_block(0, answer), *finish("end_turn")]))
    completion = await provider.complete(OPUS, request(output_schema=Draft))
    assert api.body()["output_config"] == {
        "effort": "high",
        "format": {
            "type": "json_schema",
            "schema": compile_schema("anthropic", Draft).json_schema(),
        },
    }
    assert compile_schema("anthropic", Draft).validate_json(completion.text) == Draft(
        title="Passkeys", body="They are here."
    )


def grammar_too_large() -> httpx2.Response:
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "The compiled grammar is too large, which would cause performance issues. "
            "Simplify your tool schemas or reduce the number of strict tools.",
        },
    }
    return httpx2.Response(400, headers={"request-id": "req_grammar"}, json=body)


async def test_a_schema_too_large_for_the_grammar_falls_back_to_prompted_json(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    answer = json.dumps({"title": "Passkeys", "body": "They are here."})
    api.queue(grammar_too_large())
    api.queue(
        sse([message_start(), *text_block(0, "```json\n" + answer + "\n```"), *finish("end_turn")])
    )
    completion = await provider.complete(OPUS, request(output_schema=Draft))
    first, second = api.body(0), api.body(1)
    assert "format" in cast(dict[str, object], first["output_config"])
    assert "format" not in cast(dict[str, object], second.get("output_config", {}))
    system = cast(list[dict[str, object]], second["system"])
    assert any("JSON Schema" in str(block["text"]) for block in system)
    # Fences and prose around the object are dropped before the router validates it.
    assert compile_schema("anthropic", Draft).validate_json(completion.text) == Draft(
        title="Passkeys", body="They are here."
    )
    # The provider remembers the schema: the next call goes straight to prompted JSON.
    api.queue(sse([message_start(), *text_block(0, answer), *finish("end_turn")]))
    await provider.complete(OPUS, request(output_schema=Draft))
    assert len(api.requests) == 3
    assert "format" not in cast(dict[str, object], api.body(2).get("output_config", {}))


async def test_other_bad_requests_are_not_retried(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(error_response(400, "invalid_request_error"))
    with pytest.raises(ProviderRequestError):
        await provider.complete(OPUS, request(output_schema=Draft))
    assert len(api.requests) == 1


async def test_messages_cover_breakpoints_tool_use_and_results(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()))
    messages = (
        Message("user", (TextPart("Brief: ...", cache_breakpoint=True), TextPart("Write it."))),
        Message(
            "assistant",
            (TextPart("Checking."), ToolCallPart("toolu_01", "fetch_page", '{"url": "https://a"}')),
        ),
        Message(
            "user",
            (
                TextPart("Continue."),
                ToolResultPart("toolu_01", "Page summary."),
                ToolResultPart("toolu_02", "", is_error=True),
            ),
        ),
    )
    public = request(messages=messages, sensitivity=Sensitivity.PUBLIC, tools=(FETCH,))
    await provider.complete(OPUS, public)
    assert api.body()["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Brief: ...", "cache_control": CACHE_1H},
                {"type": "text", "text": "Write it."},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "fetch_page",
                    "input": {"url": "https://a"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "is_error": False,
                    "content": "Page summary.",
                },
                {"type": "tool_result", "tool_use_id": "toolu_02", "is_error": True},
                {"type": "text", "text": "Continue."},
            ],
        },
    ]


async def test_native_thinking_blocks_are_replayed_verbatim(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()), sse(draft_stream()))
    thinking = {"type": "thinking", "thinking": "Plan.", "signature": "EqQBCgIYAhIM-sig"}
    tool_use = {"type": "tool_use", "id": "toolu_01", "name": "fetch_page", "input": {"url": "x"}}
    own = Message(
        "assistant",
        (ToolCallPart("toolu_01", "fetch_page", '{"url": "x"}'),),
        NativeItems("anthropic", (thinking, tool_use)),
    )
    results = Message.tool_results(ToolResultPart("toolu_01", "ok"))
    loop = request(
        messages=(Message.user("go"), own, results), sensitivity=Sensitivity.PUBLIC, tools=(FETCH,)
    )
    await provider.complete(OPUS, loop)
    assert sent_message(api, 1) == {"role": "assistant", "content": [thinking, tool_use]}

    other = Message("assistant", (TextPart("Earlier."),), NativeItems("openai", ({"x": 1},)))
    await provider.complete(OPUS, request(messages=(Message.user("a"), other, Message.user("b"))))
    assert sent_message(api, 1) == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Earlier."}],
    }


async def test_tools_are_strict_and_cached_when_there_is_no_system_prompt(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()), sse(draft_stream()))
    public = request(sensitivity=Sensitivity.PUBLIC, tools=(FETCH,))
    await provider.complete(OPUS, public)
    tool = {
        "name": "fetch_page",
        "description": "Fetch one page.",
        "input_schema": compile_schema("anthropic", FetchArgs).json_schema(),
        "strict": True,
    }
    assert api.body()["tools"] == [tool]
    assert "tool_choice" not in api.body()
    await provider.complete(
        OPUS, request(sensitivity=Sensitivity.PUBLIC, tools=(FETCH,), system="")
    )
    assert api.body()["tools"] == [{**tool, "cache_control": CACHE_1H}]


async def test_unparseable_tool_arguments_are_rejected_before_sending(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    broken = Message("assistant", (ToolCallPart("toolu_01", "fetch_page", "[1, 2]"),))
    loop = request(
        messages=(
            Message.user("go"),
            broken,
            Message.tool_results(ToolResultPart("toolu_01", "x")),
        ),
        sensitivity=Sensitivity.PUBLIC,
        tools=(FETCH,),
    )
    with pytest.raises(UnsupportedRequestError, match="JSON object"):
        await provider.complete(OPUS, loop)
    assert api.requests == []


# --------------------------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------------------------


async def test_stream_normalizes_thinking_text_and_usage(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse(draft_stream()))
    events = await drain(provider.stream(OPUS, request()))
    usage = Usage(input=2088, cached_input=0, cache_write=2048, output=512, reasoning=200)
    assert events[:5] == [
        ThinkingSummary("Outline: hook, "),
        ThinkingSummary("three points."),
        TextDelta("Passkeys "),
        TextDelta("are here."),
        UsageEvent(usage),
    ]
    done = events[5]
    assert isinstance(done, Done)
    assert len(events) == 6
    completion = done.completion
    assert done.finish_reason == completion.finish_reason == "stop"
    assert completion.text == "Passkeys are here."
    assert completion.usage == usage
    assert completion.model == "claude-opus-5-5"
    assert completion.response_id == "msg_01"
    assert completion.message is not None
    assert completion.message.parts == (TextPart("Passkeys are here."),)
    assert completion.message.native == NativeItems(
        "anthropic",
        (
            {
                "type": "thinking",
                "thinking": "Outline: hook, three points.",
                "signature": "EqQBCgIYAhIM-sig",
            },
            {"type": "text", "text": "Passkeys are here."},
        ),
    )


async def test_cache_reads_are_counted_as_cached_input(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(
        sse(
            [
                message_start(cache_write=0, cache_read=2048),
                *text_block(0, "x"),
                *finish("end_turn"),
            ]
        )
    )
    completion = await provider.complete(OPUS, request())
    assert completion.usage == Usage(input=2088, cached_input=2048, output=512, reasoning=200)
    assert completion.usage.uncached_input == 40


async def test_tool_use_is_streamed_as_a_complete_call(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(
        sse(
            [
                message_start(),
                *thinking_block(0, "Need the page."),
                *tool_block(1, '{"url": ', '"https://example.com/a"}'),
                *finish("tool_use", output=90),
            ]
        )
    )
    public = request(sensitivity=Sensitivity.PUBLIC, tools=(FETCH,))
    events = await drain(provider.stream(OPUS, public))
    call = ToolCall("toolu_01", "fetch_page", '{"url": "https://example.com/a"}')
    assert events[1] == call
    done = events[-1]
    assert isinstance(done, Done)
    assert done.finish_reason == "tool_calls"
    assert done.completion.tool_calls == (call,)
    assert done.completion.message is not None
    assert done.completion.message.parts == (
        ToolCallPart("toolu_01", "fetch_page", '{"url": "https://example.com/a"}'),
    )


@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("pause_turn", "pause"),
    ],
)
async def test_stop_reasons_map_to_finish_reasons(
    api: MockAPI, provider: AnthropicFoundryProvider, stop_reason: str, finish_reason: str
) -> None:
    api.queue(sse([message_start(), *text_block(0, "x"), *finish(stop_reason)]))
    completion = await provider.complete(OPUS, request())
    assert completion.finish_reason == finish_reason


async def test_refusals_carry_the_category_and_discard_output(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    details: dict[str, object] = {
        "type": "refusal",
        "category": "cyber",
        "explanation": "Exploit development.",
    }
    api.queue(
        sse(
            [
                message_start(),
                *text_block(0, "Here is how to "),
                *finish("refusal", details=details),
            ]
        )
    )
    events = await drain(provider.stream(OPUS, request()))
    assert events[0] == TextDelta("Here is how to ")  # already streamed: the caller discards it
    refusal = Refusal("cyber", "Exploit development.")
    assert events[1] == refusal
    assert isinstance(events[2], UsageEvent)
    done = events[3]
    assert isinstance(done, Done)
    assert done.finish_reason == "refusal"
    assert done.completion.refusal == refusal
    assert done.completion.text == ""
    assert done.completion.message is None
    assert done.completion.usage.output == 512


async def test_refusals_without_details(api: MockAPI, provider: AnthropicFoundryProvider) -> None:
    api.queue(sse([message_start(), *finish("refusal")]))
    completion = await provider.complete(OPUS, request())
    assert completion.refusal == Refusal(None, None)


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind_name", "headers", "error_kind", "retryable"),
    [
        (429, "rate_limit_error", {"retry-after": "3"}, RateLimitedError, True),
        (529, "overloaded_error", {}, ProviderUnavailableError, True),
        (500, "api_error", {}, ProviderUnavailableError, True),
        (401, "authentication_error", {}, ProviderAuthError, False),
        (403, "permission_error", {}, ProviderAuthError, False),
        (400, "invalid_request_error", {}, ProviderRequestError, False),
        (413, "request_too_large", {}, ProviderRequestError, False),
    ],
)
async def test_http_errors_map_to_provider_errors(
    api: MockAPI,
    provider: AnthropicFoundryProvider,
    status: int,
    kind_name: str,
    headers: dict[str, str],
    error_kind: type[ProviderError],
    retryable: bool,
) -> None:
    api.queue(error_response(status, kind_name, headers))
    with pytest.raises(error_kind) as caught:
        await provider.complete(OPUS, request())
    error = caught.value
    assert error.status_code == status
    assert error.code == kind_name
    assert error.retryable is retryable
    assert error.request_id == "req_err"
    assert "Something failed" not in str(error)
    if status == 429:
        assert error.retry_after == 3.0


async def test_overload_is_retried_before_streaming(api: MockAPI) -> None:
    provider = make_provider(api, max_retries=1)
    api.queue(error_response(529, "overloaded_error", {"retry-after-ms": "1"}), sse(draft_stream()))
    completion = await provider.complete(OPUS, request())
    await provider.aclose()
    assert completion.text == "Passkeys are here."
    assert len(api.requests) == 2


async def test_error_events_mid_stream_raise(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    error_event: dict[str, object] = {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }
    api.queue(sse([message_start(), *text_block(0, "Half a "), error_event]))
    received: list[StreamEvent] = []
    with pytest.raises(ProviderStreamError) as caught:
        await drain_into(received, provider.stream(OPUS, request()))
    assert received == [TextDelta("Half a ")]
    assert caught.value.code == "overloaded_error"
    assert caught.value.retryable is True


async def test_error_events_mid_stream_are_classified_by_their_type(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    # Inside a 200 stream only the broad type says what happened: a specific error_code this
    # client does not know must not turn an overload into a final, never-retried error.
    error_event: dict[str, object] = {
        "type": "error",
        "error": {
            "type": "overloaded_error",
            "message": "Overloaded",
            "details": {"error_code": "capacity_exhausted"},
        },
    }
    api.queue(sse([message_start(), *text_block(0, "Half a "), error_event]))
    with pytest.raises(ProviderStreamError) as caught:
        await provider.complete(OPUS, request())
    assert caught.value.code == "overloaded_error"
    assert caught.value.retryable is True
    assert retry_reason(caught.value) == "unavailable"

    # HTTP errors are classified by status, so they keep the more specific code.
    body = {
        "type": "error",
        "error": {
            "type": "rate_limit_error",
            "message": "Slow down.",
            "details": {"error_code": "ramp_limit_exceeded"},
        },
    }
    api.queue(httpx2.Response(429, headers={"request-id": "req_err"}, json=body))
    with pytest.raises(RateLimitedError) as limited:
        await provider.complete(OPUS, request())
    assert limited.value.code == "ramp_limit_exceeded"
    assert retry_reason(limited.value) == "rate_limited"


async def test_a_dropped_connection_mid_stream_raises(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    first = sse_bytes([message_start(), *text_block(0, "Partial")][:3])
    api.queue(
        httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken(first))
    )
    received: list[StreamEvent] = []
    with pytest.raises(ProviderStreamError) as caught:
        await drain_into(received, provider.stream(OPUS, request()))
    assert received == [TextDelta("Partial")]
    assert caught.value.retryable is True


async def test_connection_failures_before_streaming(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(httpx2.ConnectError("name resolution failed"))
    with pytest.raises(ProviderUnavailableError) as caught:
        await provider.complete(OPUS, request())
    assert caught.value.retryable is True


async def test_a_stream_without_message_stop_raises(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse([message_start(), *text_block(0, "Cut off")]))
    with pytest.raises(ProviderStreamError, match="before message_stop"):
        await provider.complete(OPUS, request())


async def test_a_message_without_a_stop_reason_raises(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(sse([message_start(), *text_block(0, "x"), {"type": "message_stop"}]))
    with pytest.raises(ProviderStreamError, match="without a stop reason") as caught:
        await provider.complete(OPUS, request())
    assert caught.value.usage is not None


async def test_unparseable_tool_json_does_not_leak_model_output(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    api.queue(
        sse([message_start(), *tool_block(0, '{"url" SECRET-DRAFT-TEXT'), *finish("tool_use")])
    )
    public = request(sensitivity=Sensitivity.PUBLIC, tools=(FETCH,))
    with pytest.raises(ProviderStreamError) as caught:
        await provider.complete(OPUS, public)
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


# --------------------------------------------------------------------------------------------
# Pre-flight rules
# --------------------------------------------------------------------------------------------


async def test_private_data_never_reaches_claude(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    with pytest.raises(SensitivityError, match="never goes to Claude"):
        await provider.complete(OPUS, request(sensitivity=Sensitivity.PRIVATE))
    with pytest.raises(SensitivityError):
        await drain(provider.stream(OPUS, request(sensitivity=Sensitivity.PRIVATE)))
    assert api.requests == []


async def test_unsupported_requests_fail_before_sending(
    api: MockAPI, provider: AnthropicFoundryProvider
) -> None:
    public = Sensitivity.PUBLIC
    with pytest.raises(UnsupportedRequestError, match="background"):
        await provider.complete(OPUS, request(sensitivity=public, background=True))
    with pytest.raises(UnsupportedRequestError, match="web_search"):
        await provider.complete(OPUS, request(sensitivity=public, tools=(WebSearchTool(),)))
    sol = ModelSpec("openai", "gpt-6.2-sol", "gpt-6.2-sol", "high", 100, True)
    with pytest.raises(UnsupportedRequestError, match="sent to the anthropic provider"):
        await provider.complete(sol, request())
    no_tools = ModelSpec("anthropic", "claude-opus-5-5", "opus", "high", 100, supports_tools=False)
    with pytest.raises(UnsupportedRequestError, match="without tools"):
        await provider.complete(no_tools, request(sensitivity=public, tools=(FETCH,)))
    assert api.requests == []
    assert provider.name == "anthropic"


def test_satisfies_the_provider_protocol(provider: AnthropicFoundryProvider) -> None:
    typed: Provider = provider  # checked statically by pyright
    assert typed.name == "anthropic"
