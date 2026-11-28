# Phase 1: AI provider layer

The provider-neutral types, the per-provider structured-output compiler, and the two providers
(Azure OpenAI Responses on the v1 endpoint, Claude on Foundry). The router, limiters, ledger and
brake live elsewhere and build on this. Everything here is offline-tested; nothing has been called
against a live endpoint yet (see "Not verified live").

## Files

| Path (`server/`) | What it holds |
|---|---|
| `src/blogwriter/ai/types.py` | `Task`, `Sensitivity`, `ModelSpec`, `Message` and its parts, `NativeItems`, `FunctionTool`, `WebSearchTool`, `Request`, `Usage`, `Completion`, `Citation`, the stream events, `SensitivityError` |
| `src/blogwriter/ai/schema.py` | `compile_schema`, `CompiledSchema`, `repair_request`, `validate_with_repair`, `validate_tool_call`, the errors |
| `src/blogwriter/ai/providers/base.py` | `Provider` protocol, the `ProviderError` family, `UnsupportedRequestError`, `BackgroundPoll`, `collect`, `error_for_status`, `cap_sdk_logging` |
| `src/blogwriter/ai/providers/auth.py` | `entra_token_provider`, `FOUNDRY_SCOPE`, resource-name validation |
| `src/blogwriter/ai/providers/openai_responses.py` | `create_openai_client`, `OpenAIResponsesProvider` |
| `src/blogwriter/ai/providers/anthropic_foundry.py` | `create_anthropic_client`, `AnthropicFoundryProvider` |
| `tests/ai/test_types.py`, `tests/ai/test_schema.py`, `tests/ai/providers/*` | 251 tests; replayed SSE and JSON over `httpx2.MockTransport`, no network |

## Wiring (for the router)

```python
from azure.identity.aio import DefaultAzureCredential  # ManagedIdentityCredential in ACA
from blogwriter.ai.providers import (
    AnthropicFoundryProvider, OpenAIResponsesProvider,
    create_anthropic_client, create_openai_client, entra_token_provider,
)

tokens = entra_token_provider(DefaultAzureCredential())          # scope https://ai.azure.com/.default
openai = OpenAIResponsesProvider(create_openai_client(
    base_url=AZURE_OPENAI_BASE_URL, token_provider=tokens))       # https://<res>.openai.azure.com/openai/v1/
claude = AnthropicFoundryProvider(create_anthropic_client(
    resource=ANTHROPIC_FOUNDRY_RESOURCE, token_provider=tokens))  # https://<res>.services.ai.azure.com/anthropic/
```

`infra/v2` already sets `AZURE_OPENAI_BASE_URL` and `ANTHROPIC_FOUNDRY_RESOURCE` on the container
app; the router's `BW_AI_` settings class should map them. The factories take explicit values and
read no environment variables themselves. Close providers with `await provider.aclose()` on shutdown.

Both providers satisfy `Provider`: `stream(spec, request)` yields `TextDelta`, `ThinkingSummary`,
`ToolCall`, then `Refusal` (if refused), `UsageEvent`, and finally `Done(finish_reason, completion)`;
`complete(spec, request)` returns the same `Completion`. Errors raise `ProviderError` subclasses
with `retryable`, `status_code`, `code`, `request_id`, `retry_after` and, when the provider reported
it, `usage` (record it in the ledger even on failure).

Router duties this layer leaves to the caller:
- On `Refusal` (or `finish_reason == "refusal"`), discard any `TextDelta` text already shown and never
  checkpoint it. Anthropic can stream text and then refuse.
- Structured output: `compile_schema(spec.provider, Model)` is what the provider sent; validate with
  `validate_with_repair(compiled, request, completion, retry)`, which does at most one repair turn
  and raises `StructuredOutputError("refusal" | "incomplete" | "invalid")`. Then fall back.
- Tool loops: append `completion.message` (it carries `NativeItems` for lossless replay) and a
  `Message.tool_results(...)` turn; validate arguments with `validate_tool_call`.
- Background runs (OpenAI only): `start_background` returns the response id. Persist it before
  polling, then `poll_background` until `done`. Afterwards (completed, failed, cancelled or abandoned)
  call `cancel_background` if it is still running, and then `delete_background`.

## Exact SDK calls (verified against the installed source)

Pins: `openai==3.60.0`, `anthropic==2.14.0`, `azure-identity==1.27.0`; both SDKs run on `httpx2==2.16.2`.

### httpx2, not httpx
Both SDKs depend on `httpx2` (`Requires-Dist: httpx2<3`), not `httpx`. `anthropic/_base_client.py:1884`
calls `_reject_httpx_object("http_client", ...)`, which raises a `TypeError` for any `httpx` object.
Both SDKs type `http_client` as `httpx2.AsyncClient`. Tests therefore inject
`httpx2.AsyncClient(transport=httpx2.MockTransport(handler))`. The `httpx.MockTransport` wording in
`server/ARCHITECTURE.md` does not apply to the SDK clients (plain `httpx` stays right for other HTTP).

### Azure OpenAI (`openai.AsyncOpenAI`)
- Client: `AsyncOpenAI(base_url="https://<res>.openai.azure.com/openai/v1/", api_key=<async token
  callable>, http_client=..., max_retries=..., timeout=...)`. `lib/_client_auth.py:565` stores a
  callable `api_key` as `_api_key_provider`, and `async_prepare_options` (`:844`) awaits it for every
  request whose options carry `security={"bearer_auth": True}` (all Responses calls). The result is
  `Authorization: Bearer <token>`. Passing the callable means `OPENAI_API_KEY` is never read. There is
  no Azure provider object in `openai.providers` (only Bedrock and Vertex), and `AzureOpenAI` is built
  around `api_version` and `/deployments/{name}` routing, so the plain client on the v1 base URL is
  the right one. The factory
  only accepts `https` hosts under `.openai.azure.com`, `.services.ai.azure.com` or
  `.cognitiveservices.azure.com` with path `/openai/v1/`, so a bearer token cannot be sent elsewhere.
- `client.responses.create(...)` (`resources/responses/responses.py:3344`) with:
  `model=<deployment>`, `input=[...]`, `instructions=<system>`, `max_output_tokens`,
  `store=False`, `include=["reasoning.encrypted_content"]` (+ `"web_search_call.action.sources"`
  with web search), `reasoning={"effort": e, "summary": "auto"}` (no summary when effort is `none` or
  `thinking_summary` is off), `tools=[{"type": "function", ..., "strict": True},
  {"type": "web_search", "filters": {"allowed_domains": [...]}, "search_context_size": ...}]`,
  `max_tool_calls` (default 8), `text={"format": {"type": "json_schema", "name", "schema",
  "strict": True}}`, and `stream=True|False`. `temperature`, `top_p` and `tool_choice` are never sent.
- Background: `client.with_options(max_retries=0).responses.create(..., background=True,
  store=True, stream=False)`; `client.responses.retrieve(id, include=["web_search_call.action.sources"])`
  (`:3590`); `client.responses.cancel(id)` (`:3668`, POST `/responses/{id}/cancel`);
  `client.responses.delete(id)` (`:3630`, DELETE `/responses/{id}`; a 404 counts as done).
- Stream events used: `response.output_text.delta`, `response.reasoning_summary_text.delta`,
  `response.reasoning_summary_part.added` (a blank line between summary parts),
  `response.output_item.added` (to spot `phase: "commentary"` messages),
  `response.output_item.done` (complete `function_call` items), `error`, and the terminal
  `response.completed` / `response.incomplete` / `response.failed`. The final `Response` from the
  terminal event is mapped exactly like a non-streaming one.
- Usage: `Response.usage.input_tokens`, `.input_tokens_details.cached_tokens`,
  `.input_tokens_details.cache_write_tokens`, `.output_tokens`,
  `.output_tokens_details.reasoning_tokens`; searches from `Response.tool_usage.web_search.num_requests`.
  If `tool_usage` is absent, the count of `web_search_call` items whose action is `search` is used
  instead, which over-counts rather than under-counts. Fields are read leniently (`count_attr`)
  because the SDK builds models without validation and Azure may omit newer fields.
- Errors: `APIStatusError.status_code/.code/.request_id/.response.headers`; `BadRequestError` with
  `code == "content_filter"` is Azure's prompt filter; `APIConnectionError`/`APITimeoutError`; an
  in-stream `{"error": ...}` payload raises `APIError` (`_streaming.py`, legacy stream mode); a
  `type: "error"` event arrives as `ResponseErrorEvent`.

### Claude on Foundry (`anthropic.AsyncAnthropicFoundry`)
- Client: `AsyncAnthropicFoundry(base_url="https://<res>.services.ai.azure.com/anthropic/",
  azure_ad_token_provider=<async token callable>, http_client=..., max_retries=..., timeout=...)`
  (`lib/foundry.py:305`). `_prepare_options` (`:481`) awaits the provider on every request and sends
  `Authorization: Bearer <token>`. `auth_headers` (`:499`) returns `{}`, so an environment
  `ANTHROPIC_API_KEY` is never sent. The base URL is passed explicitly, so `ANTHROPIC_FOUNDRY_RESOURCE`
  and `ANTHROPIC_FOUNDRY_BASE_URL` are ignored. A test sets all four Anthropic environment variables
  and checks that only the bearer token goes to the configured host.
- `client.messages.stream(...)` (`resources/messages/messages.py:3105`) with `model=<deployment>`,
  `max_tokens`, `messages`, `system=[{"type": "text", "text": ..., "cache_control": {"type":
  "ephemeral", "ttl": "1h"}}]`, `tools=[{"name", "description", "input_schema", "strict": True}]`
  (the last tool gets the 1 h breakpoint when there is no system prompt),
  `thinking={"type": "adaptive", "display": "summarized"}` (`"omitted"` if `thinking_summary` is
  off), and `output_config={"effort": e, "format": {"type": "json_schema", "schema": ...}}`. A message
  `TextPart(cache_breakpoint=True)` also gets the 1 h breakpoint (at most 3, plus the system prompt).
  In 2.14.0, `create` and `stream` have no `temperature`, `top_k` or `top_p` parameters at all, and
  `tool_choice` is never sent. Structured output goes through `output_config.format` with the schema
  compiled here. The SDK's `output_format=` helper is not used: it runs the SDK's own
  `transform_schema`, and `lib/_parse/_transform.py` marks it deprecated in favour of
  `output_config.format`.
- `complete` drains the same stream, so long drafts never hit the SDK's non-streaming timeout guard.
- Stream events used (from `lib/streaming/_messages.py:build_events`): `text`, `thinking`,
  `content_block_stop` (complete `tool_use` blocks; input serialized as JSON text) and
  `message_stop` (the accumulated `ParsedMessage`). `ping` and `signature` are ignored.
- Refusal: `Message.stop_reason == "refusal"` with `stop_details` (`RefusalStopDetails.category`:
  `cyber`, `bio`, `frontier_llm`, `reasoning_extraction`, `general_harms`; `.explanation`).
- Usage: `input = input_tokens + cache_creation_input_tokens + cache_read_input_tokens`,
  `cached_input = cache_read_input_tokens`, `cache_write = cache_creation_input_tokens` (always the
  1 h TTL here), `reasoning = output_tokens_details.thinking_tokens`,
  `web_search_requests = server_tool_use.web_search_requests`.
- Errors: HTTP errors raise `APIStatusError` subclasses (`.status_code`, `.type`, `.error_code`,
  `.request_id`). A mid-stream `event: error` raises `APIStatusError` carrying the stream's 200 status
  (`_streaming.py:228`), so status < 400 means "failed mid-stream". A dropped connection surfaces as a
  raw `httpx2.TransportError`. Unparseable partial tool JSON raises a `ValueError` whose message quotes
  the model output; it is converted with `from None` so the text never reaches a traceback.

### Entra tokens
`azure.identity.aio.get_bearer_token_provider(credential, "https://ai.azure.com/.default")`
(`azure/identity/aio/_bearer_token_provider.py:17`) returns an async callable backed by
`AsyncBearerTokenCredentialPolicy`, which caches the token and refreshes it before expiry. One
provider serves both clients.

## Rules and where they are enforced

| Rule | Where |
|---|---|
| Only `public` data reaches tools or stored background runs | `Request.__post_init__` (`SensitivityError`) |
| `private` data never goes to Claude | `AnthropicFoundryProvider._check` (`SensitivityError`, nothing sent) |
| Every request declares its class | `Request.sensitivity` has no default |
| `store=False` on every non-background call | `_params`, asserted on the wire |
| Background runs: `store=True`, no SDK retries, then DELETE | `start_background` / `delete_background` |
| No `temperature`, `top_k`, `top_p`, `tool_choice` | both providers, asserted on the wire |
| Refusals are results; partial output is dropped from the `Completion` | `Refusal` event, `Completion` invariant |
| Prompts and completions stay out of logs | content fields hidden from `repr`; error messages carry codes and ids only; `cap_sdk_logging()` keeps the `openai`/`anthropic` loggers at INFO or above (at DEBUG the Anthropic SDK logs full request options) |

Refusal sources: an OpenAI `refusal` content part (category `None`, the refusal text as
explanation); `incomplete` with reason `content_filter`; `failed` with `bio_policy` or
`misalignment_policy_violation`; an Azure HTTP 400 with code `content_filter`; Anthropic
`stop_reason: refusal`.

Finish reasons: `stop`, `tool_calls`, `length` (OpenAI `incomplete`, or Anthropic `max_tokens` or
`model_context_window_exceeded`), `refusal`, `pause` (Anthropic `pause_turn`), `cancelled`.

OpenAI messages with `phase: "commentary"` (progress notes between tool calls) stream as
`ThinkingSummary` and are kept out of `Completion.text`. They are still replayed verbatim.

## Structured-output compiler
- Both providers: `$ref` inlined; `oneOf` becomes `anyOf`; a single-schema `allOf` is merged; `const`
  becomes a one-value `enum`; `additionalProperties: false` everywhere; `title`, `default` and other
  annotations dropped. `minLength`, `maxLength`, `minItems`, `maxItems`, `minimum`, `maximum`,
  `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `pattern`, `format`, `uniqueItems`,
  `minProperties` and `maxProperties` are stripped and restated in the description
  (`Constraints: maxLength=40`). Pydantic re-checks them. Any keyword not on these lists fails to
  compile (`SchemaCompileError`), as do `dict`/`Any`/tuple fields and non-object roots.
- OpenAI strict: every property is required, and an optional field that cannot already be null
  becomes `anyOf [T, null]`. On the way back, `null` for such a field means "absent", so Pydantic
  applies the default. A one-value literal (a union tag) is filled in instead, because discriminated
  unions need the tag. Recursive models keep `$ref` into `$defs`.
  Constraints are stripped for OpenAI too: Azure's structured-output keyword support is not verified.
- Anthropic: only the model's required fields are required. Recursion is stripped: an optional
  recursive field or union branch is dropped; a required recursive field fails to compile.
- Validation always uses the original model (`model_validate_json`). Problems are reported as
  `location: message` lines without input values, and validation errors are raised `from None`.

## Not verified live (Phase 0 cloud spikes still pending)
- Azure acceptance of `reasoning.summary="auto"`, `include` values, `web_search` `filters` and
  `max_tool_calls` on `gpt-6.2-sol`. If summaries are gated, set `ModelSpec.thinking_summary=False`.
- Whether Azure reports `tool_usage` and `input_tokens_details.cache_write_tokens`. Both are read
  leniently, with a fallback for `tool_usage`.
- Background mode on the Azure v1 endpoint: the exact DELETE semantics for a still-running response
  (the intended order is cancel, then delete), and whether Azure honours idempotency keys.
- Foundry support for `thinking.display`, `output_config.effort`/`format` and the 1 h cache TTL on
  `claude-opus-5-5`.
- The exact Azure content-filter error body (assumed `error.code == "content_filter"`, HTTP 400).

## Requests for other owners
- `pyproject.toml`: add `httpx2==2.16.2` (and optionally `azure-core==1.41.0`) as direct
  dependencies. `ai.providers` imports them; today they come in transitively and are pinned only by
  `uv.lock`.
- Core logging: keep the `openai`, `anthropic` and `httpx2` loggers at WARNING in the logging config.
  The factories cap the SDK loggers at INFO as a backstop.
- `server/ARCHITECTURE.md`: the SDK clients use `httpx2` (see above).
