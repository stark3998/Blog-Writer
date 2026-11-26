# Phase 1: jobs, scheduler, broker and SSE (`server/src/blogwriter/jobs`)

Plan sections: "Reliability model: crash-only, one claim mechanism" and "Weekly production
calendar". Status: done. Verified offline (138 tests) and once against the real v2 Cosmos account
(two runners, exactly-once steps, full cleanup).

## Files

| File | Contents |
|---|---|
| `jobs/models.py` | The `Job` document (`kind: "job"` in container `jobs`): `StepState`, `JobEvent`, `InputRequest`, `JobError`, statuses, the capped events array (50), `check_json` |
| `jobs/claims.py` | Generic claim documents: `acquire`, `Claim.renew/commit/release` (If-Match CAS, owner + fence re-check, write tokens), `LeasePolicy`, `ClaimLost`, `cas_update` |
| `jobs/runner.py` | `JobRegistry`, `Step`, `StepContext`, `JobRunner` (claim loop, lease keeper, outcomes, quarantine, shutdown), `JobFailed`, `QuarantineNotice` |
| `jobs/queue.py` | Operations without a claim: `enqueue`, `get_job`, `request_cancel`, `provide_input`, `retry_job`, `JobStateError` |
| `jobs/scheduler.py` | Schedule items (`kind: "schedule"` in `jobs`), rules (`daily`, `weekly`, `every`), `next_run_after` (zoneinfo, DST-safe), `latest_due`, `Scheduler` (tick, confirm, pause/resume, put/get/list/delete) |
| `jobs/bootguard.py` | Crash-loop guard: boot records in `settings/jobs-boot-log`, safe mode past N boots per window |
| `jobs/broker.py` | In-process pub/sub: `Broker`, `Subscription`, `JobEventMessage`, `DeltaMessage` |
| `jobs/sse.py` | `get_router()`: `GET /api/events` (sse-starlette 3.7.1) |
| `jobs/lifecycle.py` | `JobsService` (`app.state.jobs`), `JobsLifecycle`, module-level `startup`/`shutdown`, `get_jobs_service`, `server_exit_requested`, `default_registry` |
| `jobs/settings.py` | `JobsSettings` (`BW_JOBS_*`), `replica_name` |
| `jobs/clock.py` | `Clock` protocol, `SystemClock`, fixed-width ISO-8601 UTC (`to_iso`/`parse_iso`) |
| `jobs/testing.py` | `FakeClock`, `fast_settings`, `eventually`, `DelegatingStore` (fault injection) for tests in any package |
| `tests/jobs/` | 11 test files (see "Tests") |

## Wiring (for whoever owns `core.app` composition)

```python
from blogwriter import db, jobs

registry = jobs.default_registry()          # packages register job types here at import time
app = create_app(
    routers=[jobs.get_router(), ...],
    lifecycles=[db, jobs.JobsLifecycle(registry, on_quarantine=notify_hook)],
)
```

- `jobs` must start **after** `db`: startup reads `app.state.store` and raises if it is missing.
  A module works too (`lifecycles=[db, jobs]` uses `jobs.startup`/`jobs.shutdown` with the
  default registry and no quarantine hook).
- Routes reach the service with `service: Annotated[JobsService, Depends(jobs.get_jobs_service)]`
  (503 `jobs_unavailable` if it is not running). `JobsService` offers `enqueue(type, input,
  job_id=, run_after=)`, `get`, `cancel`, `provide_input(job_id, value, key=)`, `retry`,
  `exit_safe_mode()`, `.scheduler` (put/get/list_items/pause/resume/confirm/delete),
  `.safe_mode`, `.boot`, `.runner.stats`. Each operation publishes its events and wakes the
  local runner.
- Shutdown order (reverse of startup): scheduler stops, the runner stops claiming and releases
  running jobs, the broker closes (open SSE streams end with `close`/`shutdown`). Everything
  finishes well inside core's 10 s per-lifecycle limit (grace 2 s + release budget 5 s).

### Defining a job type

```python
async def draft(ctx: StepContext) -> JsonValue:
    response_id = ctx.remote_ids.get("response")
    if response_id is None:
        response_id = await start_background_response(...)
        await ctx.record_remote_id("response", response_id)   # BEFORE awaiting the work
    ref = await poll_until_done(response_id)                  # re-attach on resume
    await ctx.emit("draft.ready", {"words": 1200})            # ids/counts only
    return {"articleId": ref}                                 # small: a reference

registry.register("article.draft", [Step("draft", draft)])
```

`StepContext`: `job_id`, `job_type`, `step`, `attempt`, `fence` (pass it to remote systems that
take a fencing token), `resumed`, `input`, `outputs` (earlier steps), `output` (last checkpoint),
`remote_ids`, `scheduled_for`; durable calls `checkpoint(output)`, `record_remote_id(key, value)`,
`emit(event_type, data)`, `wait_for_input(prompt, key="input")`; `delta(text)` (broker only).
Raise `JobFailed(code, detail)` for a permanent failure. Steps must be re-entrant: after a crash,
a release or an input wait the step runs again from the start with its checkpoint and remote ids.

## Reliability rules as implemented

**Claims** (`claims.py`, shared by jobs and schedule items): fields `owner` (`<replica>:<boot
uuid4 hex>`; replica = `BW_JOBS_REPLICA_NAME`, else `CONTAINER_APP_REPLICA_NAME`, else the
hostname), `leaseUntil`, `fence`, `writeId`.
- Acquire = CAS replace with `If-Match` the found etag; claimable when there is no owner or the
  lease ended more than `skew` (5 s) ago on the acquirer's clock. `fence + 1` on every acquire.
- Renew every 15 s (`leaseUntil = now + 60 s`). Commits and releases are CAS writes. On 412 the
  holder re-reads: if owner **and** fence are still its own (another writer, e.g. a cancel
  request, touched the document) the change is re-applied on the fresh copy; otherwise
  `ClaimLost` and the work is abandoned with no further writes. The fence check is what stops a
  stale holder that re-reads, including a replica that re-acquired its own lapsed job.
- Unknown outcomes (timeout, 5xx after SDK retries): each logical write carries one `writeId`
  token; a re-read that finds it adopts the stored copy. Retrying with the same token never
  applies a change twice (tested with lost requests, lost responses and failed re-reads).
- Self-fencing: the holder trusts its lease until `leaseUntil - skew`; a runner that cannot renew
  by then cancels the step (`job_lease_expired_locally`) before anyone may take over.

**Runner** (`runner.py`):
- Claim loop: every `poll_seconds` (5 s) or at once when woken (enqueue, answered input, finished
  job). Queries `kind == job`, `type in <registered types>` and either `status == running` with
  `leaseUntil < now - skew` (dead owner) or `status == queued` with `runAfter <= now`. Only
  registered types are claimed, so revisions with different job types can overlap safely.
  At most `concurrency` (4) jobs per runner.
- One task runs the steps, one renews. Each step: a `step.started`/`step.resumed` write, the
  function, then a `step.succeeded` commit with its output, before the next step starts.
  Succeeded steps are never re-run.
- Attempts: `attempt` + 1 on every acquire; releases at shutdown and input waits give it back.
  So it counts crashes and errors only.
  - Exception: retry after `retry_base_seconds * 2^(attempt-1)` (30 s, 60 s, capped at 900 s),
    or **quarantine** once `attempt >= maxAttempts` (3). A job whose attempts are used up by
    crashes is quarantined on its next claim without running (error type `crash`).
  - `JobFailed`: `failed`, no retry. Non-JSON output: `failed` with `invalid_output`.
  - The quarantine hook gets a `QuarantineNotice` (job id, type, attempts, step, error type),
    never job content, so `notify` can alert out of band (title + link).
  - Errors store the exception **type** only, never its message (it may carry content).
- `wait_for_input`: the job goes to `waiting_input` with `awaiting {step, key, prompt}`, the
  claim and the slot are released, the attempt is given back. `provide_input` stores the answer
  on the step (never in an event or a log) and re-queues the job.
- Cancel: queued/waiting jobs are cancelled at once; a running job gets `cancelRequested`, and its
  runner stops the step at its next renewal or step call (immediately when the request reaches
  the replica holding it, via `JobsService.cancel`). A crashed job with a cancel request is
  cancelled without running.
- Shutdown: claiming stops the moment uvicorn receives SIGTERM/SIGINT (`server_exit_requested`
  is polled at least once a second; uvicorn waits for SSE connections before it runs the lifespan
  shutdown, so this matters). At lifespan shutdown running steps get `shutdown_grace_seconds`
  (2 s), then are cancelled and their jobs released to `queued` (attempt given back) so the next
  revision resumes them without waiting for the lease. `stop(release=False)` is what a crash looks
  like (no writes; leases lapse).

**Crash-loop guard** (`bootguard.py`): each boot appends `{at, owner}` to `settings/jobs-boot-log`
(CAS, pruned to the window). More than `crash_loop_max_boots` (5) boots in
`crash_loop_window_seconds` (1 h), this one included, starts **safe mode**: the API, UI and SSE
work, the runner claims nothing and the scheduler does not tick. A boot log that cannot be written
fails closed (safe mode, reason `boot_log_unavailable`). Exit: a later boot after the old boots
age out, or `JobsService.exit_safe_mode()` (clears the log and starts both loops).

**Scheduler** (`scheduler.py`):
- Items live in `jobs` with `kind: "schedule"`: `jobType`, `jobInput`, `timezone` (IANA, validated
  with zoneinfo), `rule` (`daily` HH:MM, `weekly` days + HH:MM, `interval` seconds >= 60 from an
  anchor), `maxLatenessSeconds` (default 7200, the plan's 2 h grace), `status`
  (`active`/`needs_confirmation`/`paused`), `nextRunAt` (UTC), `revision`, claim fields.
- DST: a nonexistent local time fires once at the same offset after the gap (02:30 on 2026-03-08
  in New York fires at 03:30 EDT); a repeated local time fires once at its first occurrence
  (01:30 EDT on 2026-11-01). Comparisons are always done in UTC (same-zone aware datetimes compare
  by wall time and ignore `fold`).
- Tick (every 60 s): due items are claimed with the shared claim mechanism; the job is enqueued
  with the deterministic id `<itemId>-<slotUTC>` (a retry after a crash gets `Conflict`, never a
  second job), then the release advances `nextRunAt`. Missed slots collapse into the latest one
  (counted in `skippedRuns`, no catch-up burst). If that slot is more than `maxLateness` late the
  item becomes `needs_confirmation` and nothing fires until `confirm(item, run=True|False)`.
  A definition changed while a tick held the item is not overwritten (`revision` check).

**Broker** (`broker.py`): one ordered, bounded queue per subscriber (`broker_queue_size` 256).
Token deltas are dropped oldest-first and the next delta of that job step carries `gap: true`.
Job events are never dropped: a subscriber whose queue is full of job events is closed with reason
`overflow` (its SSE stream ends and the reconnect snapshot re-syncs it). Publishing never waits.
At most `broker_max_subscribers` (16) streams.

## `GET /api/events`

```
GET /api/events[?jobs=<jobId>,<jobId>]     (1-20 ids; owner only, like every route)
Last-Event-ID: <jobId>:<seq>                (EventSource sends it on reconnect)
```

1. `retry: 2000` (comment `stream open`).
2. `event: snapshot`, `id: <jobId>:<seq>`, one per affected job: `jobId, type, status, attempt,
   maxAttempts, seq, cancelRequested, createdAt, updatedAt, startedAt, finishedAt,
   scheduledFor, steps[{name, status, runs, startedAt, finishedAt}], awaiting{step, key,
   prompt, requestedAt}, error{code, step, errorType, detail, at}, events[...]` (the retained
   events the client has not seen: for the `Last-Event-ID` job only those after its seq).
   Affected jobs: the `jobs` filter if given, else every active job + jobs updated in the last
   15 min + the `Last-Event-ID` job (most recent first, at most 50). Never job input or outputs.
3. `event: job`, `id: <jobId>:<seq>`, data `{jobId, seq, at, type, step, status, data}`; events
   already covered by the snapshot are not repeated. `event: delta` (no id), data
   `{jobId, step, text, gap}`.
4. `: heartbeat` comments every 15 s.
5. `event: close`, data `{"reason": "max_duration" | "overflow" | "shutdown" | "unavailable"}`;
   the server ends every stream after 200 s (the platform cuts at 240 s).

The broker subscription is taken before the snapshot is read, so nothing falls in between.
Errors: 401 unauthenticated, 422 `invalid_jobs_filter`, 503 `too_many_streams` (Retry-After 5),
503 `jobs_unavailable`. HEAD is not served (405); a subscription is closed by a background task
even when a stream never starts. Client guidance: open the stream only while jobs are active,
de-duplicate by `(jobId, seq)`, treat `delta.gap` as "reload the step output when it lands".

## Settings (`BW_JOBS_*`)

| Setting | Default | Notes |
|---|---|---|
| `LEASE_SECONDS` / `RENEW_SECONDS` / `SKEW_SECONDS` | 60 / 15 / 5 | validated: renew < lease - skew, skew < lease / 2 |
| `STORE_TIMEOUT_SECONDS` | 10 | bound per store call |
| `CONCURRENCY` | 4 | jobs per runner |
| `POLL_SECONDS` | 5 | claim loop (wake-ups are immediate) |
| `MAX_ATTEMPTS` | 3 | new jobs |
| `RETRY_BASE_SECONDS` / `RETRY_MAX_SECONDS` | 30 / 900 | backoff doubles per attempt |
| `SHUTDOWN_GRACE_SECONDS` | 2 | |
| `CRASH_LOOP_MAX_BOOTS` / `CRASH_LOOP_WINDOW_SECONDS` | 5 / 3600 | |
| `TICK_SECONDS` | 60 | |
| `BROKER_QUEUE_SIZE` / `BROKER_MAX_SUBSCRIBERS` | 256 / 16 | |
| `SSE_HEARTBEAT_SECONDS` / `SSE_MAX_STREAM_SECONDS` | 15 / 200 | max stream capped at 235 |
| `SSE_RETRY_MS` / `SSE_SEND_TIMEOUT_SECONDS` | 2000 / 10 | |
| `SSE_RECENT_SECONDS` / `SSE_SNAPSHOT_LIMIT` | 900 / 50 | |
| `REPLICA_NAME` | unset | claim owner prefix |

## Tests and checks

`tests/jobs/` (138 passed, 1 live test skipped by default; ~6 s):

| File | Tests | Covers |
|---|---|---|
| `test_runner.py` | 23 | **two runners on one MemoryStore run every step exactly once** (24 jobs x 3 steps, both runners claim work); **crashed runner resumed only after lease + skew** (checked at +30 s and +64 s, claimed at +66 s); **stale runner's commit rejected by fencing** (paused runner wakes after the takeover); **remote ids re-attached, not re-issued**; self-fencing when renewals fail; renewals keep others away; graceful release + immediate resume; grace period; stop claiming on shutdown; **waiting_input frees the slot** (concurrency 1, another job runs meanwhile); **quarantine after 3 attempts** with 30 s/60 s backoff and the alert; crash-loop quarantine without a 4th run; `JobFailed`; invalid output; completed steps not re-run; cancel (local, via renewal, crashed job); concurrency limit; unregistered types left alone; events capped and every event published once; step context validation; registry rules |
| `test_claims.py` | 19 | acquire/fence, racing acquirers, lease + skew boundary, stale holder rejected (also same owner), foreign write merged, lost response/request applied once, token retry, renew, release, deleted doc, id and claim-field protection, `cas_update` |
| `test_scheduler.py` | 19 | **America/New_York spring-forward and fall-back** (next-run math and minute-by-minute ticks over 3 days each: one job per day), weekly production slots through DST, interval, missed-slot collapse, **max lateness -> needs_confirmation**, confirm run/skip, two schedulers -> one job, crash between enqueue and advance, definition change during a tick, put/pause/resume/delete/list, validation, stop flag, tick loop |
| `test_sse.py` | 12 | **heartbeat comments and server close** (0.05 s / 0.4 s), **Last-Event-ID resume: snapshot then only newer live events** (deterministic: the step is released after the snapshot read), deltas without ids, app-wide snapshot selection, jobs filter, invalid documents skipped, shutdown close, store failure, auth/validation/HEAD, 503s |
| `test_models.py`, `test_queue.py`, `test_broker.py`, `test_bootguard.py`, `test_settings.py`, `test_lifecycle.py` | 21, 10, 10, 6, 11, 6 | wire format and caps; queue operations; drop policy; safe mode; settings relations; lifespan wiring, safe mode exit, shutdown release, `server_exit_requested` |
| `test_live.py` | 2 | live guard (offline) and the live two-runner run |

The SSE tests use httpx's in-process ASGI transport through `core.app.create_app` (auth middleware
included). That transport returns a response only when the app finishes it, which is why they
rely on short configured intervals and the server-side close.

**Live run** (once, as asked): `BW_LIVE_COSMOS=1
BW_COSMOS_ENDPOINT=https://cosmos-blogwriter-v2-7c3167.documents.azure.com:443/ uv run pytest
tests/jobs/test_live.py` with `az login`: 1 passed in 8.2 s. Two runners (concurrency 2 each) ran
6 jobs x 3 steps, each step exactly once, fence 1 and attempt 1 on every job, remote ids and an
emitted event stored, both runners claimed work. Writes were guarded (`jobs` container, `test-`
ids only, `ttl: 3600`, a run-unique job type that no other runner would claim). A read-only check
afterwards found 0 job documents (0 `test-` leftovers) in `jobs`.

Server checks at the end (whole `server/`): `uv sync --locked` ok; `ruff format --check .` ok
(124 files); `pyright` (strict) 0 errors; `pytest -q`: 2597 passed, 2 skipped (the two opt-in live
tests). `ruff check` is clean for `src/blogwriter/jobs` and `tests/jobs`; the whole-tree run
reported 2 findings in `tests/core/test_auth.py` (PT018) and `tests/core/test_logging.py`
(ASYNC110), files another agent was editing at the time. An earlier full pytest run also caught 2
`tests/core` failures mid-edit; they passed on their own and in later runs.

## Notes for integration

- No new dependencies (uses fastapi, sse-starlette 3.7.1, pydantic(-settings), stdlib zoneinfo
  with the pinned `tzdata`).
- `core.app` composition: add `jobs.get_router()` and `jobs.JobsLifecycle(registry,
  on_quarantine=...)` after `db`. The `notify` package can provide the quarantine hook.
- `api` routes still to add (thin): job read/cancel/answer/retry, schedule list/confirm/pause,
  and an owner-only "exit safe mode" action; health could expose `app.state.jobs.safe_mode`.
- The broker is per process: during a revision overlap a client connected to the other replica
  sees that replica's jobs only after its next reconnect (at most 200 s; the snapshot catches up).
- Job outputs and results live on the job document: keep them to references; large content
  belongs in its own container (a document that would exceed 2 MB fails the step).
- Live-test documents of the db suite never carry `kind`, so the runner's queries (`kind == job`)
  never see them; this suite's live documents use a run-unique job type.
