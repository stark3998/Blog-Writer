# Phase 1: `blogwriter.core` (platform and security baseline)

What exists in `server/src/blogwriter/core` and `server/src/blogwriter/api`, how to wire packages
into it, and what the other parts of v2 (web, infra, CI) need to know. The binding rules are in
`server/ARCHITECTURE.md`.

## Public API

| Module | Use it for |
|---|---|
| `core.app` | `create_app(settings=None, *, routers=(), lifecycles=()) -> FastAPI`; `Lifecycle` protocol; `app()` zero-argument factory for uvicorn |
| `core.settings` | `CoreSettings` (env prefix `BW_`); `settings_from_request` (FastAPI dependency); `normalize_oid`, `normalize_origin` |
| `core.auth` | `OwnerPrincipal` (route parameter annotation), `current_principal` (dependency), `Principal`, `COOKIE_NAME`, `EXEMPT_PATH`; internals `Authenticator`, `CookieSigner`, `parse_easy_auth_principal`, `is_same_origin` |
| `core.errors` | `ProblemError` (raise from routes), `problem_response(...)`, `PROBLEM_MEDIA_TYPE`, `exception_handlers()` |
| `core.logging` | `register_log_fields(*names)`, `current_request_id()`, `describe_exception(exc)`, `configure_logging()`, `JsonFormatter`, `REQUEST_ID_HEADER` |
| `core.security_headers` | `SPA_CSP`, `API_CSP`, `is_api_path` |
| `core.spa` | `SpaFiles` (used by `create_app` when `BW_SPA_DIR` is set) |
| `core.watchdog` | `LoopWatchdog` (started by the app lifespan; `app.state.watchdog`) |
| `core.testing` | test helpers: `make_settings`, `asgi_client`, `principal_headers`, `owner_cookie`, `write_headers`, `OWNER_OID`, `OTHER_OID`, `ORIGIN` |
| `api.health`, `api.me` | `get_router()` for `GET /api/health` and `GET /api/me` (always mounted by `create_app`) |

## Environment variables (`CoreSettings`)

| Variable | Default | Notes |
|---|---|---|
| `BW_ENV` | `test` | `local`, `test` or `prod`. Inside Azure Container Apps (`CONTAINER_APP_NAME` set) anything but `prod` refuses to start. |
| `BW_OWNER_OID` | unset | Owner's Entra object ID (GUID, normalized to lowercase). Unset: every request except health is denied. **Required in prod.** |
| `BW_COOKIE_KEY` | unset | HMAC key for `__Host-bw`, at least 32 bytes. **Required in prod.** Outside prod a random per-process key is used when unset. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `BW_PUBLIC_ORIGIN` | unset | For example `https://writer.jatinmadan.com` (origin only, no path). **Required in prod, https.** Used by the CSRF `Origin` fallback. |
| `BW_BUILD_SHA` | `dev` | Reported by `/api/health`. 1-64 chars of `[0-9A-Za-z._-]`. |
| `BW_SPA_DIR` | unset | Built SPA (`index.html` + `assets/`). Unset: no SPA serving. Missing directory or `index.html` fails the boot. |
| `BW_DEV_PRINCIPAL_OID` | unset | Injected as the Easy Auth principal **only when `BW_ENV=local`** (ignored with a warning elsewhere). Still subject to the owner check, so set it to `BW_OWNER_OID` (or to another GUID to try the 403 path). |
| `BW_COOKIE_TTL_HOURS` | `12` | 1-24. |
| `BW_WATCHDOG_THRESHOLD_MS` | `500` | 10-60000. |

Empty values count as unset. Other packages' `BW_<PACKAGE>_...` variables are ignored by `CoreSettings`.

## Wiring routers and lifecycles

```python
from blogwriter.core.app import create_app

def app() -> FastAPI:  # uvicorn blogwriter.<wiring module>:app --factory
    return create_app(
        routers=[jobs.get_router(), ai.get_router()],
        lifecycles=[db_lifecycle, jobs_lifecycle],  # objects or modules with async startup(app)/shutdown(app)
    )
```

- Each router carries its full paths (`APIRouter(prefix="/api/jobs")`); `create_app` includes them
  without a prefix, after `/api/health` and `/api/me` and before the SPA catch-all. Two routes with
  the same method and path fail the boot.
- `Lifecycle` is `async startup(app: FastAPI)` / `async shutdown(app: FastAPI)` (ARCHITECTURE rule 7).
  A module with module-level functions of those names qualifies. Lifecycles start in order after
  the loop watchdog and stop in reverse order; a failed startup stops the ones already started and
  aborts the boot; each shutdown is bounded to 10 s and its errors are logged, not raised.
- Routes get the owner with `principal: OwnerPrincipal` and settings with
  `Annotated[CoreSettings, Depends(settings_from_request)]`. Raise `ProblemError(status, code=...,
  detail=...)` for errors.
- No route can opt out of auth. The only exempt path is `/api/health`. WebSockets are refused
  (SSE is the push channel).
- Run: `uvicorn blogwriter.core.app:app --factory --no-access-log --no-server-header`
  (core only, until a wiring module exists). `create_app` configures logging itself and turns
  uvicorn's access log off regardless of flags.

Middleware order (outermost first): request id, access log, security headers, error boundary, auth.

## Client contract (for `web/`)

- **401** `code: unauthenticated` or `invalid_principal`: no session. Probe with
  `fetch(..., {redirect: 'manual'})`; if expired, top-level redirect to `/.auth/login/aad`.
- **403** `code: not_owner` or `owner_not_configured`: wrong account; show a sign-out screen.
- **403** `code: csrf_failed`: an unsafe request without a valid `__Host-bw` or not same-origin.
  The response carries a fresh cookie when the Easy Auth headers were valid: retry once (or call
  `GET /api/me`, which refreshes the cookie, then retry).
- Every error is `application/problem+json`: `{type, title, status, detail?, code?, request_id}`;
  422 adds `errors: [{loc, msg, type}]` (the submitted values are never echoed).
- `GET /api/me` returns `{oid, name}`; `name` is `null` when only the cookie authenticated.
- Every response has `X-Request-Id`; show it in error toasts for support.
- OpenAPI for the generated client: `GET /api/openapi.json` (authenticated), or offline with
  `create_app().openapi()`. Swagger/ReDoc pages are off (CDN scripts break the CSP).
- **CSP**: no inline `<script>` or `<style>`, no `eval`, no third-party origins (fonts, images,
  scripts all self-hosted); `img-src` allows `data:` and `blob:`. With `vite-plugin-pwa` use
  `injectRegister: 'script'` (not `'inline'`). Libraries that inject `<style>` tags at runtime
  need their CSS imported as a file instead. React `style={...}` props are fine (CSSOM).
- `<link rel="manifest" ... crossorigin="use-credentials">`: manifests are fetched without
  credentials otherwise, and Easy Auth would redirect them to login.
- Caching: `/assets/*` is `private, max-age=31536000, immutable` (keep Vite's default
  `build.assetsDir` for hashed output only; do not put unhashed files from `public/` under
  `assets/`). Everything else, `index.html` and `sw.js` included, is `no-cache` with an ETag.
- Local dev: the Vite proxy keeps requests same-origin. `__Host-` cookies need a secure context;
  Chrome and Firefox treat `http://localhost` as one, Safari does not.

## Logging rules (all packages)

- `logger.info("constant_event_name", extra={...})`. Message arguments are **never** interpolated
  (only the format string is logged as `event`), and only allowlisted `extra` fields are written;
  others are dropped and listed by name in `dropped_fields`.
- Core allowlist: `request_id, method, route, status, duration_ms, lag_ms, threshold_ms, env, sha,
  lifecycle, reason, count, oid, aborted, error_type, error_frames`. Add yours at import time with
  `register_log_fields("job_id", "attempt", ...)`: ids, counts, durations, costs, fixed codes only.
- `logger.exception(...)` logs `error_type` and `error_frames` (`dir/file.py:line:function`), never
  the exception message.
- The access log has method, route **template**, status, duration and request id; never the raw
  path, query string, headers, client address or body. Health checks log at DEBUG.

## Decisions worth knowing

- `Sec-Fetch-Site`, when present, decides alone (must be `same-origin`); `Origin ==
  BW_PUBLIC_ORIGIN` is only the fallback for browsers that do not send it. Stricter than an OR.
- Present-but-malformed principal headers are a 401 even with a valid cookie; only *missing*
  headers (bug #1815) fall back to the cookie. A valid non-owner principal is a 403 even with a
  valid owner cookie, and the cookie is cleared.
- The cookie is re-issued only when missing, invalid or past half its TTL (not on every request),
  and a cookie whose expiry exceeds now + TTL is rejected (a shortened TTL takes effect at once).
  The HMAC key is a purpose-bound subkey of `BW_COOKIE_KEY`.
- Accepted IdP names: `aad` and `azureactivedirectory` (header `X-MS-CLIENT-PRINCIPAL-IDP` and
  `auth_typ`). Object ID claim: `http://schemas.microsoft.com/identity/claims/objectidentifier` or
  `oid`. `name` prefers the `name` claim, then `X-MS-CLIENT-PRINCIPAL-NAME`.
- `redirect_slashes` is off; FastAPI's native OpenTelemetry is off (it would record exception
  messages).
- FastAPI 0.165 no longer copies included routes, so the access log reads the full route template
  from FastAPI's effective route context (tested; re-check on FastAPI upgrades).

## Open items for other workstreams

- **infra/v2** sets `OWNER_OID`, `APP_ENV=production` and plans to derive the cookie key from
  `APP_ENCRYPTION_KEY`. The app reads `BW_ENV=prod`, `BW_OWNER_OID`, `BW_COOKIE_KEY` (a separate
  Key Vault secret is recommended), `BW_PUBLIC_ORIGIN`, `BW_BUILD_SHA` and `BW_SPA_DIR`. Without
  `BW_ENV=prod` the app refuses to start in ACA.
- **Phase 0 Easy Auth spike**: confirm the IdP header value and the principal JSON shape on ACA,
  and that client-supplied `X-MS-CLIENT-PRINCIPAL*` headers are stripped by the platform.
- **Dockerfile/CI**: run uvicorn with the flags above; CI can set `PYTHONASYNCIODEBUG=1`.
- **Wiring module**: one place (for example `blogwriter.api.main:app`) composing the db, jobs, ai
  and notify routers/lifecycles once their entry points exist.
- Possible follow-ups: an app logout endpoint that clears `__Host-bw` before `/.auth/logout`;
  serving Vite's precompressed assets; a global request-body limit (`FastAPI(max_body_size=...)`).

## Updates after the independent review (2026-11-26)
This section supersedes anything above that conflicts with it.

**CSRF**
- Only `GET` and `HEAD`, exactly as sent, skip the check.
- `OPTIONS`, lowercase methods and unknown methods need `__Host-bw` plus same-origin.

**Dev principal** (`BW_ENV=local`)
- Granted only when the request has exactly one well-formed `Host` header naming a loopback host or the `BW_PUBLIC_ORIGIN` host. This blocks DNS rebinding.

**Logging**
- Pre-formatted `asyncio` messages keep only their leading words, with no exception or argument text.
- `CoreSettings` hides rejected inputs, so a bad `BW_COOKIE_KEY` is never printed.

**Startup backstop**
- `create_app` refuses to start with a non-prod env inside Container Apps, or with an `http` public origin in prod.

**Wiring**
- Production wiring lives in `blogwriter.main` (`uvicorn blogwriter.main:app --factory`).
