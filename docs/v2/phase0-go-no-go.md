# Phase 0: go/no-go matrix

The status of every Phase 0 risk in the v2 plan. Spike reports are in `docs/v2/spikes/`. The Terraform platform
slice is in `infra/v2/` and has not been applied yet.

## Done on 2026-11-25
| Item | Result |
|---|---|
| Contain v1 | `cicd-main.yml` runs only when triggered by hand. `infra/terraform/outputs.txt` is no longer tracked. `ca-portfolio-dev` is deleted: it was only ever a hello-world placeholder, with no image in ACR and no custom domain. **Correction:** `ca-blog-writer-dev` was *not* down. Its hello-world revisions never became ready, so revision `0000034` with the real image kept serving `writer.jatinmadan.com`. **Restored 2026-11-26** to image `blog-writer-webapp:20260402095033`, min 1 / max 3, revision `0000038`, healthy. `external-id-demo` is untouched. Configs saved to `backups/v1-azure/` (gitignored). |
| v2 database | **Created 2026-11-26** with the `infra/v2/data` root: `cosmos-blogwriter-v2-7c3167` in `rg-blog-writer-dev`. Serverless, Continuous7Days, key auth disabled, 15 containers, owner Data Contributor role, CanNotDelete lock. An Entra write, read and delete smoke test passed. |
| Lab constraints found | The subscription policy allows only certain regions (West US 3 and Sweden Central are blocked), requires the tags `Purpose`, `Owner` and `Expiry Date` on every resource group, enforces disabled local auth on AI Services, and requires HTTPS on storage. Only BizTalk Contributor is denied as a role. `Microsoft.WebIQ` is not registered. |
| Region | **East US**. It has GlobalStandard `gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6-luna`, `gpt-5.6-sol`, `text-embedding-3-large`, `claude-opus-5-5` and `claude-sonnet-5-5`. `gpt-image-2` exists only in East US 2, so it would need an optional second resource. |
| v1 data location | Database `blog-writer` in the `dmigdatabase` Cosmos account (DMig_group). DMigrator shares that account, so it is read-only migration input and v2 never changes it. |
| `infra/v2` Terraform | 48 resources across 2 roots. `fmt` and `validate` pass. An independent review applied 8 fixes. Not applied to Azure. |

## Local spikes
| Spike | Verdict | Evidence | Changes it forces |
|---|---|---|---|
| Editor dialect (TipTap v3 ↔ markdown-it-py) | **Go with changes** | 310 web tests and 276 Python tests pass, plus 1 expected failure that documents a known gap. Re-run 2026-11-25. All 22 corpus files round-trip byte-stable. Hostile input is neutralized: 24 Markdown and 20 HTML XSS cases. Everything is MIT/ISC/BSD; nothing paid. | See "Decisions" below. Also: `@tiptap/markdown` needs 26 overrides, so pin `@tiptap/*` and `marked` exactly and allow updates only when the corpus, parity and property suites pass. Build a Python canonical normalizer that must reproduce `corpus/normalized/` byte-for-byte. Lint on every save. `ArticleCodec.check()` warns when content would be lost. Add the renderer's sanitizer-profile additions. Add pip-audit and the 512 KB cap. |
| Chromium render (carousel PDF, OG, Mermaid) | **Go with changes** | 149 tests pass. Re-run 2026-11-25. One 12-slide deck takes about 0.8 s. The whole process tree peaks at about 0.8 GiB for one job and about 1.5 GiB for two. Network isolation holds layer by layer, WebRTC included. Chromium never sees app secrets. | **Render concurrency is 1 by default**, with the driver and browser launched per job. Use `@mermaid-js/tiny`, loaded through `add_script_tag` under a hash-pinned CSP. Run V8 with `--jitless` and a 256 MB heap cap. Pages are 1080×1350 pt, printed on a 15in × 18.75in sheet at 4/3 scale. Inter needs `calt` off and no `tnum`, or Private Use Area glyphs end up in the PDF text layer. Carousels allow at most 4 steps and 5 sources; recap is the last content slide, with an optional Sources slide after it. Rasters are capped at 12 MP. |
| LinkedIn little-text serializer + lint | **Go with changes** | 369 tests pass; ruff and strict pyright are clean. Adversarial timing tests run under 10 ms, and a 30-sentence security-prose corpus produces no findings. The dry run shows the exact `/rest/posts` and `/rest/documents` payloads. Report: `docs/v2/spikes/littletext.md`. | The 3,000 limit is counted on the escaped payload in UTF-16 units until the live test confirms how LinkedIn counts. Bait rules are anchored to sentence starts. Approvals store both the display text and the API payload. |

### Decisions taken from spike findings (defaults; can be changed)
- **Undefined citations:** the Python normalizer **reports** an unresolved `[^label]` as a gate-5 error. It does not escape it. A hallucinated citation therefore blocks publishing instead of shipping as literal text.
- **Linked images** (`[![alt](src)](href)`): not part of the dialect. They get their own lint error, `linked-image`.
- **Editor hostile input:** normalize in a Web Worker with a timeout, and refuse to open documents over 128 KB.
- **Diagram label size:** compute the effective label size after the diagram is scaled to fit. Warn below 22 px, block below 18 px on the 1080 px canvas. Both thresholds are configurable. Lint also covers diagram labels, the deck title and brand fields for word limits, domain-like tokens and invisible characters.

## Cloud spikes (blocked on owner actions or on applying `infra/v2`)
| Spike | Needs | Status |
|---|---|---|
| `gpt-6.2-sol` + `web_search` reasoning behaviour; refusal behaviour; schemas compile on both providers | Foundry resource and deployments (applying `infra/v2`) | Not started |
| Full Opus 5.5 draft at production `max_tokens`; quota and 429 behaviour | Claude Marketplace deployment, which the owner creates in the portal | Not started |
| Web IQ Browse with managed identity | Register `Microsoft.WebIQ`, set `enable_webiq`, bind the identity in the portal | Not started |
| Chromium sandbox and memory inside the real container at 2 GiB | ACR build of `spikes/render/Dockerfile.draft` plus a test container app | Not started |
| Easy Auth: XHR on an expired session, #1815 soak, `cookieExpiration`, LinkedIn callback, forged headers, identity as federated credential | Applying `infra/v2` plus an Entra app registration | Not started |
| Phone: PWA push on iPhone and Android, re-login that keeps the publish intent | Easy Auth app deployed | Not started |
| SSE past 240 s on ACA | Easy Auth app deployed | Not started |
| LinkedIn document post, escaping golden test (post, then delete), mobile PDF attach and scheduling, drafts, prefilled composer, analytics export sample | Owner's LinkedIn token; LinkedIn app checks | Not started |
| Claude quota approval (go/no-go for Phase 4) | The owner files the request | Not started |
