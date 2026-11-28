# Blog Writer v2: build contract for Phases 2-7

This is the contract that the implementation agents code against in parallel. It turns the v2 plan
(Phases 2-7) into packages, Python APIs, HTTP routes, jobs, schedules, events and web sections.
When this document and the code in `server/src/blogwriter/domain/` disagree, the code wins and
this document is fixed.

Binding inputs: the v2 plan, `server/ARCHITECTURE.md`, `docs/v2/phase1/*.md`,
`docs/v2/article-dialect.md`, `contracts/`, `docs/v2/build/render.md` and
`docs/v2/build/publish-render.md` (the two ports that exist already).

**Contents**
0. How to use this document
1. Conventions (ids, time, naming, documents and ownership, ETags, pagination, problems, jobs,
   AI calls, security rules, settings, tests)
2. State machines
3. Work orders, wave A: storage, kb, stories, voice, research, signals, articles, gates,
   linkedin transport, backups
4. Work orders, wave B: pipeline, publish job, linkedin posts, planner, engagement, analytics
5. Integrator work order (wiring, system routes, follow-ups)
6. HTTP API index
7. Job types
8. Schedule
9. Server-sent events
10. Web map
11. AI task matrix
12. Owner inputs and empty states
13. Deferred items
14. Revision log

---

## 0. How to use this document

- Read section 1 and your own work order. Everything you need from another package is in that
  package's **Public Python API** (wave B may use wave A's) or in `blogwriter.domain`. Wave A
  packages depend on `domain`, the Phase 1 packages (`core`, `db`, `jobs`, `ai`, `notify`,
  `linkedin.littletext`/`lint`, `publish`) and the two ports (`render`, `publish` renderer) only:
  **never on another wave A package**.
- Touch only your package directory and its tests (`server/src/blogwriter/<pkg>/**`,
  `server/tests/<pkg>/**`). `blogwriter.main`, `api/`, `pyproject.toml` and `uv.lock` belong to
  the integrator. Ask for dependencies in your report.
- Every package exposes, as needed:
  - `get_router() -> fastapi.APIRouter` (full paths, `APIRouter(prefix="/api/...")`);
  - `register_jobs(registry: blogwriter.jobs.JobRegistry) -> None` (registers every job type
    the package owns; called once by `blogwriter.main`);
  - `schedule_entries(cadence: blogwriter.domain.settings.CadenceSettings) ->
    list[blogwriter.domain.ports.ScheduleEntry]` (pure and deterministic, no I/O; type alias
    `ScheduleProvider`);
  - a lifecycle object or module with `async startup(app)` / `async shutdown(app)` (only if the
    package owns a runtime service, for example `storage` puts the blob store on
    `app.state.blobs`).
- Section numbers like `W-A3` name work orders; `R-` prefixes are HTTP routes.

### What exists already (do not rebuild)

| Package | Use |
|---|---|
| `blogwriter.domain` | **New, this contract.** Every stored document model, every state machine and its article rules (`check_article_transition`), ids (random and derived), timestamps, hashes, blocklist matching (fail closed) and the AI-use gate (`domain.confidentiality`), calibration math, the canonical article document and word counts (`domain.compose`), slot math (`domain.slots`), typed store access that validates before writing (`domain.docstore`), response envelopes (`domain.api`), service protocols and the services registry (`domain.ports`), FastAPI dependencies and problem mapping (`domain.deps`), job type names and cross-package job inputs (`domain.jobtypes`), enqueue-once, parent/child jobs and deferral (`domain.jobflow`), and fakes for every service (`domain.testing`). |
| `core` | `create_app`, auth (`OwnerPrincipal`), CSRF, `ProblemError`, logging (`register_log_fields`), `TokenVault`, `SafeHttpClient`, `core.testing` |
| `db` | `Store`, `MemoryStore`, `Filter`, `OrderBy`, errors |
| `jobs` | `JobRegistry`, `Step`, `StepContext`, `JobFailed`, `JobsService`, scheduler rules `daily`/`weekly`/`every`, `/api/events` SSE, `jobs.testing.FakeClock` |
| `ai` | From `blogwriter.ai`: `AIRouter.run/stream`, `RouterResult`, `ServedBy`, `BrakeEngaged`, `PolicyViolation`, `DISCARD_EVENTS`, `AISettings` (`price_table()`). From `blogwriter.ai.types` (not re-exported by `blogwriter.ai`): `Task`, `Sensitivity`, `Request`, `Message`, `WebSearchTool`, `FunctionTool` |
| `notify` | `emit_alert(kind, ref_id)`, `NotificationPayload(title, url, tag)`, `WebPushNotifier` |
| `linkedin` | `littletext` (`parse_display`, `api_commentary`, `display_text`, `escape`, `unescape`), `lint` (`lint_post`, `AI_TELLS`, `fold_index`, `has_blocking`); convert its findings with `domain.common.from_linkedin_finding` |
| `publish` | `contract.validate_published_doc`, `renderer.render_article`, `sanitizer.sanitize_html`, `normalize.normalize_markdown`, `lint.lint_article` (convert with `domain.common.from_publish_finding`), `frontmatter` (`Frontmatter`, `dump_frontmatter`, `parse_frontmatter`). The canonical article document is `domain.compose.compose_document`, never a package's own join |
| `render` | `render_carousel`, `carousel_preview_html`, `render_image_card`, `render_og_image`, `render_mermaid_svg`, `render_mermaid_png`, `lint_carousel`, `RenderBrand` (see `docs/v2/build/render.md`); packages reach it only through the `RenderService` port below |

---

## 1. Conventions

### 1.1 Reaching services (routes and job steps)

Routes take services through `blogwriter.domain.deps`. Job steps (a `StepContext` carries no app)
call `blogwriter.domain.ports.current_services()`.

```python
from blogwriter.domain.deps import StoreDep, AIDep, JobsDep, IfMatch, problems, versioned
from blogwriter.domain.ports import current_services

@router.put("/api/things/{thing_id}")
async def put_thing(thing_id: str, body: ThingIn, store: StoreDep, if_match: IfMatch,
                    response: Response) -> Versioned[Thing]:
    with problems():                       # store, transition and service errors -> problems
        current = await read_doc(store, Thing, thing_id)
        ...
        stored = await replace_doc(store, changed, if_match=if_match)
    return versioned(stored, response)     # {"item": ..., "etag": ...} + ETag header

async def my_step(ctx: StepContext) -> JsonValue:
    services = current_services()          # store, jobs, ai, blobs, render, ...
```

| Dependency (`domain.deps`) | Type | Missing | `app.state` attribute (set by) |
|---|---|---|---|
| `ServicesDep` | `ports.Services` | 503 `services_unavailable` | `services` (integrator: `StateServices(app)`) |
| `StoreDep` | `db.Store` | 503 `store_unavailable` | `store` (db) |
| `JobsDep` | `ports.JobQueue` | 503 `jobs_unavailable` | `jobs` (jobs, `JobsService`) |
| `ClockDep` | `jobs.Clock` | 503 `clock_unavailable` | `clock` (integrator, `SystemClock`) |
| `NotifierDep` | `ports.Notifier` | 503 `notifier_unavailable` | `notifier` (integrator; a null notifier when push is off) |
| `EgressDep` | `ports.HttpGetter` | 503 `egress_unavailable` | `egress` (integrator, `SafeHttpClient`) |
| `AIDep` | `ports.ModelRunner` | 503 `ai_unavailable` | `ai` (main `AILifecycle`, `AIRouter` or `None`) |
| `EmbedderDep` | `ports.Embedder` | 503 `embeddings_unavailable` | `embedder` (integrator: `services.RouterEmbedder` when the AI router has a `Task.EMBED` route, else `None`) |
| `BlobsDep` | `ports.BlobStore` | 503 `storage_unavailable` | `blobs` (storage lifecycle) |
| `FetcherDep` | `ports.PageFetcher` | 503 `fetcher_unavailable` | `fetcher` (research lifecycle) |
| `RenderDep` | `ports.RenderService` | 503 `render_unavailable` | `render` (integrator adapter over `blogwriter.render`) |
| `VaultDep` | `core.crypto.TokenVault` | 503 `vault_unavailable` | `vault` (integrator, `TokenVault.from_settings()` or `None`) |
| `SlopDep` | `ports.SlopChecker` | 503 `slop_unavailable` | `slop` (gates lifecycle) |
| `ClassifierDep` | `ports.ConfidentialityClassifier` | 503 `classifier_unavailable` | `classifier` (stories lifecycle) |
| `SchedulesDep` | `ports.ScheduleSeeder` | 503 `schedules_unavailable` | `schedules` (integrator) |

Job steps reach the same services with `domain.jobflow.step_services()` (services not installed
yet defer the job) and check an optional one with `require_service(services.ai, "ai")` (missing
means "not configured": `JobFailed("ai_unavailable")`).

Owner identity: declare `principal: OwnerPrincipal` (core) only where the handler needs it. The
auth middleware already refused everyone else, and every non-GET already passed CSRF.

### 1.2 Ids, time, naming

- **Ids**: `Document.new_id()` = `<prefix>-<12 hex ms><16 hex random>`, sortable by creation
  time. Prefixes: `art`, `post`, `story`, `pov`, `intv`, `sample`, `idea`, `feed` (sources),
  `run` (research runs), `brief`, `qs` (quick searches), `rem`, `eng`, `rec`, `watch`, `exp`,
  `sug`, `evt`, `evr`, `imp`, `seed`. Deterministic ids (retry-safe):
  `ArticleVersion.id_for(article, n)` = `art-...-v00042`, `StyleGuide.id_for(n)` =
  `style-guide-0003`, `VoiceDigest.id_for(date)`, `Signal.id_for(source_id, url_sha256)` (unique
  across partitions), `OAuthState.id_for(state)`, `Brief.id_for(run_id)` = `brief-<runId>`, week
  plans `plan-<Monday>` (`domain.weeks.plan_id`), singletons (fixed ids, section 1.3), job ids in
  `domain.jobtypes`.
- **A step that creates a document never mints a random id** (a resumed step would create a
  second one). It uses a derived id, `domain.base.derived_id(prefix, *parts)` =
  `<prefix>-<28 hex>` (same shape as `new_id`, not time-sortable), through the class helpers:
  `ResearchRun.id_for_pipeline(article_id, pipeline_run)` and `.id_for_eval(topic_id, eval_job_id)`,
  `Interview.id_for_pipeline(article_id, pipeline_run)`, `Reminder.id_for(key)`,
  `Post.id_for_derivative(article_id, version, ordinal)` and `.id_for_plan_slot(plan_id, slot_id)`,
  `Article.id_for_eval(topic_id, eval_job_id)`, `EvalResult.id_for(topic_id, eval_job_id)`,
  `InsightSuggestion.id_for(week_start, dimension, ordinal)`, `SeedText.id_for(text)`. A create
  that hits `Conflict` reads the existing document and carries on. Where no stable key exists,
  checkpoint the minted id before the expensive or remote call.
- **Timestamps**: fixed-width ISO-8601 UTC with microseconds, `2026-11-26T15:04:05.123456Z`
  (`domain.base.to_iso`, `parse_iso`, `now_iso`), the same format as `jobs.clock` and the AI
  ledger. String order is time order; range filters and cursors rely on it. Never store naive
  or local times. Local values are stored next to their IANA zone (`Slot`: `localDate`,
  `localTime`, `timezone`, `utc`) and converted with `domain.weeks.slot_utc` (the scheduler's
  DST rule). The published-blogs contract uses its own second-precision format: the publish job
  converts.
- **Naming**: JSON on the wire and in Cosmos is camelCase (`statusChangedAt`), Python is
  snake_case. `DomainModel` maps both ways. API request models subclass `domain.api.ApiModel`
  (camelCase, `extra="forbid"`): never accept a whole document from the client.
- **Kinds**: every document has `kind` (snake_case), every query filters on it
  (`domain.docstore.find_docs` adds the filter). Several kinds share a container.
- **Unknown fields survive** (`extra="allow"` on domain models), so a revision overlap never loses
  a newer field. Do not remove fields; add them with defaults. `schemaVersion` starts at 1.

### 1.3 Documents, containers and write ownership

One package owns each kind and creates it. Other packages read anything (with
`domain.docstore`) but write only the fields listed, always with an If-Match CAS
(`docstore.update_doc` for server-side writers, `replace_doc` with the owner's etag for UI
edits).

| Kind (class) | Container (partition) | Owner | Other writers (fields) |
|---|---|---|---|
| `pillars` `format_packs` `lanes` `brand_kit` `blocklist` `employer_policy` `cadence` `model_preferences` `autonomy` (singletons) | `settings` (`/id`) | kb | `autonomy.calibration` by the publish package (`record_review` on approval, `record_correction`) and by planner (`record_review(outcome="vetoed")` on a veto), `autonomy.judge` by analytics; server-side writers use `docstore.update_singleton` |
| `linkedin_connection` (singleton) | `settings` | linkedin transport | none |
| `voice_preferences` (singleton) | `settings` | voice | none |
| `standards_catalog` (singleton) | `settings` | gates | none |
| `profile_checklist` (singleton) | `settings` | engagement | none |
| `backup_status` (singleton) | `settings` | backups | none |
| `experiment` `insight_suggestion` `eval_topic` `eval_result` `analytics_import` | `settings` | analytics | none |
| `story` `pov` | `stories` (`/id`) | stories | none |
| `interview` | `stories` | pipeline | none |
| `voice_sample` `style_guide` `voice_digest` | `stories` | voice | `voice_sample` created by pipeline through voice's `add_sample` API |
| `idea` | `ideas` (`/id`) | signals | `status`/`articleId`/`postId` (used) by articles, planner, linkedin posts |
| `week_plan` | `ideas` | planner | none |
| `research_run` `brief` `quick_search` | `research` (`/id`) | research | none |
| `article` | `articles` (`/id`) | articles | pipeline (Markdown, meta, `servedBy`, `reviewOnly*`, `media`, links, `calibration` flags incl. `gatedVersion`; on the auto path status gating -> approved with `publish.publishAt` and `publish.auto = true`) through articles' API; gates (`gates`, `embedding`, `embeddingSha256`); publish package (`publish` incl. `lastOperation`, `corrections`, `calibration.outcome`/`approvedSha256`/`substantiveEdit` on approval, status in and out of publish states; `clearance` read-only); planner (`veto`, approved -> in_review on veto, `calibration.outcome = vetoed`); analytics (creates `purpose=eval` articles through articles' API) |
| `article_version` | `article-versions` (`/articleId`) | articles | none |
| `post` | `posts` (`/id`) | linkedin (posts part creates) | transport (`intent`, `linkedinUrn`, `postedAt`, `postUrl`, `finalText`, `kit`, media `linkedinUrn`/`linkedinStatus`, status in publish/kit states); planner (`slot`, `weekPlanId`, staged -> approved and -> missed); analytics (`snapshots`) |
| `source` | `sources` (`/id`) | signals | none |
| `signal` | `signals` (`/sourceId`, TTL 90 d) | signals | none |
| `reminder` | `engagement` (`/id`, per-item TTL) | planner | none |
| `engagement_item` `engagement_record` `watch_entry` | `engagement` | engagement | none |
| `oauth_state` | `ephemeral` (`/id`, TTL 1 d) | linkedin transport | none |
| `seed_text` | `ephemeral` | stories | none (resume or portfolio text for `stories.seed`; deleted by the job, TTL 1 d) |
| published-blogs v2 documents | `published-blogs`, `published-blogs-staging` | publish job | none (shape: `contracts/published-blogs.v2.schema.json`) |
| jobs, schedule items | `jobs` | jobs | none (packages use `JobsService`) |
| usage records | `usage` | ai ledger | none |

Large text fields use the names Terraform excludes from indexing: `markdown`, `embedding`,
`payload` (posts). Never filter or sort on them (I-8 extends the list). Documents stay under
2 MB: stored checks keep at most 50 findings per gate (`domain.common.cap_findings`, the count
of the rest in `truncated`), and a test builds an article with every field at its maximum
(1.75 MB). Job inputs and outputs are references, never content.

**Writes fail closed.** `domain.docstore` validates every document against its model and the
store's rules before any I/O (`InvalidDocumentWrite`, 500 `invalid_document`): a
`model_copy(update=...)` skips pydantic validation, so without this a bad value is committed and
the document then vanishes from queries. Lists skip unreadable documents; every query that
guards an action (`slug_taken`, `slot_conflict`, near-duplicates, `pillar_in_use`, one running
experiment) passes `find_docs(..., strict=True)` so an unreadable document fails the check.

### 1.4 ETag / If-Match

- Every single-resource response is `domain.api.Versioned[T]`: `{"item": {...}, "etag": "..."}`
  plus the same value in the `ETag` header (`domain.deps.versioned`). The etag is the store's
  value verbatim (it already contains the quotes).
- Every mutating request on an existing document sends `If-Match: <etag>`
  (`domain.deps.IfMatch`); missing gives **428** `if_match_required`. A stale etag gives **412**
  `etag_mismatch`, and when the route knows the current document the problem carries
  `current: {etag, updatedAt, updatedBy?, version?, status?}` (`domain.deps.etag_mismatch`).
- Singletons that were never saved are returned with `etag: null` (the plan's defaults,
  `SingletonDocument.default`). The first save sends `If-None-Match: *`
  (`domain.deps.SingletonPrecondition`); losing that race is a 412.
- Actions that do not edit content (approve, transition, review) also take If-Match, so a
  double tap or a stale screen is refused rather than applied twice. One-tap phone actions that
  are naturally idempotent (pause autonomy, dismiss a reminder) may omit it and use
  `docstore.update_doc`.
- **Article content conflicts are about content.** Background writers change an article while
  the owner edits it in review (gate reports, links, embeddings): those writes never change
  `version`, `meta` or `markdown`. Autosave sends `baseVersion` (the article `version` the editor
  loaded) with If-Match; when the etag is stale but the current `version` still equals
  `baseVersion`, articles applies the owner's meta and Markdown on top of the current document
  (CAS, retried) and answers 200 with the new etag. Only a changed `version` (another tab, a
  restore) is a 412.
- **412 in the UI**: autosave stops, a conflict dialog shows who changed what (`updatedBy`:
  `owner` from another tab, `pipeline`, `system`) and when, and a jsdiff of local vs server;
  buttons "Keep mine" (re-save with the new etag), "Take theirs" (reload), "Copy mine" (to the
  clipboard). Never retry a 412 silently.
- Server-side writers of a singleton that may never have been saved use
  `docstore.update_singleton(store, Cls, mutate, now=...)`; routes return singletons with
  `deps.versioned_singleton(doc, etag, response)` (`etag: null` while unsaved).

### 1.5 Pagination

List routes take `?limit=` (1-100, default 50, `domain.api.clamp_limit`) and `?cursor=`
(opaque, `domain.api.encode_cursor`/`decode_cursor`; a malformed cursor raises
`InvalidCursor`, which `problems()` turns into 422 `invalid_cursor`). They return
`domain.api.Page[T]` = `{"items": [{"item": ..., "etag": ...}], "nextCursor": "..." | null}`.
Sort by one indexed field (default `updatedAt` descending, or `createdAt`/`id` ascending for
append-only data) and page with a range filter on it (`Filter("updatedAt", "<", cursor)`). A
step that writes several documents at once stamps them with `domain.base.distinct_stamps(now,
n)` so a page boundary never splits a tie; ties between unrelated writers in the same
microsecond are accepted. List items are summaries: no `markdown`, `payload`, `embedding` or
other large fields (`Store.find` reads whole documents until I-10 adds a projection, so lists of
large kinds default to 20 items). Lists hide eval articles (`purpose != "eval"`) unless asked.

### 1.6 Problems (errors)

Every error is `application/problem+json` (core). Codes the web relies on:

| Status | `code` | When |
|---|---|---|
| 401 | `unauthenticated`, `invalid_principal` | core |
| 403 | `not_owner`, `owner_not_configured`, `csrf_failed` | core |
| 404 | `not_found` | missing, a document of another kind, or an id that cannot exist (`#`, `/`) |
| 409 | `conflict` | create of an existing id |
| 409 | `invalid_transition` | not an edge of the machine; extensions `machine`, `from`, `to`, `allowed[]` |
| 409 | `live_article`, `eval_article` | an edge an article rule refuses (`check_article_transition` raises `TransitionRefused`) |
| 409 | `read_only_state` | who-writes rule; extensions `state` (the document's status: `status` is the problem's reserved HTTP status member), `writer` |
| 409 | `blocklist_not_configured` | a check needs the blocklist and the owner never saved it (`BlocklistNotConfigured`) |
| 409 | `ai_use_not_confirmed` | anonymized owner material without the AI-use confirmation (`AiUseNotConfirmed`) |
| 409 | `meta_incomplete` | a document needs frontmatter fields that are unset (`MetaIncomplete`, extension `missing[]`) |
| 409 | route-specific (listed per route) | for example `gates_blocking`, `not_calibrated`, `slot_conflict` |
| 412 | `etag_mismatch` | stale If-Match; optional `current` |
| 413 | `too_large`, `markdown_too_large` | uploads over the cap, Markdown over 512 KiB |
| 415 | `unsupported_media_type` | uploads |
| 422 | `validation_failed` (core, with `errors[]`), `confidential_text` (`ConfidentialText`; `findings[]` of `{field, code, severity, message, start, end, ref}` with term ids, never terms), `invalid_cursor` | |
| 428 | `if_match_required` | |
| 500 | `internal_error`, `invalid_document` | |
| 503 | `<service>_unavailable` (`Retry-After: 5`), `store_unavailable` (`Retry-After: 2`) | |

`detail` is owner-facing and never quotes content, prompts, confidential terms or URLs.
`problems()` maps store, transition, writer, confidentiality, cursor and service errors (every
exception class named in this table); raise `ProblemError` for the rest.

### 1.7 Jobs

- **Types** are the constants in `domain.jobtypes` (`<package>.<verb>`). Each package registers
  only its own types. To start another package's job, enqueue the constant with the input model
  from `domain.jobtypes`; `JobsService.enqueue` raises `ValueError` for an unregistered type
  (wave B not shipped yet): catch it, log `job_hook_unregistered` (`job_type` only) and continue
  (`domain.testing.StoreJobQueue(registered=...)` reproduces it in tests). Only the types in
  `domain.jobtypes.SCHEDULED_JOB_TYPES` may run from a schedule; none of them generates content
  (`generates_content`: `pipeline.*`, `posts.*`, `publish.*` start from owner actions only).
- **Ids** are deterministic wherever a retry could enqueue twice (`domain.jobtypes.*_job_id`).
  Enqueue with `domain.jobflow.enqueue_once(jobs, type, input, job_id=..., run_after=...,
  on_conflict=...)`, which says what a `Conflict` means: `reuse` (default; a resumed step that
  already enqueued it: the existing job, whatever its status), `rerun` (the owner asks again:
  an active job is returned, a finished one gets a new job `<id>-r<n>` with the new input and
  `run_after`; re-approving after a veto, re-running gates on unchanged content, re-staging a
  slot after unapprove) or `replace` (cancel the active one, then a new run: publish-now on a
  scheduled article).
- **Inputs** are small JSON (`JobInput.to_json()`), with `origin: "owner" | "background"`.
  Owner clicks enqueue with `origin="owner"`, so their AI calls are never braked; schedules use
  `background`; **chained work inherits its parent's origin** (the research child of an
  owner-started pipeline runs as `owner`).
- **Steps** are re-entrant: checkpoint (`ctx.checkpoint`) before anything expensive, record
  remote ids (`ctx.record_remote_id`) **before** awaiting remote work (GitHub run correlation
  ids, LinkedIn upload URNs), never re-issue on resume. Raise `JobFailed(code, detail)` for a
  permanent failure (detail: owner-facing, no content).
- **Pause, do not fail.** Background AI calls run inside `with
  domain.jobflow.defer_on_brake(services.clock.now()):`, which turns `BrakeEngaged` into
  `JobDeferred(run_after, "brake_<reason>")` (1 h; 10 min when the ledger is unreadable):
  background AI work waits instead of burning its three attempts into a quarantine. Services
  not installed yet (`step_services()`) also defer (`services_starting`). The runner releases
  a deferred job without using an attempt once the integrator lands I-7; until then it treats
  `JobDeferred` as an ordinary error (backoff), which is no worse than today.
- **Missing services**: "not configured" (settings say off, the service is `None`) is
  `require_service(...)` -> `JobFailed("<name>_unavailable")`. "Not ready yet" cannot happen
  for an optional service: the runner starts only after every lifecycle has run (I-1).
- **Events**: `ctx.emit(type, data)` with ids and counts only (4 KB max). After writing a
  document a step emits `domain.jobtypes.ENTITY_CHANGED` with
  `entity_changed("<entity>", doc_id)` (section 9). Token streams use `ctx.delta(text)`
  (broker only, never stored); when the router discards streamed text (any of
  `ai.DISCARD_EVENTS`: refusal, retry, repair, fallback) the step emits `DRAFT_RESET` so the web
  clears what it showed.
- **Human waits**: `await ctx.wait_for_input(prompt, key=...)` (the job goes `waiting_input`,
  frees its slot; the prompt is stored, so it is a short neutral sentence). The answer arrives
  through the owning package's route, which calls `JobsService.provide_input`. **The value is
  stored on the job document, so it is never raw owner text**: interview answers are stored on
  the `interview` document after redaction and the job receives `{"interviewId": ...}`. There
  is no generic "answer a job" route.
- **Chaining** (parent waits for a child). Child results are durable and read by the parent, so
  a child that finishes before its parent suspends (runner concurrency is 4; a fast failure, a
  parent whose lease is lapsing) is never lost:
  1. The parent enqueues the child with a deterministic id (`enqueue_once`, `reuse`) and
     `parent=ParentRef(job_id=ctx.job_id, key=k)` and the parent's `origin`.
  2. The parent calls `result = await jobflow.wait_for_child(ctx, jobs, child_job_id, key=k,
     prompt=...)` right after enqueueing and on every resume. It reads the child job first: a
     recorded `child.result` event (current run), or a finished child (failed: its error code;
     cancelled), is the result at once; otherwise it waits (`waiting_input`).
  3. The child ends every outcome, success or failure (before raising `JobFailed`), with
     `await jobflow.finish_child(ctx, jobs, parent, ChildResult(...))`: it records the result
     on itself (`CHILD_RESULT` event) and then calls `provide_input`; while the parent is still
     running it retries for 90 s (more than a lease), and stops when the parent is queued,
     finished or waiting for another key (those re-read the child). Its last step returns
     `ChildResult(...).to_json()`.
  4. A quarantined child has no result: the parent keeps waiting and the owner retries the
     child from System (it then records and delivers). A cancelled child is
     `ChildResult(ok=False, error_code="cancelled")`; a cancelled child runs no step code, so
     every route that may cancel a child (System, pipeline cancel, research cancel) goes
     through `jobflow.cancel_job`, which tells the waiting parent, and a running child with a
     pending cancel request already reads as cancelled.
- **Delayed work** uses `run_after` on a deterministic id (staging at T-60, reminders, slot
  checks, scheduled publishes, interview expiry). A delayed job re-reads its document and turns
  into a no-op when the document changed (for example the slot moved). **After downtime it
  never fires late without the owner**: its first step compares now with the time it was meant
  for (`jobflow.past_max_lateness`): `publish.article` past `PUBLISH_MAX_LATENESS` (1 h after
  `PublishInput.publishAt`) holds (article approved -> in_review, `lastError: publish_late`,
  `emit_alert("needs_attention", id)`, push; the owner re-approves), `posts.stage` at or
  after the slot itself (`STAGE_MAX_LATENESS` is 0 past the slot; before the slot it stages
  however late it was claimed, for any stage lead) does nothing (the slot check then offers
  the next free slot), `planner.remind` expires a reminder past its `expiresAt`.
  `planner.slot-check`, `posts.expire` and `pipeline.interview-expire` run whenever they run:
  lateness is what they handle.
- **Stuck states have exits.** A quarantined or failed job never strands its document: cancel
  routes (`pipeline/cancel`, research run cancel) accept a job that already finished
  (`JobsService.cancel` returns a finished job unchanged) and still move the document (article
  -> needs_attention, research run -> cancelled), and archive routes never depend on the job.
  An article left `publishing`/`unpublishing` by a publish job that ended without its own
  failure handling (System cancel, quarantine, a crash before the enqueue) is released to
  `publish_failed` by the System cancel and the quarantine hook (`publish.recovery`), and
  publish-now and unpublish release it themselves (409 `publish_in_progress` while the job is
  still active).
- **Pause**: when `AutonomySettings.paused`, scheduled jobs that would call a model
  (`signals.*`, `voice.digest`, `planner.plan-week`, `analytics.suggest`) finish with
  `{"skipped": "paused"}` and auto-publish holds. Owner-started work still runs.

### 1.8 AI calls

- Use `services.ai` (`ModelRunner`: `run`/`stream`), never a provider. Pass `origin` and
  `job_id`. Classify every `Request.sensitivity` honestly: `public` (topic, pillar, sources,
  briefs, feed titles), `anonymized` (approved stories, POVs, confirmed interview answers,
  drafts, other members' pasted text), `private` (raw owner text before confirmation: only
  `Task.ANONYMIZE`).
- Before sending any `anonymized` owner material, call
  `domain.confidentiality.require_ai_use(policy)`: `AiUseNotConfirmed` is 409
  `ai_use_not_confirmed` through `problems()` (routes) or `JobFailed("ai_use_not_confirmed")`
  (jobs). Public-only work (research, triage, quick search) runs regardless.
- **Blocklist before every model or search call that carries owner-editable or model-written
  text.** Call `domain.confidentiality.check_texts({field: text, ...}, blocklist)` and refuse on
  a hit: routes let `ConfidentialText` (422 `confidential_text`) and `BlocklistNotConfigured`
  (409 `blocklist_not_configured`) reach `problems()`, jobs raise `JobFailed("blocklist_hit")` /
  `JobFailed("blocklist_not_configured")`. This covers the revise instruction, the selection
  text and instruction, the post regenerate instruction, research topics, audiences and owner
  URLs (in `research.create_run` and again in the `plan` step, so runs created by the pipeline
  or evals are checked too), quick-search queries and scout queries. `gates.run` stops after
  its `deterministic` step when it found any blocklist finding: no body goes to CRITIQUE or
  FACT_CHECK. Each gate model request is also screened whole at call time (the brief, stories,
  POVs, interview answers, the style guide and settings it carries were saved before a term
  may have joined the list): a hit sends nothing and the gate is `skipped`
  (`material_blocklisted`).
- Confidentiality verdicts come from one place: `services.classifier`
  (`ports.ConfidentialityClassifier`, implemented by stories), used by stories, voice exemplars
  and gate 3, so three packages never decide confidentiality three ways.
- Structured output: pass `output_schema` (a pydantic model built from `ApiModel`-like
  plain fields; no `dict`/`Any`), read `result.output(Model)`. On `result.refused` the output is
  empty: record it and decide (drafts: review-only and needs-attention).
- Record every AI step on the document it produced as
  `ServedByRecord.from_served_by(result.served_by, step=..., at=..., refused=...,
  cost_usd=float(result.cost_usd), prompt_version=...)`. Drafts and posts are `reviewOnly` with
  the reason codes of `domain.common.review_only_reasons(result)`: `fallback_model` when
  `result.served_by.fallback_reason` is set, `refusal` when `result.refusals` is non-empty or the
  result itself is a refusal ("any refusal in the chain").
- Prompts are versioned package data (`<pkg>/prompts/<name>.v<N>.md`, loaded with
  `importlib.resources`), with the version recorded in `promptVersion`. Untrusted text
  (feed titles, fetched passages, pasted comments) goes inside clearly delimited sections with
  an instruction to treat it as data. Never put blocklist terms in a prompt.
- Never log prompts, completions, fetched text or confidential terms; log ids, counts,
  durations and costs (register field names with `core.logging.register_log_fields`).

### 1.9 Security rules every package keeps

1. Every route is owner-only and every non-GET passes CSRF (core does both; no opt-outs). **No
   GET changes state**: confirm tokens are stateless HMACs, previews are read-only, notification
   links open confirmation pages. The one exception is the OAuth callback (protocol), which only
   consumes its own single-use state.
2. **Private interview text is never persisted**: not in documents, job inputs, job outputs,
   checkpoints, `provide_input` values, events, logs or blobs. Raw text lives in the request
   that redacts it and in the `ANONYMIZE` request; only owner-confirmed anonymized wording is
   stored.
3. **Research passages are never persisted**: only claims (short summaries), quotes of at most
   200 characters per source in total, URL, title, publisher and date.
4. **LinkedIn publishing happens only inside `POST /api/posts/{id}/publish`**, from a fresh
   authenticated owner click that carries a confirm token from the go-live page. No job, step,
   schedule or other route can reach the publisher. The LinkedIn client never retries a POST.
5. Blocklist terms never leave the app process: no model, log, event, push, query or problem
   detail; findings point at them by term id. Text bound for a model or a search is checked
   first (section 1.8, `check_texts`), so a term the owner typed into an instruction or a topic
   never reaches a model either. Gate reports and post approvals are keyed on content only, so
   what ships is screened again against the **current** list when it leaves: article approve
   and publish-now (422 `confidential_text`), the `publish.article` check step (hold
   `blocklist_hit`) and the document the upsert writes, staging previews, `posts.stage` and the
   LinkedIn tap; pushes name an article title only when the current list clears it.
6. Push notifications carry a title (at most 80 characters) and a same-origin path only.
7. All outbound fetches of URLs we did not configure go through the egress helper
   (`services.egress` or the research fetcher). Configured APIs (LinkedIn, GitHub, Web IQ, the
   owner's site) use their own `httpx.AsyncClient` with a fixed base URL, timeouts, no
   redirects to other hosts and no proxies.
8. Fail closed: a check that cannot run (no blocklist, no embedder, a fetch failure, an
   unreadable document in a guarding query) is a failing finding, never a pass. A blocklist the
   owner never saved (`configured` false) clears nothing: `blocklist_findings` returns a
   non-overridable `blocklist-not-configured` block, `blocklist_hit` is true and `check_texts`
   raises `BlocklistNotConfigured` (an empty list the owner saved is a valid answer).
9. Private owner text never persists on the client either: the web keeps intake and interview
   raw text (dictation, typed answers before redaction) in component state only: never in
   `localStorage`, `sessionStorage`, IndexedDB, a persisted query cache or a service-worker
   cache, and not in a draft saved on session expiry. Intake and interview routes answer with
   `Cache-Control: no-store` (core's default for `/api`; do not override it).

### 1.10 Settings and environment

One `pydantic-settings` class per package, env prefix `BW_<PKG>_`, safe defaults (unset = the
feature is off and its routes answer 503). Build settings lazily (`functools.cache` factory) so
tests can construct their own; secrets are `SecretStr` and never logged.

| Package | Class | Variables (default) |
|---|---|---|
| storage | `StorageSettings` | `BW_STORAGE_BLOB_ENDPOINT` (unset), `BW_STORAGE_CONTAINER_MEDIA` (`media`), `BW_STORAGE_CONTAINER_PUBLISHED` (`published`), `BW_STORAGE_CONTAINER_BACKUPS` (`backups`), `BW_STORAGE_MAX_UPLOAD_BYTES` (10485760), `BW_STORAGE_MAX_IMAGE_PIXELS` (12000000) |
| stories | `StoriesSettings` | `BW_STORIES_INTAKE_MAX_CHARS` (8000), `BW_STORIES_SEED_MAX_CHARS` (60000) |
| research | `ResearchSettings` | `BW_RESEARCH_WEBIQ_ENDPOINT` (unset), `BW_RESEARCH_WEBIQ_SCOPE` (unset; required with the endpoint), `BW_RESEARCH_WEBIQ_QPS` (15), `BW_RESEARCH_MAX_SOURCES` (12), `BW_RESEARCH_MAX_TOOL_CALLS` (8), `BW_RESEARCH_FETCH_TIMEOUT_SECONDS` (15), `BW_RESEARCH_PASSAGE_MAX_CHARS` (3000), `BW_RESEARCH_QUICK_TIMEOUT_SECONDS` (90) |
| signals | `SignalsSettings` | `BW_SIGNALS_MAX_ITEMS_PER_FEED` (50), `BW_SIGNALS_MAX_AGE_DAYS` (14), `BW_SIGNALS_TRIAGE_BATCH` (20), `BW_SIGNALS_MIN_RELEVANCE` (0.5), `BW_SIGNALS_FAILING_AFTER` (5) |
| articles | `ArticlesSettings` | `BW_ARTICLES_KEEP_VERSIONS` (50) |
| gates | `GatesSettings` | `BW_GATES_MAX_LINKS_CHECKED` (25), `BW_GATES_LINK_TIMEOUT_SECONDS` (10), `BW_GATES_NOVELTY_CORPUS` (50), `BW_GATES_ATLAS_URL` (unset), `BW_GATES_SITE_ORIGIN` (unset: links to the owner's site are judged like any other domain in gate 7; set it to the same value as `BW_PUBLISH_SITE_ORIGIN`) |
| linkedin | `LinkedInSettings` | `BW_LINKEDIN_CLIENT_ID`, `BW_LINKEDIN_CLIENT_SECRET` (SecretStr), `BW_LINKEDIN_REDIRECT_URI` (all unset), `BW_LINKEDIN_TIMEOUT_SECONDS` (20), `BW_LINKEDIN_CONFIRM_TOKEN_TTL_SECONDS` (600), `BW_LINKEDIN_UPLOAD_POLL_SECONDS` (120). The confirm-token key is derived from core's `BW_COOKIE_KEY` (no new secret; section W-A9) |
| backups | `BackupsSettings` | `BW_BACKUPS_PAGE_SIZE` (200) |
| pipeline | `PipelineSettings` | `BW_PIPELINE_MAX_REVISE_LOOPS` (2) |
| publish | `PublishSettings` (**one class for the package**: it absorbs `contract.ContractSettings` and keeps `contracts_dir`) | `BW_PUBLISH_CONTRACTS_DIR` (unset), `BW_PUBLISH_PORTFOLIO_REPOSITORY` (`owner/repo`), `BW_PUBLISH_PORTFOLIO_REF` (`main`), `BW_PUBLISH_PORTFOLIO_WORKFLOW` (workflow file name), `BW_PUBLISH_PORTFOLIO_TOKEN` (SecretStr), `BW_PUBLISH_SITE_ORIGIN` (`https://...`), `BW_PUBLISH_PREVIEW_WORKFLOW` (unset), `BW_PUBLISH_BUILD_TIMEOUT_SECONDS` (1800), `BW_PUBLISH_VERIFY_TIMEOUT_SECONDS` (900), `BW_PUBLISH_POLL_SECONDS` (20) |
| analytics | `AnalyticsSettings` | `BW_ANALYTICS_IMPORT_MAX_BYTES` (5242880) |
| render (exists) | `RenderSettings` | `BW_RENDER_*` (see `render.md`) |
| kb, voice, planner, engagement | none | owner-editable values live in settings documents |

Existing: `BW_` (core), `BW_COSMOS_`, `BW_AI_`, `BW_VAULT_`, `BW_NOTIFY_`, `BW_JOBS_`.

### 1.11 Tests

- Tests live in `server/tests/<pkg>/` (no `__init__.py`; importlib mode; share helpers through
  `conftest.py` fixtures). No network, no Azure, no Chromium: use `domain.testing`:
  `make_services()` (every service faked: `MemoryStore`, `StoreJobQueue`, `FakeClock`,
  `ScriptedModelRunner`, `HashEmbedder`, `MemoryBlobStore`, `StaticPageFetcher`, `StaticHttp`,
  `FakeRenderService`, `RecordingNotifier`, `fake_vault()`, `FakeSlopChecker`,
  `FakeClassifier`, `RecordingSeeder`; `StoreJobQueue(store, registered={...})` refuses other
  types like `JobsService`), `make_app(services, routers=[...])` (the real app: auth, CSRF,
  problems), `services_installed(services)` for job steps, `sample_document(Cls, **fields)` for
  any document, `png_bytes`/`pdf_bytes` for real files. Requests use
  `core.testing.principal_headers(OWNER_OID)` and, for non-GET, `core.testing.write_headers`.
- `ScriptedModelRunner` enforces the real sensitivity policy: a test that sends anonymized data
  to a public-only task fails like production. Script replies per `Task`
  (`ScriptedReply(text=..., value=Model(...), refused=True, fallback_reason="refusal")`; like the
  router, a `fallback_reason="refusal"` reply also lists the refusal in `result.refusals`) and
  assert on `runner.calls_for(Task.X)`.
- Parent/child jobs: test "the child finishes before the parent suspends" on a real `JobRunner`
  (`tests/domain/test_jobflow.py` shows the harness).
- Run steps directly (`await step(ctx)` with a real `JobRunner` on a `MemoryStore`, or call the
  step's underlying function). `jobs.testing.FakeClock` drives time.
- Invariant tests are part of every work order (listed there). Golden files sit in
  `tests/<pkg>/goldens/` and change only deliberately.
- Checks (from `server/`, scoped to your paths):
  `timeout 600 uv run ruff check <paths> && timeout 600 uv run ruff format --check <paths> &&
  PYRIGHT_PYTHON_IGNORE_WARNINGS=1 timeout 600 uv run pyright <paths> &&
  PYTHONASYNCIODEBUG=1 timeout 600 uv run pytest -q tests/<pkg>`.

---

## 2. State machines

Defined as data in `blogwriter.domain.states` (golden tables in
`tests/domain/test_states.py`). Change an edge only together with this section. Use
`transition(MACHINE, current, target)` (raises `TransitionError` -> 409 `invalid_transition`);
for articles always use `domain.articles.check_article_transition(article, target)`, which
adds the article rules (`TransitionRefused`: `live_article` for archiving an article whose
`publish.live` is true, unpublish first; `eval_article` for approving or publishing a
`purpose=eval` article).

| Machine | States (initial first) and edges |
|---|---|
| `article` | planned -> researching, interviewing, drafting, in_review, archived; researching -> interviewing, drafting, needs_attention, archived; interviewing -> drafting, needs_attention, archived; drafting -> gating, in_review, needs_attention, archived; gating -> in_review, approved (auto path only), needs_attention; in_review -> researching, interviewing (the owner restarts the pipeline earlier), drafting, gating, approved, archived; approved -> in_review, publishing; publishing -> published, publish_failed; publish_failed -> publishing, unpublishing (repeat `publish.lastOperation`), in_review; published -> in_review (correction), publishing (republish), unpublishing; unpublishing -> unpublished, publish_failed; unpublished -> in_review, archived; needs_attention -> researching, interviewing, drafting, in_review, archived; archived -> planned |
| article writer (`ARTICLE_WRITER`) | owner writes Markdown/meta in planned, in_review, needs_attention; pipeline in researching, interviewing, drafting, gating; `publish` in publishing, unpublishing (the publish job, and only its publish fields through `articles.transition(update=...)`: nobody changes the bytes being rendered and verified, `pipeline_write` is refused); nobody in approved, published, publish_failed, unpublished, archived (`check_article_writer(status, "owner" \| "pipeline" \| "publish")`, 409 `read_only_state`) |
| `post` | draft (initial) -> generating, approved, archived, expired; generating -> draft, needs_attention, archived; approved -> draft (any edit), staged, publishing, kit, missed, expired, archived; staged -> draft, approved (re-slot), publishing, kit, missed, expired; publishing -> posted (201 + URN), approved (a definite 4xx: LinkedIn created nothing), needs_attention (5xx, timeout, restart); needs_attention -> posted, approved, kit, draft, archived; kit -> posted, approved, missed, expired, archived; missed -> approved, kit, expired, archived; posted -> deleted; expired -> draft, archived; archived -> draft; deleted (terminal). A post is `publishing` exactly while `intent.status` is `pending` (model invariant); leave `publishing` only through `domain.posts.settle_publish_intent(post, "posted" \| "rejected" \| "unknown", now=...)`, which sets the intent to `succeeded`, `failed` or `unknown` in the same write |
| `kit` (`post.kit.state`) | prepared -> native_draft_saved, natively_scheduled, posted; native_draft_saved -> natively_scheduled, posted; natively_scheduled -> native_draft_saved, posted; posted (terminal). A new native time T is an update, not a transition. |
| `idea` | new -> accepted, snoozed, used, dismissed, expired; snoozed -> new, dismissed, expired; accepted -> used, dismissed, new; used -> accepted; dismissed -> new; expired -> new |
| `research_run` | queued -> running, cancelled; running -> succeeded, failed, quarantined, cancelled; failed -> queued |
| `review` (stories, POVs) | proposed -> approved, rejected; approved -> archived; rejected -> proposed; archived -> approved |
| `voice_sample` | active <-> excluded |
| `style_guide` | proposed -> active, rejected; active -> superseded; superseded -> active |
| `suggestion` (digest and insight suggestions) | proposed -> accepted, dismissed |
| `interview` | pending -> answered, skipped, expired; expired -> answered, skipped |
| `week_plan` | generating -> proposed; proposed -> generating, approved; approved -> proposed, closed |
| `reminder` | pending -> sent, cancelled, expired (kinds `reply`, `snapshot`, `kit_confirm`; id `Reminder.id_for(key)`) |
| `engagement_item` | open -> finalized, dismissed, expired |
| `experiment` | draft -> running, abandoned; running -> concluded, abandoned |
| `source` | active -> paused, failing; failing -> active, paused; paused -> active |
| `linkedin_connection` | disconnected -> connected; connected -> needs_reconnect, disconnected; needs_reconnect -> connected, disconnected |

Status changes always set `statusChangedAt`. Every status the web shows has a label in the web
map's glossary (section 10).

---

## 3. Work orders, wave A

Wave A packages are built in parallel. Each depends only on `domain`, the Phase 1 packages and
the `render`/`publish` ports. Cross-package needs inside wave A go through the ports
(`services.slop`, `services.fetcher`, `services.blobs`) or through documents read with
`domain.docstore`.

### W-A1 storage (`blogwriter.storage`)

**Purpose.** Private Blob storage for media, published assets and backups; owner uploads
(images re-encoded, metadata stripped); same-origin serving of media to the SPA.

**Files.** `storage/__init__.py` (exports below), `settings.py`, `blobs.py`
(`AzureBlobStore`), `media.py` (ingest, naming, `store_media`), `routes.py`, `lifecycle.py`;
`tests/storage/`.

**Depends on.** `azure.storage.blob.aio` (pinned), `azure.identity.aio`, Pillow, `domain`.

**Public Python API.**
```python
class AzureBlobStore:  # satisfies domain.ports.BlobStore
    @classmethod
    async def open(cls, settings: StorageSettings | None = None, *,
                   credential: AsyncTokenCredential | None = None) -> "AzureBlobStore": ...
    async def put(self, container: BlobContainer, name: str, data: bytes, *,
                  content_type: str, overwrite: bool = False) -> BlobInfo: ...   # sets metadata sha256
    async def get(self, container: BlobContainer, name: str) -> bytes: ...       # BlobNotFound
    async def head(self, container: BlobContainer, name: str) -> BlobInfo | None: ...
    async def delete(self, container: BlobContainer, name: str) -> None: ...     # idempotent
    async def list_blobs(self, container: BlobContainer, prefix: str = "") -> list[BlobInfo]: ...
    async def close(self) -> None: ...

type MediaArea = Literal["uploads", "posts", "articles", "brand"]
def media_name(area: MediaArea, owner_id: str | None, sha256: str, ext: str, *,
               now: datetime) -> str
    # uploads/<YYYY-MM>/<sha256>.<ext>; posts/<postId>/<sha256>.<ext>; articles/<id>/...; brand/<sha>.<ext>
@dataclass(frozen=True) class IngestedImage: data: bytes; content_type: str; width: int; height: int; sha256: str
async def ingest_image(data: bytes, *, max_pixels: int, max_bytes: int) -> IngestedImage
    # Pillow in asyncio.to_thread: PNG/JPEG/WebP only, first frame only, re-encoded (EXIF, XMP,
    # ICC text and comments dropped), refuses > max_pixels; raises MediaRejected(code)
async def store_media(blobs: BlobStore, data: bytes, *, role: MediaRole, area: MediaArea,
                      owner_id: str | None, content_type: str, now: datetime,
                      alt_text: str | None = None, filename: str | None = None,
                      width: int | None = None, height: int | None = None,
                      page_count: int | None = None) -> MediaRef
    # content-addressed; BlobExists means the same bytes are already there (fine)
class StorageLifecycle:  # startup: app.state.blobs = AzureBlobStore | None (unset endpoint)
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/media?role=<upload\|headshot\|name_mark\|image>&filename=<plain>` | raw body, `Content-Type: image/png\|image/jpeg\|image/webp` (no multipart: `python-multipart` is not installed) | 201 `MediaRef` (`container: media`, name `uploads/...`) | 413 `too_large`, 415 `unsupported_media_type`, 422 `invalid_image`, 503 `storage_unavailable` |
| GET | `/api/media/{name:path}` | none | 200 bytes; `Content-Type` from the blob, `Cache-Control: private, max-age=31536000, immutable`, `Content-Security-Policy: default-src 'none'; sandbox`, `X-Content-Type-Options: nosniff` | 404 (also for names outside `uploads/ posts/ articles/ brand/` or with `..`), 503 |

Read the body with a running byte cap (`request.stream()`), never `await request.body()` on an
unbounded body.

**Jobs, schedule, AI.** None.

**Settings.** `StorageSettings` (section 1.10). Startup: unset endpoint means
`app.state.blobs = None` and log `storage_not_configured`; a set endpoint that cannot reach
the three containers fails the boot (like `db`).

**Tests.** A fake container-client protocol (no Azure): put/get/head/list/delete, metadata
`sha256`, overwrite rules; EXIF/GPS stripped (golden JPEG in, no EXIF out); SVG, HTML and
polyglot inputs refused; > 12 MP refused; traversal names refused; cache and CSP headers;
503 without storage; nothing ever sets public access.

### W-A2 kb (`blogwriter.kb`)

**Purpose.** The knowledge base and policy settings: pillars, format packs, lanes, brand kit,
blocklist, employer policy, cadence/slots/modes, model preferences, autonomy switches; the
owner-input checklist.

**Files.** `kb/__init__.py`, `routes.py`, `inputs.py`, `models.py` (request/response models);
`tests/kb/`.

**Depends on.** `domain`, `render` (brand validation through `RenderBrand`), `ai` (read-only
routing view).

**Public Python API.**
```python
async def owner_inputs(store: Store, *, now: datetime) -> OwnerInputs
class OwnerInputs(ApiModel): items: list[OwnerInput]
class OwnerInput(ApiModel):
    code: str; label: str; done: bool; have: int | None; need: int | None; href: str
    # codes: linkedin_samples (>=5), stories_approved (>=10), povs_per_pillar (>=3 each),
    # blocklist, employer_policy, ai_use_confirmed, brand_kit, linkedin_connected,
    # watchlist (>=5), eval_labels (12 labelled)
async def pillar_ids_in_use(store: Store) -> set[str]
def validate_brand(brand: BrandKit, *, disclaimer: str | None) -> list[Finding]   # via render.RenderBrand (WCAG AA)
```
Everyone else reads settings with `domain.docstore.read_singleton(store, Cls, now=...)`.
`owner_inputs` counts the blocklist as done once it is `configured` (saved at least once, even
empty). `pillar_ids_in_use` queries with `find_docs(..., strict=True)` (fail closed).

**HTTP routes.** Every `GET` returns `Versioned[Doc]` (`etag: null` = defaults, through
`deps.versioned_singleton`). Every `PUT` uses `SingletonPrecondition` (`If-Match` or
`If-None-Match: *`) and returns 200 `Versioned[Doc]`; common codes 412, 422, 428.

| Method | Path | Request | Response / extra codes |
|---|---|---|---|
| GET, PUT | `/api/kb/pillars` | `PillarsIn{pillars: Pillar[]}` | `PillarSettings`; 409 `pillar_in_use` (removing or renaming an id referenced by any idea, article, post, story, POV or source) |
| GET, PUT | `/api/kb/formats` | `FormatPacksIn{packs}` | `FormatPacks` |
| GET, PUT | `/api/kb/lanes` | `LanesIn` (all `LaneSettings` fields) | `LaneSettings` |
| GET, PUT | `/api/kb/brand` | `BrandIn{authorName, authorHeadline, siteName, colors, font, headshot: MediaRef\|null, nameMark: MediaRef\|null, artBriefs}` | `BrandKit`; 422 `brand_contrast` (`findings[]` name the failing pairs) |
| GET, PUT | `/api/kb/blocklist` | `BlocklistIn{terms: [{id?, term, category, match}]}` | `Blocklist` (server assigns `t-<n>` ids, sets `configured: true`, also for an empty list: until the first save every blocklist check fails closed) |
| GET, PUT | `/api/kb/policy` | `PolicyIn{disclaimerShort, disclaimerLong, forbiddenTopics, noEmployerBranding, aiUseConfirmed, clearanceStatement}` | `EmployerPolicy` (`configured: true`; `aiUseConfirmedAt` set when it turns true) |
| GET, PUT | `/api/kb/cadence` | `CadenceIn` (all `CadenceSettings` fields) | `CadenceSettings`; after saving calls `services.schedules.reseed()` (failure is logged, not fatal) |
| GET | `/api/kb/models` | none | `ModelsView{preferences: Versioned[ModelPreferences], routes: [{task, maxClass, tools, chain: [{model, deployment, provider, effort}]}], prices: [{model, input, output, cachedInput, cacheWrite}], webSearchUsdPer1k}` (routes from `services.ai.routing` when it is an `AIRouter`, else empty; prices from `blogwriter.ai.AISettings().price_table()` (`AIRouter` exposes no prices; I-11 may add a property); both env-configured and read-only here) |
| PUT | `/api/kb/models` | `ModelPreferencesIn{imagesEnabled, noveltyMaxSimilarity, nearDuplicateMaxSimilarity}` | `ModelPreferences` |
| GET | `/api/kb/autonomy` | none | `AutonomyView{settings: Versioned[AutonomySettings], decision: {allowed, reasons[]}}` (`auto_publish_decision`) |
| PUT | `/api/kb/autonomy` | `AutonomyIn{autoPublishEnabled}` | `AutonomyView` (calibration and judge fields are read-only here) |
| POST | `/api/kb/autonomy/pause` | none (idempotent, no If-Match) | `AutonomyView` (`paused: true`, `pausedAt`) |
| POST | `/api/kb/autonomy/resume` | none | `AutonomyView` |
| GET | `/api/kb/inputs` | none | `OwnerInputs` |

**Jobs, schedule, AI.** None.

**Settings.** None (everything is in settings documents).

**Tests.** Defaults with `etag: null`; create with `If-None-Match: *`, race -> 412; stale etag
412; `pillar_in_use`; brand contrast failures; cadence save triggers one reseed; autonomy PUT
cannot write calibration; pause/resume idempotent; **blocklist terms never appear in logs
(caplog), problem details or any other response**; owner inputs counts against seeded
documents.

### W-A3 stories (`blogwriter.stories`)

**Purpose.** The story bank and POV bank; input-side confidentiality (blocklist, entity
detection, anonymizer proposal, owner confirmation); the confidentiality classifier (the
`ConfidentialityClassifier` port every package uses); seeding from the resume and portfolio.

**Files.** `stories/__init__.py`, `intake.py` (local findings, redaction proposal),
`classify.py`, `bank.py`, `routes.py`, `jobs.py`, `lifecycle.py`, `prompts/`; `tests/stories/`.

**Depends on.** `domain`, `ai`.

**Public Python API.**
```python
def local_findings(text: str, blocklist: Blocklist) -> list[Finding]
    # deterministic, no I/O: blocklist (domain.confidentiality.blocklist_findings: block; an
    # unconfigured list gives the blocklist-not-configured block), e-mail (block), phone (block),
    # URL (block), IP address (block), @handle (block), exact figures and money (warn: bucket
    # them), capitalised multi-word names (warn: possible-name). Spans index `text`.
class RedactionProposal(ApiModel):
    findings: list[Finding]
    proposal: str | None          # anonymized wording, or None
    reason: Literal["strict_mode", "ai_unavailable", "ai_use_not_confirmed", "refused"] | None
    anonymizer_used: bool
async def propose(text: str, *, purpose: IntakePurpose, services: Services) -> RedactionProposal
    # Task.ANONYMIZE, Sensitivity.PRIVATE, origin="owner", tool-free; nothing stored or logged
type IntakePurpose = Literal["story", "pov", "interview_answer", "voice_sample", "idea_note", "take"]
class StoriesClassifier:   # satisfies domain.ports.ConfidentialityClassifier
    def __init__(self, services: Callable[[], Services]) -> None: ...   # resolves ai/store per call
    async def classify(self, texts: Sequence[str], *, origin: Origin,
                       job_id: str | None = None) -> list[ClassifierCheck]
    # require_ai_use first (AiUseNotConfirmed before any model call); Task.FACT_CHECK,
    # Sensitivity.ANONYMIZED: confidential details + mosaic (industry, counts, vendor stack,
    # geography, timeline); merges local_findings; sets content_sha256; ServiceUnavailable("ai")
    # without a model; background calls inside defer_on_brake (jobs) or let BrakeEngaged through
class StoriesLifecycle:   # startup: app.state.classifier = StoriesClassifier(lambda: StateServices(app))
@dataclass(frozen=True) class StoryBank: stories: list[StoryEntry]; povs: list[PovEntry]
async def load_bank(store: Store, *, pillar: str | None = None, usable_only: bool = True) -> StoryBank
def story_gaps(bank: StoryBank, pillars: PillarSettings) -> list[str]
    # codes such as "no-stories:<pillar>", "few-povs:<pillar>", "metrics-missing:<storyId>"
```
`require_ai_use` and `AiUseNotConfirmed` live in `domain.confidentiality` (every package needs
them). Voice exemplars and gate 3 call `services.classifier`, never stories' internals.

**HTTP routes.** Lists return `Page[...]`; single documents `Versioned[...]`.

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/stories/intake/redact` | `RedactIn{text (1-8000), purpose, pillar?}` | 200 `RedactionProposal` | 422 (too long), 409 `blocklist_not_configured` (every intake route and save, until the owner saved the blocklist once) |
| POST | `/api/stories/intake/check` | `CheckIn{text}` | 200 `{ok, findings[]}` (local only) | |
| GET | `/api/stories?status=&pillar=&usable=&limit=&cursor=` | | `Page[StoryEntry]` | |
| POST | `/api/stories` | `StoryIn{title, problem, actions, decisions, metrics[], lessons, pillars[], confidentiality, tags[]}` (owner-confirmed wording) | 201 `Versioned[StoryEntry]` (origin `owner`, status `approved`; the classifier runs synchronously when AI is available and `aiUseConfirmed` is true, otherwise it stays pending and the entry is not usable yet) | 422 `confidential_text` (blocking local findings) |
| GET, PUT, DELETE | `/api/stories/{id}` | PUT: `StoryIn`, If-Match (re-checks and re-classifies) | `Versioned[StoryEntry]`, DELETE 204 | 404, 412, 422 |
| POST | `/api/stories/{id}/review` | `ReviewIn{decision: approve\|reject\|archive\|restore}`, If-Match | `Versioned[StoryEntry]` | 409 `invalid_transition` |
| GET | `/api/povs?pillar=&status=&ideaId=&limit=&cursor=` | | `Page[PovEntry]` | |
| POST | `/api/povs` | `PovIn{pillar, statement, context?, confidentiality, origin: owner\|quick_capture\|idea_take, ideaId?}` | 201 `Versioned[PovEntry]` | 422 `confidential_text` |
| GET, PUT, DELETE | `/api/povs/{id}` | as stories | | |
| POST | `/api/povs/{id}/review` | `ReviewIn` | `Versioned[PovEntry]` | |
| POST | `/api/stories/seed` | `SeedIn{text (1-60000), source: cv\|portfolio}` (public material only) | 202 `JobAccepted` (stores the text as an ephemeral `SeedText`, id `SeedText.id_for(text)`, and enqueues `stories.seed` with `{seedId, source}`: job inputs never hold content) | 409 `ai_use_not_confirmed` |
| POST | `/api/stories/classify` | none | 202 `JobAccepted` | |

Intake flow in the UI: dictate or type -> `redact` (findings highlighted, proposal shown) ->
owner edits/accepts -> `check` -> create. The raw text exists only in the browser, the
`redact` request and its `ANONYMIZE` call.

**Jobs.**

| Type | Input | Steps | Notes |
|---|---|---|---|
| `stories.seed` | `{seedId, source}` + `origin` (`JobInput` subclass in the package) | `extract` (read the `SeedText`; SYNTHESIS, public: proposed stories and POVs as structured output; checkpoint), `classify` (the classifier per proposal), `save` (create `proposed` entries with deterministic ids `story-seed-<sha256(text)[:12]>-<n>` / `pov-seed-...` and `distinct_stamps`; `Conflict` = already saved; one `stories.seeded {count}` event; delete the `SeedText`) | The resume and portfolio are public; the owner approves each entry |
| `stories.classify` | `{}` | `classify` (every story and POV whose `classifier.contentSha256` differs from its current text, in batches, through the classifier inside `defer_on_brake`; `update_doc` each) | Phase 2 exit: classifier over all stories |

**Schedule.** None. **AI.** ANONYMIZE (private, owner), FACT_CHECK (anonymized), SYNTHESIS
(public seed extraction).

**Settings.** `StoriesSettings`.

**Tests.** **Private text never persisted**: a unique marker in the raw text never appears in
the `MemoryStore` dump, job documents, events, blobs or caplog, and appears only in
`ANONYMIZE` requests with `Sensitivity.PRIVATE`; confirmed text with a blocklisted term or an
e-mail is refused; strict mode (`ScriptedModelRunner(allow_private=False)`) and
`aiUseConfirmed=false` make no model call; `usable` needs approval and a passing classifier;
an edit makes the classifier verdict stale; seed ids are deterministic; the seed text never
appears in a job document; an unconfigured blocklist refuses every intake save (409);
`StoriesClassifier` satisfies the port (a pyright conformance function, as in
`tests/domain/test_ports_testing.py`).

### W-A4 voice (`blogwriter.voice`)

**Purpose.** The voice corpus (portfolio posts and About, 5-10 past LinkedIn posts, interview
answers, final posted text), AI-tell and slop scoring, the versioned Voice & Style Guide, the
weekly (first quarter) then monthly diff digest.

**Files.** `voice/__init__.py`, `scoring.py`, `guide.py`, `digest.py`, `routes.py`, `jobs.py`,
`schedule.py`, `prompts/`; `tests/voice/`.

**Depends on.** `domain`, `ai`, `linkedin.lint` (AI tells), `services.slop` (`SlopChecker`
port, implemented by gates), `services.classifier` (exemplar candidates), `services.fetcher`
(owner-typed sample URLs: the research fetcher turns HTML into text; never `services.egress`
plus a second HTML parser).

**Public Python API.**
```python
@dataclass(frozen=True) class TextScore: ai_tell_score: float; flags: list[Finding]; exemplar_eligible: bool
def score_text(text: str, *, slop: SlopChecker | None) -> TextScore
    # AI tells + slop findings; per 100 words; without a slop checker exemplar_eligible is False
    # (fail closed). AI tells: linkedin.lint.ai_tell_findings(text) once I-9 adds it; until then
    # the exact recipe of linkedin.lint (AI_TELLS are `regex`-module patterns: apostrophes
    # U+2018/U+2019 -> "'", each wrapped as (?i)(?<![\p{L}\p{N}])<pattern>(?![\p{L}\p{N}])),
    # never plain substring matching. Convert linkedin findings with from_linkedin_finding.
async def add_sample(store: Store, *, source_type: VoiceSourceType, text: str, now: datetime,
                     slop: SlopChecker | None, title: str | None = None, url: str | None = None,
                     posted_at: str | None = None, pillar: str | None = None,
                     owner_unassisted: bool = False, source_ref: str | None = None) -> Stored[VoiceSample]
    # used by pipeline for confirmed interview answers (source_type "interview_answer")
async def load_active_guide(store: Store) -> StyleGuide | None
def guide_prompt(guide: StyleGuide) -> str   # the cached drafting prefix (body + exemplars)
async def linkedin_sample_count(store: Store) -> int
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/voice/samples?sourceType=&status=&limit=&cursor=` | | `Page[VoiceSample]` | |
| POST | `/api/voice/samples` | `SampleIn{sourceType, text? (<=20000), url? (https, owner-typed: `services.fetcher.fetch(url, allowed_urls=[url])`), title?, postedAt?, pillar?, ownerUnassisted}` | 201 `Versioned[VoiceSample]` (scored; the text passes `check_texts` like every intake save) | 422 `text_or_url_required`, `confidential_text`; 409 `blocklist_not_configured`; 502 `fetch_failed`; 503 `fetcher_unavailable` |
| PUT | `/api/voice/samples/{id}` | `SampleUpdateIn{text?, title?, pillar?, ownerUnassisted?}`, If-Match | `Versioned[VoiceSample]` (re-scored) | 412 |
| POST | `/api/voice/samples/{id}/status` | `{status: active\|excluded}`, If-Match | `Versioned[VoiceSample]` | |
| DELETE | `/api/voice/samples/{id}` | If-Match | 204 | |
| GET | `/api/voice/guide` | | `GuideView{active: Versioned[StyleGuide]\|null, versions: [{id, version, status, createdAt, approvedAt}], linkedinSamples, required, canBuild}` | |
| GET | `/api/voice/guide/{id}` | | `Versioned[StyleGuide]` | |
| POST | `/api/voice/guide/build` | none | 202 `JobAccepted` | 409 `not_enough_samples` |
| POST | `/api/voice/guide/{id}/versions` | `GuideEditIn{body, rules}` | 201 `Versioned[StyleGuide]` (new `proposed` version, `editedByOwner`) | |
| POST | `/api/voice/guide/{id}/activate` | If-Match | `Versioned[StyleGuide]` (previous active -> superseded; `VoicePreferences.activeGuideId`; first activation sets `weeklyUntil` = now + 91 days) | 409 |
| POST | `/api/voice/guide/{id}/reject` | If-Match | `Versioned[StyleGuide]` | |
| GET | `/api/voice/digests?limit=&cursor=`, `/api/voice/digests/{id}` | | `Page`/`Versioned[VoiceDigest]` | |
| POST | `/api/voice/digests/{id}/suggestions/{sid}` | `{decision: accept\|dismiss}`, If-Match | `Versioned[VoiceDigest]`; accepting also creates a new `proposed` guide version with the rule added (deterministic, no AI) | |
| POST | `/api/voice/digests/run` | none | 202 | |

**Jobs.**

| Type | Steps | Notes |
|---|---|---|
| `voice.build-guide` | `score` (re-score active samples), `classify` (`services.classifier` over exemplar candidates; flagged, failing or unclassifiable ones are never exemplars), `synthesize` (`require_ai_use`; SYNTHESIS, anonymized: new `StyleGuide` version `proposed`, deterministic id) | 409 at the route below 5 LinkedIn samples |
| `voice.digest` | `collect` (posts posted in the period with `aiDraftText` and `finalText`, kit text included; adds `final_post` samples), `synthesize` (SYNTHESIS: comparisons and suggestions), `save` (`VoiceDigest`, push "Voice digest ready") | Monthly after `weeklyUntil`: the weekly run is a no-op except in the first week of a month |

**Schedule.** `sched-voice-digest`: `weekly([cadence.digest.day], cadence.digest.time)`,
`voice.digest`, max lateness 24 h.

**AI.** SYNTHESIS (anonymized), FACT_CHECK (classifier).

**Tests.** Flagged passages are never exemplars; at least 5 LinkedIn samples to build; versions
monotonic, one active; digest weekly -> monthly switch; owner-typed URLs fetched only through
`services.fetcher` with `allowed_urls=[url]`; AI-tell matches equal `linkedin.lint`'s on the
same text (curly apostrophes included); Phase 2 exit "blind voice test" is a manual check (no
test).

### W-A5 research (`blogwriter.research`)

**Purpose.** Standard research runs (plan, search, fetch-and-extract, verify, brief), quick
search, the `fetch_page` implementation (Web IQ Browse, egress fallback), the blocklist audit.
**No passages are ever persisted.**

**Files.** `research/__init__.py`, `settings.py`, `fetch.py` (`WebIqFetcher`, `EgressFetcher`,
`FallbackFetcher`), `run.py` (steps), `quick.py`, `audit.py`, `routes.py`, `jobs.py`,
`lifecycle.py`, `prompts/`; `tests/research/`.

**Depends on.** `domain`, `ai`, `core.egress`, BeautifulSoup and lxml (HTML to text in
`asyncio.to_thread`), `azure.identity.aio` (Web IQ token).

**Public Python API.**
```python
class WebIqFetcher:   # PageFetcher over Web IQ Browse (managed identity bearer token, QPS limiter)
class EgressFetcher:  # PageFetcher over services.egress + HTML-to-text (main text only, <= PASSAGE_MAX_CHARS)
class FallbackFetcher:  # tries Web IQ, then egress; records which one served (FetchedPage.via)
    # every fetcher: url must be in allowed_urls, https, no query string added -> FetchRefused
class ResearchLifecycle:  # startup: app.state.fetcher = FallbackFetcher(...) (egress-only without Web IQ)
async def create_run(store: Store, *, topic: str, pillar: str, now: datetime, audience: str | None = None,
                     recency_days: int = 365, article_id: str | None = None,
                     idea_id: str | None = None, owner_urls: Sequence[str] = (),
                     run_id: str | None = None) -> Stored[ResearchRun]
    # reads the blocklist and runs check_texts on topic, audience and every owner URL before
    # writing (ConfidentialText / BlocklistNotConfigured): pipeline- and eval-created runs are
    # checked like route-created ones. run_id: a derived id (ResearchRun.id_for_pipeline /
    # id_for_eval); a Conflict returns the existing run, so a resumed step never makes two.
async def start_run(services: Services, run: ResearchRun, *, origin: Origin,
                    parent: ParentRef | None = None) -> Job
    # enqueue_once(research.run, research_job_id(run.id), reuse); origin = the parent's origin
async def quick_search(services: Services, query: str, *, pillar: str | None, origin: Origin,
                       article_id: str | None = None, post_id: str | None = None,
                       idea_id: str | None = None) -> Stored[QuickSearch]
def audit_texts(texts: Sequence[str], blocklist: Blocklist) -> bool   # blocklist_hit wrapper
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/research/runs` | `RunIn{topic (3-300), pillar, audience?, recencyDays?, articleId?, ideaId?, ownerUrls? (<=10 https)}` | 202 `{run: Versioned[ResearchRun], jobId}` | 422 `confidential_text`, 409 `blocklist_not_configured` (checked by `create_run` before anything leaves the app) |
| GET | `/api/research/runs?articleId=&status=&limit=&cursor=` | | `Page[ResearchRun]` | |
| GET | `/api/research/runs/{id}` | | `Versioned[ResearchRun]` | |
| POST | `/api/research/runs/{id}/cancel` | If-Match | `Versioned[ResearchRun]` (job cancelled, run `cancelled`) | 409 |
| GET | `/api/research/briefs/{id}` | | `Versioned[Brief]` | |
| PUT | `/api/research/briefs/{id}` | `BriefEditIn{executiveSummary?, findings?, angles?, hookSeeds?, counterpoints?, openQuestions?, removeClaimIds?, removeCitationIds?}` (the owner may edit summaries and remove items, never add quotes or citations), If-Match | `Versioned[Brief]` (`editedByOwner`) | 412, 422 |
| POST | `/api/research/quick` | `QuickIn{query (3-300), pillar?, articleId?, postId?, ideaId?}` | 201 `Versioned[QuickSearch]` (synchronous, <= `QUICK_TIMEOUT_SECONDS`; `check_texts` on the query first) | 422 `confidential_text`, 409 `blocklist_not_configured`, 503 `ai_unavailable`, 504 `timeout` |
| GET | `/api/research/quick/{id}`, `/api/research/quick?articleId=&postId=&ideaId=` | | `Versioned`/`Page[QuickSearch]` | |

**Jobs.** `research.run`, input `domain.jobtypes.ResearchRunInput`, id `research_job_id(runId)`:

| Step | What it does |
|---|---|
| `plan` | `check_texts` on topic, audience and owner URLs again (the blocklist may have changed since the run was created; a hit -> quarantine as in `audit`). run -> running. PLAN (public): sub-questions, audience, recency window; stored on the run |
| `search` | RESEARCH with `WebSearchTool(allowed_domains=pillar.research_domains)` (allowlist pass) and `WebSearchTool()` (open pass), `max_tool_calls`; record `queries` and `sources` (URLs, titles, domains) on the run. Blocklist audit of the queries: a hit quarantines (below) |
| `extract` | One atomic fetch-and-extract per source (top `MAX_SOURCES`, allowlisted first): `services.fetcher.fetch(url, allowed_urls=run sources + owner URLs)`; the passage stays in memory; RESEARCH tool-free (public) extracts claims (<= 600 chars) and quotes; every claim goes through `domain.research.strip_verbatim(claim, passage)` (any run of 40+ characters shared with the passage is cut; an emptied claim is dropped) and quotes are cut so each source totals <= 200 verbatim characters. Checkpoint per source: claims and quotes only (never the passage). On resume, sources not done are re-fetched. Web IQ failing switches to egress (`webiqFallback`); both failing means claims come from the search citations alone (`web_search` alone) |
| `verify` | SYNTHESIS (public): dedupe, key stats need 2 independent sources, check dates against the recency window, flag conflicts |
| `brief` | SYNTHESIS (public): the `Brief` (id `Brief.id_for(runId)`, `Conflict` = done): executive summary, cited findings, stats, counterpoints, open questions, angles, hook seeds, claims, quotes, citations (`src-<slug>` ids); `briefId` set; `entity.changed` |
| `audit` | Blocklist check of every query, fetched URL and brief text. A hit: run -> quarantined (`quarantineReason: blocklist_hit`), brief deleted, `emit_alert("quarantined", run_id)`, `JobFailed("blocklist_hit")`. Clean: run -> succeeded (here, not in `brief`: `succeeded -> quarantined` is not an edge) |

Every outcome (success, `JobFailed`) ends with `jobflow.finish_child(ctx, jobs, parent,
ChildResult{ok, ref: briefId, errorCode})` (it records the result on the job even without a
parent), and the last step returns that `ChildResult` JSON; the run document records the same
outcome (`status`, `briefId`, `errorCode`). Background AI calls run inside `defer_on_brake`
(the research child of an owner-started pipeline inherits `origin: owner` and is never braked).
No background Responses are used (all calls are `store=false`), so there is nothing to delete
afterwards; if deep mode later uses background runs, record the response id first and DELETE it
on every outcome.

**Schedule.** None.

**AI.** PLAN, RESEARCH (tools: `web_search` only; `fetch_page` is code-driven, not a model
tool), SYNTHESIS, QUICK_SEARCH (`web_search` with the pillar allowlist). All public.

**Settings.** `ResearchSettings`.

**Tests (Phase 3 exit).** A unique marker inside fetched passages never reaches the store, job
documents, events or logs, **including when the scripted RESEARCH reply echoes the passage
(marker included) as a claim**: the stored claim has lost it; quotes <= 200 characters per
source; only allowed URLs are fetched (a URL injected inside a passage is never fetched); Web IQ
down -> egress; both down -> search citations only; blocklist hit -> quarantine + alert; a
blocklisted topic in a pipeline-created run is refused before PLAN; brief references resolve
(the `Brief` validator); quick search persists citations only; parent callback on success and
failure, and a child that finishes before its parent suspends (the parent still gets the
result); a resumed pipeline step finds the run it created (`id_for_pipeline`).

### W-A6 signals (`blogwriter.signals`)

**Purpose.** Sources (feeds and per-pillar scouting queries), conditional crawling through the
egress helper, signal hashes (dedupe only), triage into the ideas inbox. **Feed items never
generate content without an owner action**: these jobs write only `sources`, `signals` and
`ideas`.

**Files.** `signals/__init__.py`, `settings.py`, `feeds.py` (discover, parse), `crawl.py`,
`triage.py`, `ideas.py`, `routes.py`, `jobs.py`, `schedule.py`, `prompts/`; `tests/signals/`.
Reference implementation (rewrite async, do not port bugs): v1
`backend/services/feed_crawler.py` `discover_feed`, `parse_rss_feed`, `_article_id`,
`_filter_by_age` (`:47-151,243-273`).

**Depends on.** `domain`, `ai`, `core.egress` (via `services.egress`), feedparser and
BeautifulSoup in `asyncio.to_thread`.

**Public Python API.**
```python
@dataclass(frozen=True) class FeedItem: url: str; title: str; published_at: str | None; publisher: str | None
def parse_feed(content: bytes, *, base_url: str, max_items: int) -> list[FeedItem]       # sync; call in a thread
def discover_feeds(html: bytes, *, base_url: str) -> list[str]                          # sync; <link rel=alternate>
async def mark_idea_used(store: Store, idea_id: str, *, now: datetime,
                         article_id: str | None = None, post_id: str | None = None) -> Stored[Idea]
async def expire_ideas(store: Store, *, now: datetime) -> int   # time-sensitive ideas past expiresAt
```
(Articles, planner and linkedin posts may also set `idea.status = used` themselves through
`IDEA_MACHINE`; `mark_idea_used` is the convenience.)

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/sources?type=&status=&limit=&cursor=` | | `Page[Source]` | |
| POST | `/api/sources` | `SourceIn{type: feed\|scout_query, title, url? (https), siteUrl?, query?, pillar?}` | 201 `Versioned[Source]` | 422 |
| POST | `/api/sources/discover` | `DiscoverIn{siteUrl (https)}` | 200 `{feeds: [{url, title}]}` (egress GET of the owner-typed site) | 502 `fetch_failed` |
| GET, PUT, DELETE | `/api/sources/{id}` | PUT `SourceIn`, If-Match | `Versioned[Source]` / 204 | 412 |
| POST | `/api/sources/{id}/pause`, `/api/sources/{id}/resume` | If-Match | `Versioned[Source]` | 409 |
| POST | `/api/sources/crawl` | none | 202 `JobAccepted` (`signals.crawl`, origin owner) | |
| GET | `/api/ideas?status=&pillar=&lane=&limit=&cursor=` | | `Page[Idea]` (default: new and accepted, newest first) | |
| POST | `/api/ideas` | `IdeaIn{title, note?, url?, pillar?, suggestedLane?, timeSensitive?}` | 201 `Versioned[Idea]` (origin `quick_capture`; `check_texts` on title and note) | 422 `confidential_text`, 409 `blocklist_not_configured` |
| GET, PUT | `/api/ideas/{id}` | PUT `IdeaEditIn{title?, note?, pillar?, suggestedLane?, suggestedFormat?, angle?}`, If-Match | `Versioned[Idea]` | 412 |
| POST | `/api/ideas/{id}/transition` | `{to: accepted\|snoozed\|dismissed\|new, snoozedUntil?}`, If-Match | `Versioned[Idea]` | 409 `invalid_transition` |
| POST | `/api/ideas/repurpose` | `RepurposeIn{postId, kind: recut\|flagship}` | 201 `Versioned[Idea]` (origin `repurpose`, `repurposeOf`; derived id per post and kind: asking again answers the same idea with 200) | 409 `not_posted`, 404 |

**Jobs.**

| Type | Steps | Notes |
|---|---|---|
| `signals.crawl` | `fetch`: expire old ideas (`expire_ideas`), then for each active feed source: conditional GET through `services.egress` (`If-None-Match`/`If-Modified-Since`, https only), parse, drop items older than `MAX_AGE_DAYS`, create a `Signal` per new item (`Signal.id_for(source.id, url_sha256)`; `Conflict` = seen in this source); update source health (`failing` after `FAILING_AFTER` consecutive errors); checkpoint the new items as `{title, url, publisher, publishedAt}` only. `triage`: TRIAGE in batches inside `defer_on_brake` (pillar, relevance, novelty, lane, format, angle as structured output) -> `Idea` (origin `feed`, id `idea-<urlSha256[:24]>`, `Conflict` = exists, `distinct_stamps` per batch) for relevance >= `MIN_RELEVANCE`; mark signals `triaged`; one `entity.changed` per batch (`idea`, last id) | Skips `triage` with `{"skipped": "paused"}` when autonomy is paused |
| `signals.scout` | `search`: QUICK_SEARCH per pillar (and per active `scout_query` source, after `check_texts` on the query) with `WebSearchTool(allowed_domains=pillar.research_domains)`; candidates are the result citations. `triage`: as above (origin `scout`) | weekly |

**Schedule.** `sched-signals-crawl`: `every(cadence.crawl_interval_hours * 3600)`,
`signals.crawl`, max lateness 6 h. `sched-signals-scout`: `weekly([cadence.scout.day],
cadence.scout.time)`, `signals.scout`, max lateness 12 h.

**AI.** TRIAGE (public, tool-free), QUICK_SEARCH (public, `web_search`).

**Settings.** `SignalsSettings`.

**Tests.** Signals hold hashes only (no title or URL in any `signal` document); dedupe across
runs; conditional GET headers sent and 304 handled; an http feed URL is refused; the crawl and
scout jobs write only `sources`, `signals`, `ideas` (a `ScriptedModelRunner` with only TRIAGE
and QUICK_SEARCH scripts: any other task fails the test); paused autonomy skips triage; feed
parsing goldens (RSS 2.0, Atom, malformed).

### W-A7 articles (`blogwriter.articles`)

**Purpose.** Articles from plan to publication: create, autosave with If-Match, versions (keep
the last N), the article status machine and the who-writes rule, portfolio preview, Markdown
export, clearance attestation.

**Files.** `articles/__init__.py`, `settings.py`, `service.py` (Python API), `versions.py`
(snapshots, pruning), `routes.py`; `tests/articles/`.

**Depends on.** `domain` (`domain.compose`), `publish` (`normalize_markdown`, `render_article`,
`sanitize_html`, `frontmatter`).

`Article.markdown` is the article-dialect **body**; the frontmatter fields live in
`Article.meta`. The canonical document (gates, publish, export) is
`domain.compose.compose_document(meta, body)` and word counts are `domain.compose.word_stats`
(pinned to the publish renderer's count). They live in `domain` because gates (wave A) needs
the exact bytes the publish job ships and may not import articles; articles re-exports both for
convenience.

**Public Python API** (wave B uses these; owner routes use them too).
```python
from blogwriter.domain.compose import compose_document, word_stats   # re-exported
    # compose_document: MetaIncomplete (409 meta_incomplete) / FrontmatterError; golden-tested
async def create_article(store: Store, *, now: datetime, meta: ArticleMeta | None = None,
                         idea_id: str | None = None, week_plan_id: str | None = None,
                         purpose: Literal["flagship", "eval"] = "flagship",
                         article_id: str | None = None) -> Stored[Article]
    # article_id: a derived id (analytics: Article.id_for_eval); a second create raises Conflict
async def owner_save(store: Store, article_id: str, *, if_match: str, base_version: int, meta: ArticleMeta,
                     markdown: str, now: datetime) -> SavedArticle
    # check_article_writer(status, "owner"); normalize (thread); version + 1. Etag stale but
    # current.version == base_version (only system fields changed): apply meta + markdown on top
    # of the current document (CAS loop); a changed version is PreconditionFailed with `current`.
    # ArticleVersion (source autosave) only when autosave_snapshot_due(...) (coalesced: at most one
    # per 10 minutes of typing); prune with versions_to_prune(..., keep=KEEP_VERSIONS,
    # pinned=article.pinned_versions()); returns the stored article and the normalizer's findings
async def pipeline_write(store: Store, article_id: str, *, now: datetime, markdown: str | None = None,
                         meta: ArticleMeta | None = None, served_by: Sequence[ServedByRecord] = (),
                         review_only_reasons: Sequence[str] = (), media: Sequence[MediaRef] | None = None,
                         note: str | None = None) -> Stored[Article]
    # check_article_writer(status, "pipeline") (refused in publishing/unpublishing: writer "publish");
    # CAS loop; version + 1 and an ArticleVersion (source pipeline) when meta/markdown change
class ArticleLinks(ApiModel):   # only the fields set (model_fields_set) are written
    brief_id: str | None = None; research_run_id: str | None = None; interview_id: str | None = None
    pipeline_job_id: str | None = None; week_plan_id: str | None = None
    owner_signals: list[OwnerSignalRef] | None = None; pipeline_runs: int | None = None
async def set_links(store: Store, article_id: str, links: ArticleLinks, *, now: datetime) -> Stored[Article]
    # any non-archived state, any writer (references, never content)
async def transition(store: Store, article_id: str, to: ArticleStatus, *, actor: Literal["owner", "pipeline", "system"],
                     now: datetime, if_match: str | None = None, reason: str | None = None,
                     update: Callable[[Article], Article] | None = None) -> Stored[Article]
    # check_article_transition (machine + live_article + eval_article); with if_match: one replace
    # (owner); without: update_doc (system); `update` applies extra field changes in the same write
    # (publish fields, lastError, veto, calibration). Every transition stores an ArticleVersion
    # (source of the actor) when the content changed since the last one.
@dataclass(frozen=True) class SavedArticle: stored: Stored[Article]; findings: list[Finding]
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/articles?status=&pillar=&purpose=flagship&limit=&cursor=` | | `Page[ArticleSummary]` (`id, status, statusChangedAt, meta{title, slug, pillar, format}, version, wordCount, updatedAt, updatedBy, reviewOnly, gates{stale, autoPublishReady, manualPublishReady, blocking[]}, publish{live, publishAt, url}, ideaId, weekPlanId`) | |
| POST | `/api/articles` | `ArticleIn{meta?, ideaId?, weekPlanId?}` | 201 `Versioned[Article]` (planned; the idea becomes `used`) | 409 |
| GET | `/api/articles/{id}` | | `Versioned[Article]` | 404 |
| PUT | `/api/articles/{id}` | `ArticleSaveIn{meta, markdown (<= 512 KiB UTF-8), baseVersion}`, If-Match | 200 `{item, etag, findings[]}` (normalized Markdown; findings from the normalizer; a stale etag with an unchanged `version` is merged, section 1.4) | 409 `read_only_state`, 412 (content changed; with `current`), 413 `markdown_too_large`, 428 |
| POST | `/api/articles/{id}/transition` | `{to, reason?}`, If-Match | `Versioned[Article]` | 409 `invalid_transition`, 409 `transition_not_allowed_here` |
| GET | `/api/articles/{id}/versions?limit=&cursor=` | | `Page[VersionSummary]` (`version, source, author, statusAtSave, createdAt, contentSha256, note, pinned`; default limit 20) | |
| GET | `/api/articles/{id}/versions/{version}` | | `Versioned[ArticleVersion]` | 404 |
| POST | `/api/articles/{id}/versions/{version}/restore` | If-Match | `Versioned[Article]` (new version, source `restore`; owner states only) | 409, 412 |
| GET | `/api/articles/{id}/preview?version=` | | `text/html` portfolio-like page (publish renderer, sanitized, `PREVIEW_HEADERS`); images and diagrams are inlined as `data:` URIs (the preview CSP allows only `img-src data:`; an image over 2 MB shows its alt text instead) | 404 |
| GET | `/api/articles/{id}/export.md` | | `text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="<slug or id>.md"` | 409 `meta_incomplete` |
| POST | `/api/articles/{id}/clearance` | `{statement?}`, If-Match | `Versioned[Article]` (attests the current `contentSha256`) | 409 `read_only_state` |

Owner transitions allowed through `/transition`: planned -> archived; in_review -> archived;
needs_attention -> in_review, archived; archived -> planned; approved -> in_review (pull back);
published -> in_review (start a correction; the live version stays up); unpublished ->
in_review, archived. Archiving a live article (`publish.live`, for example under correction)
answers 409 `live_article`: unpublish first. Everything else belongs to pipeline (including
in_review -> researching/interviewing through `POST .../pipeline`), gates, publish or planner
and answers 409 `transition_not_allowed_here`.

`clearance.required` is set when `meta.format == "case-study"` or any `ownerSignals` story is
`anonymized` (computed on every save).

**Jobs, schedule, AI.** None.

**Settings.** `ArticlesSettings`.

**Tests.** Who-writes (owner PUT while drafting -> 409; `pipeline_write` while in review or
publishing -> `WriterConflict`); autosave 412 carries `current`; a gates `save_report` or
`set_links` between load and autosave does not 412 (merged), another tab's save does;
version ids deterministic, pruning keeps N plus the pinned versions (gated, gate report, last
published); typing for an hour stores at most 6 autosave versions; the 22 corpus files saved
through `owner_save` come back byte-identical to `contracts/fixtures/corpus/normalized/`;
513 KiB -> 413; export uses `compose_document`; preview headers, inlined media and sanitized
HTML (XSS corpus); allowed transitions only; archiving a live article -> 409 `live_article`.

### W-A8 gates (`blogwriter.gates`)

**Purpose.** The seven gates over one exact article version, the structural slop detectors
(also the `SlopChecker` port for voice, posts and engagement), the standards-ID validator and
its catalog. All seven must pass on their own for auto-publish; any failure sends the article
to review, where the owner may override overridable findings with a reason (manual publish
only).

**Files.** `gates/__init__.py`, `settings.py`, `slop.py`, `standards.py` (+ seed data files),
`rubric.py`, `factcheck.py`, `confidentiality.py`, `originality.py`, `lint.py`,
`ownersignal.py`, `outbound.py`, `run.py`, `routes.py`, `jobs.py`, `schedule.py`,
`lifecycle.py`, `prompts/`; `tests/gates/` (with a positive and a negative corpus per rule).

**Depends on.** `domain` (with `domain.compose` for the exact bytes that ship: gates never
imports articles), `ai`, `publish` (`lint_article`), and the ports `fetcher`, `embedder`,
`render`, `egress`, `classifier`.

**Public Python API.**
```python
class SlopDetectors:                    # satisfies domain.ports.SlopChecker
    def check(self, text: str) -> list[Finding]
    # codes: isnt-x-its-y, rule-of-three, rhetorical-opener, colon-reveal, formulaic-closer,
    # one-sentence-run, flat-rhythm (low sentence-length variation), low-specificity, ai-tell
RUBRIC_DIMENSIONS: Final = ("clarity", "structure", "accuracy", "depth",
                            "first_hand_specificity", "voice", "originality", "actionability")
RUBRIC_OVERALL_MIN = 8; RUBRIC_DIMENSION_MIN = 6; RUBRIC_SPECIFICITY_MIN = 7
async def run_gates(article: Article, *, services: Services, origin: Origin, job_id: str | None = None,
                    gates: Collection[GateName] = GATE_ORDER) -> GateReport
async def save_report(store: Store, article_id: str, report: GateReport, *, now: datetime,
                      embedding: list[float] | None = None) -> Stored[Article] | None
    # writes article.gates (+ embedding, embeddingSha256) only if article.contentSha256 still equals
    # report.contentSha256; returns None when stale (never overwrite a newer version's report).
    # Every GateResult keeps at most 50 findings (domain.common.cap_findings; the rest counted in
    # `truncated`), so the article stays under 2 MB. Never changes version/meta/markdown, so an
    # owner editing in review is not interrupted (section 1.4).
def lint_text(meta: ArticleMeta, body: str, *, brief: Brief | None, catalog: StandardsCatalog,
              pillars: PillarSettings, slop: SlopChecker) -> list[Finding]   # deterministic, no I/O
def validate_standard_ids(text: str, catalog: StandardsCatalog) -> list[Finding]
class GatesLifecycle:   # startup: app.state.slop = SlopDetectors()
```

**Gate rules** (finding `overridable` decides whether the owner may override; hard rules are
`overridable: false`):

| Gate | Checks | Hard |
|---|---|---|
| 1 rubric | CRITIQUE (anonymized, structured output: overall and the 8 dimensions, 0-10): overall >= 8, every dimension >= 6, `first_hand_specificity` >= 7 | none |
| 2 fact_check | every `[^src-id]` exists in the brief; every claim maps to a brief claim/stat or a story metric (FACT_CHECK maps them); each cited URL is re-fetched (`services.fetcher`, `allowed_urls` = the brief's citation URLs; passage in memory only) and the claim verified against it (FACT_CHECK, anonymized, tool-free); unreachable sources are findings | none |
| 3 confidentiality | blocklist on the exact bytes that ship (`compose_document`, alt texts, asset file names; an unconfigured blocklist is the hard `blocklist-not-configured` finding); `services.classifier` (classifier and mosaic check: industry, counts, vendor stack, geography, timeline); employer forbidden topics | blocklist hits |
| 4 originality | verbatim text from any one source <= 200 characters across the piece (against the brief's quotes); slop detectors over threshold; minimum density of specifics; embedding novelty (cosine against the last `NOVELTY_CORPUS` published articles >= `ModelPreferences.noveltyMaxSimilarity` fails); no embedder = finding `novelty-unavailable` | verbatim budget |
| 5 lint | `publish.lint.lint_article(domain.compose.compose_document(meta, body), source_ids=brief citation ids)` errors, converted with `from_publish_finding(f, document, body_start=frontmatter_length(meta))` so spans index the body; links resolve (egress GET, <= `MAX_LINKS_CHECKED`); diagrams render (`render_mermaid_svg` per Mermaid source); standards ids valid (catalog; warn only while the catalog is empty or stale) | dialect errors, diagram failures |
| 6 owner_signal | at least one usable story or POV in `ownerSignals`, or a verbatim confirmed interview answer (>= 40 characters) in the body | none |
| 7 outbound | links only to pillar research domains, allowlisted brief citation domains or the site origin (`BW_GATES_SITE_ORIGIN`); no tracking parameters (`domain.hashing.TRACKING_PARAMETERS`, `utm_*`); no raw HTML, iframes or forms; the clearance attestation is valid for this version when required | all |

A gate that cannot run (service missing, error) is `skipped`/`error`: never a pass.

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/articles/{id}/gates/run` | none | 202 `JobAccepted` (`gates.run`, id `gates_job_id`) | 409 `read_only_state` (pipeline states) |
| GET | `/api/articles/{id}/gates` | | `GatesView{report: GateReport\|null, stale, autoPublishReady, manualPublishReady, blocking[]}` | |
| POST | `/api/articles/{id}/gates/{gate}/override` | `{reason (3-500)}`, If-Match (article etag) | `Versioned[Article]` | 409 `not_overridable`, 409 `report_stale` |
| POST | `/api/gates/lint` | `LintIn{meta, markdown, briefId?}` | `{findings[]}` (`lint_text`, in a thread; no AI, no network) | 413 |
| GET | `/api/gates/standards` | | `Versioned[StandardsCatalog]` | |
| POST | `/api/gates/standards/refresh` | none | 202 | |

**Jobs.** `gates.run` (input `GatesRunInput`; the route enqueues it with `enqueue_once(...,
gates_job_id(...), on_conflict="rerun")`, so a re-run on unchanged content after a skipped or
error result gets `-r<n>`): `deterministic` (lint_text: dialect lint, outbound, owner signal,
blocklist, verbatim budget, slop, standards; **any blocklist finding, `blocklist-not-configured`
included, ends the model work here**: the remaining gates are `skipped`, gate 3 fails, the report
is saved, nothing goes to CRITIQUE, FACT_CHECK or the classifier), `network` (links, diagrams),
`confidentiality`, `fact_check`, `originality`, `rubric` (model steps inside `defer_on_brake`),
`report` (`save_report`; stale -> event `gates.stale`, no write; else `entity.changed` article;
`finish_child` when `parent` is set). Each step checkpoints its `GateResult`. `gates.standards-refresh`:
`refresh` (seed lists shipped with the package plus the optional MITRE ATLAS data file from
`BW_GATES_ATLAS_URL` through egress; never drops seeded ids; failure keeps the old catalog).

**Schedule.** `sched-gates-standards`: `weekly(["sat"], "05:00")`, max lateness 48 h.

**AI.** CRITIQUE (anonymized), FACT_CHECK (anonymized; passages are public data), EMBED through
`services.embedder`.

**Settings.** `GatesSettings`.

**Tests.** A golden positive and negative case for every rule and every slop detector;
blocklist findings never echo the term; a blocklist hit (or an unconfigured blocklist) makes no
model call at all; fact-check passages never stored; stale report discarded; 300 slop findings
store 50 plus `truncated: 250` and keep every non-overridable blocker; no embedder ->
originality fails (never passes); gate 5 lints exactly `compose_document`; `GateReport`
readiness: auto needs all seven passing on their own, manual accepts allowed overrides only;
outbound allowlist and tracking parameters; clearance per version.

### W-A9 linkedin transport (`blogwriter.linkedin.transport`)

**Purpose.** OAuth (top-level redirect), encrypted token storage, the LinkedIn REST client,
document and image upload, the publisher (intents, single attempt, no retries), the ready-to-post
kit states, post edit and delete, the token lifecycle (introspection, banner, alerts).

**Files.** `linkedin/settings.py` (`LinkedInSettings`, the package's one settings class),
`linkedin/transport/__init__.py`, `client.py`, `oauth.py`, `tokens.py`, `upload.py`,
`publisher.py`, `kit.py`, `routes.py`, `jobs.py`, `schedule.py`, `lifecycle.py`;
`tests/linkedin/transport/`. Do not edit `linkedin/littletext.py`, `lint.py` or
`linkedin/__init__.py`.

**Depends on.** `domain`, `core.crypto` (`services.vault`), `linkedin.littletext`, `httpx`,
`services.blobs` (media bytes).

**LinkedIn API** (headers `Authorization: Bearer`, `Linkedin-Version: 202611`,
`X-Restli-Protocol-Version: 2.0.0`; base `https://api.linkedin.com/rest`):
authorize `https://www.linkedin.com/oauth/v2/authorization` (scopes `LINKEDIN_SCOPES`),
token `POST https://www.linkedin.com/oauth/v2/accessToken`, introspect
`POST https://www.linkedin.com/oauth/v2/introspectToken`, userinfo
`GET https://api.linkedin.com/v2/userinfo` (`sub` -> `urn:li:person:<sub>`); documents and
images `POST /documents?action=initializeUpload` / `/images?action=initializeUpload`
(`{"initializeUploadRequest": {"owner": memberUrn}}` -> `uploadUrl`, URN), `PUT uploadUrl`,
`GET /documents/{urn}` / `/images/{urn}` until `AVAILABLE` (fall back to a fixed wait if the
token may not read it); `POST /posts` (201, URN in `x-restli-id`), `POST /posts/{urn}` with
`X-RestLi-Method: PARTIAL_UPDATE` (`{"patch": {"$set": {"commentary": ...}}}`),
`DELETE /posts/{urn}`. Post body: `author`, `commentary` (= `post.payload`), `visibility:
PUBLIC`, `distribution {feedDistribution: MAIN_FEED, targetEntities: [],
thirdPartyDistributionChannels: []}`, `lifecycleState: PUBLISHED`,
`isReshareDisabledByAuthor: false`, and `content`: carousel `{"media": {"title":
documentTitle, "id": documentUrn}}`, image or card `{"media": {"id": imageUrn, "altText":
...}}`, several images `{"multiImage": {"images": [...]}}`, poll `{"poll": ...}`, text none.

**Public Python API** (wave B: linkedin posts, planner).
```python
class LinkedInClient:   # one httpx.AsyncClient, fixed hosts, 20 s timeout, follow_redirects=False,
                        # NOT wrapped by any retry helper; logs status codes and ids only
    async def create_post(self, token: str, body: Mapping[str, object]) -> CreateOutcome   # exactly one attempt
    async def update_commentary(self, token: str, urn: str, commentary: str) -> None
    async def delete_post(self, token: str, urn: str) -> None
    async def initialize_upload(self, token: str, kind: Literal["document", "image"], owner_urn: str) -> tuple[str, str]
    async def upload_bytes(self, upload_url: str, data: bytes, content_type: str) -> None
    async def media_status(self, token: str, kind: Literal["document", "image"], urn: str) -> str
    async def userinfo(self, token: str) -> str                      # member URN
    async def introspect(self, token: str) -> Introspection
@dataclass(frozen=True) class CreateOutcome: status: int | None; urn: str | None; error_code: str | None
class ReconnectRequired(Exception): ...   # no connection, expired, or VaultError -> status needs_reconnect
async def access_token(services: Services) -> tuple[str, str]       # (token, member URN)
async def upload_post_media(services: Services, post_id: str) -> Stored[Post]
    # per media item not yet available: initialize -> record linkedinUrn/linkedinStatus=uploading on the post
    # (CAS) BEFORE the PUT -> PUT bytes -> poll until AVAILABLE (UPLOAD_POLL_SECONDS) -> available; resumable
def post_body(post: Post, member_urn: str) -> dict[str, object]
    # built only from what approval_hash v2 covers (text, title, media, format, poll, link
    # exception), so an approved hash binds the whole body except the author
async def kit_transition(store: Store, post_id: str, to: KitState, *, if_match: str, now: datetime,
                         scheduled_at: str | None = None, post_url: str | None = None,
                         final_text: str | None = None) -> Stored[Post]
def post_url_for(urn: str) -> str        # https://www.linkedin.com/feed/update/<urn>/
```

**The publisher** (`publisher.py`, used by exactly one route):
```python
@dataclass(frozen=True) class OwnerClick: post_id: str; approval_sha256: str
    # constructible only through the dependency below (module-private sentinel)
async def owner_click(post_id: str, body: PublishIn, principal: OwnerPrincipal) -> OwnerClick
    # verifies body.confirmToken: HMAC(key, postId|approvalSha256|issuedAt), age <= TTL, where
    # key = HMAC-SHA256(core cookie key, b"blogwriter/linkedin-confirm-token/v1"): tokens survive
    # a restart and a revision overlap (core uses a random per-process cookie key only outside prod)
def issue_confirm_token(post_id: str, approval_sha256: str, *, now: datetime) -> tuple[str, str]
async def publish_post(services: Services, post_id: str, *, click: OwnerClick, if_match: str) -> Stored[Post]
```
1. Read the post: status `approved` or `staged`, `approval_current`, approval hash equal to the
   click's, connection usable (`access_token`), else 409 (`not_approved`, `approval_stale`,
   `reconnect_required`).
2. CAS (`replace_doc` with the client's If-Match) to `publishing` with `intent{id, createdAt,
   approvalSha256, status: pending}` (one write: the model refuses `publishing` without a
   pending intent). 412 means another tap won: stop.
3. One `create_post` (30 s budget), then one CAS write through
   `domain.posts.settle_publish_intent`: 201 + URN -> `"posted"` (`posted`, intent `succeeded`;
   also set `linkedinUrn`, `postedAt`, `postUrl`, `finalText = displayText`) and enqueue
   `planner.post-live` (`post_live_job_id`, `PostLiveInput`). A definite 4xx -> `"rejected"`
   (back to `approved` with the approval intact, intent `failed`, `lastError`; 401 also sets
   the connection `needs_reconnect`). 5xx, timeout or network error -> `"unknown"`
   (`needs_attention`, intent `unknown`), `emit_alert("needs_attention", post_id)`, push
   "Check LinkedIn: did your post go out?". Never retried.
4. Startup sweep (`TransportLifecycle.startup`): posts in `publishing` whose intent is older than
   2 minutes -> `settle_publish_intent(post, "unknown")` + alert (a restart with an intent but no
   URN).

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/linkedin/connection` | | `ConnectionView{status, memberUrn?, connectedAt?, expiresAt?, tokenDay?, bannerDue, lastIntrospectedAt?, lastError?, scopes}` (never the token) | |
| POST | `/api/linkedin/oauth/start` | none | 200 `{authorizeUrl}` (creates `OAuthState`, 10 min); the SPA navigates top-level | 503 `linkedin_not_configured` |
| GET | `/api/linkedin/oauth/callback?code&state` | | 303 `Location: /settings/integrations` always; reflects nothing; consumes the state once (delete with If-Match, `expiresAt` checked), exchanges the code, stores the vault-encrypted token (context = document id) | (errors land in `connection.lastError`) |
| POST | `/api/linkedin/disconnect` | none | `ConnectionView` | |
| POST | `/api/linkedin/introspect` | none | `ConnectionView` | 503 |
| GET | `/api/posts/{id}/go-live` | | `GoLiveView{post: Versioned[Post], foldIndex, cover: MediaRef\|null, documentTitle, altTexts[], approvalSha256, approvalCurrent, connection{status, tokenDay}, confirmToken, confirmExpiresAt, canPublish, reasons[]}` (no side effects) | 404 |
| POST | `/api/posts/{id}/publish` | `PublishIn{approvalSha256, confirmToken}`, If-Match | 200 `Versioned[Post]`: the attempt was made, read `status` (`posted`, `needs_attention`, or `approved` with `lastError`) | 409 `confirm_invalid`, `not_approved`, `approval_stale`, `reconnect_required`; 412 |
| POST | `/api/posts/{id}/mode` | `{mode: one_click\|kit}`, If-Match | `Versioned[Post]` (approved/staged -> kit with `kit.state = prepared`; kit (prepared) -> approved) | 409 |
| GET | `/api/posts/{id}/kit` | | `KitView{displayText, documentTitle, altTexts[], downloads[{media, url, filename}], checklist[4], openLinkedInUrl, state, scheduledAt}` | |
| POST | `/api/posts/{id}/kit` | `KitIn{to: native_draft_saved\|natively_scheduled\|posted, scheduledAt?, postUrl?, finalText?}`, If-Match (`natively_scheduled` needs `scheduledAt`, again = new T; `posted` needs `postUrl` and `finalText`, prefilled with the display text) | `Versioned[Post]`; scheduled -> enqueue `planner.post-scheduled`; posted -> post `posted` and enqueue `planner.post-live` | 409, 422 |
| POST | `/api/posts/{id}/resolve` | `{outcome: posted\|not_posted, postUrl?}`, If-Match | `Versioned[Post]` (`needs_attention` -> `posted` or `approved`) | 409 |
| PATCH | `/api/posts/{id}/linkedin` | `{displayText, confirmToken}`, If-Match (lint must pass; PARTIAL_UPDATE of the commentary) | `Versioned[Post]` (`finalText`, `payload` updated) | 409 |
| POST | `/api/posts/{id}/linkedin/delete` | `{confirmToken}`, If-Match | `Versioned[Post]` (`deleted`) | 409 |

**Jobs.** `linkedin.introspect`: `introspect` (introspect, update `introspectionActive`,
`expiresAt`), `alerts` (banner from day 50; for each due day 53/57/59 not yet in `alertsSent`:
push + `emit_alert("needs_attention", "linkedin-connection")`; also warn 60 days before the API
version sunset constant). Hooks enqueued here (registered by planner):
`planner.post-live`, `planner.post-scheduled`.

**Schedule.** `sched-linkedin-introspect`: `daily("08:30")`, max lateness 24 h.

**AI.** None.

**Settings.** `LinkedInSettings`.

**Tests (invariants).** The publisher never retries (a mocked transport counts exactly one POST
for 500, timeout and connection reset); nothing but the publish route reaches `publish_post`
(an import-graph test over `blogwriter.*`: no job module, schedule provider or registered step
references it; `OwnerClick` cannot be built directly); scopes are the constant; the callback
reflects nothing (constant `Location`, no query echo) and tokens are never logged or returned;
a vault decrypt failure -> `needs_reconnect`; kit text equals the display text; the startup
sweep; upload URNs recorded before polling and re-attached on resume; a 4xx answer leaves the
post `approved` with intent `failed` (and never a second POST); a confirm token issued before a
restart still verifies after it (same `BW_COOKIE_KEY`); connection writes use
`docstore.update_singleton`.

### W-A10 backups (`blogwriter.backups`)

**Purpose.** Daily gzip JSONL dumps of the durable containers to Blob `backups`, a manifest,
status for System, owner-attested restore drills.

**Files.** `backups/__init__.py`, `settings.py`, `dump.py`, `routes.py`, `jobs.py`,
`schedule.py`; `tests/backups/`.

**Depends on.** `domain`, `services.blobs`.

**Public Python API.**
```python
async def dump_containers(store: Store, blobs: BlobStore, *, date: str, now: datetime,
                          containers: Sequence[str] = BACKUP_CONTAINERS, page_size: int = 200,
                          done: Collection[str] = ()) -> list[BackupFile]
    # keyset pages per container: Filter("id", ">", last) + OrderBy("id"), limit page_size (ids are
    # unique per container, not just per partition: Signal ids include the source id); containers
    # in BACKUP_KINDS add Filter("kind", "in", [...]) (engagement: records and watch entries only);
    # bodies only (no etags), sorted by id, one JSON object per line, gzip;
    # blob "<date>/<container>.jsonl.gz" (overwrite=True); sha256 of the gzip bytes
def restore_drill_due(status: BackupStatus, *, now: datetime) -> bool   # > 31 days since the last drill
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/backups` | | `BackupsView{status: Versioned[BackupStatus], dumps: [{date, files[]}] (last 35 days), restoreDrillDue}` | 503 `storage_unavailable` |
| POST | `/api/backups/run` | none | 202 | 503 |
| GET | `/api/backups/{date}/{container}` | | `application/gzip` download, `Content-Disposition: attachment` | 404 |
| POST | `/api/backups/restore-drill` | none | `Versioned[BackupStatus]` (`lastRestoreDrillAt = now`, the owner attests the `RESTORE.md` drill) | |

**Jobs.** `backups.dump`: `dump` (checkpoint after each container), `manifest`
(`<date>/manifest.json` = `BackupManifest`; `BackupStatus` updated; failure ->
`consecutiveFailures` + 1 and `emit_alert("job_failed", job_id)`).

**Schedule.** `sched-backups-dump`: `daily(cadence.backup_time)`, max lateness 12 h.

**AI.** None. **Settings.** `BackupsSettings`.

**Tests.** Every durable container dumped, `jobs`/`ephemeral` never, `engagement` only for
`engagement_record` and `watch_entry` (no pasted third-party text, no reminders); pagination
over more than one page, with signals of two sources for the same URL across a page boundary;
deterministic output (sorted by id); manifest hashes match; a re-run the same day overwrites;
storage missing -> `JobFailed("storage_unavailable")`; `BackupStatus` through
`update_singleton`.

---

## 4. Work orders, wave B

Wave B starts after wave A is merged. It may use wave A's public Python APIs. Wave B packages
still do not import each other: they meet through documents, job types and ports.

### W-B1 pipeline (`blogwriter.pipeline`)

**Purpose.** The article pipeline: research (chained), interview questions and the interview
wait, outline, draft (streamed), canonical normalization, rubric critique and revise loops
(at most 2), gates, draft assets, calibration flags; owner-requested revisions and selection AI.

**Files.** `pipeline/__init__.py`, `settings.py`, `steps.py`, `interviews.py`, `prompts/`,
`routes.py`, `jobs.py`; `tests/pipeline/`.

**Depends on.** `domain` (`compose`, `slots`, `jobflow`), `ai`, `publish` (normalize, lint),
articles (`pipeline_write`, `set_links`, `transition`), research (`create_run`, `start_run`),
stories (`load_bank`, `story_gaps`, `local_findings`), voice (`load_active_guide`,
`guide_prompt`, `add_sample`), gates (`run_gates`, `save_report`).

**Public Python API.**
```python
async def generate_interview(services: Services, article: Article, *, pipeline_run: int, origin: Origin,
                             job_id: str | None) -> Stored[Interview]
    # id Interview.id_for_pipeline(article.id, pipeline_run): a Conflict returns the existing
    # interview and pushes nothing (a resumed step never asks twice)
def interview_mode(pack: FormatPack) -> Literal["micro", "full"]
def drafting_request(*, guide: StyleGuide, brief: Brief, bank: StoryBank, interview: Interview | None,
                     outline: Outline, pack: FormatPack, pillar: Pillar) -> Request
    # cached prefix (cache_breakpoint=True): style guide + brief + stories; Sensitivity.ANONYMIZED;
    # the drafter only ever receives the brief (never a source passage)
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/articles/{id}/pipeline` | `{startFrom: research\|interview\|draft}`, If-Match | 202 `{article: Versioned[Article], jobId}` (from planned, in_review or needs_attention: article -> researching / interviewing / drafting; job id `pipeline_job_id(articleId, pipelineRuns + 1)`, origin owner. If `pipelineJobId` names a queued job (the start planned by plan approval), it is cancelled and this run replaces it) | 409 `invalid_transition`, `pipeline_active` (a running or waiting run), `voice_guide_missing`, `ai_use_not_confirmed`, `brief_missing` (startFrom draft without a brief) |
| POST | `/api/articles/{id}/pipeline/cancel` | If-Match | `Versioned[Article]` (job cancelled, or already finished, failed or quarantined: a terminal job is fine; article -> needs_attention, `lastError: cancelled`) | |
| POST | `/api/articles/{id}/revise` | `{instruction (1-1000)}`, If-Match | 202 (`check_texts` on the instruction, `require_ai_use`; in_review -> drafting; `pipeline.revise`) | 409, 422 `confidential_text` |
| POST | `/api/articles/{id}/selection` | `{text (1-8000), instruction (1-500)}` | 200 `{proposal}` (`check_texts` on text and instruction, `require_ai_use`; REVISE, owner origin, nothing saved; the editor shows a jsdiff popover and applies it through autosave) | 409 `ai_use_not_confirmed`, `blocklist_not_configured`; 422 `confidential_text`; 503 |
| GET | `/api/interviews?articleId=&postId=&status=&limit=&cursor=` | | `Page[Interview]` | |
| GET | `/api/interviews/{id}` | | `Versioned[Interview]` | |
| POST | `/api/interviews/{id}/answers` | `{answers: [{questionId, text (1-4000), redactionFindings}]}` (confirmed through stories intake), If-Match | `Versioned[Interview]` (pending or expired -> answered; each answer also becomes a voice sample `interview_answer`; then `provide_input(jobId, {"interviewId": id}, key="interview")`, ignoring `JobStateError` when the pipeline already went on after expiry) | 422 `confidential_text`, 409 |
| POST | `/api/interviews/{id}/skip` | If-Match | `Versioned[Interview]` (skipped; `provide_input(..., {"interviewId": id, "skipped": true})`) | |

**Jobs.**

`pipeline.article` (input `PipelineInput`):

| Step | What it does |
|---|---|
| `research` | A planned start (article still `planned`) moves it to researching first. Skip when `briefId` is set. Else `create_run(run_id=ResearchRun.id_for_pipeline(articleId, n))` (blocklist-checked; `Conflict` = the run this step already made) + `start_run(parent=ParentRef(job_id, "research"), origin=<this job's origin>)`, then `wait_for_child(ctx, jobs, research_job_id(runId), key="research", prompt="Research is running.")` (also on every resume); failure -> article needs_attention, `JobFailed("research_failed")`; success -> `set_links(briefId, researchRunId)` |
| `interview_questions` | Skip when `interview` is `skip` in the input or an answered interview exists. INTERVIEW (anonymized: brief + story gaps; never private text) -> `Interview` (id `Interview.id_for_pipeline(articleId, n)`, pending, `dueAt` = cadence interview due), enqueue `pipeline.interview-expire` (`interview_expire_job_id`, `run_after = dueAt`), article -> interviewing, push "<n> quick questions" -> `/studio/<id>/interview` (only when the create did not hit `Conflict`) |
| `interview` | `wait_for_input("Answer the interview.", key="interview")`; value `{"interviewId", "skipped"?, "expired"?}`; record `ownerSignals` for answers (none when skipped or expired: drafting goes on with the story bank, and gate 6 decides) |
| `outline` | article -> drafting. OUTLINE (anonymized) -> outline (structured output, checkpoint) |
| `draft` | DRAFT streamed (`ctx.delta`), cached prefix; on any of `ai.DISCARD_EVENTS` emit `DRAFT_RESET`; `pipeline_write(markdown, servedBy, reviewOnlyReasons=review_only_reasons(result))`; refusal with no fallback -> needs_attention `refused` |
| `normalize` | `normalize_markdown` (thread); errors kept as findings; write the normalized body |
| `critique_revise` | up to `MAX_REVISE_LOOPS`: CRITIQUE (rubric) -> below thresholds -> REVISE -> normalize -> write (loop count checkpointed; `DRAFT_RESET` on discards as in `draft`) |
| `gates` | article -> gating; `run_gates` + `save_report` |
| `assets` | Mermaid sources -> `render_mermaid_svg`/`png`, OG (`render_og_image`, 1200x630) -> blobs `articles/<id>/<sha>.<ext>` -> `article.media`. The hero is the owner's uploaded `hero_image` media when there is one, else the OG PNG (role `hero_image`, alt text = title): the same rule as the publish job |
| `finalize` | `CalibrationFlags{gatedSha256, gatedVersion, gatesPassed, reviewOnly}`. Auto path only if the article is not `purpose=eval`, `report.auto_publish_ready`, not review-only, `auto_publish_decision(...).allowed`, clearance valid: article -> approved with `publish.publishAt = slots.next_article_publish(cadence, after=now, min_lead=timedelta(hours=cadence.min_veto_lead_hours)).utc` and `publish.auto = true`, and enqueue `publish.article` (`enqueue_once(..., publish_job_id(articleId, version), on_conflict="rerun")`, `PublishInput(publish_at=..., auto=True)`, `run_after = publishAt`). Otherwise -> in_review and push "Your article is ready for review". With `parent` set (evals): `finish_child` with `ref = articleId` |

`pipeline.revise` (input `ReviseInput`): `revise` (REVISE with the instruction and the brief;
the route already checked the instruction), `normalize`, `gates`, `finalize` (-> in_review).

`pipeline.interview-expire` (input `InterviewInput`, id `interview_expire_job_id(interviewId)`,
`run_after = dueAt`): `expire` (interview pending -> expired and
`provide_input(jobId, {"interviewId": id, "expired": true}, key="interview")`, ignoring
`JobStateError`; answered or skipped: no-op). Late answers are still accepted (expired ->
answered) and become story material and voice samples.

Background-origin AI calls (a planned start runs as `owner`: plan approval is an owner action)
run inside `defer_on_brake`.

**Schedule.** None. Pipelines start from owner actions: the pipeline route, or plan approval
(planner enqueues the flagship's `pipeline.article` for `cadence.research_start` of the plan's
week, origin owner, so research runs Mon-Tue, the interview is due Wed and drafting follows the
answers or the Wed expiry).

**AI.** INTERVIEW, OUTLINE, DRAFT, REVISE, CRITIQUE (all anonymized, tool-free) plus the gates'
tasks.

**Settings.** `PipelineSettings`.

**Tests.** The DRAFT request contains the brief and never a passage marker; no private text in
any request but ANONYMIZE; fallback or refusal -> review-only (`review_only_reasons`); a
blocklisted revise or selection instruction never reaches a model; the interview wait frees the
runner slot and resumes on answers; expiry resumes it without answers; research chaining
(success, failure, and a research child that finishes before the pipeline suspends); a resumed
`research` or `interview_questions` step reuses the run and the interview (no second push);
`DRAFT_RESET` on a scripted fallback; who-writes; e2e with fakes: idea -> research ->
micro-interview -> draft -> gates -> in_review (the full chain is I-6).

### W-B2 publish job (`blogwriter.publish`, job part)

**Purpose.** Approve and schedule, publish (render, assets to Blob, contract-validated upsert,
portfolio dispatch, build wait, live verification), unpublish, corrections and slug renames,
staging previews; calibration outcome on approval.

**Files.** Add `publish/settings.py` (`PublishSettings`, absorbing `ContractSettings` with the
same `BW_PUBLISH_CONTRACTS_DIR`), `publish/document.py` (published-blogs v2 document builder),
`publish/assets.py`, `publish/portfolio.py` (GitHub client), `publish/jobs.py`,
`publish/routes.py`; `tests/publish/job/`. Do not change the renderer modules or `contract.py`
beyond pointing `ContractSettings` at the new class.

**Depends on.** `domain` (`compose`, `slots`, `jobflow`), articles (API), `services.blobs`,
`services.render`, `services.egress`, `httpx` (GitHub API with the PAT).

**Public Python API.**
```python
def build_published_document(article: Article, rendered: RenderedArticle, assets: Sequence[PublishedAsset], *,
                             now: datetime, site_origin: str) -> dict[str, object]   # validate before upsert
def redirect_stub(old_slug: str, new_slug: str, *, now: datetime) -> dict[str, object]
class PortfolioClient:
    async def dispatch(self, workflow: str, *, ref: str, correlation_id: str) -> None   # inputs.correlation_id
    async def find_run(self, workflow: str, correlation_id: str) -> int | None          # by run name
    async def run_status(self, run_id: int) -> tuple[str, str | None]                   # status, conclusion
def classify_review(article: Article, gated_body: str, approved_body: str) -> CalibrationOutcome
    # gated_body: the ArticleVersion of article.calibration.gatedVersion (pinned, never pruned)
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/articles/{id}/approve` | `{publishAt?, correctionNote?, slug?}`, If-Match | 200 `{article: Versioned[Article], jobId}`: in_review -> approved, `publish.publishAt` (default: `slots.next_article_publish(cadence, after=now)`), `publish.auto = false`, calibration recorded (`record_review` through `update_singleton`, `CalibrationFlags.outcome`), `publish.article` enqueued with `enqueue_once(..., publish_job_id(articleId, version), on_conflict="rerun")` (a re-approval after a veto or cancel gets `-r<n>`), `PublishInput(publish_at=publishAt)`, `run_after = publishAt` | 409 `gates_blocking{blocking[]}`, `meta_incomplete{missing[]}`, `clearance_required`, `correction_note_required` (live article), `slug_taken` (a `published-blogs/<slug>` document exists and `not article.owns_slug(slug)`), `report_stale`, `eval_article` |
| POST | `/api/articles/{id}/publish-now` | If-Match | as approve with `publishAt = now`, from in_review or approved (`on_conflict="replace"` cancels the scheduled job) | as approve |
| POST | `/api/articles/{id}/unpublish` | If-Match | 202 `{jobId}` (published or publish_failed after a failed unpublish) | 409 |
| POST | `/api/articles/{id}/staging-preview` | none | 202 `{jobId}` | 503 `portfolio_not_configured`, 409 `slug_taken` |
| GET | `/api/articles/{id}/publish-status` | | `{status, publish: PublishState, job: {id, status, steps[]}\|null}` | |

Calibration on approval: `clean` when the report passed on its own, the draft is not
review-only and `is_substantive_edit(gated body, approved body)` is false (the gated body is the
pinned `calibration.gatedVersion`); `edited`, `gates_failed`, `review_only` otherwise; a
correction of a live article calls `record_correction` (auto-publish turns itself off). While
the Embedder is not served (I-3) gate 4 never passes, so no review is `clean` and the streak
stays at 0: auto-publish cannot turn on before embeddings.

**Jobs.**

| Type | Steps |
|---|---|
| `publish.article` (`PublishInput`) | `check` (article approved and `version` matches; started more than `PUBLISH_MAX_LATENESS` after `publishAt` -> hold: approved -> in_review, `lastError: publish_late`, alert and push, the owner re-approves; auto path (`publish.auto`): not paused, `veto.delivered` true and not vetoed, else -> in_review and alert; point-read `published-blogs/<slug>`: another document there and `not article.owns_slug(slug)` -> `JobFailed("slug_taken")`; article -> publishing with `publish.lastOperation = publish`. From here the writer is `publish`: only publish fields, through `articles.transition(update=...)`), `render` (`compose_document`, `render_article`, diagrams via `render_mermaid_svg` to `published/<slug>/diagram-<id>.svg`, OG PNG, hero = the owner's `hero_image` media or else the OG PNG (alt = title); content-hashed names; build and `validate_published_doc` in a thread), `upsert` (`published-blogs`; redirect stub for a renamed slug; dated correction note), `dispatch` (record `correlation` remote id first, then dispatch; find the run id and record it), `wait_build` (poll until concluded; failure -> `JobFailed("build_failed")`), `verify` (egress GET `<site>/blog/<slug>` until the page carries the new version: `has_published_marker(page, <upserted updatedAt>)`, `VERIFY_TIMEOUT_SECONDS`; a 200 with the old page is not enough), `finalize` (published, `publish.*` fields, `entity.changed`, push "Published: <title>"). Any failure: article -> publish_failed + `emit_alert("publish_failed", id)` |
| `publish.unpublish` (`ArticleJobInput`) | `upsert` (tombstone `status: unpublished`, slug reserved; `publish.lastOperation = unpublish`), `dispatch`, `wait_build`, `verify` (the page no longer renders: 404 or gone), `finalize` (unpublished). Failure -> publish_failed; the owner retries it (publish_failed -> unpublishing) |
| `publish.staging` (`ArticleJobInput`) | `render`, `upsert` (`published-blogs-staging`; the same `slug_taken` check), `dispatch` (`BW_PUBLISH_PREVIEW_WORKFLOW`) |

**Schedule.** None (publishes are delayed jobs).

**AI.** None.

**Settings.** `PublishSettings`.

**Tests.** Only validated documents are written; `publishedAt` never in the future; published
only after verification, and a republish is not "verified" by the old page; remote ids recorded
before awaiting and re-attached on resume (a crash between dispatch and run lookup does not
dispatch twice); auto path needs `publish.auto`, the veto receipt and the autonomy decision;
paused autonomy holds; a job claimed 2 days late holds instead of publishing; a slug owned by
another document is refused on the auto path too; `pipeline_write` during publishing is
refused; a failed unpublish can be retried; redirect stubs; correction notes dated; alerts on
failure; calibration outcomes against the pinned gated version.

### W-B3 linkedin posts (`blogwriter.linkedin.posts`)

**Purpose.** Post derivation (at most 2 derivatives per flagship), native posts from the story
and POV bank, quick takes, hooks (3 variants), the little-text serializer and lint, derived-claim
and near-duplicate checks, approval bound to the SHA-256 of text plus media, carousel specs and
renders, staging at T-60, expiry of time-sensitive posts.

**Files.** `linkedin/posts/__init__.py`, `derive.py`, `serialize.py`, `checks.py`,
`carousel.py`, `staging.py`, `routes.py`, `jobs.py`, `prompts/`; `tests/linkedin/posts/`.

**Depends on.** `domain`, `ai`, `linkedin.littletext`, `linkedin.lint`, linkedin transport
(`upload_post_media`, `access_token`, `ReconnectRequired`), stories (`load_bank`), voice
(`guide_prompt`), research (briefs and quick searches through the store), `services.render`,
`services.blobs`.

**Public Python API.**
```python
def serialize(display_text: str, approved_hashtags: Sequence[str]) -> tuple[str, str]   # (display, payload)
def lint_post_text(post: Post, *, hashtags: Sequence[str], blocklist: Blocklist, slop: SlopChecker,
                   catalog: StandardsCatalog, pillars: PillarSettings) -> list[Finding]
    # display text AND every carousel slide's text: linkedin.lint (from_linkedin_finding), AI tells
    # and bait, blocklist (an unconfigured list is the hard blocklist-not-configured block),
    # gates.validate_standard_ids (catalog), pillar renames (deprecated names), slop
def near_duplicate(text: str, corpus: Sequence[tuple[str, str]]) -> tuple[float, str | None]  # word 5-gram Jaccard
async def check_post(services: Services, post: Post, *, origin: Origin, job_id: str | None) -> Post
    # lint, derived claims (numbers exact vs article/brief/story; FACT_CHECK for claims), near-dup (90 days)
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/posts?status=&lane=&pillar=&articleId=&weekPlanId=&from=&to=&limit=&cursor=` | | `Page[PostSummary]` (`id, status, lane, pillar, format, hook (first line), slot, goLiveMode, kitState, approvalCurrent, postedAt, reviewOnly`) | |
| POST | `/api/posts` | `PostCreateIn{lane: native\|quick_take\|repurpose, pillar, format, storyIds?, povIds?, ideaId?, quickSearchId?, takeText? (quick take; confirmed through intake, `check_texts`), slot?: SlotIn, goLiveMode?}` | 202 `{post: Versioned[Post], jobId}` (generating; `posts.generate`) | 409 `ai_use_not_confirmed`, `blocklist_not_configured`; 422 `confidential_text` |
| POST | `/api/articles/{id}/posts/derive` | none | 202 `{jobId}` (`posts.derive`) | 409 (no published or approved article) |
| GET | `/api/posts/{id}` | | `Versioned[Post]` | |
| PUT | `/api/posts/{id}` | `PostEditIn{displayText, selectedHookId?, documentTitle?, hashtags?, format?, pillar?, attributions?, linkException?{url, reason}}`, If-Match (approved/staged -> draft first: the approval is invalidated) | `Versioned[Post]` (payload re-serialized, lint re-run) | 409 `read_only_state` (publishing, posted, kit) |
| POST | `/api/posts/{id}/lint` | `{displayText}` | `{findings[], payloadUnits, foldIndex}` (deterministic, for the live composer) | |
| POST | `/api/posts/{id}/check` | none | 202 (`posts.check`) | |
| PUT | `/api/posts/{id}/carousel` | `{spec}`, If-Match | `Versioned[Post]` (render lint findings; media invalidated) | 422 `invalid_spec` |
| GET | `/api/posts/{id}/carousel/preview` | | `text/html` (`carousel_preview_html`, `PREVIEW_HEADERS`) | 404 |
| POST | `/api/posts/{id}/render` | none | 202 (`posts.render`) | 503 |
| POST | `/api/posts/{id}/regenerate` | `{what: hooks\|text\|carousel, instruction?}`, If-Match | 202 (`check_texts` on the instruction) | 422 `confidential_text` |
| POST | `/api/posts/{id}/approve` | `{slot?: SlotIn}`, If-Match | `Versioned[Post]` (approval hash v2; one-click with a slot: enqueue `posts.stage` at slot - `stageLeadMinutes` and `planner.slot-check` at slot + grace, both with `enqueue_once(..., on_conflict="rerun")` so a re-approval of the same slot after unapprove works) | 409 `checks_failing{failing[]}`, `media_missing`, `slot_conflict` (`domain.slots.conflicting_slots` against the slots of approved, staged and natively scheduled posts, read with `strict=True`), `payload_mismatch`, `blocklist_not_configured` |
| POST | `/api/posts/{id}/unapprove` | If-Match | `Versioned[Post]` (draft; cancels its `posts.stage` and `planner.slot-check` jobs) | |
| POST | `/api/posts/{id}/archive` | If-Match | `Versioned[Post]` | |

**Jobs.** `posts.derive` (`ArticleJobInput`): `derive` (DRAFT: up to
`lanes.derivatives_max_per_week` posts with ids `Post.id_for_derivative(articleId, version, n)`
(`Conflict` = already drafted), 3 hooks each, formats, carousel specs; `generating` -> `draft`),
`check`. `posts.generate` (`PostJobInput`): `generate`, `check`. `posts.check`:
`check`. `posts.render`: `render` (carousel PDF and page PNGs or the image card -> blobs
`posts/<id>/<sha>.<ext>`; PDF metadata Title/Author only, text layer scanned against the
blocklist), `save` (media). `posts.stage` (`SlotJobInput`, `stage_job_id`, `run_after` slot -
60 min): no-op if the slot or approval changed, or if it starts more than `STAGE_MAX_LATENESS`
after slot minus the stage lead (downtime: the slot check offers the next free slot); `render`
if media missing, `upload`
(`upload_post_media`), `token` (`access_token`), `ready` (staged; push "Post at HH:MM: tap
to publish" -> `/studio/<piece>/go-live?post=<id>`); failure -> `emit_alert("needs_attention",
post_id)` + push. `posts.expire` (`PostJobInput`, `run_after = expiresAt`): -> expired unless
posted.

**Schedule.** None (delayed jobs only).

**AI.** DRAFT (derivation, hooks, carousel copy; anonymized), REVISE (regenerate with an
instruction), FACT_CHECK (derived claims).

**Tests (invariants).** `payload == api_commentary(parse_display(display, approved))` for every
saved post; kit text equals the display text; approval invalidated by any edit of text, title,
media or alt text; default prompts produce posts with 0 links (scripted output with a link is
blocked by lint); near-duplicate against 90 days; derived numbers must match exactly; staging is
a no-op after a re-slot and after downtime past its lateness; PDF metadata and text-layer
checks; slide text goes through the same lint as the display text; the approval hash changes
with the format, the poll and the link exception (v2); an unconfigured blocklist blocks
approval.

### W-B4 planner (`blogwriter.planner`)

**Purpose.** The weekly production calendar: week plans and their approval, slots and DST math,
modes (normal, light, away), lane and mix rules, spacing, the missed-slot policy, reminders, the
veto notice, the weekly scorecard, and the Today inbox.

**Files.** `planner/__init__.py`, `slots.py`, `plan.py`, `today.py`, `reminders.py`,
`veto.py`, `scorecard.py`, `routes.py`, `jobs.py`, `schedule.py`, `prompts/`;
`tests/planner/` (calendar goldens).

**Depends on.** `domain` (`slots`, `jobflow`), `ai`, articles (`create_article`, `transition`,
`set_links`), signals (`mark_idea_used`), `services.notifier`.

**Public Python API.** The slot math other packages need lives in `domain.slots`
(`week_slots`, `spacing_findings`, `conflicting_slots`, `next_free_slot`,
`next_article_publish`, `weekly_slot`, `posts_for_mode`; DST goldens in
`tests/domain/test_slots.py`). Planner adds:
```python
def reminders_for_go_live(post: Post, cadence: CadenceSettings, go_live_at: str, *, now: datetime) -> list[Reminder]
    # reply +15/+60/+180 min (expiresAt = dueAt + 2 h), snapshot +48 h/+7 d (expiresAt = dueAt + 3 d);
    # key "<kind>:<postId>:<offset>", id Reminder.id_for(key) (Conflict = already created)
```

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/today` | | `Today{generatedAt, week{weekStart, mode, planStatus, scorecard?}, items: [TodayItem]}` | |
| GET | `/api/plan/weeks?from=&limit=` | | `Page[WeekPlan]` | |
| GET | `/api/plan/weeks/{weekStart}` | | `Versioned[WeekPlan]` | 404 |
| POST | `/api/plan/weeks/{weekStart}/generate` | none | 202 (`planner.plan-week`, origin owner) | 409 (approved plan) |
| PUT | `/api/plan/weeks/{weekStart}` | `PlanEditIn{mode?, flagship?, slots: [{id, localDate, localTime, postId?, lane?, pillar?, format?, title?}]}`, If-Match (drag and drop) | `Versioned[WeekPlan]` (UTC recomputed, warnings) | 409 |
| PUT | `/api/plan/weeks/{weekStart}/mode` | `{mode}`, `SingletonPrecondition`-style (If-Match, or If-None-Match: * to create) | `Versioned[WeekPlan]` | |
| POST | `/api/plan/weeks/{weekStart}/approve` | If-Match | `{plan: Versioned[WeekPlan], articleId?, jobIds[]}`. An owner action, so everything it starts runs as `origin: owner`: creates the flagship article (planned, `set_links(pipelineJobId, pipelineRuns=1)`) and enqueues its `pipeline.article` (`pipeline_job_id(articleId, 1)`, `run_after = parse_iso(slots.weekly_slot(weekStart, cadence.research_start, cadence.timezone).utc)`); creates the native slots' posts (`Post.id_for_plan_slot(planId, slotId)`, generating) and enqueues `posts.generate` for them. Quick-take slots generate nothing: they become `take_needed` Today items ("What's your take?"; the owner's take starts the post). Derivative slots wait for the flagship (`posts.derive` is an owner action) | 409 `slot_conflict`, `invalid_transition` |
| POST | `/api/posts/{id}/reslot` | `{action: snooze\|next_slot\|set, slot?}`, If-Match | `Versioned[Post]` (staged -> approved, new `posts.stage` and `planner.slot-check`) | 409 `slot_conflict` |
| GET | `/api/reminders?status=pending` | | `Page[Reminder]` | |
| POST | `/api/reminders/{id}/dismiss` | none | `Versioned[Reminder]` (cancelled) | |
| GET | `/api/articles/{id}/veto` | | `{publishAt, auto, notice: VetoNotice\|null}` | |
| POST | `/api/articles/{id}/veto` | If-Match | `Versioned[Article]` (approved -> in_review, publish job cancelled, `veto.vetoedAt`, `calibration.outcome = vetoed`; `autonomy.calibration` through `update_singleton(record_review(outcome="vetoed"))`: the streak restarts) | 409 |
| GET | `/api/plan/scorecard?weeks=8` | | `[{weekStart, scorecard}]` | |

`TodayItem{id, kind, title (<= 80), href, dueAt?, priority, refs{articleId?, postId?, interviewId?, scheduleId?}}`,
kinds: `approve_plan`, `answer_interview` (pending or expired interviews), `review_article`
(also `lastError: publish_late`: "publish time passed, approve again"), `veto_window`,
`approve_post`, `take_needed` (a quick-take slot waiting for the owner's take),
`publish_post` (slot within 2 h), `kit_confirm`, `missed_slot`, `reply` (reminder due),
`snapshot` (snapshot form due), `needs_attention` (article, post or job), `schedule_confirm`
(a schedule item waiting for confirmation), `reconnect_linkedin` (banner day reached),
`owner_input` (from kb's owner inputs), `restore_drill` (`backups.restore_drill_due`). Today is
computed from documents on each request; only `reply`, `snapshot` and `kit_confirm` are stored
reminders (other prompts are direct pushes by the job that knows: plan ready, veto notice,
token expiry, eval drafts).

**Jobs.**

| Type | Input | Steps |
|---|---|---|
| `planner.plan-week` | `{weekStart}` (default: next week) | `collect` (ideas, story/POV coverage, last weeks, mode), `propose` (PLAN, anonymized: flagship and post mix; skipped when paused), `slots` (deterministic: `domain.slots.week_slots`, lane rules, >= 80% on-pillar, `spacing_findings`), `save` (`WeekPlan` proposed), `notify` (push "Your plan for next week is ready" -> `/calendar/plan`). Generates no content |
| `planner.veto-notice` | `{}` | `notify`: for approved articles with `publish.auto` and `publishAt` in the next window (`find_docs(..., strict=True)`): push title + link only; `veto.sentAt`, `delivered` = receipt; no receipt -> `emit_alert("needs_attention", articleId)`, `delivered: false` (publish holds) |
| `planner.remind` | `RemindInput` | `send` (pending and not past `expiresAt`, else -> expired; snapshot reminders skip when the snapshot exists; push; sent) |
| `planner.post-live` | `PostLiveInput` | `reminders` (create reminders with `Reminder.id_for(key)`, `Conflict` = exists; enqueue `planner.remind` with `run_after = dueAt`, id `remind_job_id`) |
| `planner.post-scheduled` | `PostScheduledInput` | `reminders` keyed on T (cancel earlier ones for the post; a `kit_confirm` reminder at T + 30 min: "Did it go out? Add the final text"), spacing warnings |
| `planner.slot-check` | `SlotJobInput` | `check` (runs whenever it is claimed: lateness is what it handles): no-op if posted, publishing, kit or re-slotted; else -> missed, offer `slots.next_free_slot` (Today item, push). Never stack posts; time-sensitive posts are not offered a slot after `expiresAt` |
| `planner.scorecard` | `{}` | `compute` (last week's posts with owner signal, replies and substantive comments from engagement records, on-pillar share) and close the plan |

**Schedule.** `sched-planner-plan-week`: `weekly([day], time)` = `cadence.plan_week` minus
`plan_lead_minutes` (day shifts when the time crosses midnight), max lateness 6 h.
`sched-planner-veto-notice`: `weekly([cadence.veto_notice.day], cadence.veto_notice.time)`,
max lateness 4 h. `sched-planner-scorecard`: `weekly(["mon"], "07:00")`, max lateness 24 h.

**AI.** PLAN (anonymized, tool-free).

**Tests (goldens).** The slot goldens (DST weeks, optional slots, spacing, next free slot) are
in `tests/domain/test_slots.py`; planner tests its use of them: light (2 posts, flagship
optional) and away (nothing generated); missed-slot grace (slot + 2 h) and next free slot, never
stacked; reminders at +15/+60/+180 min and +48 h/+7 d with stable ids (a resumed step creates
none twice) and expiry of stale ones; veto without a receipt holds; a veto restarts the
calibration streak; plan approval starts the flagship pipeline for Monday research and asks for
quick takes instead of generating them; the plan job writes no articles or posts.

### W-B5 engagement (`blogwriter.engagement`)

**Purpose.** Reply and comment helpers on pasted text (nothing is automated on LinkedIn), the
48-hour limit on other members' text, the watchlist, the profile checklist.

**Files.** `engagement/__init__.py`, `helpers.py`, `lint.py`, `routes.py`, `jobs.py`,
`schedule.py`, `prompts/`; `tests/engagement/`.

**Depends on.** `domain`, `ai`, stories (`load_bank`), `services.slop`.

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| GET | `/api/engagement/items?status=open` | | `Page[EngagementItem]` (expired items are filtered in code) | |
| POST | `/api/engagement/items` | `{itemKind: reply\|comment, pastedText (1-5000), postId?, targetUrl?}` | 201 `Versioned[EngagementItem]` (`expiresAt` = now + 48 h, `ttl`) | |
| POST | `/api/engagement/items/{id}/angles` | none | `Versioned[EngagementItem]` (`require_ai_use`; ENGAGEMENT: <= 3 angles grounded in stories, POVs and briefs; the pasted text is delimited as untrusted data) | 409 `ai_use_not_confirmed`, 410 `expired` |
| POST | `/api/engagement/items/{id}/lint` | `{draftText (1-1250)}` | `{findings[]}` (restating the post, links, AI tells, over 1000 characters, blocklist) | |
| POST | `/api/engagement/items/{id}/finalize` | `{finalText (1-1250), url?}`, If-Match | 201 `Versioned[EngagementRecord]` (the item is deleted at once) | 409 |
| POST | `/api/engagement/items/{id}/dismiss` | If-Match | 204 (deleted) | |
| GET | `/api/engagement/records?limit=&cursor=` | | `Page[EngagementRecord]` | |
| GET, POST | `/api/engagement/watchlist` | `{name, profileUrl?, pillar?, note?}` | `Page`/201 `Versioned[WatchEntry]` | |
| PUT, DELETE | `/api/engagement/watchlist/{id}` | If-Match | `Versioned[WatchEntry]` / 204 | |
| POST | `/api/engagement/watchlist/{id}/engaged` | none | `Versioned[WatchEntry]` (`lastEngagedAt`) | |
| GET, PUT | `/api/engagement/profile-checklist` | `{items}`, `SingletonPrecondition` | `Versioned[ProfileChecklist]` | |

**Jobs.** `engagement.purge`: `purge` (delete items with `expiresAt <= now`).

**Schedule.** `sched-engagement-purge`: `every(6 * 3600)`, max lateness 6 h.

**AI.** ENGAGEMENT (anonymized, tool-free; never more than anonymized data).

**Tests.** Other members' text never outlives 48 h (expiry in code, purge job, deletion on
finalize) and is never in a backup (only `engagement_record` and `watch_entry` are,
`BACKUP_KINDS`); no tools in any ENGAGEMENT request; lint rules (the blocklist fails closed);
no import of the LinkedIn client or publisher (import-graph test).

### W-B6 analytics (`blogwriter.analytics`)

**Purpose.** The snapshot form, LinkedIn export import, baselines and rates per 1K impressions
against the owner's trailing median, experiments, suggestions (owner-approved), the spend view,
the eval set and judge calibration.

**Files.** `analytics/__init__.py`, `settings.py`, `snapshots.py`, `imports.py`, `rates.py`,
`spend.py`, `experiments.py`, `suggestions.py`, `evals.py`, `routes.py`, `jobs.py`,
`schedule.py`, `prompts/`; `tests/analytics/`.

**Depends on.** `domain` (`jobflow`), `ai`, articles (`create_article`, `set_links`), research
(`create_run`, `start_run`), gates (`run_gates`), openpyxl (in a thread), the `usage` container
(read only). It never imports pipeline (wave B): it enqueues `pipeline.article` by its constant.

**HTTP routes.**

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| POST | `/api/posts/{id}/snapshots` | `{offset: h48\|d7\|other, impressions?, reactions?, comments?, reposts?, saves?, sends?, takenAt?}`, If-Match | `Versioned[Post]` | 409 (not posted) |
| POST | `/api/analytics/imports?filename=` | raw `.xlsx` body (<= `IMPORT_MAX_BYTES`) | 201 `Versioned[AnalyticsImport]` (rows matched by date, then URN) | 413, 415, 422 `unknown_export_format` |
| GET | `/api/analytics/imports` | | `Page[AnalyticsImport]` | |
| GET | `/api/analytics/overview?weeks=12&dimension=` | | `{ownerMedian, rows: [{dimension, value, posts, ratesPer1k{reactions, comments, reposts, saves, sends}, vsMedian}]}` (dimensions: pillar, format, hookType, lane, goLiveMode, ownerSignal) | |
| GET | `/api/analytics/spend?days=30` | | `{days: [{date, costUsd, byTask{}, byModel{}}], total, trailingMedian, brakeThreshold, fallbackRate, reviewOnlyRate}` | |
| GET, POST | `/api/analytics/experiments` | `ExperimentIn{question, hypothesis, metric, arms[]}` | `Page`/201 `Versioned[Experiment]` | |
| GET, PUT | `/api/analytics/experiments/{id}` | If-Match | `Versioned[Experiment]` | |
| POST | `/api/analytics/experiments/{id}/transition` | `{to: running\|concluded\|abandoned, conclusion?}`, If-Match | `Versioned[Experiment]` | 409 `experiment_running` (one at a time; checked with `strict=True`), `too_few_posts` (< 8 per arm to conclude) |
| GET | `/api/analytics/suggestions?status=` | | `Page[InsightSuggestion]` | |
| POST | `/api/analytics/suggestions/{id}/decide` | `{decision: accept\|dismiss}`, If-Match | `Versioned[InsightSuggestion]` (accepting records the decision only; the planner never self-adjusts) | |
| POST | `/api/analytics/suggestions/run` | none | 202 | |
| GET, POST | `/api/evals/topics` | `{pillar, format, topic}` | `Page`/201 `Versioned[EvalTopic]` | |
| PUT, DELETE | `/api/evals/topics/{id}` | If-Match | | |
| POST | `/api/evals/runs` | none | 202 (`analytics.eval`, origin owner) | 409 `voice_guide_missing` |
| GET | `/api/evals/results?runId=` | | `Page[EvalResult]` | |
| POST | `/api/evals/results/{id}/label` | `{label: as_is\|needs_edits\|reject}`, If-Match | `Versioned[EvalResult]` (recomputes `autonomy.judge`: among judge-passed results, the share labelled as-is) | |

**Jobs.** `analytics.suggest`: `compute` (rates vs trailing median per dimension), `suggest`
(SYNTHESIS, public aggregates only, inside `defer_on_brake`; skipped when paused), `save`
(`InsightSuggestion.id_for(weekStart, dimension, n)`, `Conflict` = saved).

`analytics.eval` measures the generator that ships, not a copy of it. Per active topic
(checkpoint each; children inherit `origin: owner`):
1. `brief`: when `EvalTopic.briefId` is unset (or the owner asked for fresh briefs), one
   research child: `create_run(run_id=ResearchRun.id_for_eval(topicId, jobId), topic=...)` +
   `start_run(parent=...)`, `wait_for_child`; then the topic's `briefId`.
2. `draft`: create the eval article `Article.id_for_eval(topicId, jobId)` (`purpose=eval`,
   `Conflict` = exists), `set_links(briefId)`, and enqueue the real `pipeline.article` as a
   child (`pipeline_job_id(articleId, 1)`, `PipelineInput(start_from="draft",
   interview="skip", parent=ParentRef(jobId, "eval-<n>"))`), then `wait_for_child`.
3. `judge`: `gates.run_gates(article, gates={GateName.RUBRIC})`, so the judge is gate 1 with its
   `RUBRIC_*` thresholds: `EvalResult` (`EvalResult.id_for(topicId, jobId)`) `judgePassed` =
   the rubric result passed, `judgeScore` = its overall score.
4. `notify` (push "Eval drafts ready to label").
Eval articles are never approved or published (`check_article_transition`: `eval_article`) and
lists hide them.

**Schedule.** `sched-analytics-suggest`: `weekly(["mon"], "07:30")`, max lateness 24 h.

**AI.** SYNTHESIS (public). Evals use no model directly: the research and pipeline children
and gate 1 make their calls.

**Settings.** `AnalyticsSettings`.

**Tests.** Rates per 1K and medians on fixed data; import matching (date first, then URN);
one running experiment; suggestions never change plans or settings and keep their ids on
resume; the eval job enqueues `pipeline.article` with `interview: skip` and judges with
`run_gates(gates={RUBRIC})` only (with `StoreJobQueue(registered=...)` lacking
`pipeline.article` the job fails `pipeline_unavailable` instead of waiting forever); judge
calibration math; spend view sums the `usage` container like the ledger's `spend_between`.

---

## 5. Integrator work order (`blogwriter.main`, `blogwriter.api`)

**I-1 Wiring** (`blogwriter.main.build_app`):
- Routers: every package's `get_router()` plus the system routers below.
- Jobs: one `JobRegistry`; call each package's `register_jobs(registry)`; pass it to
  `JobsLifecycle(registry, on_quarantine=...)`.
- Lifecycles, in order (**the runner starts last**: a job resumed after a deploy must find every
  service; `JobsService.start()` starts the runner and the scheduler at once):
  1. `db`;
  2. `AILifecycle`;
  3. `CoreServicesLifecycle` (new: `app.state.clock = SystemClock()`, `egress =
     SafeHttpClient()`, `vault = TokenVault.from_settings()` or `None` when `BW_VAULT_KEYS` is
     unset, `notifier` = web push adapter or a null notifier, `embedder` = `RouterEmbedder` when
     the AI router has a `Task.EMBED` route, else `None`);
  4. `StorageLifecycle`, `render` (module lifecycle), `RenderAdapterLifecycle` (new:
     `app.state.render`), `ResearchLifecycle`, `StoriesLifecycle` (`app.state.classifier`),
     `GatesLifecycle`, the linkedin `TransportLifecycle` (its startup sweep needs only the store);
  5. `ServicesLifecycle` (new: `app.state.services = StateServices(app)`,
     `install_services(...)`; shutdown: `uninstall_services()`);
  6. `JobsLifecycle(registry, on_quarantine=...)`: records the boot, starts the runner and the
     scheduler; every service is set by now, so a missing one means "not configured";
  7. `SchedulesLifecycle` (new: `app.state.schedules = seeder`, `await seeder.reseed()`; it
     needs `app.state.jobs.scheduler`).
  Shutdown runs in reverse: the runner stops (and releases running jobs) before any service it
  uses closes. No lifecycle before `JobsLifecycle` may need `app.state.jobs`.
- The OpenAPI document for the web client comes from `blogwriter.main:build_app().openapi()`
  (`npm run gen:api` must import it instead of `core.app`).

**I-2 System routes** (`blogwriter/api/system.py`, thin):

| Method | Path | Response |
|---|---|---|
| GET | `/api/jobs?status=&type=&limit=` | `Page[JobView]` (the SSE snapshot shape: no input or outputs) |
| GET | `/api/jobs/{id}` | `JobView` (an id that cannot exist is 404 through `problems()`) |
| POST | `/api/jobs/{id}/cancel`, `/api/jobs/{id}/retry` | `JobView` (409 `job_state`) |
| GET | `/api/schedules` | `[ScheduleView{id, name, jobType, rule, timezone, status, nextRunAt, pendingRunAt, lastRunAt, skippedRuns}]` |
| POST | `/api/schedules/{id}/confirm` `{run: bool}`, `/api/schedules/{id}/pause`, `/resume` | `ScheduleView` |
| POST | `/api/system/safe-mode/exit` | `SystemStatus` |
| GET | `/api/system/status` | `SystemStatus{safeMode, boot, runner{running, claimed}, brake{spendTodayUsd, thresholdUsd, engaged}, services{ai, storage, render, fetcher, webiq, vault, push, portfolio, embeddings, linkedin}, expiry{linkedinTokenDay, patExpiresAt?}, buildSha}` |
| GET | `/api/push/key` | `{applicationServerKey}` |
| POST | `/api/push/subscriptions` | 201 (body = `PushSubscription.toJSON()`) |
| DELETE | `/api/push/subscriptions/{id}` | 204 |
| POST | `/api/system/test-alert` `{push: bool}` | 202 (`emit_alert("test", request_id)` and an optional test push) |
| GET | `/api/meta/machines` | `{<machine>: {<state>: [targets]}}` from `domain.states.MACHINES` |

**I-3 Adapters**: the `Notifier` over `WebPushNotifier` (`delivered` = receipts with outcome
`delivered`) and a Cosmos `SubscriptionRepo` (`settings` container, kind `push_subscription`);
the `RenderService` over `blogwriter.render` (map `BrandKit` -> `RenderBrand`: `authorName` ->
`name_mark` (missing -> `RenderFailed("brand_incomplete")`), `authorHeadline` -> `headline`,
`siteName`, `colors`, headshot bytes from Blob; map `RenderError` subclasses to `RenderFailed`
codes; `render_mermaid_svg` returns UTF-8 bytes); later the `Embedder` (an embeddings call on
the OpenAI provider; `Task.EMBED` routes and prices exist already).

**I-4 Schedule seeding** (`ScheduleSeeder.reseed`): read `CadenceSettings`
(`read_singleton`), call every package's `schedule_entries(cadence)`, `scheduler.put(...)`
each entry, delete schedule items whose id starts with `sched-` and is not in the new set. Run
at startup and when kb saves the cadence. An entry whose job type is not in
`domain.jobtypes.SCHEDULED_JOB_TYPES` is refused (logged with its id, never seeded).

**I-5 Follow-ups**: give finished jobs a TTL (today only `jobs.queue.enqueue` takes `ttl`;
`JobsService.enqueue` and the scheduler do not, so job documents accumulate: set a 30-day TTL when
a job reaches a terminal status); `Task` enum: **no additions are required** (see section 11);
mount `DataProvider` in `web/src/main.tsx`; add `PUBLISH`-related secrets
(`BW_PUBLISH_PORTFOLIO_TOKEN`, `BW_LINKEDIN_CLIENT_SECRET`) to Key Vault references and
`BW_GATES_SITE_ORIGIN` next to `BW_PUBLISH_SITE_ORIGIN`; keep the alert rule on `alert_event`.

**I-6 End-to-end test** (`server/tests/test_e2e.py`): `make_services()` with every package
router and every job type registered on a real `JobRunner` (dispatch, build, verification and
LinkedIn mocked; fake clock). It runs the plan's chains: idea -> research -> micro-interview ->
draft -> gates -> approve -> publish (verified by the version marker) -> derive post -> lint ->
staging -> one-click publish -> reminders; the kit path through natively_scheduled to posted with
the final text; unpublish and a correction; a veto. It also asserts the invariant that no
`ScheduleEntry` of any package has a job type outside `domain.jobtypes.SCHEDULED_JOB_TYPES` or
one for which `generates_content` is true.

**I-7 Job deferral** (jobs package): steps raise `domain.jobflow.JobDeferred(run_after, reason)`.
`blogwriter.jobs` cannot import `domain` (`domain.jobflow` imports `blogwriter.jobs.runner`), so
move the class into `blogwriter.jobs.runner` next to `JobFailed` (same constructor) and make
`domain.jobflow.JobDeferred` a re-export of it. Then `JobRunner._settle` handles it: park the
steps, status `queued`, `runAfter = exc.run_after` (clamped to now + 1 s .. now + 24 h), give the
attempt back (like a release), add the event `status {queued, reason: deferred, code}`, and
quarantine after 200 consecutive deferrals. Until then a `JobDeferred` counts as an ordinary
error (backoff, attempts), which is what `BrakeEngaged` does today.

**I-8 Indexing**: exclude the large text fields from indexing in `infra/v2/data` and add the
same names to `db.UNINDEXED_FIELDS` in one change: `body` (style guides), `text` (voice
samples), `displayText`, `finalText`, `aiDraftText` (posts), `pastedText` (engagement items),
`gates` and `servedBy` (articles; nobody filters on them).

**I-9 `linkedin.lint.ai_tell_findings(text) -> list[Finding]`**: a public wrapper over
`AI_TELLS` with the module's phrase boundaries and apostrophe normalization, for voice and
engagement lint (until then they use the recipe in W-A4).

**I-10 Projections**: `Store.find(..., fields=[...])` and `find_docs(..., fields=...)` so
summary lists (articles, versions, posts) stop reading whole documents.

**I-11 Prices**: an `AIRouter.prices` property (or keep kb on `AISettings().price_table()`).

---

## 6. HTTP API index

All routes are owner-only; every non-GET passes CSRF. Details and codes are in the work orders.

| Method | Path | Package |
|---|---|---|
| POST | `/api/media` | storage |
| GET | `/api/media/{name}` | storage |
| GET, PUT | `/api/kb/pillars`, `/api/kb/formats`, `/api/kb/lanes`, `/api/kb/brand`, `/api/kb/blocklist`, `/api/kb/policy`, `/api/kb/cadence`, `/api/kb/models`, `/api/kb/autonomy` | kb |
| POST | `/api/kb/autonomy/pause`, `/api/kb/autonomy/resume` | kb |
| GET | `/api/kb/inputs` | kb |
| POST | `/api/stories/intake/redact`, `/api/stories/intake/check` | stories |
| GET, POST | `/api/stories` | stories |
| GET, PUT, DELETE | `/api/stories/{id}` | stories |
| POST | `/api/stories/{id}/review`, `/api/stories/seed`, `/api/stories/classify` | stories |
| GET, POST | `/api/povs` | stories |
| GET, PUT, DELETE | `/api/povs/{id}` | stories |
| POST | `/api/povs/{id}/review` | stories |
| GET, POST | `/api/voice/samples` | voice |
| PUT, DELETE | `/api/voice/samples/{id}` | voice |
| POST | `/api/voice/samples/{id}/status` | voice |
| GET | `/api/voice/guide`, `/api/voice/guide/{id}` | voice |
| POST | `/api/voice/guide/build`, `/api/voice/guide/{id}/versions`, `/api/voice/guide/{id}/activate`, `/api/voice/guide/{id}/reject` | voice |
| GET | `/api/voice/digests`, `/api/voice/digests/{id}` | voice |
| POST | `/api/voice/digests/{id}/suggestions/{sid}`, `/api/voice/digests/run` | voice |
| GET, POST | `/api/research/runs` | research |
| GET | `/api/research/runs/{id}` | research |
| POST | `/api/research/runs/{id}/cancel` | research |
| GET, PUT | `/api/research/briefs/{id}` | research |
| GET, POST | `/api/research/quick` | research |
| GET | `/api/research/quick/{id}` | research |
| GET, POST | `/api/sources` | signals |
| POST | `/api/sources/discover`, `/api/sources/crawl` | signals |
| GET, PUT, DELETE | `/api/sources/{id}` | signals |
| POST | `/api/sources/{id}/pause`, `/api/sources/{id}/resume` | signals |
| GET, POST | `/api/ideas` | signals |
| GET, PUT | `/api/ideas/{id}` | signals |
| POST | `/api/ideas/{id}/transition` | signals |
| POST | `/api/ideas/repurpose` | signals |
| GET, POST | `/api/articles` | articles |
| GET, PUT | `/api/articles/{id}` | articles |
| POST | `/api/articles/{id}/transition`, `/api/articles/{id}/clearance` | articles |
| GET | `/api/articles/{id}/versions`, `/api/articles/{id}/versions/{version}` | articles |
| POST | `/api/articles/{id}/versions/{version}/restore` | articles |
| GET | `/api/articles/{id}/preview`, `/api/articles/{id}/export.md` | articles |
| POST | `/api/articles/{id}/gates/run`, `/api/articles/{id}/gates/{gate}/override` | gates |
| GET | `/api/articles/{id}/gates` | gates |
| POST | `/api/gates/lint`, `/api/gates/standards/refresh` | gates |
| GET | `/api/gates/standards` | gates |
| GET | `/api/linkedin/connection` | linkedin transport |
| POST | `/api/linkedin/oauth/start`, `/api/linkedin/disconnect`, `/api/linkedin/introspect` | linkedin transport |
| GET | `/api/linkedin/oauth/callback` | linkedin transport |
| GET | `/api/posts/{id}/go-live`, `/api/posts/{id}/kit` | linkedin transport |
| POST | `/api/posts/{id}/publish`, `/api/posts/{id}/mode`, `/api/posts/{id}/kit`, `/api/posts/{id}/resolve`, `/api/posts/{id}/linkedin/delete` | linkedin transport |
| PATCH | `/api/posts/{id}/linkedin` | linkedin transport |
| GET | `/api/backups`, `/api/backups/{date}/{container}` | backups |
| POST | `/api/backups/run`, `/api/backups/restore-drill` | backups |
| POST | `/api/articles/{id}/pipeline`, `/api/articles/{id}/pipeline/cancel`, `/api/articles/{id}/revise`, `/api/articles/{id}/selection` | pipeline |
| GET | `/api/interviews`, `/api/interviews/{id}` | pipeline |
| POST | `/api/interviews/{id}/answers`, `/api/interviews/{id}/skip` | pipeline |
| POST | `/api/articles/{id}/approve`, `/api/articles/{id}/publish-now`, `/api/articles/{id}/unpublish`, `/api/articles/{id}/staging-preview` | publish |
| GET | `/api/articles/{id}/publish-status` | publish |
| GET, POST | `/api/posts` | linkedin posts |
| GET, PUT | `/api/posts/{id}` | linkedin posts |
| POST | `/api/articles/{id}/posts/derive` | linkedin posts |
| POST | `/api/posts/{id}/lint`, `/api/posts/{id}/check`, `/api/posts/{id}/render`, `/api/posts/{id}/regenerate`, `/api/posts/{id}/approve`, `/api/posts/{id}/unapprove`, `/api/posts/{id}/archive` | linkedin posts |
| PUT | `/api/posts/{id}/carousel` | linkedin posts |
| GET | `/api/posts/{id}/carousel/preview` | linkedin posts |
| GET | `/api/today` | planner |
| GET | `/api/plan/weeks`, `/api/plan/weeks/{weekStart}`, `/api/plan/scorecard` | planner |
| POST | `/api/plan/weeks/{weekStart}/generate`, `/api/plan/weeks/{weekStart}/approve` | planner |
| PUT | `/api/plan/weeks/{weekStart}`, `/api/plan/weeks/{weekStart}/mode` | planner |
| POST | `/api/posts/{id}/reslot` | planner |
| GET | `/api/reminders` | planner |
| POST | `/api/reminders/{id}/dismiss` | planner |
| GET, POST | `/api/articles/{id}/veto` | planner |
| GET, POST | `/api/engagement/items` | engagement |
| POST | `/api/engagement/items/{id}/angles`, `/lint`, `/finalize`, `/dismiss` | engagement |
| GET | `/api/engagement/records` | engagement |
| GET, POST | `/api/engagement/watchlist` | engagement |
| PUT, DELETE | `/api/engagement/watchlist/{id}` | engagement |
| POST | `/api/engagement/watchlist/{id}/engaged` | engagement |
| GET, PUT | `/api/engagement/profile-checklist` | engagement |
| POST | `/api/posts/{id}/snapshots` | analytics |
| GET, POST | `/api/analytics/imports` | analytics |
| GET | `/api/analytics/overview`, `/api/analytics/spend` | analytics |
| GET, POST | `/api/analytics/experiments` | analytics |
| GET, PUT | `/api/analytics/experiments/{id}` | analytics |
| POST | `/api/analytics/experiments/{id}/transition` | analytics |
| GET | `/api/analytics/suggestions` | analytics |
| POST | `/api/analytics/suggestions/{id}/decide`, `/api/analytics/suggestions/run` | analytics |
| GET, POST | `/api/evals/topics` | analytics |
| PUT, DELETE | `/api/evals/topics/{id}` | analytics |
| POST | `/api/evals/runs`, `/api/evals/results/{id}/label` | analytics |
| GET | `/api/evals/results` | analytics |
| GET | `/api/jobs`, `/api/jobs/{id}`, `/api/schedules`, `/api/system/status`, `/api/push/key`, `/api/meta/machines` | integrator |
| POST | `/api/jobs/{id}/cancel`, `/api/jobs/{id}/retry`, `/api/schedules/{id}/confirm`, `/pause`, `/resume`, `/api/system/safe-mode/exit`, `/api/push/subscriptions`, `/api/system/test-alert` | integrator |
| DELETE | `/api/push/subscriptions/{id}` | integrator |
| GET | `/api/health`, `/api/me`, `/api/events` | core, jobs (exist) |

---

## 7. Job types

All constants are in `domain.jobtypes`. "Waits" = `waiting_input` points; "Remote ids" are
recorded before awaiting. Steps are listed in the work orders.

| Type | Package | Input | Id (idempotency) | Waits | Remote ids | Enqueued by |
|---|---|---|---|---|---|---|
| `research.run` | research | `ResearchRunInput` | `research_job_id(runId)`; chained runs have derived run ids (`ResearchRun.id_for_pipeline` / `id_for_eval`) | none | none (`store=false` calls) | research route, pipeline and analytics.eval (chained: `finish_child`) |
| `signals.crawl` | signals | `{}` | scheduler slot id; signal and idea ids deterministic | none | none | schedule, owner |
| `signals.scout` | signals | `{}` | scheduler slot id | none | none | schedule |
| `stories.seed` | stories | `{seedId, source}` (the text is an ephemeral `SeedText`) | random; entry ids deterministic | none | none | owner |
| `stories.classify` | stories | `{}` | random; idempotent re-run | none | none | owner |
| `voice.build-guide` | voice | `{}` | random; guide version id deterministic | none | none | owner |
| `voice.digest` | voice | `{}` | scheduler slot id; digest id per period | none | none | schedule, owner |
| `gates.run` | gates | `GatesRunInput` | `gates_job_id(articleId, sha)`, re-runs `-r<n>` (`enqueue_once` `rerun`) | none | none | owner, (pipeline runs gates inline) |
| `gates.standards-refresh` | gates | `{}` | scheduler slot id | none | none | schedule, owner |
| `linkedin.introspect` | linkedin transport | `{}` | scheduler slot id | none | none | schedule |
| `backups.dump` | backups | `{date?}` | scheduler slot id; blobs overwritten per date | none | none | schedule, owner |
| `pipeline.article` | pipeline | `PipelineInput` (`startFrom`, `interview: ask\|skip`, `parent?`) | `pipeline_job_id(articleId, n)` | `research` (child: `wait_for_child`), `interview` (`{interviewId, skipped?, expired?}`) | none | owner, planner (plan approval: delayed to `research_start`, origin owner), analytics.eval (child) |
| `pipeline.interview-expire` | pipeline | `InterviewInput` | `interview_expire_job_id(interviewId)`, `run_after` dueAt | none | none | pipeline |
| `pipeline.revise` | pipeline | `ReviseInput` | random | none | none | owner |
| `publish.article` | publish | `PublishInput` (`publishAt` required) | `publish_job_id(articleId, version)`, re-runs `-r<n>` | none | `correlation`, `run` (GitHub) | publish routes, pipeline (auto path) |
| `publish.unpublish` | publish | `ArticleJobInput` | random | none | `correlation`, `run` | owner |
| `publish.staging` | publish | `ArticleJobInput` | random | none | `correlation` | owner |
| `posts.derive` | linkedin posts | `ArticleJobInput` | `derive_job_id(articleId, version)`; post ids deterministic per variant | none | none | owner |
| `posts.generate` | linkedin posts | `PostJobInput` | random | none | none | owner, planner (plan approval) |
| `posts.check` | linkedin posts | `PostJobInput` | random | none | none | owner, posts |
| `posts.render` | linkedin posts | `PostJobInput` | random; blob names content-addressed | none | none | owner, posts |
| `posts.stage` | linkedin posts | `SlotJobInput` | `stage_job_id(postId, slot)`, re-runs `-r<n>`, `run_after` slot - 60 min | none | upload URNs on the post | posts (approve), planner (reslot) |
| `posts.expire` | linkedin posts | `PostJobInput` | `expire_job_id(postId)`, `run_after` expiresAt | none | none | posts |
| `planner.plan-week` | planner | `{weekStart?}` | scheduler slot id or random | none | none | schedule, owner |
| `planner.veto-notice` | planner | `{}` | scheduler slot id | none | none | schedule |
| `planner.remind` | planner | `RemindInput` | `remind_job_id(reminderId)` with `reminderId = Reminder.id_for(key)`, `run_after` dueAt | none | none | planner |
| `planner.post-live` | planner | `PostLiveInput` | `post_live_job_id(postId)` | none | none | linkedin transport |
| `planner.post-scheduled` | planner | `PostScheduledInput` | `post_scheduled_job_id(postId, scheduledAt)` | none | none | linkedin transport |
| `planner.slot-check` | planner | `SlotJobInput` | `slot_check_job_id(postId, slot)`, re-runs `-r<n>`, `run_after` slot + grace | none | none | linkedin posts, planner |
| `planner.scorecard` | planner | `{}` | scheduler slot id | none | none | schedule |
| `engagement.purge` | engagement | `{}` | scheduler slot id | none | none | schedule |
| `analytics.suggest` | analytics | `{}` | scheduler slot id | none | none | schedule, owner |
| `analytics.eval` | analytics | `{}` | random; per-topic checkpoints and derived document ids | per topic: the research child, the pipeline child | none | owner |

The LinkedIn publisher is **not** a job and no job can reach it. Re-runs (`-r<n>`) come from
`domain.jobflow.enqueue_once`; a delayed job checks its lateness first (section 1.7).

---

## 8. Schedule

Times are local to `CadenceSettings.timezone` (default `America/New_York`); the seeder rebuilds
the items when the cadence changes. Past `maxLateness` an item waits for the owner's
confirmation (System, and a Today item). Every job type below is in
`domain.jobtypes.SCHEDULED_JOB_TYPES`, and none of them generates content.

| Id | Job type | Rule (default) | Max lateness | Package |
|---|---|---|---|---|
| `sched-signals-crawl` | `signals.crawl` | `every(cadence.crawl_interval_hours * 3600)` (6 h; `every` takes seconds) | 6 h | signals |
| `sched-signals-scout` | `signals.scout` | weekly `cadence.scout` (Sun 15:00) | 12 h | signals |
| `sched-planner-plan-week` | `planner.plan-week` | weekly `cadence.plan_week` minus `plan_lead_minutes` (Sun 17:00) | 6 h | planner |
| `sched-planner-veto-notice` | `planner.veto-notice` | weekly `cadence.veto_notice` (Fri 12:00) | 4 h | planner |
| `sched-planner-scorecard` | `planner.scorecard` | weekly Mon 07:00 | 24 h | planner |
| `sched-voice-digest` | `voice.digest` | weekly `cadence.digest` (Mon 08:00; monthly after the first quarter, decided inside the job) | 24 h | voice |
| `sched-analytics-suggest` | `analytics.suggest` | weekly Mon 07:30 | 24 h | analytics |
| `sched-linkedin-introspect` | `linkedin.introspect` | daily 08:30 | 24 h | linkedin transport |
| `sched-gates-standards` | `gates.standards-refresh` | weekly Sat 05:00 | 48 h | gates |
| `sched-backups-dump` | `backups.dump` | daily `cadence.backup_time` (03:30) | 12 h | backups |
| `sched-engagement-purge` | `engagement.purge` | `every(6 * 3600)` | 6 h | engagement |

Per-item timing (staging at T-60, reminders, slot checks, scheduled publishes, post expiry,
interview expiry) uses delayed jobs, never sub-hourly schedule items. Delayed jobs follow the
same "after downtime, re-confirm" rule through their own maximum lateness (section 1.7): a
scheduled publish holds for the owner, staging gives up, stale reminders expire.

---

## 9. Server-sent events

The only stream is the existing `GET /api/events` (`jobs.sse`). No new broker message types.
The web may rely on:

| Event | Data | Web action |
|---|---|---|
| `snapshot`, `job` with `type: status` | job status | invalidate `['jobs']` (exists) |
| `job` with `type: step.*`, `input.requested` | step bookkeeping | update the job timeline (exists) |
| `job` with `type: entity.changed` | `{entity, id}` (`domain.jobtypes.entity_changed`) | invalidate `[<plural>, 'detail', id]` and `[<plural>, 'list']`, plus `['today']` |
| `job` with `type: progress` | `{done, total, label?}` (optional, long steps) | progress bar on the timeline |
| `job` with `type: draft.reset` (`domain.jobtypes.DRAFT_RESET`) | `{}` | clear the live draft text: the router discarded what was streamed (refusal, retry, repair, fallback); the next deltas start over |
| `job` with `type: child.result` (`CHILD_RESULT`) | `ChildResult` (`ok`, `ref`, `errorCode`) | none (job timeline only) |
| `delta` | `{jobId, step, text, gap}` | live draft text in the read-only editor (pipeline `draft` and `critique_revise` steps); on `gap` reload the article when the step lands |

Entity -> query key prefix: `article` -> `articles`, `post` -> `posts`, `idea` -> `ideas`,
`research_run` -> `researchRuns`, `brief` -> `briefs`, `quick_search` -> `quickSearches`,
`story` -> `stories`, `pov` -> `povs`, `interview` -> `interviews`, `voice_sample` ->
`voiceSamples`, `style_guide` -> `styleGuides`, `voice_digest` -> `voiceDigests`, `week_plan`
-> `plans`, `reminder` -> `reminders`, `source` -> `sources`, `engagement_item` ->
`engagementItems`, `experiment` -> `experiments`, `insight_suggestion` -> `suggestions`,
`eval_result` -> `evalResults`, `backup_status` -> `backups`, `linkedin_connection` ->
`linkedin`, `settings` -> `kb`. Owner edits invalidate their own queries; events cover changes
made by jobs. The stream opens only while a job is on screen (existing retain rules); pages
refetch on mount and on focus (Today).

---

## 10. Web map

Routes and section ids are the existing ones (`web/src/app/nav.ts`, `web/src/app/routes.tsx`).
A **piece** is an article (`/studio/art-.../<tab>`) or a standalone post
(`/studio/post-.../<tab>`, native and quick-take lanes); the tab set adapts to the id prefix.
Query keys follow section 9 (`[<plural>, 'list', filters]`, `[<plural>, 'detail', id]`).
"Phone-first" pages are designed for one hand at 375 px first; the others for a desktop editor
first and stay usable on a phone.

| Area / section | Shows | Calls | Phone-first |
|---|---|---|---|
| **Today** `/today` | The action inbox (`TodayItem`s grouped: approve, answer, publish, reply, needs attention, "What's your take?" for quick-take slots), the week (mode, plan status, scorecard), reconnect and owner-input banners, quick capture (idea, POV take) | `GET /api/today`, `/api/me`, `/api/system/status`; `POST /api/ideas`, `POST /api/stories/intake/redact` + `/check` + `POST /api/povs`; item actions deep-link to their pages; `POST /api/reminders/{id}/dismiss`; snapshot form `POST /api/posts/{id}/snapshots` | yes |
| Today, Engage panel (sheet in Today; share-target landing `/today?share=` is client-side and confirms before any POST) | Reply and comment helper: paste, angles, draft, lint, finalize; the daily watchlist session | `/api/engagement/items` (+ `angles`, `lint`, `finalize`, `dismiss`), `/api/engagement/watchlist` (+ `engaged`), `/api/engagement/records` | yes |
| **Calendar / Plan** `/calendar/plan` | Next week's plan (flagship, slots by day with local times, lanes, pillars, spacing warnings), approve; this week's status; drag and drop | `GET/PUT /api/plan/weeks/{weekStart}`, `POST .../generate`, `POST .../approve`, `GET /api/posts?weekPlanId=`, `GET /api/articles?...`, `POST /api/posts/{id}/reslot` | review and approve on phone; drag and drop desktop |
| Calendar / Ideas `/calendar/ideas` | The ideas inbox (relevance, novelty, lane, angle, time-sensitive badge), accept / snooze / dismiss, "What's your take?" (POV), start a flagship or a quick take, quick search | `GET/POST /api/ideas`, `PUT /api/ideas/{id}`, `POST /api/ideas/{id}/transition`, `POST /api/povs` (+ intake), `POST /api/articles {ideaId}`, `POST /api/posts {lane: quick_take}`, `POST /api/research/quick` | yes (triage) |
| Calendar / Availability `/calendar/availability` | Mode per week (normal, light, away) for the next 6 weeks | `GET /api/plan/weeks?from=`, `PUT /api/plan/weeks/{weekStart}/mode` | yes |
| **Studio** `/studio` | Pieces in progress (articles and standalone posts) by stage, new piece | `GET /api/articles`, `GET /api/posts?lane=native&lane=quick_take`, `POST /api/articles`, `POST /api/posts` | no |
| Studio / Brief & research `/studio/:id/brief` | Research runs and the brief (summary, findings with citations, stats, counterpoints, angles, hook seeds), quick search sidebar, job timeline | `GET /api/articles/{id}`, `GET/POST /api/research/runs`, `POST .../cancel`, `GET/PUT /api/research/briefs/{id}`, `POST /api/research/quick`, `GET /api/jobs/{id}` + SSE | no |
| Studio / Interview `/studio/:id/interview` | Micro or full interview: one question per screen, dictation, the redaction proposal to confirm, skip | `GET /api/interviews?articleId=`, `POST /api/stories/intake/redact`, `/check`, `POST /api/interviews/{id}/answers`, `/skip` | yes |
| Studio / Draft `/studio/:id/draft` (articles only) | TipTap editor (read-only with the live stream in pipeline and publish states; `draft.reset` clears the streamed text), autosave with If-Match plus `baseVersion` and the conflict dialog (content conflicts only), versions (pinned ones marked), gates panel ("and N more" from `truncated`), selection AI popover, portfolio preview (sandboxed iframe, images inlined), pipeline start/revise, approve and publish, veto, publish status, export, clearance | `GET/PUT /api/articles/{id}`, versions routes, `POST /api/gates/lint` (debounced), `GET /api/articles/{id}/gates`, `POST .../gates/run`, `.../override`, `POST .../pipeline`, `/revise`, `/selection`, `GET .../preview`, `POST /api/media`, `POST .../clearance`, `POST .../approve`, `/publish-now`, `/unpublish`, `GET .../publish-status`, `GET/POST .../veto`, `GET .../export.md`, SSE `delta` | no |
| Studio / Posts & carousel `/studio/:id/posts` | The LinkedIn composer: feed-card preview at mobile and desktop widths with the fold, escaped-character counter, hook picker, lint panel, chips (pillar, format, lane), carousel slide editor and sandboxed preview, approve | `GET /api/posts?articleId=`, `POST /api/articles/{id}/posts/derive`, `GET/PUT /api/posts/{id}`, `POST .../lint` (live), `/check`, `/regenerate`, `PUT .../carousel`, `GET .../carousel/preview`, `POST .../render`, `/approve`, `/unapprove`, `/archive`, `POST /api/media` | no (approve works on phone) |
| Studio / Go-live `/studio/:id/go-live?post=` | The publish page (exact final render: fold marker, first PDF page, title, alt text; "can you reply in the next hour?"), Publish now / Snooze 30 min / Next slot / Switch to kit; the kit (copy buttons, downloads, 4-step checklist, Open LinkedIn, mark draft saved / scheduled at T / posted with URL and final text); needs-attention resolution; edit or delete a live post | `GET /api/posts/{id}/go-live`, `POST .../publish`, `POST .../reslot`, `POST .../mode`, `GET/POST .../kit`, `POST .../resolve`, `PATCH .../linkedin`, `POST .../linkedin/delete`, `GET /api/linkedin/connection` | **yes** (push notifications open it) |
| **Library / Pillars** `/library/pillars` | Pillars (description, audience, vocabulary, renames, hashtags, research allowlist, target share), series, lane rules | `GET/PUT /api/kb/pillars`, `GET/PUT /api/kb/lanes` | no |
| Library / Voice guide `/library/voice` | Corpus samples with AI-tell flags, required LinkedIn posts counter, the active guide and versions (edit, activate), digests and suggestions | `/api/voice/samples*`, `/api/voice/guide*`, `/api/voice/digests*` | no |
| Library / Stories & POV `/library/stories` | Story bank and POV bank (status, confidentiality, classifier verdict, pillars), intake, seeding from cv/portfolio, review queue | `/api/stories*`, `/api/povs*`, `POST /api/stories/seed`, `POST /api/stories/classify` | review queue yes |
| Library / Brand kit `/library/brand` | Author name and headline, colours with contrast findings, headshot and name mark uploads, art briefs | `GET/PUT /api/kb/brand`, `POST /api/media` | no |
| Library / Format packs `/library/formats` | The four format packs (lengths, must-contain, owner input) | `GET/PUT /api/kb/formats` | no |
| **Settings / Models** `/settings/models` | Routing and prices (read-only, from env), preferences (images, novelty and near-duplicate thresholds) | `GET/PUT /api/kb/models` | no |
| Settings / Slots `/settings/slots` | Time zone, weekly calendar anchors, post slots, posts per mode, spacing, grace, reminder offsets, crawl and backup times; the schedule items | `GET/PUT /api/kb/cadence`, `GET /api/schedules`, `POST /api/schedules/{id}/confirm`, `/pause`, `/resume` | no |
| Settings / Sources `/settings/sources` | Feeds and scouting queries, health, discover, crawl now | `/api/sources*`, `POST /api/sources/discover`, `POST /api/sources/crawl` | no |
| Settings / Integrations `/settings/integrations` | LinkedIn connection (connect, token day, banner, introspect, disconnect), push on this device, portfolio and Web IQ status, profile alignment checklist | `/api/linkedin/*`, `/api/push/key`, `/api/push/subscriptions`, `/api/system/status`, `/api/engagement/profile-checklist` | connect and push setup yes |
| Settings / Blocklist & policy `/settings/policy` | Blocklist terms, employer policy (disclaimers, forbidden topics, AI-use confirmation), autonomy (auto-publish switch, calibration streak, judge rate, pause all), owner inputs | `/api/kb/blocklist`, `/api/kb/policy`, `/api/kb/autonomy` (+ `/pause`, `/resume`), `/api/kb/inputs` | pause switch yes |
| Settings / Insights `/settings/insights` | Rates per 1K vs median by dimension (use the dataviz skill), snapshots, export import, experiments, suggestions, spend and fallback rate | `/api/analytics/*`, `POST /api/posts/{id}/snapshots` | snapshot form yes |
| Settings / System `/settings/system` | Jobs (timeline, cancel, retry), schedules, safe mode, test alert, backups (status, run, download, restore drill), evals (topics, runs, labelling), build and device info | `/api/jobs*`, `/api/schedules*`, `/api/system/*`, `/api/backups*`, `/api/evals/*`, `/api/meta/machines` | no |

**Glossary for statuses** (labels the web shows): articles: Planned, Researching, Interview,
Drafting, Checking (gating), In review, Approved, Publishing, Live, Publish failed,
Unpublishing, Unpublished, Needs you, Archived. Posts: Writing (generating), Draft, Approved,
Ready (staged), Publishing, Posted, Kit, Needs you, Missed, Expired, Deleted, Archived. Kit:
Prepared, Draft saved in LinkedIn, Scheduled in LinkedIn, Posted.

**Rules for the web.** Read-only editor whenever `ARTICLE_WRITER[status] != "owner"`; enable
action buttons from `/api/meta/machines`; show `X-Request-Id` in error toasts; previews only
in `<iframe sandbox src=...>` (never `srcdoc`); never render returned HTML outside that iframe.
Intake and interview raw text (dictation, typed answers before redaction) lives in component
state only: never `localStorage`, `sessionStorage`, IndexedDB, a persisted TanStack Query cache
or a service-worker cache, and a session-expiry redirect drops it rather than saving a draft
(rule 9). Problems with `findings[]` (`confidential_text`) highlight by `field` and span and
never need the term.

---

## 11. AI task matrix

`Task` values are the existing enum (`blogwriter.ai.types.Task`). **No additions are
required.** (Optional later: a separate `CLASSIFY` task for the confidentiality classifier,
today routed as `FACT_CHECK`; it would need an enum value, a ceiling and a route.)

| Feature | Package | Task | Data class | Tools | Origin |
|---|---|---|---|---|---|
| Anonymizer proposal (intake) | stories | ANONYMIZE | private | none | owner |
| Classifier pass, mosaic check (stories, POVs, exemplars, gate 3) | stories (implements `services.classifier`; voice and gates call the port) | FACT_CHECK | anonymized | none | owner/background |
| Seeding from resume and portfolio | stories | SYNTHESIS | public | none | owner |
| Style guide synthesis, diff digest | voice | SYNTHESIS | anonymized | none | owner/background |
| Research plan | research | PLAN | public | none | per job |
| Research search passes | research | RESEARCH | public | `web_search` (allowlist pass with `allowed_domains`, open pass) | per job |
| Claim and quote extraction from passages | research | RESEARCH | public | none | per job |
| Verify, brief | research | SYNTHESIS | public | none | per job |
| Quick search | research | QUICK_SEARCH | public | `web_search` | owner |
| Feed triage | signals | TRIAGE | public | none | background/owner |
| Weekly scouting | signals | QUICK_SEARCH | public | `web_search` | background |
| Interview questions | pipeline | INTERVIEW | anonymized | none | per job |
| Outline | pipeline (evals run the real pipeline) | OUTLINE | anonymized | none | per job |
| Draft | pipeline (evals run the real pipeline) | DRAFT | anonymized | none | per job |
| Revise loops, owner revisions, selection AI | pipeline | REVISE | anonymized | none | per job / owner |
| Rubric critique (pipeline loop, gate 1; the eval judge is gate 1) | pipeline, gates | CRITIQUE | anonymized | none | per job |
| Fact-check (gate 2), derived-claim check | gates, linkedin posts | FACT_CHECK | anonymized (passages are public data in a tool-free call) | none | per job |
| Novelty (gate 4) | gates | EMBED via `Embedder` | anonymized | none | per job |
| Post derivation, hooks, carousel copy and spec | linkedin posts | DRAFT | anonymized | none | per job |
| Post regeneration with an instruction | linkedin posts | REVISE | anonymized | none | owner |
| Weekly plan proposal | planner | PLAN | anonymized | none | background/owner |
| Reply and comment angles | engagement | ENGAGEMENT | anonymized (pasted text is untrusted) | none | owner |
| Insight suggestions | analytics | SYNTHESIS | public (aggregates) | none | background/owner |
| Illustrations (opt-in) | (deferred) | IMAGE | public | none | |

Draft, revise and critique served by a fallback, or any refusal in the chain, make the output
review-only: `domain.common.review_only_reasons(result)` (`served_by.fallback_reason` set, or
`result.refusals` non-empty, or a refused result). Opus has one concurrent slot: keep
DRAFT/REVISE calls sequential inside a job. Background calls run inside `defer_on_brake`.

---

## 12. Owner inputs and empty states

| Owner input | Where | Until it exists |
|---|---|---|
| 5-10 past LinkedIn posts written by hand (required) | Library > Voice | Guide build answers 409 `not_enough_samples`; the pipeline answers 409 `voice_guide_missing`; Today shows an owner-input item |
| Approved stories (>= 10) and POVs (>= 3 per pillar) | Library > Stories | Drafting works but gate 6 fails without owner signal; the plan prefers pillars with material; owner-input item |
| Blocklist (client and internal project names) | Settings > Policy | Everything that needs it fails closed until the owner saves the list once (an empty list is a valid answer): intake saves (stories, POVs, interview answers, voice samples, ideas), research runs and quick searches, revise and selection AI, and post approval answer 409 `blocklist_not_configured`; gate 3 and post lint carry the hard `blocklist-not-configured` finding, so nothing publishes. Today shows an owner-input item |
| Employer social-media policy and disclaimer text | Settings > Policy | Carousels and cards render without a footer; gate 7 warns (`policy-not-configured`) |
| Employer AI-use confirmation (Azure OpenAI, Claude, Web IQ) | Settings > Policy | No anonymized owner material goes to any model (409 `ai_use_not_confirmed`): intake proposals, classification, interviews, drafting, posts and engagement angles wait; research, triage and quick search run |
| Brand kit (author name required; colours, headshot, name mark) | Library > Brand | Carousel, card and OG renders fail with `brand_incomplete`; text posts and kit mode still work |
| LinkedIn connection | Settings > Integrations | One-click publishing is off (`reconnect_required`); posts default to kit mode, which needs no API |
| Push on the phone (VAPID keys plus a subscription) | Settings > Integrations | Reminders and staging notices are not delivered; veto notices have no receipt, so auto-publish holds and Azure Monitor e-mails |
| Portfolio repository, workflow and PAT (`BW_PUBLISH_PORTFOLIO_*`), and portfolio PR B | env / Key Vault, portfolio repo | Approve works; the publish job fails `portfolio_not_configured`; staging previews 503. The portfolio workflow must declare a `correlation_id` `workflow_dispatch` input and a `run-name` containing it (otherwise GitHub answers 422 and `find_run` finds nothing), and every article page must render `<meta name="bw-updated-at" content="<updatedAt>">` (`domain.articles.PUBLISHED_MARKER_NAME`) or verification never succeeds |
| Web IQ binding (`BW_RESEARCH_WEBIQ_*`) | env | Fetches use the egress fallback |
| Models (`BW_AI_*`) | env | Every AI route answers 503 `ai_unavailable`; jobs fail `ai_unavailable` |
| Storage (`BW_STORAGE_*`) | env | Uploads, renders, publish assets and backups answer 503 `storage_unavailable` |
| Resume and portfolio text for seeding | Library > Stories | Optional; stories come from intake only |
| Watchlist seed names | Today > Engage | The daily session shows an empty state with "add 5-10 niche voices" |
| Eval labels on the 12 topics | Settings > System > Evals | Judge calibration unknown: auto-publish cannot turn on (`judge_uncalibrated`) |
| A real LinkedIn analytics export sample | Settings > Insights | The import parser follows the documented columns; an unknown layout answers 422 `unknown_export_format`; the snapshot form always works |

---

## 13. Deferred items

| Item | Why |
|---|---|
| Deep research mode (one background `xhigh` run) | The plan defers it until standard runs are measured; no background Responses are used today |
| `gpt-image-2` illustrations and the "media matches text" check | `Task.IMAGE` has no route (needs an optional East US 2 resource); `imagesEnabled` stays off |
| Prompt overrides and the prompt test harness UI | Prompts are versioned package files now; the override store and Monaco editor come after the pipeline is measured |
| Prefilled LinkedIn composer link, export import format | Wait for the Phase 0 LinkedIn app checks; the kit links to the LinkedIn feed |
| Optional Teams Workflows notifications, media garbage collection, a CI deploy-freeze endpoint | Not needed for Phases 2-7 |
| Authoring polls (`POST /api/posts` and edits with `format: poll`) | The lint, approval hash, transport and kit handle `post.poll`, but no route writes one yet: create and edit answer 422 `unsupported_format` until a poll editor exists |
| v1 migration (Phase 9) and portfolio PRs B and C | Separate phases and repository. PR B must add the `correlation_id` workflow input with a matching `run-name` and the `bw-updated-at` meta tag (section 12): the publish job depends on both |

---

## 14. Revision log

**2026-11-27, revision 4** (review of the integrated server: security, reliability and
product findings; details in `integration.md`).

- Blocklist at the moment content leaves (1.9.5): approve, publish-now, staging previews, the
  publish check and upsert, `posts.stage` and the LinkedIn tap screen the current list; gate
  model requests are screened whole at call time (1.8); pushes name a title only when cleared.
- Veto receipts count only for the current approval (a notice or veto older than the article's
  `statusChangedAt` belongs to an earlier approval); the pipeline's auto-approve clears `veto`.
- Stuck publish states have exits (1.7); `jobflow.cancel_job` tells a waiting parent; a failed
  reseed retries in the background; `posts.stage` lateness is judged against the slot.
- `domain.golive`: one taken-slot rule (posts approve now refuses a day another post holds in
  any state), the default go-live mode, the slot jobs. Approving a kit-mode post moves it to the
  kit (`prepared`); an away week moves its approved one-click posts to the kit.
- Employer forbidden topics block post lint and engagement drafts; the article footer
  disclaimer ends the published `htmlContent`; more than 2 emoji block post approval;
  quick-take text gets the blocking entity checks; repurpose ideas (`POST
  /api/ideas/repurpose`, `analytics.suggest`'s `repurpose` step).

**2026-11-27, revision 3** (integration of waves A and B; details in
`docs/v2/build/integration.md`).

- `blogwriter.main` wires every package (I-1): routers, one `JobRegistry` with all 33 job types,
  the lifecycles in the I-1 order, the 11 schedule items and a `portfolio` status probe.
- Section 1.6: `read_only_state` carries `state` (not `status`, a reserved problem member that
  turned the answer into a 500) and `writer`. `domain.ports.BlobStoreUnavailable` (storage
  outages) maps to 503 `storage_unavailable`.
- Section 1.1 and I-1: the embedder is `services.RouterEmbedder` when the AI router has a
  `Task.EMBED` route. Section 13: embeddings and screenshot extraction (engagement
  `POST /api/engagement/extract`) are no longer deferred.
- W-A5: the run moves to `succeeded` in `audit`, not `brief` (`succeeded -> quarantined` is not
  an edge). W-A7: `create_article(..., article_id=None)` (analytics' derived eval ids).
- Domain: the plan's complete pillars are the `PillarSettings` default
  (`settings.default_pillars`); `Idea.suggestedFormat` takes `image_card`;
  `EngagementItem.draftText` allows 1250 characters; `MediaRole` is a named type;
  `served_by` on `StoryEntry`, `PovEntry` and `Interview` (the INTERVIEW call is recorded);
  `domain.testing.sample_document` takes overrides in either spelling.

**2026-11-26, revision 2** (review of revision 1: 4 blockers, 19 major and 21 minor findings;
all applied unless noted). Code in `blogwriter.domain`, tests in `tests/domain` (520 pass).

- Blockers: `compose_document`/`word_stats` moved to `domain.compose` (golden over the corpus;
  word counts pinned to the renderer), so gates never imports articles; `publishing -> approved`
  for a definite 4xx, with the "publishing iff intent pending" invariant and
  `settle_publish_intent`; the runner starts after every service (I-1), "not configured" fails
  and "not ready" defers; child results are recorded on the child and read by the parent
  (`jobflow.wait_for_child`/`finish_child`, tested on a real runner, including a child that
  finishes first).
- Jobs: `enqueue_once` (`reuse`/`rerun`/`replace`), `JobDeferred` + `defer_on_brake` (runner
  support is I-7), origin inherited by children, maximum lateness for delayed jobs
  (`PublishInput.publishAt`), derived ids for documents steps create, `pipeline.interview-expire`,
  `SCHEDULED_JOB_TYPES` and `generates_content`, `DRAFT_RESET`/`CHILD_RESULT` events.
- Articles: in_review -> researching/interviewing, publish_failed -> unpublishing
  (`publish.lastOperation`), writer `publish` for publishing/unpublishing, `live_article` and
  `eval_article` rules, `publish.auto`, `owns_slug`, version pinning and autosave coalescing,
  content-based 412s (`baseVersion`), the `bw-updated-at` verification marker, hero = OG PNG
  by default, previews inline media.
- Confidentiality: blocklist fails closed when never saved; `check_texts` before every model
  or search call with owner-editable text; gates stop after a blocklist hit; the
  `ConfidentialityClassifier` port (stories implements it); `require_ai_use` in domain;
  `strip_verbatim` on research claims.
- Store: writes validate before I/O, `find_docs(strict=True)` for guards, impossible ids are
  404, `update_singleton`, `versioned_singleton`, `InvalidCursor` -> 422, stored findings
  capped at 50 per gate with `truncated` (an article at every maximum is 1.75 MB).
- Also: slot math in `domain.slots` (DST goldens), reminder kinds trimmed to the three with a
  producer and validated like push payloads, `SeedText` for seeding, engagement records and
  watch entries backed up, `Signal` ids unique across partitions, approval hash v2 (format,
  poll, link exception), confirm-token key from `BW_COOKIE_KEY`, `BW_GATES_SITE_ORIGIN`, voice
  fetches through `services.fetcher`, `distinct_stamps`, fake fidelity (`refusals`,
  `registered=`), e2e test I-6, follow-ups I-7 to I-11, `ai.types` imports, `every(... * 3600)`.
- Deviations: `confidential_text` stays **422** (the review's fix text said 409; 422 is what
  section 1.6 and the existing routes use). Boot order: `JobsLifecycle` moved after
  `ServicesLifecycle` (and schedule seeding after it) instead of splitting service creation
  from start; no earlier lifecycle needs `app.state.jobs`. Backups: signal ids made unique per
  container instead of paging on `(id, partition key)`. Gate reports: 50 findings per gate (not
  100) plus a pattern on `Finding.ref`, so the worst case keeps a 250 KB margin. The public AI-tell
  matcher (I-9), projections (I-10) and indexing (I-8) are outside `domain` and left to the
  integrator.
