# Phase 1: AI router, policy, limits, ledger and brake

The layer on top of the providers (`ai-providers.md`): one entry point that routes each task to a
model, enforces the sensitivity classes, applies per-model limits and retries, falls back, records
`served_by`, ledgers every call and brakes runaway background spend. Offline-tested only.

## Files (`server/`)

| Path | What it holds |
|---|---|
| `src/blogwriter/ai/config.py` | `AISettings` (`BW_AI_*`), default routes and prices, `ModelSettings`, `RouteSettings`/`RouteTarget`, runtime `Route` and `RoutingTable`, `NoRouteError` |
| `src/blogwriter/ai/policy.py` | `TASK_CEILINGS`, `PRIVATE_TASKS`, `TaskPolicy`, `validate_route`, `SensitivityPolicy`, `PolicyViolation` |
| `src/blogwriter/ai/limits.py` | `Clock`/`SystemClock`, `TokenBucket`, `ModelLimits`, `ModelLimiter`, `Lease`, `RetryPolicy`, `retry_reason`, `estimate_input_tokens` |
| `src/blogwriter/ai/ledger.py` | `ModelPrice`, `PriceTable`, `UsageRecord`, `LedgerSink`, `MemoryLedger`, `BudgetBrake`, `BrakeStatus`, `BrakeEngaged` |
| `src/blogwriter/ai/router.py` | `AIRouter`, `RouterResult`, `ServedBy`, events `Retry`/`Repair`/`Fallback`/`Finished`, `create_providers` |
| `src/blogwriter/ai/__init__.py` | Re-exports the public API above |
| `tests/ai/test_{router,policy,ledger,limits}.py` | 292 tests; fake providers and a fake clock, plus one wire-level test with the real providers over `httpx2.MockTransport` |

## API

```python
settings = AISettings()                                    # BW_AI_*; validates the whole table
providers = create_providers(settings, entra_token_provider(credential))  # SDK max_retries=0
router = AIRouter.from_settings(settings, providers, ledger=sink)         # sink: LedgerSink
result = await router.run(Task.DRAFT, request, origin="background", job_id=job.id)
result.text; result.value / result.output(Model); result.served_by.to_dict(); result.review_only
result.usage; result.cost_usd                              # every call, discarded ones included
async with aclosing(router.stream(task, request, origin="owner")) as events: ...
await router.aclose()                                      # on shutdown
```

`origin` is required (`"owner"` or `"background"`), like `Request.sensitivity`: callers must
classify. Owner calls are never braked.

Stream events: `TextDelta`, `ThinkingSummary`, `ToolCall`, `Refusal` (live, current attempt);
`Retry`, `Repair`, `Fallback` void the current attempt (discard everything since the previous one;
`DISCARD_EVENTS`); `Finished(result)` is last and the only output to keep. Run tool calls from
`result.completion.tool_calls` only. Iterate with `aclosing` so an abandoned stream frees its slot.

## Order of operations (nothing is sent until steps 1-4 pass)

1. Route lookup: no route raises `NoRouteError` (`image` has a route only when
   `BW_AI_IMAGE_BASE_URL` is set).
2. `SensitivityPolicy.check` against every model of the chain: `PolicyViolation`.
3. `embed`/`image` and background requests: `UnsupportedRequestError` (not chat calls; images
   go through `generate_image`, embeddings through `embed`).
4. Output schema compiled for each provider of the chain; budget brake for background work.
5. Primary call through its limiter, retrying 429/5xx (default 4 attempts, waits 0.5-1 s,
   1-2 s, 2-4 s, capped at 30 s; `Retry-After` is a floor).
6. Structured output re-validated with Pydantic (per-provider compile, so OpenAI null-as-absent
   works); one repair turn quoting the problems.
7. Fallback on: refusal; retries exhausted (`rate_limited` / `unavailable`); output still invalid
   after the repair (`invalid_output`) or truncated (`incomplete_output`). Other errors are raised
   as they are, with no retry and no fallback. Without a fallback, a refusal is returned as a refused
   result (text empty) and invalid output raises `StructuredOutputError`.
8. `ServedBy(task, model, deployment, provider, effort, fallback_reason, review_only, repaired)`;
   `review_only` is true when a fallback served `draft`, `revise` or `critique`.

## Images (`Task.IMAGE`, opt-in)

`router.generate_image(ImageRequest(prompt=..., sensitivity=Sensitivity.PUBLIC, size=...),
origin=..., job_id=...)` returns an `ImageResult`: the PNG (`result.image`), or a refusal
(`result.refused`, category `content_filter`) when Azure's filter or OpenAI's moderation
declined, which is a result and never raised. It runs the same checks as `run`: the route
(`NoRouteError` until `BW_AI_IMAGE_BASE_URL` is set), the policy (the `image` ceiling is
`public`, tool-free), the brake for background work, the limiter (`gpt-image-2` concurrency 1)
and 429/5xx retries; no fallback. The provider (`OpenAIImagesProvider`) calls
`POST /images/generations` on the separate East US 2 resource with `model` (the deployment),
`prompt`, `size` (`1536x1024` hero, `1024x1024` inline), `quality: high`, `output_format: png`
and `n: 1`, authenticated with the same Entra token provider as the Responses provider
(`create_image_provider`, SDK retries off). The ledger prices each image by size
(`PriceTable.image_cost`, `ModelPrice.image_sizes`): `BW_AI_IMAGE_USD_1536X1024` (0.25) and
`BW_AI_IMAGE_USD_1024X1024` (0.17), the assumed GPT Image high-quality prices; its tokens are
recorded but never billed on top. `AIRouter.serves_images` says whether a route and a provider
are both there (the `ImageGenerator` port in `domain.ports`).

Image input (`ImagePart`) is accepted only for the tasks in `policy.IMAGE_TASKS`, up to the
class listed: `engagement` (anonymized screenshots) and `fact_check` (public only: the
illustrations' "media matches text" check of a generated image).

## Rules and where they are enforced

| Rule | Where |
|---|---|
| Request class must not exceed the task max | `SensitivityPolicy.check` (`above_task_max`) |
| Tools and `web_search` only with `public` data, only on tool tasks | `check` (`tools_not_allowed`, `tools_require_public`) and `Request` |
| `private` only to the anonymizer, only Azure OpenAI, tool-free; strict mode refuses it | ceilings, `validate_route`, `check` (`private_requires_openai`, `private_disabled`) |
| A fallback can never relax a rule | every rule is checked on every chain model before the first call; route rules are per task, not per model |
| Config can tighten, never loosen | `TASK_CEILINGS` in code; `TaskPolicy` and `validate_route` fail at startup |
| Retries only for model calls, only 429/5xx | `retry_reason`; used only by `AIRouter._call` |
| Opus one call at a time; RPM, input TPM, output TPM | `ModelLimiter`, one per model family (draft and revise share Opus's) |
| No forced `tool_choice`, no `temperature` | request types have neither; wire-level test over both real providers |
| Partial output of a refused/retried attempt never final | result text comes only from the serving completion |
| Logs: ids, counts, durations, costs only | `ai_model_call`, `ai_retry`, `ai_fallback`, `ai_brake_engaged`, `ai_ledger_write_failed`; test formats them with `JsonFormatter` |

Ceilings (from the plan): draft, revise, outline, plan, interview, synthesis, critique,
fact_check, engagement, embed = anonymized, no tools; anonymize = private, no tools; research,
quick_search = public, tools; triage, image = public, no tools.

## Defaults

Routes: see the table in `config.py`'s docstring (draft/revise Opus high, fallback Sol high;
outline/plan/interview/synthesis Sol high, fallback Astra; critique Astra, fallback Sol xhigh;
fact_check and anonymize Sol, fallback Astra; research Sol, quick_search Sol low, both with Astra
fallback; triage Luna; engagement Sol low; embed text-embedding-3-large). Anonymize has reasoning
summaries off so private text is not echoed into the stream.

Prices (USD per 1M tokens): Sol 2 / 10 / cached 0.10; Astra 8 / 40 / cached 0.40; Luna 0.10 / 0.50;
Opus 4 / 20 / cache write (1 h) 8 / cache hit 0.20; embedding 0.13; `web_search` 14 per 1K.
Unset cached or cache-write prices bill at the input price.

Limits: Opus concurrency 1, 40 RPM, 40K input TPM, 8K output TPM. Other models unlimited
client-side (429s are retried and set a shared cooldown). Input is reserved from a chars/4
estimate and settled with the real count (cached tokens included, which is conservative). Output is
charged after the call; the next call waits until the bucket is out of debt.

Brake: `max($25, 3 x median of the previous 7 days)` per `America/New_York` day; days without
records count as zero; today is not in the median. Spend must exceed the threshold. If the sink
cannot be read, background work is refused (`reason="ledger_unavailable"`). `ai_brake_engaged`
is logged once per day, for the out-of-band alert rule.

Overrides: `BW_AI_MODEL_OVERRIDES`, `BW_AI_ROUTE_OVERRIDES`, `BW_AI_PRICE_OVERRIDES` (JSON; each
entry replaces the default entry with the same key), `BW_AI_DEPLOYMENT_{SOL,ASTRA,LUNA,OPUS,EMBEDDING}`,
`BW_AI_WEB_SEARCH_USD_PER_1K`, `BW_AI_ALLOW_PRIVATE`, `BW_AI_RETRY_*`, `BW_AI_BRAKE_*`, and for
images `BW_AI_IMAGE_BASE_URL`, `BW_AI_DEPLOYMENT_IMAGE`, `BW_AI_IMAGE_USD_1536X1024`,
`BW_AI_IMAGE_USD_1024X1024`.

## Decisions to review

- Fallback also triggers on exhausted 5xx (`unavailable`) and on invalid/truncated structured
  output after the repair turn (the plan says "repair, then fall back"), not only on refusal and quota.
- A failure with no HTTP response (connection error, timeout) is retried like a 503. 408 and 409
  are not retried.
- A `Retry-After` above 60 s stops retrying at once (fall back now) and cools the model down for
  every caller.
- Research and quick search fall back to Astra, and the anonymizer falls back to Astra (plan rows).
  Triage has no fallback (the providers have no Flex service tier).
- A ledger write failure is logged and does not fail the call (it already ran and was paid for).
- `run()` drains `stream()`: one code path for both.

## Not verified live

- Opus on Foundry: whether ITPM counts cache reads, the `Retry-After` header names, real 429 rates.
- Fallback inside an OpenAI tool loop replays Sol's encrypted reasoning items to Astra
  (`NativeItems` are per provider, not per model); Azure may reject that.

## Requests for other owners

- Providers: an embeddings call and image generation: both done (`embed`, `generate_image`).
- infra/v2: set `BW_AI_OPENAI_BASE_URL`, `BW_AI_ANTHROPIC_RESOURCE` and `BW_AI_DEPLOYMENT_*`
  (it sets `AZURE_OPENAI_BASE_URL`, `ANTHROPIC_FOUNDRY_RESOURCE`, `MODEL_DEPLOYMENT_*` today).
- Wiring (core): build the router only when `AISettings().configured`, with a Cosmos-backed sink,
  and close it on shutdown.
- db/jobs: a Cosmos `LedgerSink` (upsert `UsageRecord.to_document()` by id into `usage`,
  partition `/month`; `spend_between` sums `costUsd` over `at`, across months). Jobs pass
  `origin`, store `served_by.to_dict()` per step, and treat `BrakeEngaged` as a pause, not a failure.
- Alerts: a log alert on `ai_brake_engaged` (and `ai_ledger_write_failed`).

## Checks (2026-11-26)

`uv sync --locked` ok; pyright 0 errors (whole project); pytest 2359 passed, 1 skipped (live
Cosmos). Ruff check and format pass on every file here. Whole-tree ruff runs during the parallel
build reported findings only in other packages that were still being edited (`jobs/`, then
`tests/notify/`).
