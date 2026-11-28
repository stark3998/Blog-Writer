# Blog Writer v2: server integration (waves A and B)

What `blogwriter.main` wires, the environment each package reads, the contract deviations that
were accepted, the domain changes made at integration, and the known gaps. The contract
(`contract.md`, revision 3) is still the reference; this file records the state after wiring.

## 1. What is wired

`blogwriter.main.build_app(settings=None)` (and the zero-argument `app()` factory for
`uvicorn blogwriter.main:app --factory`) composes everything through `core.app.create_app`.

**Routers** (`package_routers()`), after core's `/api/health` and `/api/me`: the system router
(`api.system`: jobs, schedules, push, status, safe mode, test alert, meta machines), the SSE
stream (`jobs.get_router()`, `/api/events`), then storage, kb, stories, voice, research,
signals, articles, gates, linkedin transport, backups, pipeline, publish
(`publish.routes.get_router()`), linkedin posts, planner, engagement, analytics and
illustrations. The OpenAPI document had 174 paths and 216 operations at wave B; the
illustrations add 6 paths (6 operations). Core's duplicate-route check passes.

**Jobs** (`build_registry()`): one `JobRegistry`, every package's `register_jobs`. All 34 types
of `domain.jobtypes.ALL_JOB_TYPES` are registered (a test keeps them equal), the opt-in
`illustrations.generate` included.

**Schedules** (`SCHEDULE_PROVIDERS`): signals, planner, voice, analytics, linkedin transport,
gates, backups and engagement provide the 11 items of contract section 8; research, stories,
pipeline, publish and linkedin posts return none. A test checks the ids are unique, the job
types are exactly `SCHEDULED_JOB_TYPES` and none generates content.

**Lifecycles** (`build_lifecycles(registry)`), in start order; shutdown is the reverse:

| # | Lifecycle | Sets on `app.state` |
|---|---|---|
| 1 | `db` | `store` (Cosmos; an injected store is kept) |
| 2 | `main.AILifecycle` | `ai` (`AIRouter`, or `None` without `BW_AI_*`; with `BW_AI_IMAGE_BASE_URL` it also generates images from that resource, same Entra token provider) |
| 3 | `services.CoreServicesLifecycle` | `clock`, `egress`, `vault`, `notifier`, `push`, `embedder` |
| 4 | `storage.StorageLifecycle` | `blobs` |
| 4 | `render.RenderLifecycle` | `render` (the `Renderer`) |
| 4 | `services.RenderAdapterLifecycle` | `render` (the `RenderService` adapter) |
| 4 | `research.ResearchLifecycle` | `fetcher` (Web IQ with the egress fallback) |
| 4 | `stories.StoriesLifecycle` | `classifier` |
| 4 | `gates.GatesLifecycle` | `slop` |
| 4 | `linkedin.transport.TransportLifecycle` | `linkedin_settings`, the shared client; sweeps stale publish intents |
| 5 | `services.ServicesLifecycle` | `services` (`StateServices`), `install_services`, status probes |
| 6 | `jobs.JobsLifecycle(registry, on_quarantine=...)` | `jobs`; records the boot, starts the runner and the scheduler |
| 7 | `services.SchedulesLifecycle(SCHEDULE_PROVIDERS)` | `schedules`; first reseed (retried in the background if it fails) |

A quarantined job raises `emit_alert("quarantined", job_id)` (ids only); a quarantined publish
job also releases its article to `publish_failed` (`publish.recovery.after_quarantine`). The
System cancel (`POST /api/jobs/{id}/cancel`) tells a cancelled child's waiting parent
(`jobflow.cancel_job`) and, for a publish job, releases its article once the run stopped. A
failed schedule reseed (boot or cadence save) retries in the background until it lands
(`ScheduleSeeder`); Blob storage that is only transiently unreachable at boot is kept and logged
(`storage_degraded`) instead of stopping the app.

**System status**: storage, render, fetcher, Web IQ (the fetcher's circuit state), vault, push,
embeddings and AI come from `app.state`. `portfolio` is a status probe
(`main.portfolio_status`: `on` when `PublishSettings.portfolio_configured`). `linkedin` is
`off`/`not_configured` while `app.state.linkedin_settings.configured` is false, else the
connection's state; `expiry.linkedinTokenDay` comes from the connection.

**Web API types**: `npm run gen:api` (from `web/`) imports `blogwriter.main:app` and regenerates
`web/src/lib/api/schema.ts`. A server test checks that every `$ref` in the document resolves
(an unresolvable one stops the generator).

**Source control**: the repository root ignores every `backups/` directory, which hid the
backups package. `server/.gitignore` re-includes `src/blogwriter/backups/` and `tests/backups/`.
The image build context (`.dockerignore`) copies `server/src` whole, and the wheel ships the
prompts, templates, fonts and standards data.

## 2. Environment per package

Unset means off: the feature's routes answer 503 and its jobs fail `<service>_unavailable`.
Secrets come from Key Vault references and are never logged.

| Package | Variables (default) |
|---|---|
| core | `BW_ENV`, `BW_OWNER_OID`, `BW_COOKIE_KEY` (also derives the LinkedIn confirm-token key), `BW_PUBLIC_ORIGIN`, `BW_BUILD_SHA`, `BW_SPA_DIR`, `BW_COOKIE_TTL_HOURS` (12), `BW_WATCHDOG_THRESHOLD_MS` (500) |
| db | `BW_COSMOS_ENDPOINT`, `BW_COSMOS_DATABASE` (`blog-writer`) |
| ai | `BW_AI_OPENAI_BASE_URL`, `BW_AI_ANTHROPIC_RESOURCE`, `BW_AI_DEPLOYMENT_*`, `BW_AI_BRAKE_*`, overrides (see `docs/v2/phase1/ai-router.md`); images (opt-in): `BW_AI_IMAGE_BASE_URL` (unset: off; the v1 endpoint of the separate East US 2 resource, `https://<resource>.openai.azure.com/openai/v1/`), `BW_AI_DEPLOYMENT_IMAGE` (`gpt-image-2`), `BW_AI_IMAGE_USD_1536X1024` (0.25) and `BW_AI_IMAGE_USD_1024X1024` (0.17): assumed per-image prices (GPT Image high quality, output tokens at USD 40 per 1M) until the bill shows the real ones |
| jobs | `BW_JOBS_*` (see `docs/v2/phase1/jobs.md`) |
| vault | `BW_VAULT_KEYS`, `BW_VAULT_PRIMARY` (needed to connect LinkedIn) |
| notify | `BW_NOTIFY_VAPID_PRIVATE_KEY`, `BW_NOTIFY_VAPID_SUBJECT`, `BW_NOTIFY_PUSH_TTL_SECONDS` (43200), `BW_NOTIFY_PUSH_TIMEOUT_SECONDS` (10) |
| storage | `BW_STORAGE_BLOB_ENDPOINT`, `BW_STORAGE_CONTAINER_MEDIA` (`media`), `_PUBLISHED` (`published`), `_BACKUPS` (`backups`), `BW_STORAGE_MAX_UPLOAD_BYTES` (10485760), `BW_STORAGE_MAX_IMAGE_PIXELS` (12000000). The app identity needs Storage Blob Data Contributor |
| render | `BW_RENDER_*` (see `render.md`) |
| stories | `BW_STORIES_INTAKE_MAX_CHARS` (8000, at most 20000), `BW_STORIES_SEED_MAX_CHARS` (60000) |
| research | `BW_RESEARCH_WEBIQ_ENDPOINT` (`https://api.microsoft.ai`, not the APIM gateway), `BW_RESEARCH_WEBIQ_SCOPE` (`https://api.microsoft.ai/.default`; without it Web IQ stays off), `_WEBIQ_QPS` (15), `_MAX_SOURCES` (12), `_MAX_TOOL_CALLS` (8), `_FETCH_TIMEOUT_SECONDS` (15), `_PASSAGE_MAX_CHARS` (3000), `_QUICK_TIMEOUT_SECONDS` (90) |
| signals | `BW_SIGNALS_MAX_ITEMS_PER_FEED` (50), `_MAX_AGE_DAYS` (14), `_TRIAGE_BATCH` (20), `_MIN_RELEVANCE` (0.5), `_FAILING_AFTER` (5) |
| articles | `BW_ARTICLES_KEEP_VERSIONS` (50) |
| gates | `BW_GATES_SITE_ORIGIN` (same value as `BW_PUBLISH_SITE_ORIGIN`), `BW_GATES_ATLAS_URL` (the ATLAS data file; the egress byte cap must fit it), `_MAX_LINKS_CHECKED` (25), `_LINK_TIMEOUT_SECONDS` (10), `_NOVELTY_CORPUS` (50) |
| linkedin | `BW_LINKEDIN_CLIENT_ID`, `BW_LINKEDIN_CLIENT_SECRET` (Key Vault), `BW_LINKEDIN_REDIRECT_URI` (default `BW_PUBLIC_ORIGIN` + `/api/linkedin/oauth/callback`), `_TIMEOUT_SECONDS` (20), `_CONFIRM_TOKEN_TTL_SECONDS` (600), `_UPLOAD_POLL_SECONDS` (120) |
| backups | `BW_BACKUPS_PAGE_SIZE` (200); the v1 migration CLI alone reads `BW_MIGRATE_V1_*` (`migration.md`) |
| pipeline | `BW_PIPELINE_MAX_REVISE_LOOPS` (2, range 0-2) |
| publish | `BW_PUBLISH_PORTFOLIO_REPOSITORY`, `_REF` (`main`), `_WORKFLOW`, `_TOKEN` (Key Vault), `BW_PUBLISH_SITE_ORIGIN`, `BW_PUBLISH_PREVIEW_WORKFLOW`, `_BUILD_TIMEOUT_SECONDS` (1800), `_VERIFY_TIMEOUT_SECONDS` (900), `_POLL_SECONDS` (20), `BW_PUBLISH_CONTRACTS_DIR` |
| analytics | `BW_ANALYTICS_IMPORT_MAX_BYTES` (5242880), `BW_ANALYTICS_IMPORT_BETA` (false: the LinkedIn export import answers 503 until a real export confirms the format) |
| kb, voice, planner, engagement, illustrations | none (owner values live in settings documents: `ModelPreferences.imagesEnabled` and `BrandKit.artBriefs` for the illustrations) |

Infrastructure follow-ups (outside the server): for illustrations, the `gpt-image-2`
deployment on its East US 2 Foundry resource, `BW_AI_IMAGE_BASE_URL` (and
`BW_AI_DEPLOYMENT_IMAGE` if the deployment is not named `gpt-image-2`) and the app identity's
Cognitive Services OpenAI User role on that resource; set `BW_RESEARCH_WEBIQ_SCOPE` next to the
Web IQ endpoint, `BW_GATES_SITE_ORIGIN` next to `BW_PUBLISH_SITE_ORIGIN`, Key Vault references for
`BW_PUBLISH_PORTFOLIO_TOKEN` and `BW_LINKEDIN_CLIENT_SECRET`, TTL on the `engagement` container
(`defaultTtl = -1`, per-item TTL), and `scheduler_tick_alert_enabled = true`.

## 3. Contract deviations accepted

Each package's report lists its own; the ones that change what other code or the web sees:

- **Problems**: many routes add codes beyond the contract tables (for example kb
  `brand_invalid`, `images_unavailable`; stories `source_required`; signals `duplicate_source`;
  publish `slug_change_unconfirmed`; posts `post_expired`, `lint_failed`; planner `mix_rules`;
  engagement 410 `expired`). The web maps unknown codes to the generic problem text.
- **Job ids**: owner-started runs that the contract calls random use one deterministic id per
  day or minute with `rerun`/`reuse` (voice build and digest, planner generate, analytics eval,
  backups run, signals crawl-now), so a double tap is one job.
- **Research**: the run moves to `succeeded` in `audit`; Web IQ policy refusals do not fall back
  to egress; quick search drops blocklisted claims before storing.
- **Gates**: `run_gates_detailed` (report plus embedding) is what the pipeline uses; slop
  findings are all `warn` with an overridable `slop-over-threshold`; ATLAS ids only warn until
  the data file is fresh.
- **Articles**: `create_article(..., article_id=None)`; restore and identical saves write
  nothing.
- **Publish**: approve behaves like `replace` (the article names its run in `publish.jobId`);
  a missing portfolio configuration parks the job in `waiting_input` instead of failing.
- **LinkedIn**: `issue_confirm_token` takes its key explicitly; upload PUTs carry the bearer
  token, to LinkedIn hosts only; `linkedin.introspect` also sweeps stale publish intents.
- **Engagement**: `POST /api/engagement/extract` reads a screenshot (ENGAGEMENT only, nothing
  stored) so the owner checks the text before creating an item.
- **Status**: services are `{state, reason, expiresAt}`; `runner.schedulerRunning` added;
  `CoreServicesLifecycle` serves the embedder whenever the router has an EMBED route.
- **Storage**: `get_router()` takes no settings; upload limits are a dependency.
- **Posts lint**: more than 2 emoji block approval (the Phase 0 lint warns); AI tells stay
  warnings: they are common words and posts have no override path, so blocking them would
  block posts the owner cannot release. Employer forbidden topics block posts and engagement
  drafts (the articles' rule, `domain.confidentiality.forbidden_topic_findings`).
- **`slot_conflict`** keeps one shape per route (posts approve: `conflicts` as UTC times;
  re-slot: holder post ids; plan approval: `findings[]`; plan edit: post ids): the web reads each
  already, and changing them while it is built in parallel would break it.
- **Images**: `ai.policy.IMAGE_TASKS` is a map from task to the highest data class an image
  request may carry: `engagement` (anonymized screenshots) and `fact_check` (public only: the
  illustrations' "media matches text" check). Illustration routes add `images_disabled` (409),
  `images_unavailable` (503), `images_in_progress` (409, `jobId`), `draft_missing`,
  `illustration_in_use`, `illustration_inserted`, `too_many_media` (409) and
  `heading_not_found`, `invalid_alt_text` (422).
- **Kit**: approving a post whose go-live mode is `kit` moves it straight to the kit
  (`prepared`, `domain.posts.into_kit`); switching a kit post back to one-click enqueues its
  staging and slot check again; an away week moves its approved one-click posts to the kit.
  Reply reminders of a natively scheduled kit post key on its native time T.

## 4. Domain changes made at integration

Applied (with domain tests):

1. `deps.problem_from(WriterConflict)`: extension `state` instead of `status` (the reserved
   problem member made every `read_only_state` a 500). A test renders every mapped problem.
2. `deps.problem_from(BlobStoreUnavailable)`: 503 `storage_unavailable`;
   `storage.StorageUnavailable` subclasses it.
3. `settings.default_pillars()`: the plan's complete pillars are the `PillarSettings` default;
   `kb.PLAN_PILLARS` is derived from it.
4. `signals.Idea.suggested_format` takes post formats (`image_card`); signals now suggests it.
5. `engagement.EngagementItem.draft_text`: up to 1250 characters.
6. `common.MediaRole`: one named type for `MediaRef.role` (storage re-uses it).
7. `stories.StoryEntry.served_by`, `PovEntry.served_by` and `Interview.served_by`; the pipeline
   now records the INTERVIEW call on the interview.
8. `testing.sample_document`: overrides in snake_case or camelCase (singletons, `OAuthState`
   and `SeedText` used to drop snake_case ones into extra fields).
9. Docs: the `Embedder` port and the services table name `RouterEmbedder`; `Signal.source_id`
   may be `scout-<pillarId>`.

Not applied (kept as stored extra fields or package workarounds, all compatible):

- `PageFetcher.fetch(..., query=)`: research passes the Browse query through
  `research.fetch.fetch_page`; widening the port would break every fake.
- Typing `FakeServices` fields as ports: tests read fake-only attributes
  (`classifier.calls`, `egress.requested`).
- Extra fields stored as extras today: `Post.ownerTake`, `CheckState.truncated`,
  `Finding.field` (adding it would change every serialized finding), `PlannedSlot` story, POV,
  idea and article ids, `WeekPlan.servedBy`, `Reminder.dismissedAt`, `EvalResult.errorCode`,
  `InsightSuggestion.value`, `migratedFrom`.
- `PipelineInput.run` (the pipeline parses its job id), `BackupDumpInput`, `jobflow.next_job_id`,
  `Blocklist.next_term`, `StyleGuide.build_job_id`, `PublishedAsset` sizes, `Correction.version`,
  `ScriptedModelRunner.stream` fallback events, `StaticHttp` header recording, the
  `FakeRenderService` PDF metadata.
- `KbSeed`/`VoiceSeed` documents from the v1 migration: they need kb and voice approval routes
  (see gaps).
- `ARTICLE_MACHINE` in_review -> unpublishing for a live article under correction: a state
  machine change needs a product decision.

## 5. Known gaps

- **Illustrations** (opt-in, `blogwriter.illustrations`): not run against the live
  `gpt-image-2` deployment (wire tests replay the v1 images API); the per-image prices are an
  assumption; images are converted to sRGB, stripped of metadata and stored as WebP (quality
  90, a few hundred KB); discarded candidates and images from failed runs keep their blob
  (media garbage collection is deferred). The Draft tab's Images panel drives it.

- **Live checks not run**: Web IQ with the managed identity, the research search-line format,
  every prompt against live models (`BW_LIVE_AI=1` tests in services, signals, posts and
  engagement), screenshot reading, LinkedIn (`BW_LIVE_LINKEDIN=1`) and Blob
  (`BW_LIVE_STORAGE=1`). Chromium is not installed in this shell, so the render tests that
  need it were skipped.
- **I-6 end-to-end test**: done, in `server/tests/e2e/` (the harness is its `conftest.py`:
  `package_routers()` and `build_registry()` on a real `JobsService` over the
  `make_services()` fakes; GitHub and LinkedIn behind `httpx.MockTransport`, the site behind
  the egress fake, a scripted model, a fake clock stepped through every delayed job and
  schedule item). `main.package_routers(publish=...)` and `main.build_registry(publish_runtime=...)`
  are its seams. `tests/test_main.py` still covers the production wiring and boot.
- **I-7** (runner support for `JobDeferred`): brake deferrals still count as attempts, so a
  long brake can quarantine background work (planner, signals).
- **I-8** (indexing exclusions) and **I-10** (projections): list routes read whole documents.
- **I-9** (`linkedin.lint.ai_tell_findings`): voice and engagement keep their parity-tested
  copies.
- **Request body cap**: core has no body-size limit; only the seed, media, screenshot and import
  routes cap their bodies (JSON routes check sizes after parsing).
- **Migration seeds**: `kb_seed` and `voice_seed` documents written by the v1 migration have no
  approve/dismiss routes yet; migrated published posts keep their slugs reserved without an
  article.
- **Publishing** needs portfolio PR B (`correlation_id` input, `bw-updated-at` meta tag, real
  404s) and the PAT expiry is not recorded anywhere (`patExpiresAt` stays null).
- **Product decisions**: an auto-path article approved after Friday's veto notice for the next
  Monday always holds (a notice counts only for the approval it was sent for; a re-approval
  after a pull back or a veto needs a fresh notice); unpublishing a live article under
  correction needs a republish first.
- **Review follow-ups not done**: the boot still starts the scheduler before the first reseed
  (a stale definition can fire once at boot); the System page shows no "schedules out of date"
  or "storage degraded" state; `EmployerPolicy.noEmployerBranding` is stored but not enforced
  (the policy has no employer name to check against; the carousel templates carry no employer
  mark); away weeks get no batch "schedule natively" Today item (the kit posts show at their
  slots); polls cannot be authored yet (contract section 13). The web API types need `npm run
  gen:api` for the new `POST /api/ideas/repurpose` and the `publish_in_progress` problem.
- **Thresholds** (gates slop budget, rhythm, specifics) and the ATLAS/OWASP seed lists need
  tuning and confirmation against the eval set.
