"""Azure OpenAI through the Responses API on the GA v1 endpoint.

The client is ``openai.AsyncOpenAI`` pointed at ``https://<resource>.openai.azure.com/openai/v1/``
with an Entra token provider as ``api_key`` (the SDK awaits it before every request and sends
``Authorization: Bearer <token>``).

Every call
    * ``store=False`` unless it is a background run, with
      ``include=["reasoning.encrypted_content"]`` so tool loops can replay reasoning statelessly
      (the assistant turn comes back as :class:`~blogwriter.ai.types.NativeItems`).
    * ``reasoning={"effort": ..., "summary": "auto"}``, ``max_output_tokens``, and
      ``max_tool_calls`` when tools are present. Never ``temperature`` and never ``tool_choice``.
    * Structured output as ``text.format`` ``json_schema`` (strict), compiled by
      :mod:`blogwriter.ai.schema`; function tools are strict too.
    * ``web_search`` with ``filters.allowed_domains``, plus
      ``include=["web_search_call.action.sources"]`` so the searched URLs are known.

Background runs (``Request.background``) are created with ``background=True, store=True`` and no
SDK retries (a retried create could leave an orphaned stored response). Record the returned id
before polling; cancel and DELETE the response when done, failed or abandoned.

Assistant messages marked ``phase: "commentary"`` (progress notes some GPT models write between
tool calls) stream as :class:`~blogwriter.ai.types.ThinkingSummary` and stay out of the answer
text; they are still replayed verbatim in tool loops.

Refusals are results, not errors: a ``refusal`` content part, an ``incomplete`` response with
reason ``content_filter``, a ``failed`` response with a policy code, or an HTTP 400 with code
``content_filter`` (Azure's prompt filter) become a :class:`~blogwriter.ai.types.Refusal`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Final, cast
from urllib.parse import urlsplit

import httpx2
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncOpenAI,
    BadRequestError,
    NotFoundError,
    OpenAIError,
    not_given,
)
from openai.types.responses import (
    EasyInputMessageParam,
    FunctionToolParam,
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCallParam,
    ResponseIncludable,
    ResponseIncompleteEvent,
    ResponseTextConfigParam,
    ToolParam,
    WebSearchToolParam,
)
from openai.types.responses.response_create_params import ResponseCreateParamsBase
from openai.types.responses.response_input_param import (
    FunctionCallOutput,
    ResponseInputItemParam,
    ResponseInputParam,
)
from openai.types.shared_params import Reasoning

from blogwriter.ai.providers.auth import validate_resource_name
from blogwriter.ai.providers.base import (
    BackgroundPoll,
    ProviderError,
    ProviderRequestError,
    ProviderStreamError,
    ProviderUnavailableError,
    RateLimitedError,
    TokenProvider,
    UnsupportedRequestError,
    cap_sdk_logging,
    check_spec,
    count_attr,
    error_for_status,
)
from blogwriter.ai.schema import compile_schema
from blogwriter.ai.types import (
    Citation,
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
    StreamEvent,
    TextDelta,
    TextPart,
    ThinkingSummary,
    ToolCall,
    ToolCallPart,
    Usage,
    UsageEvent,
    WebSearchTool,
)

__all__ = [
    "OpenAIResponsesProvider",
    "azure_openai_v1_base_url",
    "create_openai_client",
]

_PROVIDER: Final[ProviderName] = "openai"
_HOST_SUFFIXES: Final = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)
_POLICY_CODES: Final = frozenset({"bio_policy", "misalignment_policy_violation"})
_RETRYABLE_FAILURES: Final = frozenset(
    {"server_error", "rate_limit_exceeded", "vector_store_timeout"}
)


def azure_openai_v1_base_url(resource: str) -> str:
    """The v1 base URL of an Azure OpenAI (Foundry) resource."""
    return f"https://{validate_resource_name(resource)}.openai.azure.com/openai/v1/"


def _check_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or not host.endswith(_HOST_SUFFIXES)
        or parts.path.rstrip("/") != "/openai/v1"
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.port not in (None, 443)
    ):
        raise ValueError(
            "base_url must be an Azure OpenAI v1 endpoint (https://<resource>.openai.azure.com/openai/v1/)"
        )
    return f"https://{host}/openai/v1/"


def create_openai_client(
    *,
    base_url: str,
    token_provider: TokenProvider,
    http_client: httpx2.AsyncClient | None = None,
    max_retries: int = 2,
    timeout: float | None = None,
) -> AsyncOpenAI:
    """An ``AsyncOpenAI`` client for the Azure v1 endpoint, authenticated with Entra ID.

    ``http_client`` is injectable (tests use ``httpx2.MockTransport``). ``timeout`` is in seconds;
    ``None`` keeps the SDK default (600 s, 5 s to connect).
    """
    cap_sdk_logging()
    return AsyncOpenAI(
        base_url=_check_base_url(base_url),
        api_key=token_provider,
        http_client=http_client,
        max_retries=max_retries,
        timeout=timeout if timeout is not None else not_given,
    )


# --------------------------------------------------------------------------------------------
# Request building
# --------------------------------------------------------------------------------------------


def _input_items(messages: Sequence[Message]) -> ResponseInputParam:
    items: list[ResponseInputItemParam] = []
    for message in messages:
        if message.native is not None and message.native.provider == _PROVIDER:
            items.extend(cast(ResponseInputItemParam, dict(item)) for item in message.native.items)
            continue
        for part in message.parts:
            if isinstance(part, TextPart):
                text_item: EasyInputMessageParam = {
                    "type": "message",
                    "role": message.role,
                    "content": part.text,
                }
                items.append(text_item)
            elif isinstance(part, ToolCallPart):
                call_item: ResponseFunctionToolCallParam = {
                    "type": "function_call",
                    "call_id": part.call_id,
                    "name": part.name,
                    "arguments": part.arguments,
                }
                items.append(call_item)
            else:
                output_item: FunctionCallOutput = {
                    "type": "function_call_output",
                    "call_id": part.call_id,
                    "output": f"Error: {part.output}" if part.is_error else part.output,
                }
                items.append(output_item)
    return items


def _tools(request: Request) -> list[ToolParam]:
    tools: list[ToolParam] = []
    for tool in request.tools:
        if isinstance(tool, FunctionTool):
            function: FunctionToolParam = {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": compile_schema(_PROVIDER, tool.parameters).json_schema(),
                "strict": True,
            }
            tools.append(function)
        else:
            search: WebSearchToolParam = {"type": "web_search"}
            if tool.allowed_domains:
                search["filters"] = {"allowed_domains": list(tool.allowed_domains)}
            if tool.search_context_size is not None:
                search["search_context_size"] = tool.search_context_size
            tools.append(search)
    return tools


def _params(spec: ModelSpec, request: Request, *, background: bool) -> ResponseCreateParamsBase:
    params: ResponseCreateParamsBase = {
        "model": spec.deployment,
        "input": _input_items(request.messages),
        "max_output_tokens": spec.max_output_tokens,
        "store": background,
    }
    if background:
        params["background"] = True
    if request.system.strip():
        params["instructions"] = request.system
    reasoning: Reasoning = {}
    if spec.effort is not None:
        reasoning["effort"] = spec.effort
        if spec.thinking_summary and spec.effort != "none":
            reasoning["summary"] = "auto"
    if reasoning:
        params["reasoning"] = reasoning
    include: list[ResponseIncludable] = [] if background else ["reasoning.encrypted_content"]
    if request.tools:
        params["tools"] = _tools(request)
        params["max_tool_calls"] = request.tool_call_cap
        if any(isinstance(tool, WebSearchTool) for tool in request.tools):
            include.append("web_search_call.action.sources")
    if include:
        params["include"] = include
    if request.output_schema is not None:
        compiled = compile_schema(_PROVIDER, request.output_schema)
        text: ResponseTextConfigParam = {
            "format": {
                "type": "json_schema",
                "name": compiled.name,
                "schema": compiled.json_schema(),
                "strict": True,
            }
        }
        params["text"] = text
    return params


# --------------------------------------------------------------------------------------------
# Response mapping
# --------------------------------------------------------------------------------------------


def _usage(response: Response) -> Usage:
    searches = count_attr(response, "tool_usage", "web_search", "num_requests")
    if searches is None:  # no tool_usage block: count the search actions instead
        searches = sum(
            1
            for item in response.output
            if item.type == "web_search_call" and getattr(item.action, "type", None) == "search"
        )
    usage = response.usage
    return Usage(
        input=count_attr(usage, "input_tokens") or 0,
        cached_input=count_attr(usage, "input_tokens_details", "cached_tokens") or 0,
        cache_write=count_attr(usage, "input_tokens_details", "cache_write_tokens") or 0,
        output=count_attr(usage, "output_tokens") or 0,
        reasoning=count_attr(usage, "output_tokens_details", "reasoning_tokens") or 0,
        web_search_requests=searches,
    )


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _refused(refusal: Refusal, usage: Usage, model: str, response_id: str | None) -> Completion:
    return Completion(
        text="",
        finish_reason="refusal",
        usage=usage,
        model=model,
        response_id=response_id,
        refusal=refusal,
    )


def _failure(response: Response, usage: Usage, *, during_stream: bool) -> ProviderError:
    code = response.error.code if response.error is not None else None
    retryable = code in _RETRYABLE_FAILURES
    kind: type[ProviderError]
    if during_stream:
        kind = ProviderStreamError
    elif code == "rate_limit_exceeded":
        kind = RateLimitedError
    elif retryable:
        kind = ProviderUnavailableError
    else:
        kind = ProviderRequestError
    return kind(
        f"openai response {response.id} failed (code={code})",
        provider=_PROVIDER,
        retryable=retryable,
        code=code,
        usage=usage,
    )


def _completion(response: Response, spec_deployment: str, *, during_stream: bool) -> Completion:
    """Map a terminal response; raises :class:`ProviderError` for non-policy failures."""
    usage = _usage(response)
    model = response.model or spec_deployment
    status = response.status or "completed"
    if status == "failed":
        code = response.error.code if response.error is not None else None
        if code in _POLICY_CODES:
            return _refused(Refusal(category=code, explanation=None), usage, model, response.id)
        raise _failure(response, usage, during_stream=during_stream)
    if status == "cancelled":
        return Completion(
            text="", finish_reason="cancelled", usage=usage, model=model, response_id=response.id
        )
    if status not in ("completed", "incomplete"):
        raise ProviderStreamError(
            f"openai response {response.id} is not finished (status={status})",
            provider=_PROVIDER,
            retryable=True,
        )

    texts: list[str] = []
    refusals: list[str] = []
    tool_calls: list[ToolCall] = []
    citations: list[Citation] = []
    queries: list[str] = []
    sources: list[str] = []
    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if content.type == "refusal":
                    refusals.append(content.refusal)
                    continue
                if item.phase != "commentary":  # progress notes are not part of the answer
                    texts.append(content.text)
                for note in content.annotations:
                    if note.type == "url_citation":
                        citations.append(Citation(url=note.url, title=note.title or None))
        elif item.type == "function_call":
            tool_calls.append(ToolCall(item.call_id, item.name, item.arguments))
        elif item.type == "web_search_call":
            action = item.action
            if action.type == "search":
                queries.extend(action.queries or ())
                if action.query:
                    queries.append(action.query)
                sources.extend(source.url for source in action.sources or ())
            elif action.url:
                sources.append(action.url)

    if refusals:
        refusal = Refusal(category=None, explanation="\n".join(refusals) or None)
        return _refused(refusal, usage, model, response.id)
    reason = response.incomplete_details.reason if response.incomplete_details else None
    if status == "incomplete" and reason == "content_filter":
        return _refused(Refusal("content_filter", None), usage, model, response.id)
    finish: FinishReason
    if status == "incomplete":
        finish = "length"
    elif tool_calls:
        finish = "tool_calls"
    else:
        finish = "stop"

    text = "".join(texts)
    parts: list[Part] = [TextPart(text)] if text.strip() else []
    parts.extend(ToolCallPart(call.call_id, call.name, call.arguments) for call in tool_calls)
    native = tuple(
        item.to_dict(mode="json", exclude_none=True, warnings=False) for item in response.output
    )
    message = Message("assistant", tuple(parts), NativeItems(_PROVIDER, native)) if native else None
    return Completion(
        text=text,
        finish_reason=finish,
        usage=usage,
        model=model,
        response_id=response.id,
        tool_calls=tuple(tool_calls),
        message=message,
        citations=tuple(dict.fromkeys(citations)),
        search_queries=_unique(queries),
        search_sources=_unique(sources),
    )


def _content_filter(exc: APIStatusError, spec: ModelSpec) -> Completion | None:
    """Azure's prompt filter answers HTTP 400 with code ``content_filter``: a refusal."""
    if isinstance(exc, BadRequestError) and exc.code == "content_filter":
        return _refused(Refusal("content_filter", None), Usage(), spec.deployment, None)
    return None


def _map_error(exc: OpenAIError | httpx2.TransportError, *, during_stream: bool) -> ProviderError:
    if isinstance(exc, APIStatusError):
        return error_for_status(
            _PROVIDER,
            exc.status_code,
            code=exc.code,
            request_id=exc.request_id,
            headers=exc.response.headers,
            during_stream=during_stream,
        )
    if isinstance(exc, APIConnectionError | httpx2.TransportError):
        kind = ProviderStreamError if during_stream else ProviderUnavailableError
        return kind(
            f"openai connection failed ({type(exc).__name__})", provider=_PROVIDER, retryable=True
        )
    if isinstance(exc, APIError):  # an error payload without an HTTP error status
        kind = ProviderStreamError if during_stream else ProviderUnavailableError
        return kind(
            f"openai call failed (code={exc.code})",
            provider=_PROVIDER,
            retryable=exc.code is None or exc.code in _RETRYABLE_FAILURES,
            code=exc.code,
        )
    kind = ProviderStreamError if during_stream else ProviderRequestError
    return kind(f"openai call failed ({type(exc).__name__})", provider=_PROVIDER, retryable=False)


def _final_events(completion: Completion) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    if completion.refusal is not None:
        events.append(completion.refusal)
    events.append(UsageEvent(completion.usage))
    events.append(Done(completion.finish_reason, completion))
    return events


# --------------------------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------------------------


class OpenAIResponsesProvider:
    """Azure OpenAI Responses API (GPT-6.x deployments)."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @property
    def name(self) -> ProviderName:
        return _PROVIDER

    async def aclose(self) -> None:
        await self._client.close()

    def _check(self, spec: ModelSpec, request: Request) -> None:
        check_spec(_PROVIDER, spec, request)
        if request.background:
            raise UnsupportedRequestError("background requests go through start_background()")

    async def complete(self, spec: ModelSpec, request: Request) -> Completion:
        """One non-streaming call (``store=False``)."""
        self._check(spec, request)
        params = _params(spec, request, background=False)
        try:
            response = await self._client.responses.create(**params, stream=False)
        except APIStatusError as exc:
            refused = _content_filter(exc, spec)
            if refused is None:
                raise _map_error(exc, during_stream=False) from exc
            return refused
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc
        return _completion(response, spec.deployment, during_stream=False)

    async def stream(self, spec: ModelSpec, request: Request) -> AsyncIterator[StreamEvent]:
        """One streaming call (``store=False``). Ends with :class:`Done` or raises."""
        self._check(spec, request)
        params = _params(spec, request, background=False)
        try:
            events = await self._client.responses.create(**params, stream=True)
        except APIStatusError as exc:
            refused = _content_filter(exc, spec)
            if refused is None:
                raise _map_error(exc, during_stream=False) from exc
            for event in _final_events(refused):
                yield event
            return
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc

        final: Response | None = None
        commentary: set[str] = set()
        try:
            async with events:
                async for event in events:
                    if event.type == "response.output_text.delta":
                        if event.item_id in commentary:
                            yield ThinkingSummary(event.delta)
                        else:
                            yield TextDelta(event.delta)
                    elif event.type == "response.output_item.added":
                        item = event.item
                        if item.type == "message" and item.phase == "commentary":
                            commentary.add(item.id)
                    elif event.type == "response.reasoning_summary_text.delta":
                        yield ThinkingSummary(event.delta)
                    elif event.type == "response.reasoning_summary_part.added":
                        if event.summary_index > 0:
                            yield ThinkingSummary("\n\n")
                    elif event.type == "response.output_item.done":
                        item = event.item
                        if item.type == "function_call":
                            yield ToolCall(item.call_id, item.name, item.arguments)
                    elif event.type == "error":
                        raise error_for_status(
                            _PROVIDER,
                            events.response.status_code,
                            code=event.code,
                            request_id=events.response.headers.get("x-request-id"),
                            headers=events.response.headers,
                            during_stream=True,
                        )
                    elif isinstance(
                        event,
                        ResponseCompletedEvent | ResponseIncompleteEvent | ResponseFailedEvent,
                    ):
                        final = event.response
                        break
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=True) from exc
        if final is None:
            raise ProviderStreamError(
                "openai stream ended without a terminal event", provider=_PROVIDER, retryable=True
            )
        for event in _final_events(_completion(final, spec.deployment, during_stream=True)):
            yield event

    # ----------------------------------------------------------------------------------------
    # Background runs
    # ----------------------------------------------------------------------------------------

    async def start_background(self, spec: ModelSpec, request: Request) -> str:
        """Create a stored background response and return its id (record it before polling)."""
        check_spec(_PROVIDER, spec, request)
        if not request.background:
            raise UnsupportedRequestError("start_background() needs Request.background=True")
        if any(isinstance(tool, FunctionTool) for tool in request.tools):
            raise UnsupportedRequestError("background runs support hosted tools only")
        params = _params(spec, request, background=True)
        try:
            response = await self._client.with_options(max_retries=0).responses.create(
                **params, stream=False
            )
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc
        return response.id

    async def poll_background(self, response_id: str) -> BackgroundPoll:
        """Retrieve a background run. Raises only if the poll itself fails."""
        try:
            response = await self._client.responses.retrieve(
                response_id, include=["web_search_call.action.sources"]
            )
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc
        return self._poll_result(response)

    async def cancel_background(self, response_id: str) -> BackgroundPoll:
        """Cancel a background run; returns its state after the cancel."""
        try:
            response = await self._client.responses.cancel(response_id)
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc
        return self._poll_result(response)

    async def delete_background(self, response_id: str) -> None:
        """DELETE a stored response. Idempotent: an id that is already gone is fine."""
        try:
            await self._client.responses.delete(response_id)
        except NotFoundError:
            return
        except (OpenAIError, httpx2.TransportError) as exc:
            raise _map_error(exc, during_stream=False) from exc

    def _poll_result(self, response: Response) -> BackgroundPoll:
        status = response.status or "in_progress"
        if status in ("queued", "in_progress"):
            return BackgroundPoll(response.id, status)
        try:
            completion = _completion(response, response.model, during_stream=False)
        except ProviderError as error:
            return BackgroundPoll(response.id, status, error=error)
        return BackgroundPoll(response.id, status, completion)
