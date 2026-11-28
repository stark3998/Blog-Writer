# Phase 1: platform and security baseline (status 2026-11-26)

## Built
Each item was built by its own agent, reviewed independently, then integrated.

| Area | Where | Notes |
|---|---|---|
| **Server core** | `server/src/blogwriter/core`, `api` | Auth: Easy Auth principal with a fail-closed owner check, plus the `__Host-bw` cookie, which rides out Easy Auth bug #1815. CSRF: `GET` and `HEAD` only are safe. Structured JSON logs with a field allowlist. Strict CSP. SPA deep-link fallback. Loop-lag watchdog. App factory. Docs: [core.md](core.md). |
| **Vault, egress, notify** | `core/crypto.py`, `core/egress.py`, `notify/` | AES-GCM token vault with key ids. An SSRF-safe client that pins the IP, re-checks every redirect hop, and caps bodies at 5 MB and requests at 15 s. Web push carries a title and a link only. `alert_event` log lines feed Azure Monitor. Docs: [egress-crypto-notify.md](egress-crypto-notify.md). |
| **Store** | `server/src/blogwriter/db` | `CosmosStore` (Entra ID, ETag CAS, parameterized filter DSL, `ensure_containers`) plus `MemoryStore`, both behind one contract suite. Docs: [db.md](db.md). |
| **Jobs** | `server/src/blogwriter/jobs` | Crash-only runner: leases, fencing, durable writes and remote ids recorded before awaiting. Also `waiting_input`, quarantine, the crash-loop guard, a DST-safe scheduler with maximum lateness, the broker, and the resumable SSE stream at `/api/events`. Docs: [jobs.md](jobs.md). |
| **AI** | `server/src/blogwriter/ai` | OpenAI Responses (Azure v1, `store=False`) and Claude on Foundry providers. A per-provider schema compiler. Normalized stream events. Router with sensitivity policy, refusal and quota fallback (partial output discarded; `review_only`), `served_by`, per-model semaphores and limiters, the cost ledger and the spend brake. Docs: [ai-providers.md](ai-providers.md), [ai-router.md](ai-router.md). |
| **Contracts** | `contracts/` | Frozen `published-blogs` v2 JSON Schema, the sanitizer profile and shared fixtures. `blogwriter.publish.contract` validates documents, including a self-contained HTML audit. Docs: [contracts-ci.md](contracts-ci.md). |
| **CI** | `.github/workflows/ci.yml` | Server and web jobs, path filters, actions pinned by SHA, `PYTHONASYNCIODEBUG=1`. Not yet run on GitHub. |
| **Web** | `web/` | New design system: Tailwind v4 tokens, three pillar accents, light and dark themes. Five areas: Today, Calendar, Studio, Library, Settings. Desktop sidebar and phone tab bar, command palette, PWA shell. Data layer: TanStack Query, the auth redirect with a loop guard, a resumable SSE client and the job timeline. Docs: [web.md](web.md), [web-data.md](web-data.md). |
| **Wiring** | `server/src/blogwriter/main.py` | `uvicorn blogwriter.main:app --factory` starts db, then jobs (with the quarantine alert), then AI (only when `BW_AI_*` is set). Its ledger writes to Cosmos `usage`. Third-party SDK loggers are capped at WARNING. |

## Verified on 2026-11-26
- **Server:** ruff, format and strict pyright are clean. **2,657 tests pass** under `PYTHONASYNCIODEBUG=1`. The live Cosmos suites (store contract and the two-runner job test) pass against `cosmos-blogwriter-v2-7c3167`: **13 tests**.
- **Web:** lint, typecheck, **341 tests** and the build all pass. `verify-dist` confirms the build is self-contained: no CDN, no inline scripts, no `eval`. First load is 187 kB gzipped.
- **Local end-to-end smoke** (real uvicorn plus real v2 Cosmos plus the built SPA):
  - `/api/health` and `/api/me` work.
  - `/today` deep-links with the strict CSP.
  - `/api/events` streams SSE.
  - A foreign `Host` header gets 401.
  - The job runner records its boot in Cosmos, with no error logs.
  - Today renders the signed-in name and the build SHA, on desktop and on a phone.

## Known issues (non-blocking)
- Under `style-src 'self'` the browser console reports two blocked inline `<style>` injections, from Sonner and from Radix scroll lock. Their CSS is bundled statically, so nothing breaks.
- React Router warns that the `/` redirect route has no element.

## Not done: needs the owner's go-ahead on Azure resources
These are the remaining Phase 1 exit items. They all need the platform resources you deferred ("only the DB for now"):
- managed identity, Easy Auth on the existing app, Key Vault, Foundry in East US and storage;
- a deploy, OIDC CI identities and the Monitor alert rules;
- portfolio PR A (OIDC read of the new Cosmos account);
- the `infra/v2` platform root refactor so it reuses `rg-blog-writer-dev`, `cae-blog-writer-dev`, `ca-blog-writer-dev` and the ACR. **Its env names must switch to the `BW_*` names the app reads.**
