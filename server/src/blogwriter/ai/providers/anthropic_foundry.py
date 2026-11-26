"""Claude on Microsoft Foundry through ``anthropic.AsyncAnthropicFoundry``.

The client points at ``https://<resource>.services.ai.azure.com/anthropic/`` and authenticates
with an Entra token provider (``azure_ad_token_provider``; the SDK sends
``Authorization: Bearer <token>`` and never an API key header).

Every call uses ``client.messages.stream(...)`` (``complete`` drains the same stream, so long
drafts never hit the SDK's non-streaming limits) with:

* ``thinking={"type": "adaptive", "display": "summarized"}`` (Opus 5.5 always thinks; the
  summaries stream as :class:`~blogwriter.ai.types.ThinkingSummary`);
* ``output_config={"effort": ..., "format": {"type": "json_schema", "schema": ...}}`` with the
  schema compiled by :mod:`blogwriter.ai.schema`;
* a 1 hour prompt-cache breakpoint on the system prompt (the stable prefix; tools come before it
  in the cache order, so they are covered too) and on any message part marked
  ``cache_breakpoint``;
* no ``temperature``, ``top_k`` or ``tool_choice`` (the installed SDK no longer has the first
  two; Opus 5.5 does not allow a forced tool choice).

``stop_reason == "refusal"`` becomes a :class:`~blogwriter.ai.types.Refusal` carrying the stop
details' category and explanation. Claude never receives ``private`` data, never runs in the
background here, and is not offered ``web_search``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Final, Literal, cast

import httpx2
from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    AsyncAnthropicFoundry,
    not_given,
    omit,
)
from anthropic.types import (
    AssistantMessageParam,
    CacheControlEphemeralParam,
    ContentBlockParam,
    MessageParam,
    OutputConfigParam,
    TextBlockParam,
    ThinkingConfigAdaptiveParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUnionParam,
    ToolUseBlockParam,
    UserMessageParam,
)
from anthropic.types import Message as AnthropicMessage

from blogwriter.ai.providers.auth import validate_resource_name
from blogwriter.ai.providers.base import (
    ProviderError,
    ProviderStreamError,
    ProviderUnavailableError,
    TokenProvider,
    UnsupportedRequestError,
    cap_sdk_logging,
    check_spec,
    collect,
    count_attr,
    error_for_status,
)
from blogwriter.ai.schema import compile_schema
from blogwriter.ai.types import (
    Completion,
    Done,
    FinishReason,
    FunctionTool,
    Message,
    ModelSpec,
    NativeItems,
    Part,
    ProviderName,
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

__all__ = [
    "AnthropicFoundryProvider",
    "create_anthropic_client",
    "foundry_anthropic_base_url",
]

_PROVIDER: Final[ProviderName] = "anthropic"
_EFFORTS: Final[dict[str, Literal["low", "medium", "high", "xhigh", "max"]]] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}
_FINISH: Final[dict[str, FinishReason]] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "pause_turn": "pause",
}


def foundry_anthropic_base_url(resource: str) -> str:
    """The Anthropic base URL of a Foundry resource."""
    return f"https://{validate_resource_name(resource)}.services.ai.azure.com/anthropic/"


def create_anthropic_client(
    *,
    resource: str,
    token_provider: TokenProvider,
    http_client: httpx2.AsyncClient | None = None,
    max_retries: int = 2,
    timeout: float | None = None,
) -> AsyncAnthropicFoundry:
    """An ``AsyncAnthropicFoundry`` client authenticated with Entra ID.

    The base URL is passed explicitly, so ``ANTHROPIC_FOUNDRY_RESOURCE`` and
    ``ANTHROPIC_FOUNDRY_BASE_URL`` are never consulted. ``http_client`` is injectable (tests use
    ``httpx2.MockTransport``); ``timeout`` is in seconds, ``None`` keeps the SDK default.
    """
    cap_sdk_logging()
    return AsyncAnthropicFoundry(
        base_url=foundry_anthropic_base_url(resource),
        azure_ad_token_provider=token_provider,
        http_client=http_client,
        max_retries=max_retries,
        timeout=timeout if timeout is not None else not_given,
    )


# --------------------------------------------------------------------------------------------
# Request building
# --------------------------------------------------------------------------------------------


def _cache_1h() -> CacheControlEphemeralParam:
    return {"type": "ephemeral", "ttl": "1h"}


def _arguments_object(arguments: str) -> dict[str, object]:
    try:
        value: object = json.loads(arguments)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise UnsupportedRequestError("tool call arguments must be a JSON object")
    return cast(dict[str, object], value)


def _blocks(message: Message) -> list[ContentBlockParam]:
    blocks: list[ContentBlockParam] = []
    # Tool results must come first in a user turn.
    for part in message.parts:
        if isinstance(part, ToolResultPart):
            result: ToolResultBlockParam = {
                "type": "tool_result",
                "tool_use_id": part.call_id,
                "is_error": part.is_error,
            }
            if part.output:
                result["content"] = part.output
            blocks.append(result)
    for part in message.parts:
        if isinstance(part, TextPart):
            text: TextBlockParam = {"type": "text", "text": part.text}
            if part.cache_breakpoint:
                text["cache_control"] = _cache_1h()
            blocks.append(text)
        elif isinstance(part, ToolCallPart):
            use: ToolUseBlockParam = {
                "type": "tool_use",
                "id": part.call_id,
                "name": part.name,
                "input": _arguments_object(part.arguments),
            }
            blocks.append(use)
    return blocks


def _messages(messages: Sequence[Message]) -> list[MessageParam]:
    out: list[MessageParam] = []
    for message in messages:
        if message.role == "user":
            user: UserMessageParam = {"role": "user", "content": _blocks(message)}
            out.append(user)
            continue
        if message.native is not None and message.native.provider == _PROVIDER:
            content = [cast(ContentBlockParam, dict(item)) for item in message.native.items]
        else:
            content = _blocks(message)
        assistant: AssistantMessageParam = {"role": "assistant", "content": content}
        out.append(assistant)
    return out


def _tools(request: Request) -> list[ToolUnionParam]:
    tools: list[ToolUnionParam] = []
    for tool in request.tools:
        if isinstance(tool, FunctionTool):
            param: ToolParam = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": compile_schema(_PROVIDER, tool.parameters).json_schema(),
                "strict": True,
            }
            tools.append(param)
    return tools


# --------------------------------------------------------------------------------------------
# Response mapping
# --------------------------------------------------------------------------------------------


def _usage(message: AnthropicMessage) -> Usage:
    usage = message.usage
    read = count_attr(usage, "cache_read_input_tokens") or 0
    write = count_attr(usage, "cache_creation_input_tokens") or 0
    return Usage(
        input=(count_attr(usage, "input_tokens") or 0) + read + write,
        cached_input=read,
        cache_write=write,
        output=count_attr(usage, "output_tokens") or 0,
        reasoning=count_attr(usage, "output_tokens_details", "thinking_tokens") or 0,
        web_search_requests=count_attr(usage, "server_tool_use", "web_search_requests") or 0,
    )


def _completion(message: AnthropicMessage, deployment: str) -> Completion:
    usage = _usage(message)
    model = message.model or deployment
    if message.stop_reason == "refusal":
        details = message.stop_details
        refusal = Refusal(
            category=details.category if details is not None else None,
            explanation=details.explanation if details is not None else None,
        )
        return Completion(
            text="",
            finish_reason="refusal",
            usage=usage,
            model=model,
            response_id=message.id,
            refusal=refusal,
        )
    finish = _FINISH.get(message.stop_reason or "")
    if finish is None:
        raise ProviderStreamError(
            f"anthropic message {message.id} ended without a stop reason",
            provider=_PROVIDER,
            retryable=True,
            usage=usage,
        )
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in message.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            arguments = json.dumps(block.input, ensure_ascii=False)
            tool_calls.append(ToolCall(block.id, block.name, arguments))
    text = "".join(texts)
    parts: list[Part] = [TextPart(text)] if text.strip() else []
    parts.extend(ToolCallPart(call.call_id, call.name, call.arguments) for call in tool_calls)
    native = tuple(
        block.to_dict(mode="json", exclude_none=True, warnings=False) for block in message.content
    )
    reply = Message("assistant", tuple(parts), NativeItems(_PROVIDER, native)) if native else None
    return Completion(
        text=text,
        finish_reason=finish,
        usage=usage,
        model=model,
        response_id=message.id,
        tool_calls=tuple(tool_calls),
        message=reply,
    )


def _map_error(
    exc: AnthropicError | httpx2.TransportError | ValueError, *, started: bool
) -> ProviderError:
    if isinstance(exc, APIStatusError):
        # An error event inside a 200 stream arrives with the stream's own status code, so only
        # its broad error type (overloaded_error, rate_limit_error, ...) can classify it; a
        # specific details.error_code we do not know must not turn an overload into a final
        # error (the SDK's guidance: treat an unrecognized error_code as the bare type). HTTP
        # errors are classified by status and keep the more specific code.
        during_stream = exc.status_code < 400
        code = (exc.type or exc.error_code) if during_stream else (exc.error_code or exc.type)
        return error_for_status(
            _PROVIDER,
            exc.status_code,
            code=code,
            request_id=exc.request_id,
            headers=exc.response.headers,
            during_stream=during_stream,
        )
    kind = ProviderStreamError if started else ProviderUnavailableError
    if isinstance(exc, APIConnectionError | httpx2.TransportError):
        return kind(
            f"anthropic connection failed ({type(exc).__name__})",
            provider=_PROVIDER,
            retryable=True,
        )
    return kind(
        f"anthropic stream could not be read ({type(exc).__name__})",
        provider=_PROVIDER,
        retryable=True,
    )


# --------------------------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------------------------


class AnthropicFoundryProvider:
    """Claude (Opus 5.5) on Microsoft Foundry."""

    def __init__(self, client: AsyncAnthropicFoundry) -> None:
        self._client = client

    @property
    def name(self) -> ProviderName:
        return _PROVIDER

    async def aclose(self) -> None:
        await self._client.close()

    def _check(self, spec: ModelSpec, request: Request) -> None:
        check_spec(_PROVIDER, spec, request)
        if request.sensitivity is Sensitivity.PRIVATE:
            raise SensitivityError("private data never goes to Claude")
        if request.background:
            raise UnsupportedRequestError("Anthropic calls do not run as background responses")
        if any(isinstance(tool, WebSearchTool) for tool in request.tools):
            raise UnsupportedRequestError("web_search is only offered on the OpenAI provider")

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        """Run the call by draining :meth:`stream`."""
        return await collect(self.stream(spec, request), provider=_PROVIDER)

    async def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        """One streaming call. Ends with :class:`Done` or raises."""
        self._check(spec, request)
        system: list[TextBlockParam] = []
        if request.system.strip():
            system.append({"type": "text", "text": request.system, "cache_control": _cache_1h()})
        tools = _tools(request)
        if tools and not system:
            cast(ToolParam, tools[-1])["cache_control"] = _cache_1h()
        output_config: OutputConfigParam = {}
        if spec.effort is not None:
            output_config["effort"] = _EFFORTS[spec.effort]
        if request.output_schema is not None:
            schema = compile_schema(_PROVIDER, request.output_schema).json_schema()
            output_config["format"] = {"type": "json_schema", "schema": schema}
        thinking: ThinkingConfigAdaptiveParam = {
            "type": "adaptive",
            "display": "summarized" if spec.thinking_summary else "omitted",
        }
        manager = self._client.messages.stream(
            model=spec.deployment,
            max_tokens=spec.max_output_tokens,
            messages=_messages(request.messages),
            system=system if system else omit,
            tools=tools if tools else omit,
            thinking=thinking,
            output_config=output_config if output_config else omit,
        )

        final: AnthropicMessage | None = None
        started = False
        try:
            async with manager as events:
                started = True
                async for event in events:
                    if event.type == "text":
                        yield TextDelta(event.text)
                    elif event.type == "thinking":
                        yield ThinkingSummary(event.thinking)
                    elif event.type == "content_block_stop":
                        block = event.content_block
                        if block.type == "tool_use":
                            arguments = json.dumps(block.input, ensure_ascii=False)
                            yield ToolCall(block.id, block.name, arguments)
                    elif event.type == "message_stop":
                        final = event.message
        except (AnthropicError, httpx2.TransportError) as exc:
            raise _map_error(exc, started=started) from exc
        except ValueError as exc:
            # The SDK's partial-JSON errors quote model output: do not chain them.
            raise _map_error(exc, started=started) from None
        if final is None:
            raise ProviderStreamError(
                "anthropic stream ended before message_stop", provider=_PROVIDER, retryable=True
            )
        completion = _completion(final, spec.deployment)
        if completion.refusal is not None:
            yield completion.refusal
        yield UsageEvent(completion.usage)
        yield Done(completion.finish_reason, completion)
