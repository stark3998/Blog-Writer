# Phase 1: web data layer

Date: 2026-11-26. Plan sections: "Frontend (`web/`)" (Stack, Auth) and "Reliability model"
(Streaming). Server contracts it follows: `docs/v2/phase1/core.md` ("Client contract") and
`server/src/blogwriter/jobs/sse.py` (the event stream).

## What was built

| Path (`web/src/`) | What |
|---|---|
| `lib/api/schema.ts` | OpenAPI types in openapi-typescript's exact shape. Hand-written for `GET /api/health` `{status, sha}` and `GET /api/me` `{oid, name}`; `npm run gen:api` replaces it |
| `lib/api/types.ts` | `HealthResponse`, `MeResponse` aliases |
| `lib/api/client.ts` | `createApiClient()` (openapi-fetch) with the session middleware; `unwrap()` for query functions; `REQUEST_DEFAULTS` (`credentials: 'same-origin'`, `redirect: 'manual'`) |
| `lib/api/errors.ts` | `ApiError` (`kind`, `status`, `code`, `requestId`, `transient`), problem+json parsing, `describeError()` |
| `lib/api/session.ts` | `isLoggedOutResponse()`, `loginUrl()`, `logoutUrl()`, `safeReturnPath()` |
| `lib/auth/redirect-guard.ts` | The loop guard: sign-in redirects counted in sessionStorage |
| `lib/auth/auth-controller.ts` | Auth state (`ok`, `signing-in`, `sign-in-blocked`, `forbidden`) and the only code that navigates to sign in |
| `lib/query/` | `createQueryClient()` (retry rules), `backoffDelay()`, `queryKeys` |
| `lib/events/events.ts` | The SSE wire format and its parser |
| `lib/events/job-store.ts` | Job state keyed by job id: steps, event history, input request, error, streamed text per step |
| `lib/events/event-stream.ts` | The one app-wide `EventSource`: retain/release, session probe, backoff |
| `lib/events/event-router.ts` | Events into the store, then batched query invalidation |
| `lib/app-data.ts` | `createAppData()` wires all of the above; `useAppData()`; `probeSession()` |
| `features/data/data-provider.tsx` | `DataProvider`: context, `QueryClientProvider`, `AuthGate`, stream teardown. Nesting-safe |
| `features/auth/` | `AuthGate` and the screens: "Not authorised" (403), "Sign-in didn’t work" (loop guard), "Taking you to sign in…" |
| `features/jobs/` | `JobTimeline` and hooks (`useJobSteps`, `useJobStatus`, `useJobStepText`, `useLiveEvents`, …) |
| `features/system/queries.ts` | `meQuery`, `healthQuery`, `useMe`, `useHealth` |
| `features/today/today-status.tsx` | Today's "Signed in as" and "Server build", with skeletons, errors and retry |
| `routes/today.tsx` | Wiring only: wraps the page in `DataProvider`, renders `TodayStatus` under the title |
| `test/` | msw server and fixtures, `MockEventSource`, server-shaped event payloads, `createTestAppData()`; `setup.ts` and `render-app.tsx` extended |

Dependencies (exact pins): `@tanstack/react-query` 5.108.4, `openapi-fetch` 0.17.0; dev:
`msw` 3.0.8, `openapi-typescript` 7.13.0. `package.json` also gains an `overrides` entry:
openapi-typescript 7.13 declares `typescript ^5` as a peer, and the project is on 6.0.3, so the
override points it at the project's TypeScript (`npm ci` and `gen:api` both work with it).

## Wiring (one change outside this work's paths)

Mount the provider once around the router in `src/main.tsx`:

```tsx
<ThemeProvider>
  <DataProvider layout="fullscreen">
    <RouterProvider router={router} />
  </DataProvider>
</ThemeProvider>
```

Until then, the Today route mounts its own `DataProvider`, which uses the same browser-wide
instance (`getBrowserAppData()`), so Today works today. Once `main.tsx` provides one, Today's
becomes a pass-through (an inner `DataProvider` renders its children as they are) and can be
removed. `layout="fullscreen"` gives the auth screens a full page when no shell is around them.

## Session handling

All calls are same-origin (`baseUrl` is the page origin), with `credentials: 'same-origin'` and
`redirect: 'manual'`, so an expired Easy Auth session is never followed into a cross-origin fetch
of the login page. The client middleware applies `core.md`'s contract to every response:

| Response | Result |
|---|---|
| 401, a 3xx (what Node returns for manual redirects), or `type: 'opaqueredirect'` (what browsers return) | `auth.handleUnauthenticated()`: top-level `location.assign('/.auth/login/aad?post_login_redirect_uri=<path>')`, path = pathname + search + hash. Paths under `/.auth` or `/api`, and anything not a same-origin path, become `/` |
| 403 `csrf_failed` on a write | `GET /api/me` (re-issues `__Host-bw`), then the write once more (a clone kept from `onRequest`). A second failure is returned as `ApiError` kind `csrf`, never "not authorised" |
| Any other 403 (`not_owner`, `owner_not_configured`, Easy Auth's HTML 403) | `auth.handleForbidden()`: the "Not authorised" screen with **Sign out** (`/.auth/logout?post_logout_redirect_uri=%2F`), **Try again** (reload) and the request id. Never redirects |
| No response | `ApiError` kind `network`. An `AbortError` (cancelled query) passes through unchanged |

**Loop guard.** Every automatic redirect is recorded in sessionStorage (`bw:auth-redirects`,
a list of timestamps). Three within five minutes and the next is refused: the app shows
"Sign-in didn’t work" with a **Sign in** button instead. A success in between does not reset the
count (a loop can include successful calls); entries just age out. The manual button always
navigates and is counted too, so if sign-in still fails the screen comes back without another
automatic bounce. If sessionStorage is unavailable the guard cannot count, so there is no
automatic redirect at all, only the button. One page makes one decision: later 401s, or a 401
after a 403, change nothing.

Errors are `ApiError`s: `kind` (`unauthenticated`, `forbidden`, `csrf`, `not-found`, `client`,
`server`, `network`), `status` (0 without a response), problem `code`, `requestId` (the
`X-Request-Id` header, else the problem's `request_id`; use it in error toasts). The message holds
status and code only, never response content.

## TanStack Query defaults

- `retry`: only transient failures (no response, 5xx, 408, 429), at most 3 retries. Never 401,
  403 (the session handling owns those), 404 or other 4xx, and never errors that are not
  `ApiError`s (bugs).
- `retryDelay`: capped exponential backoff with ±20 % jitter: 1 s, 2 s, 4 s, at most 30 s.
- `staleTime` 30 s; `/api/me` and `/api/health` 5 min.
- `refetchOnWindowFocus` off by default, on for Today's queries (refetch when stale as the owner
  returns, which also notices an expired session early). Mutations never retry.

## Event stream

One `EventSource` to `/api/events` per app, shared by reference counting: `useLiveEvents(true)`
(the `JobTimeline` does this while its job is queued, running or unknown) retains it; it closes
15 s after the last release, so moving between pages does not reconnect. In Phase 1 nothing
retains it outside a timeline, so the app opens no stream until a job is on screen.

**Wire format** (the server's, `jobs/sse.py`; camelCase JSON): `retry: 2000`; one `snapshot` per
active or recently updated job (id `<jobId>:<seq>`: status, steps, retained events, `awaiting`,
`error`, attempt); live `job` events (id `<jobId>:<seq>`: `type`, `step`, `data`, and the job
status after it); `delta` events (no id: `jobId`, `step`, `text`, `gap`); `: heartbeat` comments;
a final `close` (`max_duration` before the 240 s platform limit, `overflow`, `shutdown`,
`unavailable`). Unknown or malformed events are dropped and logged by event type only
(`console.warn('event_stream_dropped', { type })`).

**Reconnects.** On every `error` the client probes `GET /api/me` (through the API client, so
`redirect: 'manual'` and the session middleware apply; 10 s timeout):

- logged out: the stream closes for good and `auth.handleUnauthenticated()` takes over (loop guard
  included);
- 403: closes for good, "Not authorised";
- otherwise: after a healthy stream ends (the server's `close`), the browser's own retry runs once,
  because only it sends `Last-Event-ID` and the server resumes from there. When the browser gave
  up (`CLOSED`: a 5xx, the 503 `too_many_streams`, a wrong content type) or its retry failed as
  well, the client closes it and reconnects with capped exponential backoff (1 s, 2 s, 4 s … 30 s,
  jitter). A connection that stayed open 10 s resets the backoff; connections that open and drop
  at once keep backing off. Coming back online (`online` event) skips the wait. A new
  `EventSource` cannot send `Last-Event-ID`; the server then sends full snapshots and the store
  skips what it already has.

**Store** (`lib/events/job-store.ts`, keyed by job id, read per field with
`useSyncExternalStore`, so a timeline does not re-render for each token): a snapshot replaces a
job's state but merges event history by `seq` (after a `Last-Event-ID` resume the server sends
only newer events); each live `job` event applies once (ignored when its `seq` is at or below the
job's) and mirrors the runner's step bookkeeping (`step.started`/`step.resumed` running with
`runs + 1`, `step.succeeded`, `step.failed`, `input.requested` waiting, a `status` of queued,
cancelled or quarantined puts unfinished steps back to pending); deltas append per step
(`gap` remembered; capped at 500 000 characters). A snapshot for a job the store already knew
marks its live text `gap: true` (deltas sent while disconnected are lost); reload the step output
when it lands. Caps: 50 events per job (as the server), 50 jobs (the least recently updated job
nobody watches is dropped).

**Invalidation** (batched per microtask; a key under a pending prefix is skipped): `snapshot` and
`job` events of type `status` invalidate `['jobs']` (every job query); other `job` events
`['jobs', 'detail', jobId]`; `delta` and `close` nothing. The job queries themselves arrive with
the jobs API.

**JobTimeline** (`features/jobs/job-timeline.tsx`): one row per step in the job's order,
labelled by `labels[step]` or a readable form of the name (`research.search` becomes "Research
search"), with an icon and visually hidden state ("not started", "in progress", "waiting for
you", "done", "failed") and the time it started or finished. Rows sit in a stable
`aria-live="polite"` region and each row is `aria-atomic`, so a new step or a changed state is
read out whole. A summary line covers the job ("Waiting for you.", "Finished.", "Stopped: <the
step's owner-facing detail>", "Attempt 2 of 3.", …), and "Live updates paused. Reconnecting…"
shows while the stream reconnects.

## Today

Under the title: "Signed in as" (the `name` from `/api/me`, or "the owner" when only the cookie
authenticated, `name: null`) and "Server build" (a full SHA shortened to 7 characters, the full
one in `title`; labels like `dev` as they are). Each shows a skeleton with a visually hidden
"Loading" while pending, and "Couldn’t load" plus a Retry button ("Retry loading the server
build") after the retries are spent. A 401 or 403 never reaches these states: the gate takes over.

## Generated API types

`npm run gen:api` dumps the server's OpenAPI offline (`uv run --project ../server --locked`,
`blogwriter.core.app:app().openapi()`, written to `node_modules/.tmp/openapi.json`; the app logs
to stdout, so the spec goes to a file), runs `openapi-typescript` into `src/lib/api/schema.ts` and
`eslint --fix`es it (it turns the generator's index signatures into `Record`). Run once against
today's server: the output matched the hand-written file in content, and lint and typecheck
passed on it; the hand-written file was kept, as asked. When the wiring module that composes
every router exists, point the script's import at it instead of `blogwriter.core.app`.

## Tests

`msw` 3 intercepts every fetch (`server.listen({ onUnhandledFrame: 'error' })`; note msw 3
renamed `onUnhandledRequest`), with `/api/me` and `/api/health` answered by default. Event
streams use `MockEventSource` (jsdom has none). New files (191 tests):

- `lib/api/session.test.ts`, `client.test.ts`: 401, 302 and opaque redirect to sign in with the
  current path; one redirect for parallel failures; the loop guard across "page loads"; 403
  `not_owner` and Easy Auth HTML 403 to "not authorised"; CSRF refresh-and-retry-once; network
  and abort errors; `credentials`/`redirect` on the actual `Request` (msw drops `redirect`, so a
  capturing fetch checks it).
- `lib/auth/*.test.ts`: guard limits, window, storage failures; controller states.
- `lib/query/*.test.ts`: retry matrix (401/403/404/422 once; 5xx and no response retried; four
  attempts at most), delays, backoff caps and jitter.
- `lib/events/*.test.ts`: parsing of every server payload and malformed ones; the store
  (bookkeeping, replays, resumes, caps, eviction); invalidation batching; the stream with fake
  timers (one `EventSource`, linger, probe per error burst, logged-out and forbidden probes,
  backoff sequence 1→30 s, browser retry first, takeover, flapping, stable reset, `online`, stop
  during a probe, no `EventSource`).
- `lib/app-data.test.ts`: the real wiring (probe through msw, sign-in on a dropped stream, 403,
  reconnect, routing into the store and invalidation).
- `features/**`: `JobTimeline` rendering and live updates, Today's facts (skeletons, `null` name,
  error and retry, refetch on focus when stale), the gate screens, the provider, Today without an
  app-level provider.

Pure-logic test files run in Node (`// @vitest-environment node`; `setup.ts` skips the DOM parts
there), which keeps the suite lighter.

**A harness change worth knowing.** `renderApp` now renders `RouterProvider` with
`useTransitions={false}`. With the default (transitions), the shell's
`navigation.test.tsx › follows the arrow keys and keeps focus on the tab list` failed in 20–40 %
of full runs on this machine under load: it asserts `aria-selected` right after
`waitFor(router.state …)`, and a time-sliced transition render can commit later. With
`useTransitions={false}` it passed every one of 13 full runs since. The app itself still uses
transitions. Making that test wait for the attribute (`await waitFor(() => expect(voice)…)`)
would make it robust either way; it is outside this work's paths.

## Checks (run in `web/`)

- `npm run lint`: clean. `npm run typecheck`: clean.
- `npm test`: 316 tests in 29 files pass (125 existing, 191 new); 13 consecutive full runs green
  after the harness change. The only stderr is the shell's existing React Router warnings.
- `npm run build`: passes, `verify-dist` clean. First load unchanged at about 559 kB raw /
  171 kB gzip; the data layer (TanStack Query, openapi-fetch, this code) is in the lazy Today
  chunk (59 kB / 20 kB gzip) until `main.tsx` mounts the provider, which moves about 40 kB raw
  into the entry.

## Not verified

- A real browser against the real server: opaque redirects, Easy Auth's 302 vs 401 for fetches,
  `EventSource` reconnect timing and `Last-Event-ID`, and the server's `close` at 200 s. The
  client logic for each is unit-tested with fakes only.
- A clean `npm ci` from the lockfile works locally (macOS, Node 24.14.1) and lint, typecheck, tests
  and build pass after it; CI's Linux runner was not tried.

## Follow-ups

- Mount `DataProvider layout="fullscreen"` in `main.tsx` (above), then drop Today's own.
- Jobs API queries (`['jobs', 'detail', id]`, lists) and an app-level retain for the stream when
  Today shows running jobs; "opens only while jobs are running" then follows from what is on screen.
- Error toasts with `requestId`; an app logout endpoint that clears `__Host-bw` before
  `/.auth/logout` (core.md follow-up).
- Update `web/README.md` and `docs/v2/phase1/web.md` ("No server data yet") once wired.
