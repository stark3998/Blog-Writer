"""The Azure OpenAI Responses provider against replayed HTTP traffic (no network).

Plan: "AI layer" (normalized streaming events, structured output, refusals, no forced tool_choice),
"Research engine" > Calls (store=false; tool loops replay with reasoning.encrypted_content;
background runs record the id, then DELETE) and "Facts" > Search (web_search with
allowed_domains; Web IQ terms need the cited sources).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator, Iterator

import httpx2
import pytest
from pydantic import BaseModel, Field

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
from blogwriter.ai.providers.openai_responses import (
    OpenAIResponsesProvider,
    azure_openai_v1_base_url,
    create_openai_client,
)
from blogwriter.ai.schema import compile_schema
from blogwriter.ai.types import (
    Citation,
    Done,
    FunctionTool,
    Message,
    ModelSpec,
    NativeItems,
    Refusal,
    Request,
    Sensitivity,
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
BASE_URL = "https://foundry-bw.openai.azure.com/openai/v1/"
SOL = ModelSpec("openai", "gpt-6.2-sol", "gpt-6.2-sol", "high", 4000, supports_tools=True)
SERVED_MODEL = "gpt-6.2-sol-2026-11-17"


async def token() -> str:
    return TOKEN


class Item(BaseModel):
    """An item."""

    name: str = Field(max_length=40)
    tags: list[str] = []


class FetchArgs(BaseModel):
    url: str


FETCH = FunctionTool("fetch_page", "Fetch one page returned by search.", FetchArgs)


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
        headers={"content-type": "text/event-stream", "x-request-id": "req_stream"},
        content=sse_bytes(events),
    )


def json_response(
    body: dict[str, object], status: int = 200, headers: dict[str, str] | None = None
) -> httpx2.Response:
    return httpx2.Response(
        status, headers={"x-request-id": "req_json", **(headers or {})}, json=body
    )


def error_response(
    status: int, code: str | None = None, headers: dict[str, str] | None = None
) -> httpx2.Response:
    error = {"message": "Something failed.", "type": "invalid_request_error", "code": code}
    return json_response({"error": error}, status, headers)


def make_provider(api: MockAPI, *, max_retries: int = 0) -> OpenAIResponsesProvider:
    client = create_openai_client(
        base_url=BASE_URL,
        token_provider=token,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
        max_retries=max_retries,
    )
    return OpenAIResponsesProvider(client)


@pytest.fixture
def api() -> MockAPI:
    return MockAPI()


@pytest.fixture
async def provider(api: MockAPI) -> AsyncIterator[OpenAIResponsesProvider]:
    instance = make_provider(api)
    yield instance
    await instance.aclose()


def request(**overrides: object) -> Request:
    fields: dict[str, object] = {
        "system": "You are a careful research assistant.",
        "messages": (Message.user("How fast is passkey adoption growing?"),),
        "sensitivity": Sensitivity.PUBLIC,
    }
    fields.update(overrides)
    return Request(**fields)  # pyright: ignore[reportArgumentType]


async def drain(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


async def drain_into(received: list[StreamEvent], stream: AsyncIterator[StreamEvent]) -> None:
    """Collect events until the stream ends or raises (to inspect what came before an error)."""
    async for event in stream:
        received.append(event)


# --------------------------------------------------------------------------------------------
# Realistic Responses API payloads (synthetic data)
# --------------------------------------------------------------------------------------------

CITATION: dict[str, object] = {
    "type": "url_citation",
    "start_index": 0,
    "end_index": 17,
    "url": "https://example.com/a",
    "title": "Passkeys report",
}
REASONING_ITEM: dict[str, object] = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [
        {"type": "summary_text", "text": "**Plan** Check sources."},
        {"type": "summary_text", "text": "Then answer."},
    ],
    "encrypted_content": "gAAAAB-opaque-reasoning-state",
}
WEB_SEARCH_ITEM: dict[str, object] = {
    "type": "web_search_call",
    "id": "ws_1",
    "status": "completed",
    "action": {
        "type": "search",
        "query": "passkey adoption 2026",
        "sources": [
            {"type": "url", "url": "https://example.com/a"},
            {"type": "url", "url": "https://example.org/b"},
        ],
    },
}
FUNCTION_CALL_ITEM: dict[str, object] = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "fetch_page",
    "arguments": '{"url":"https://example.com/a"}',
    "status": "completed",
}


def message_item(
    text: str, annotations: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": text, "annotations": annotations or [], "logprobs": []}
        ],
    }


def refusal_item(text: str) -> dict[str, object]:
    return {
        "type": "message",
        "id": "msg_r",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "refusal", "refusal": text}],
    }


def usage_block(
    input_tokens: int = 1200, cached: int = 1000, write: int = 100, output: int = 300
) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": write},
        "output_tokens": output,
        "output_tokens_details": {"reasoning_tokens": 120},
        "total_tokens": input_tokens + output,
    }


def response_body(
    *,
    status: str = "completed",
    output: list[dict[str, object]] | None = None,
    usage: dict[str, object] | None = None,
    response_id: str = "resp_1",
    **extra: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "id": response_id,
        "object": "response",
        "created_at": 1764150000,
        "model": SERVED_MODEL,
        "status": status,
        "output": output or [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "store": False,
    }
    if usage is not None:
        body["usage"] = usage
    body.update(extra)
    return body


class Events:
    """Builds a numbered event sequence."""

    def __init__(self) -> None:
        self.sequence: Iterator[int] = itertools.count()
        self.events: list[dict[str, object]] = []

    def add(self, kind: str, **fields: object) -> Events:
        self.events.append({"type": kind, "sequence_number": next(self.sequence), **fields})
        return self

    def started(self) -> Events:
        created = response_body(status="in_progress")
        return self.add("response.created", response=created).add(
            "response.in_progress", response=created
        )


def research_stream() -> list[dict[str, object]]:
    """Reasoning summary, one web search, a cited answer: a quick-search turn."""
    message = message_item("Passkeys doubled.", [CITATION])
    final = response_body(
        output=[REASONING_ITEM, WEB_SEARCH_ITEM, message],
        usage=usage_block(),
        tool_usage={"web_search": {"num_requests": 1}, "image_gen": {"input_tokens": 0}},
    )
    reasoning = {"item_id": "rs_1", "output_index": 0}
    text = {"item_id": "msg_1", "output_index": 2, "content_index": 0}
    events = Events().started()
    events.add(
        "response.output_item.added",
        output_index=0,
        item={"type": "reasoning", "id": "rs_1", "summary": []},
    )
    events.add("response.reasoning_summary_part.added", **reasoning, summary_index=0, part={})
    events.add(
        "response.reasoning_summary_text.delta", **reasoning, summary_index=0, delta="**Plan** "
    )
    events.add(
        "response.reasoning_summary_text.delta",
        **reasoning,
        summary_index=0,
        delta="Check sources.",
    )
    events.add(
        "response.reasoning_summary_text.done",
        **reasoning,
        summary_index=0,
        text="**Plan** Check sources.",
    )
    events.add("response.reasoning_summary_part.done", **reasoning, summary_index=0, part={})
    events.add("response.reasoning_summary_part.added", **reasoning, summary_index=1, part={})
    events.add(
        "response.reasoning_summary_text.delta", **reasoning, summary_index=1, delta="Then answer."
    )
    events.add("response.output_item.done", output_index=0, item=REASONING_ITEM)
    events.add(
        "response.output_item.added",
        output_index=1,
        item={**WEB_SEARCH_ITEM, "status": "in_progress"},
    )
    events.add("response.web_search_call.in_progress", item_id="ws_1", output_index=1)
    events.add("response.web_search_call.searching", item_id="ws_1", output_index=1)
    events.add("response.web_search_call.completed", item_id="ws_1", output_index=1)
    events.add("response.output_item.done", output_index=1, item=WEB_SEARCH_ITEM)
    events.add(
        "response.output_item.added",
        output_index=2,
        item={**message, "status": "in_progress", "content": []},
    )
    events.add(
        "response.content_part.added",
        **text,
        part={"type": "output_text", "text": "", "annotations": []},
    )
    events.add("response.output_text.delta", **text, delta="Passkeys ", logprobs=[])
    events.add("response.output_text.delta", **text, delta="doubled.", logprobs=[])
    events.add(
        "response.output_text.annotation.added", **text, annotation_index=0, annotation=CITATION
    )
    events.add("response.output_text.done", **text, text="Passkeys doubled.", logprobs=[])
    events.add("response.output_item.done", output_index=2, item=message)
    events.add("response.completed", response=final)
    return events.events


def function_call_stream() -> list[dict[str, object]]:
    final = response_body(output=[REASONING_ITEM, FUNCTION_CALL_ITEM], usage=usage_block(output=40))
    events = Events().started()
    events.add("response.output_item.done", output_index=0, item=REASONING_ITEM)
    events.add(
        "response.output_item.added",
        output_index=1,
        item={**FUNCTION_CALL_ITEM, "arguments": "", "status": "in_progress"},
    )
    events.add(
        "response.function_call_arguments.delta", item_id="fc_1", output_index=1, delta='{"url":'
    )
    events.add(
        "response.function_call_arguments.delta",
        item_id="fc_1",
        output_index=1,
        delta='"https://example.com/a"}',
    )
    events.add(
        "response.function_call_arguments.done",
        item_id="fc_1",
        output_index=1,
        arguments='{"url":"https://example.com/a"}',
    )
    events.add("response.output_item.done", output_index=1, item=FUNCTION_CALL_ITEM)
    events.add("response.completed", response=final)
    return events.events


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


async def test_calls_go_to_the_v1_endpoint_with_an_entra_bearer_token(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")], usage=usage_block())))
    await provider.complete(SOL, request())
    (sent,) = api.requests
    assert sent.method == "POST"
    assert str(sent.url) == "https://foundry-bw.openai.azure.com/openai/v1/responses"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert "api-key" not in sent.headers


@pytest.mark.parametrize(
    "url",
    [
        "http://foundry-bw.openai.azure.com/openai/v1/",
        "https://api.openai.com/v1/",
        "https://foundry-bw.openai.azure.com.evil.example/openai/v1/",
        "https://foundry-bw.openai.azure.com/openai/deployments/x/",
        "https://foundry-bw.openai.azure.com/openai/v1/?api-version=preview",
        "https://user:pass@foundry-bw.openai.azure.com/openai/v1/",
        "https://foundry-bw.openai.azure.com:8443/openai/v1/",
    ],
)
def test_base_url_must_be_an_azure_v1_endpoint(url: str) -> None:
    with pytest.raises(ValueError, match="Azure OpenAI v1 endpoint"):
        create_openai_client(base_url=url, token_provider=token)


async def test_base_url_variants_are_normalized() -> None:
    client = create_openai_client(
        base_url="https://Foundry-BW.services.ai.azure.com/openai/v1", token_provider=token
    )
    assert str(client.base_url) == "https://foundry-bw.services.ai.azure.com/openai/v1/"
    await client.close()
    assert azure_openai_v1_base_url("foundry-bw") == BASE_URL
    with pytest.raises(ValueError, match="resource name"):
        azure_openai_v1_base_url("evil.example/x")


# --------------------------------------------------------------------------------------------
# Request shape
# --------------------------------------------------------------------------------------------


async def test_calls_are_stateless_with_no_sampling_or_forced_tools(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")], usage=usage_block())))
    await provider.complete(SOL, request())
    body = api.body()
    assert body == {
        "model": "gpt-6.2-sol",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": "How fast is passkey adoption growing?",
            }
        ],
        "instructions": "You are a careful research assistant.",
        "max_output_tokens": 4000,
        "store": False,
        "reasoning": {"effort": "high", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "stream": False,
    }
    for forbidden in ("temperature", "top_p", "tool_choice", "background", "previous_response_id"):
        assert forbidden not in body


async def test_reasoning_settings_follow_the_spec(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    for _ in range(3):
        api.queue(json_response(response_body(output=[message_item("ok")])))
    await provider.complete(ModelSpec("openai", "luna", "gpt-6-luna", "none", 50, False), request())
    assert api.body()["reasoning"] == {"effort": "none"}
    quiet = ModelSpec("openai", "sol", "gpt-6.2-sol", "low", 50, False, thinking_summary=False)
    await provider.complete(quiet, request())
    assert api.body()["reasoning"] == {"effort": "low"}
    await provider.complete(ModelSpec("openai", "sol", "gpt-6.2-sol", None, 50, False), request())
    assert "reasoning" not in api.body()


async def test_input_items_cover_text_tool_calls_and_results(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")])))
    messages = (
        Message.user("Find sources."),
        Message("assistant", (TextPart("Fetching."), ToolCallPart("call_1", "fetch_page", "{}"))),
        Message(
            "user",
            (
                ToolResultPart("call_1", "Page text summary."),
                ToolResultPart("call_2", "timeout", is_error=True),
                TextPart("Now answer."),
            ),
        ),
    )
    await provider.complete(SOL, request(messages=messages, system=" "))
    body = api.body()
    assert "instructions" not in body
    assert body["input"] == [
        {"type": "message", "role": "user", "content": "Find sources."},
        {"type": "message", "role": "assistant", "content": "Fetching."},
        {"type": "function_call", "call_id": "call_1", "name": "fetch_page", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "Page text summary."},
        {"type": "function_call_output", "call_id": "call_2", "output": "Error: timeout"},
        {"type": "message", "role": "user", "content": "Now answer."},
    ]


async def test_native_items_are_replayed_verbatim_only_to_their_provider(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")])))
    api.queue(json_response(response_body(output=[message_item("ok")])))
    parts = (TextPart("Earlier answer."),)
    own = Message("assistant", parts, NativeItems("openai", (REASONING_ITEM, message_item("x"))))
    other = Message("assistant", parts, NativeItems("anthropic", ({"type": "thinking"},)))
    await provider.complete(SOL, request(messages=(Message.user("a"), own, Message.user("b"))))
    assert api.body()["input"] == [
        {"type": "message", "role": "user", "content": "a"},
        REASONING_ITEM,
        message_item("x"),
        {"type": "message", "role": "user", "content": "b"},
    ]
    await provider.complete(SOL, request(messages=(Message.user("a"), other, Message.user("b"))))
    assert api.body()["input"] == [
        {"type": "message", "role": "user", "content": "a"},
        {"type": "message", "role": "assistant", "content": "Earlier answer."},
        {"type": "message", "role": "user", "content": "b"},
    ]


async def test_structured_output_uses_the_strict_compiled_schema(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item('{"name":"x","tags":null}')])))
    result = await provider.complete(SOL, request(output_schema=Item))
    assert api.body()["text"] == {
        "format": {
            "type": "json_schema",
            "name": "Item",
            "schema": compile_schema("openai", Item).json_schema(),
            "strict": True,
        }
    }
    assert compile_schema("openai", Item).validate_json(result.text) == Item(name="x")


async def test_tools_are_strict_and_web_search_is_domain_filtered(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")])))
    search = WebSearchTool(("example.com", "nist.gov"), search_context_size="low")
    await provider.complete(SOL, request(tools=(FETCH, search)))
    body = api.body()
    assert body["tools"] == [
        {
            "type": "function",
            "name": "fetch_page",
            "description": "Fetch one page returned by search.",
            "parameters": compile_schema("openai", FetchArgs).json_schema(),
            "strict": True,
        },
        {
            "type": "web_search",
            "filters": {"allowed_domains": ["example.com", "nist.gov"]},
            "search_context_size": "low",
        },
    ]
    assert body["max_tool_calls"] == 8
    assert body["include"] == ["reasoning.encrypted_content", "web_search_call.action.sources"]
    assert "tool_choice" not in body


async def test_unfiltered_web_search_and_explicit_tool_cap(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(output=[message_item("ok")])))
    await provider.complete(SOL, request(tools=(WebSearchTool(),), max_tool_calls=3))
    body = api.body()
    assert body["tools"] == [{"type": "web_search"}]
    assert body["max_tool_calls"] == 3


# --------------------------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------------------------


async def test_stream_normalizes_reasoning_text_citations_and_usage(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(sse(research_stream()))
    search = WebSearchTool(("example.com",))
    events = await drain(provider.stream(SOL, request(tools=(search,))))
    assert api.body()["stream"] is True
    assert api.body()["store"] is False
    assert events[:6] == [
        ThinkingSummary("**Plan** "),
        ThinkingSummary("Check sources."),
        ThinkingSummary("\n\n"),
        ThinkingSummary("Then answer."),
        TextDelta("Passkeys "),
        TextDelta("doubled."),
    ]
    usage = Usage(
        input=1200,
        cached_input=1000,
        cache_write=100,
        output=300,
        reasoning=120,
        web_search_requests=1,
    )
    assert events[6] == UsageEvent(usage)
    done = events[7]
    assert isinstance(done, Done)
    assert len(events) == 8
    completion = done.completion
    assert done.finish_reason == completion.finish_reason == "stop"
    assert completion.text == "Passkeys doubled."
    assert completion.usage == usage
    assert completion.model == SERVED_MODEL
    assert completion.response_id == "resp_1"
    assert completion.citations == (Citation("https://example.com/a", "Passkeys report"),)
    assert completion.search_queries == ("passkey adoption 2026",)
    assert completion.search_sources == ("https://example.com/a", "https://example.org/b")
    assert completion.refusal is None
    assert completion.message is not None
    assert completion.message.native is not None
    assert completion.message.native.items[0] == REASONING_ITEM
    assert completion.message.parts == (TextPart("Passkeys doubled."),)


async def test_stream_emits_tool_calls_and_the_loop_replays_reasoning(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(sse(function_call_stream()))
    first = request(tools=(FETCH,))
    events = await drain(provider.stream(SOL, first))
    call = ToolCall("call_1", "fetch_page", '{"url":"https://example.com/a"}')
    assert events[0] == call
    done = events[-1]
    assert isinstance(done, Done)
    assert done.finish_reason == "tool_calls"
    assert done.completion.tool_calls == (call,)
    assert done.completion.text == ""
    reply = done.completion.message
    assert reply is not None
    assert reply.parts == (ToolCallPart("call_1", "fetch_page", '{"url":"https://example.com/a"}'),)

    # The next turn of the stateless loop sends the reasoning state back verbatim.
    api.queue(sse(research_stream()))
    second = request(
        tools=(FETCH,),
        messages=(*first.messages, reply, Message.tool_results(ToolResultPart("call_1", "text"))),
    )
    await drain(provider.stream(SOL, second))
    sent = api.body()
    assert sent["store"] is False
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["input"] == [
        {"type": "message", "role": "user", "content": "How fast is passkey adoption growing?"},
        REASONING_ITEM,
        FUNCTION_CALL_ITEM,
        {"type": "function_call_output", "call_id": "call_1", "output": "text"},
    ]


async def test_commentary_messages_are_progress_notes_not_answer_text(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    note = {**message_item("Searching the web."), "id": "msg_c", "phase": "commentary"}
    answer = {**message_item("Passkeys doubled."), "id": "msg_f", "phase": "final_answer"}
    final = response_body(output=[note, WEB_SEARCH_ITEM, answer], usage=usage_block())
    events = Events().started()
    events.add("response.output_item.added", output_index=0, item={**note, "content": []})
    events.add(
        "response.output_text.delta",
        item_id="msg_c",
        output_index=0,
        content_index=0,
        delta="Searching the web.",
        logprobs=[],
    )
    events.add("response.output_item.done", output_index=0, item=note)
    events.add("response.output_item.done", output_index=1, item=WEB_SEARCH_ITEM)
    events.add("response.output_item.added", output_index=2, item={**answer, "content": []})
    events.add(
        "response.output_text.delta",
        item_id="msg_f",
        output_index=2,
        content_index=0,
        delta="Passkeys doubled.",
        logprobs=[],
    )
    events.add("response.completed", response=final)
    api.queue(sse(events.events))
    received = await drain(provider.stream(SOL, request(tools=(WebSearchTool(),))))
    assert received[:2] == [ThinkingSummary("Searching the web."), TextDelta("Passkeys doubled.")]
    done = received[-1]
    assert isinstance(done, Done)
    assert done.completion.text == "Passkeys doubled."
    assert done.completion.message is not None
    assert done.completion.message.native is not None
    replayed = done.completion.message.native.items
    assert [item.get("phase") for item in replayed] == ["commentary", None, "final_answer"]


async def test_refusals_discard_partial_output(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    final = response_body(output=[refusal_item("I can't help with that.")], usage=usage_block())
    events = Events().started()
    events.add(
        "response.refusal.delta", item_id="msg_r", output_index=0, content_index=0, delta="I can't"
    )
    events.add(
        "response.refusal.done",
        item_id="msg_r",
        output_index=0,
        content_index=0,
        refusal="I can't help with that.",
    )
    events.add("response.completed", response=final)
    api.queue(sse(events.events))
    received = await drain(provider.stream(SOL, request()))
    refusal = Refusal(None, "I can't help with that.")
    assert received[0] == refusal
    assert not any(isinstance(event, TextDelta) for event in received)
    done = received[-1]
    assert isinstance(done, Done)
    assert done.finish_reason == "refusal"
    assert done.completion.refusal == refusal
    assert done.completion.text == ""
    assert done.completion.message is None
    assert done.completion.usage.output == 300


async def test_content_filtered_output_is_a_refusal(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    final = response_body(
        status="incomplete",
        output=[message_item("Partial")],
        incomplete_details={"reason": "content_filter"},
    )
    api.queue(sse(Events().started().add("response.incomplete", response=final).events))
    received = await drain(provider.stream(SOL, request()))
    assert received[0] == Refusal("content_filter", None)
    done = received[-1]
    assert isinstance(done, Done)
    assert done.completion.text == ""


async def test_output_budget_exhaustion_finishes_with_length(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    final = response_body(
        status="incomplete",
        output=[message_item('{"name": "trunc')],
        incomplete_details={"reason": "max_output_tokens"},
    )
    api.queue(sse(Events().started().add("response.incomplete", response=final).events))
    done = (await drain(provider.stream(SOL, request())))[-1]
    assert isinstance(done, Done)
    assert done.finish_reason == "length"
    assert done.completion.text == '{"name": "trunc'


@pytest.mark.parametrize("code", ["bio_policy", "misalignment_policy_violation"])
async def test_policy_failures_are_refusals(
    api: MockAPI, provider: OpenAIResponsesProvider, code: str
) -> None:
    final = response_body(status="failed", error={"code": code, "message": "Blocked."})
    api.queue(sse(Events().started().add("response.failed", response=final).events))
    received = await drain(provider.stream(SOL, request()))
    assert received[0] == Refusal(code, None)
    assert isinstance(received[-1], Done)


async def test_failed_responses_raise_with_their_usage(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    final = response_body(
        status="failed", error={"code": "server_error", "message": "x"}, usage=usage_block()
    )
    events = Events().started()
    events.add(
        "response.output_text.delta",
        item_id="msg_1",
        output_index=0,
        content_index=0,
        delta="Part",
        logprobs=[],
    )
    events.add("response.failed", response=final)
    api.queue(sse(events.events))
    received: list[StreamEvent] = []
    with pytest.raises(ProviderStreamError) as caught:
        await drain_into(received, provider.stream(SOL, request()))
    assert received == [TextDelta("Part")]
    error = caught.value
    assert error.retryable is True
    assert error.code == "server_error"
    assert error.usage is not None
    assert error.usage.input == 1200


async def test_error_events_mid_stream_raise(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    events = Events().started()
    events.add(
        "response.output_text.delta",
        item_id="msg_1",
        output_index=0,
        content_index=0,
        delta="Half",
        logprobs=[],
    )
    events.add("error", code="server_error", message="The server had an error.", param=None)
    api.queue(sse(events.events))
    received: list[StreamEvent] = []
    with pytest.raises(ProviderStreamError) as caught:
        await drain_into(received, provider.stream(SOL, request()))
    assert received == [TextDelta("Half")]
    assert caught.value.code == "server_error"
    assert caught.value.retryable is True
    assert caught.value.request_id == "req_stream"
    assert "The server had an error" not in str(caught.value)


async def test_legacy_error_payloads_mid_stream_raise(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    body = (
        sse_bytes(Events().started().events)
        + b'data: {"error": {"message": "boom", "code": "server_error"}}\n\n'
    )
    api.queue(httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body))
    with pytest.raises(ProviderStreamError) as caught:
        await drain(provider.stream(SOL, request()))
    assert caught.value.code == "server_error"


async def test_a_dropped_connection_mid_stream_raises(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    first = sse_bytes(
        Events()
        .started()
        .add(
            "response.output_text.delta",
            item_id="msg_1",
            output_index=0,
            content_index=0,
            delta="Partial",
            logprobs=[],
        )
        .events
    )
    api.queue(
        httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken(first))
    )
    received: list[StreamEvent] = []
    with pytest.raises(ProviderStreamError) as caught:
        await drain_into(received, provider.stream(SOL, request()))
    assert received == [TextDelta("Partial")]
    assert caught.value.retryable is True


async def test_a_stream_without_a_terminal_event_raises(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(sse(Events().started().events))
    with pytest.raises(ProviderStreamError, match="without a terminal event"):
        await drain(provider.stream(SOL, request()))


# --------------------------------------------------------------------------------------------
# HTTP errors and retries
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "headers", "kind", "retryable"),
    [
        (429, {"retry-after": "2"}, RateLimitedError, True),
        (500, {}, ProviderUnavailableError, True),
        (503, {}, ProviderUnavailableError, True),
        (401, {}, ProviderAuthError, False),
        (403, {}, ProviderAuthError, False),
        (400, {}, ProviderRequestError, False),
        (404, {}, ProviderRequestError, False),
    ],
)
@pytest.mark.parametrize("streaming", [True, False])
async def test_http_errors_map_to_provider_errors(
    api: MockAPI,
    provider: OpenAIResponsesProvider,
    status: int,
    headers: dict[str, str],
    kind: type[ProviderError],
    retryable: bool,
    streaming: bool,
) -> None:
    api.queue(error_response(status, "some_code", headers))
    call = (
        drain(provider.stream(SOL, request())) if streaming else provider.complete(SOL, request())
    )
    with pytest.raises(kind) as caught:
        await call
    error = caught.value
    assert error.status_code == status
    assert error.retryable is retryable
    assert error.request_id == "req_json"
    assert "Something failed" not in str(error)
    if status == 429:
        assert error.retry_after == 2.0


@pytest.mark.parametrize("streaming", [True, False])
async def test_azure_prompt_filter_rejections_are_refusals(
    api: MockAPI, provider: OpenAIResponsesProvider, streaming: bool
) -> None:
    api.queue(error_response(400, "content_filter"))
    if streaming:
        events = await drain(provider.stream(SOL, request()))
        assert events[0] == Refusal("content_filter", None)
        assert events[1] == UsageEvent(Usage())
        done = events[2]
        assert isinstance(done, Done)
        completion = done.completion
    else:
        completion = await provider.complete(SOL, request())
    assert completion.finish_reason == "refusal"
    assert completion.refusal == Refusal("content_filter", None)


async def test_transient_errors_are_retried_before_streaming(api: MockAPI) -> None:
    provider = make_provider(api, max_retries=1)
    api.queue(error_response(429, "rate_limit_exceeded", {"retry-after-ms": "1"}))
    api.queue(sse(research_stream()))
    events = await drain(provider.stream(SOL, request()))
    await provider.aclose()
    assert isinstance(events[-1], Done)
    assert len(api.requests) == 2


async def test_connection_failures_before_streaming(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(httpx2.ConnectError("name resolution failed"))
    with pytest.raises(ProviderUnavailableError) as caught:
        await drain(provider.stream(SOL, request()))
    assert caught.value.retryable is True


# --------------------------------------------------------------------------------------------
# Non-streaming completion
# --------------------------------------------------------------------------------------------


async def test_complete_maps_function_calls_and_usage(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(
        json_response(
            response_body(output=[REASONING_ITEM, FUNCTION_CALL_ITEM], usage=usage_block())
        )
    )
    completion = await provider.complete(SOL, request(tools=(FETCH,)))
    assert completion.finish_reason == "tool_calls"
    assert completion.tool_calls == (
        ToolCall("call_1", "fetch_page", '{"url":"https://example.com/a"}'),
    )
    assert completion.usage.reasoning == 120
    assert completion.usage.web_search_requests == 0


async def test_search_counts_fall_back_to_search_actions(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    open_page: dict[str, object] = {
        "type": "web_search_call",
        "id": "ws_2",
        "status": "completed",
        "action": {"type": "open_page", "url": "https://example.net/c"},
    }
    api.queue(
        json_response(response_body(output=[WEB_SEARCH_ITEM, open_page, message_item("Done.")]))
    )
    completion = await provider.complete(SOL, request(tools=(WebSearchTool(),)))
    assert completion.usage.web_search_requests == 1
    assert completion.search_sources == (
        "https://example.com/a",
        "https://example.org/b",
        "https://example.net/c",
    )


async def test_missing_usage_details_do_not_break_the_ledger(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(
        json_response(
            response_body(
                output=[message_item("ok")],
                usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            )
        )
    )
    completion = await provider.complete(SOL, request())
    assert completion.usage == Usage(input=10, output=2)


# --------------------------------------------------------------------------------------------
# Background runs
# --------------------------------------------------------------------------------------------


async def test_background_runs_are_stored_and_never_retried(api: MockAPI) -> None:
    provider = make_provider(api, max_retries=2)
    background = request(tools=(WebSearchTool(("example.com",)),), background=True)
    api.queue(error_response(500))
    with pytest.raises(ProviderUnavailableError):
        await provider.start_background(SOL, background)
    assert len(api.requests) == 1  # a retried create could orphan a stored response

    api.queue(json_response(response_body(status="queued", response_id="resp_bg", background=True)))
    assert await provider.start_background(SOL, background) == "resp_bg"
    body = api.body()
    assert body["background"] is True
    assert body["store"] is True
    assert body["include"] == ["web_search_call.action.sources"]
    assert "stream" not in body or body["stream"] is False
    await provider.aclose()


async def test_background_lifecycle_poll_cancel_delete(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    api.queue(json_response(response_body(status="in_progress", response_id="resp_bg")))
    poll = await provider.poll_background("resp_bg")
    assert (poll.status, poll.done, poll.completion) == ("in_progress", False, None)
    assert api.requests[-1].method == "GET"
    assert api.requests[-1].url.path == "/openai/v1/responses/resp_bg"
    assert api.requests[-1].url.params.get_list("include[]") == ["web_search_call.action.sources"]

    done = response_body(
        response_id="resp_bg", output=[message_item("Brief.")], usage=usage_block()
    )
    api.queue(json_response(done))
    poll = await provider.poll_background("resp_bg")
    assert poll.done
    assert poll.completion is not None
    assert poll.completion.text == "Brief."

    api.queue(json_response(response_body(status="cancelled", response_id="resp_bg")))
    cancelled = await provider.cancel_background("resp_bg")
    assert api.requests[-1].method == "POST"
    assert api.requests[-1].url.path == "/openai/v1/responses/resp_bg/cancel"
    assert cancelled.status == "cancelled"
    assert cancelled.completion is not None
    assert cancelled.completion.finish_reason == "cancelled"
    assert cancelled.completion.text == ""

    api.queue(httpx2.Response(200, json={"id": "resp_bg", "object": "response", "deleted": True}))
    await provider.delete_background("resp_bg")
    assert api.requests[-1].method == "DELETE"
    assert api.requests[-1].url.path == "/openai/v1/responses/resp_bg"

    api.queue(error_response(404))
    await provider.delete_background("resp_bg")  # already gone: idempotent


async def test_background_failures_are_reported_not_raised(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    failed = response_body(
        status="failed",
        response_id="resp_bg",
        error={"code": "server_error", "message": "x"},
        usage=usage_block(),
    )
    api.queue(json_response(failed))
    poll = await provider.poll_background("resp_bg")
    assert poll.status == "failed"
    assert poll.done
    assert poll.completion is None
    assert isinstance(poll.error, ProviderUnavailableError)
    assert poll.error.retryable is True
    assert poll.error.usage is not None

    refused = response_body(status="failed", response_id="resp_bg", error={"code": "bio_policy"})
    api.queue(json_response(refused))
    poll = await provider.poll_background("resp_bg")
    assert poll.completion is not None
    assert poll.completion.refusal == Refusal("bio_policy", None)


async def test_poll_failures_raise(api: MockAPI, provider: OpenAIResponsesProvider) -> None:
    api.queue(error_response(404))
    with pytest.raises(ProviderRequestError):
        await provider.poll_background("resp_missing")
    api.queue(error_response(503))
    with pytest.raises(ProviderUnavailableError):
        await provider.cancel_background("resp_bg")
    api.queue(error_response(500))
    with pytest.raises(ProviderUnavailableError):
        await provider.delete_background("resp_bg")


async def test_background_and_foreground_paths_do_not_mix(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    with pytest.raises(UnsupportedRequestError, match=r"Request\.background"):
        await provider.start_background(SOL, request())
    background = request(background=True)
    with pytest.raises(UnsupportedRequestError, match="start_background"):
        await provider.complete(SOL, background)
    with pytest.raises(UnsupportedRequestError, match="start_background"):
        await drain(provider.stream(SOL, background))
    with pytest.raises(UnsupportedRequestError, match="hosted tools only"):
        await provider.start_background(SOL, request(background=True, tools=(FETCH,)))
    assert api.requests == []


async def test_specs_must_match_the_provider(
    api: MockAPI, provider: OpenAIResponsesProvider
) -> None:
    claude = ModelSpec("anthropic", "claude-opus-5-5", "claude-opus-5-5", "high", 100, False)
    with pytest.raises(UnsupportedRequestError, match="sent to the openai provider"):
        await provider.complete(claude, request())
    toolless = ModelSpec("openai", "gpt-6-luna", "gpt-6-luna", "low", 100, supports_tools=False)
    with pytest.raises(UnsupportedRequestError, match="without tools"):
        await drain(provider.stream(toolless, request(tools=(WebSearchTool(),))))
    assert api.requests == []
    assert provider.name == "openai"


def test_satisfies_the_provider_protocol(provider: OpenAIResponsesProvider) -> None:
    typed: Provider = provider  # checked statically by pyright
    assert typed.name == "openai"
