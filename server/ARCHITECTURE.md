# Blog Writer v2 server: architecture rules

These rules bind every contributor (human or agent). The full design is in the v2 plan; this
file covers the conventions that keep separately built packages fitting together.

## Package boundaries (`src/blogwriter/`)
| Package | Owns | Must not |
|---|---|---|
| `core` | settings base, structured logging, auth (Easy Auth principal + owner check + `__Host-bw` cookie + CSRF), errors, crypto vault, egress helper, app factory (`core.app:create_app`), SPA serving, security headers, loop-lag watchdog | import from `ai`, `jobs`, `db` internals (only their public entry points, during wiring) |
| `db` | `Store` protocol, `CosmosStore` (azure.cosmos.aio, Entra ID), `MemoryStore` (tests), filter DSL, container registry (mirrors `infra/v2/data` Terraform), startup existence check | create or delete containers (Terraform does that) |
| `jobs` | claim documents (lease + fencing), job runner, scheduler tick, in-process broker, the app-wide SSE endpoint | call LinkedIn or any provider directly |
| `ai` | providers (OpenAI v1 Responses, Anthropic on Foundry), router, per-provider schema compiler, stream normalization, refusal handling, sensitivity classes, `served_by`, limiters, cost ledger (via a `LedgerSink` protocol), runaway brake | persist anything itself (sinks are injected) |
| `notify` | web push + out-of-band alert events (title + link only) | include draft text in any payload |
| `publish` | the published-blogs v2 contract validator (Phase 1), later the renderer | |
| `linkedin` | little-text serializer and lint (Phase 0, done) | |
| `api` | thin routers that compose the packages | hold business logic |

## Hard rules
1. **Async only on the event loop.** Use `httpx.AsyncClient`, `AsyncOpenAI`, `AsyncAnthropicFoundry`, `azure.cosmos.aio`, `azure.identity.aio`. Sync or CPU-bound libraries go through `asyncio.to_thread` (bounded executor). Never call `time.sleep` or blocking I/O in async code.
2. **Crash-only.** SIGKILL at any moment must be safe. Durable state lives in the store, never only in memory. Remote ids are recorded before awaiting.
3. **Fail closed.** Auth, owner checks, sensitivity-class checks and gates deny when unsure. No env flag may disable auth outside `BW_ENV=local`.
4. **No secrets or content in logs.** Structured JSON with a field allowlist. Never log headers, tokens, prompts, completions or fetched content. Log ids, counts, durations and costs.
5. **Strict typing.** `pyright` strict on `src` and `tests`. No `Any` leaks in public signatures, except JSON documents typed as `dict[str, Any]` at the store boundary.
6. **Configuration.** One `pydantic-settings` class per package, env prefix `BW_<PACKAGE>_` (for example `BW_COSMOS_ENDPOINT`, `BW_AI_...`, `BW_JOBS_...`). Core settings use `BW_` (`BW_ENV`, `BW_OWNER_OID`, `BW_BUILD_SHA`). Defaults must be safe for local tests.
7. **Integration points.** A package exposes its HTTP routes as `get_router() -> APIRouter` and its lifecycle as `async def startup(app)` / `async def shutdown(app)`, only if it needs them. Wiring happens in one place, `blogwriter.main:build_app()` (run it with `uvicorn blogwriter.main:app --factory`), which composes them through `core.app:create_app()`.

## Tests
- `pytest-asyncio` runs in auto mode, so `async def test_...` just works.
- **No network in unit tests.** Use fakes, `MemoryStore`, `httpx.MockTransport`, and the SDKs' injectable `http_client`. Note: openai 3.x and anthropic 2.x run on **`httpx2`**, so SDK mocks use `httpx2.AsyncClient(transport=httpx2.MockTransport(...))`. Anthropic rejects `httpx` objects.
- Live tests are opt-in and skipped by default:
  - `BW_LIVE_COSMOS=1` runs against the v2 Cosmos account with `AzureCliCredential`.
  - Live tests write only ids prefixed `test-` and clean up after themselves.
- Each package keeps its tests in `tests/<package>/`, with its own `conftest.py` there if needed. The root `tests/conftest.py` is shared: do not edit it.
- Run everything: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright && uv run pytest -q`.

## Dependencies
Exact pins live in `pyproject.toml`. Contributors working in parallel must **not** edit `pyproject.toml` or `uv.lock`. Request a new dependency in your report instead.
