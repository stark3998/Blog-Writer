# Phase 0 spike: Chromium rendering (carousels, OG cards, Mermaid)

**Question:** can Chromium in the single v2 container render carousels, OG cards and Mermaid diagrams
safely within 2 GiB?

**Verdict: GO WITH CHANGES.** The isolation model in the plan works and is now tested layer by layer.
Rendering is fast: 0.82 s median for a 12-slide deck with a diagram, on a Mac. The memory budget holds
with **one** render job at a time. The changes that make it hold are listed under
[Conditions for go](#conditions-for-go). The two things this spike could not prove, because Docker was
not available, are the Linux memory numbers and whether ACA lets Chromium use its own sandbox. Both go
into the plan's existing container spike, and the image and bench script for it are ready.

Code: `spikes/render/` (uv project, Python 3.12). Plan sections: "Security model" > "Render isolation",
"LinkedIn Studio" > "Carousels", "Target architecture" > "Runtime".

---

## What was built

| Piece | Where | Notes |
|---|---|---|
| Slide spec models | `src/render/spec.py` | Pydantic v2, 10 slide types, plain-text fields only, hard length limits sized so the longest value still fits |
| Plain-text normaliser | `src/render/text.py` | NFC; rejects control characters; strips invisible format characters (bidi overrides, zero-width) that would let a blocklisted name slip past the confidentiality gate |
| Brand kit + WCAG check | `src/render/brand.py` | Colours must be `#RRGGBB` (blocks CSS injection); every text/background pair is checked at 4.5:1 or the kit is rejected |
| Templates | `src/render/templates/` | Jinja2, `autoescape=True` everywhere, `StrictUndefined`. cover, statement, list, steps, framework (2x2 and layered), diagram, stat, quote, recap, sources; 1200x630 OG card |
| Fonts | `src/render/fonts/` | Inter 4.1 (OFL-1.1): Regular, SemiBold, Display Bold, Display ExtraBold. SHA-256 checked at load, inlined as `data:` URIs under private family names |
| Browser lifecycle and isolation | `src/render/browser.py` | Per-job Chromium, minimal env, route guard, JS-off contexts, job semaphore, stale-home sweeper |
| Carousel renderer | `src/render/carousel.py` | `render_carousel(spec_json) -> (pdf_bytes, first_slide_png_2x)` |
| OG renderer | `src/render/og.py` | `render_og_card(spec) -> png` (1x or 2x) |
| Mermaid | `src/render/mermaid.py` | Separate JS context, pinned bundle, CSP hash pin, strict config, source neutralisation, sanitised SVG and 2x PNG |
| SVG sanitiser | `src/render/svg_sanitize.py` | lxml allowlist sanitiser plus an independent structural checker |
| PDF finishing | `src/render/pdf.py` | Page geometry and active-content checks; metadata exactly `{Title, Author}` |
| Lint | `src/render/lint.py` | The plan's carousel rules (title ≤ 58 characters, cover ≤ 10 words, ≤ 35 words per slide, 8-12 slides, recap last, no domain-like tokens) |
| Telemetry | `src/render/telemetry.py` | Stage timer; memory sampler (RSS, macOS footprint / Linux PSS, cgroup v2 `memory.current`/`memory.peak`) |
| Bench | `bench/measure.py`, `bench/render_sample.py` | Timing and memory scenarios; visual review output in `out/` (gitignored) |
| Dockerfile draft | `Dockerfile.draft` | Not built. Pinned bookworm-slim, `/ms-playwright`, headless shell, tini, non-root, digest TODO |
| Tests | `tests/` | 101 pytest tests, about 16 s. ruff and pyright (strict) are clean |

### Run it

```bash
cd spikes/render
uv sync                                              # Python 3.12, locked
export PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright  # spike-local, like /ms-playwright in the image
uv run playwright install chromium --only-shell      # what worked on macOS (see below)
(cd vendor && npm ci --ignore-scripts)               # pinned Mermaid bundles
uv run pytest                                        # 101 tests
uv run ruff check . && uv run pyright                # lint + strict types
uv run python bench/render_sample.py                 # out/deck.pdf, out/slide-NN.png, out/og.png
uv run python bench/measure.py --runs 10             # numbers below; out/bench.json
```

## Versions

| Component | Version | How it is pinned |
|---|---|---|
| Python | 3.12.13 (Homebrew); image `python:3.12.13-slim-bookworm` | `.python-version`, `requires-python` |
| uv | 0.12.5 | image `ghcr.io/astral-sh/uv:0.12.5` |
| Playwright (Python) | 1.65.2 | `==` in `pyproject.toml` + `uv.lock` |
| Chrome Headless Shell | 159.0.8154.6 (Playwright `chromium-headless-shell` revision 1255) | determined by the Playwright version |
| Jinja2 / MarkupSafe | 3.1.6 / 3.0.4 | `==` + lock |
| Pydantic | 2.14.4 | `==` + lock |
| pypdf | 7.1.1 | `==` + lock |
| lxml | 6.1.3 | `==` + lock |
| psutil | 7.2.2 | `==` + lock |
| Mermaid | 12.3.0 (`mermaid` and `@mermaid-js/tiny`, MIT) | `--save-exact`, `package-lock.json` (sha512), plus SHA-256 of each shipped file in `settings.py` |
| Inter | 4.1 (OFL-1.1) | release zip SHA-256 `9883fdd4…6b11e`, per-file SHA-256 in `fonts/manifest.json` |
| Dev | pytest 9.2.0, pytest-asyncio 1.5.0, Pillow 13.0.0, ruff 0.17.6, pyright 1.1.414, types-lxml 2026.2.16 | `==` + lock |

**What worked for the browser on macOS (arm64):**
`PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright uv run playwright install chromium --only-shell`.
It downloaded Chrome Headless Shell from `cdn.playwright.dev` in 4.3 s (206 MB on disk) and also pulled
FFmpeg (2.9 MB), which we don't use. `--with-deps` was not used on macOS (it installs Linux system
packages); in the image it installs the Debian libraries. `headless=True` picked the headless shell automatically, and Chromium's own sandbox
worked on macOS (`chromium_sandbox=True`).

## Security model as implemented

The isolation layers below are independent: each is enough on its own for the content we render, and
each has its own test. The last two rows are output guarantees rather than isolation.

| Layer | Implementation | Test that proves it |
|---|---|---|
| Plain text + autoescape | Pydantic plain-text fields; Jinja2 `autoescape=True`; CSS built only from validated hex values | `test_xss.py`: 14-payload corpus in **every** field of every slide type and the brand fields. The markup contains only the template's tags (no `script`/`a`/`iframe`/`svg`/`base`/`link`, no `on*`, one `img` = the diagram); payloads appear verbatim in the PDF text layer |
| Escaping holds without the other layers | (test only) | The hostile deck loaded **with JS on and the CSP removed**: 0 scripts, 0 handlers, 0 links, 0 dialogs, 0 network attempts |
| Document CSP | `default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'`. The same HTML serves the sandboxed Studio preview | `test_csp_alone_blocks_remote_loads_before_the_route_guard` |
| JS off | `java_script_enabled=False` for slide, OG and SVG-to-PNG pages | `test_static_pages_never_run_page_script` (inline script and `onerror` stay dead). Our own fit check and font wait still work because Playwright evaluates in a DevTools world |
| Route guard | `context.route('**/*')` aborts everything and records each URL. Chromium never routes `data:` URLs, the only thing we reference | `test_route_guard_aborts_http_and_https_images_and_reports_them`: a test template references `https://` and loopback images, CSS `background`, `@import`, `<link>`, `@font-face`, `<iframe>`, prefetch. All are aborted and recorded; the canary server gets 0 hits |
| Launch flags | `--host-resolver-rules=MAP * ~NOTFOUND` (no DNS), a dead-end proxy (Playwright forces loopback through it too), `--js-flags=--max-old-space-size=256 --jitless` | `test_launch_flags_alone_stop_requests_when_no_route_is_installed`; a positive control proves the canary does see a real request from plain Chromium; `test_mermaid_engine_runs_jitless_without_webassembly` |
| Minimal env | Chromium gets only `PATH, HOME, TMPDIR, LANG, TZ` (+ XDG on Linux), with a throwaway per-job HOME | `test_chromium_does_not_inherit_app_secrets` reads the live Chromium processes' environment: canary secrets absent |
| Per-job lifecycle | Driver + Chromium launched per job and torn down; temp home deleted; `sweep_stale_homes()` at start-up for SIGKILL leftovers | `test_session_cleans_up_browser_and_temp_home`, `test_sweeper_removes_only_stale_job_homes` |
| Mermaid: own context | Separate JS-enabled context (separate renderer process from the slide pages) | `test_hostile_source_is_neutralised_end_to_end` |
| Mermaid: source neutralisation | Strips front-matter config, `%%{init}%%` directives, `click`/`link`/`callback` statements, and style statements with `url()`; each removal returns a warning | unit tests and the end-to-end hostile diagram |
| Mermaid: locked config | `securityLevel:'strict'`, `htmlLabels:false` (top level and per diagram), sensitive keys in `secure`, `maxTextSize` / `maxEdges` limits, `suppressErrorRendering` | `test_locked_config_holds_even_if_a_directive_reaches_mermaid`: neutralisation skipped, hostile `%%{init}%%` sets `securityLevel: loose`, `htmlLabels: true` and `themeCSS` with `url()`; no `foreignObject`, no hostile CSS, `securityLevel` stays `strict` |
| Mermaid: pinned bundle **and** pinned CSP | Bundle SHA-256 checked before use; injected with `add_script_tag`; page CSP `script-src 'sha256-<bundle>'` | `test_bundle_is_the_only_script_the_mermaid_page_will_run`: any other script is refused by Chromium |
| Mermaid: inert extraction + sanitiser | SVG re-parsed with `DOMParser` (inert), serialised as XML, then the lxml allowlist sanitiser (no DTD/entities; drops `script`, `foreignObject`, `image`, animation, unknown elements; unwraps `<a>`; `href` local-only; no `on*`; CSS `url()` local-only, no `@import` or escapes) | `test_svg_sanitize.py` (18 tests with hand-made attack SVGs); `assert_svg_safe` on Mermaid output in the tests |
| Diagram embedding | Sanitised SVG (with Inter embedded) shown as `<img src="data:image/svg+xml;base64,…">`. SVG-as-image never runs script or fetches | `test_diagram_is_embedded_in_the_slide_as_a_data_uri_img` |
| Output checks | PDF: exact page count and size, no `/Annots`/`/AA`/`/OpenAction`/`/AcroForm`/JavaScript/embedded files, metadata exactly Title + Author, no Producer/Creator/dates even as orphaned objects. PNG: only `IHDR`/`IDAT`/`IEND` chunks | `test_carousel_pdf.py`, `test_rasters_carry_no_metadata_chunks` |
| Fail closed on layout | Every bounded box is measured after layout; any overflow raises `LayoutOverflowError`, so clipped slides never ship | `test_overflow_fails_closed_instead_of_clipping`, `test_longest_allowed_text_in_every_field_still_fits` |

In an early experiment without our neutralisation, Mermaid 12's strict mode alone already defused
hostile labels and links: the `<script>` label came out empty, `<img onerror>` became escaped text and
the `javascript:` link lost its `href`. Our layers are independent defence in depth, not the only line.

## Findings worth knowing

1. **Exact 1080x1350 PDF pages need a trick.** `page.pdf(width="1080px")` gives 810 x 1013.04 pt pages
   (Chromium sizes in inches and rounds the height). Printing the 1080 px layout on a 15 in x 18.75 in
   sheet at `scale=4/3` gives exactly **1080 x 1350 pt** on every page, with no extra pages. pypdf
   verifies this in the renderer and in the tests.
2. **OpenType alternates corrupt the PDF text layer.** With Inter's default contextual alternates
   (`calt`), Chromium/Skia writes **Private Use Area code points** into the text layer for `:` between
   digits, `->`, and brackets next to capitals. `font-variant-numeric: tabular-nums` does the same to
   every digit (slide numbers came out as " / "). The plan's confidentiality gate scans the PDF text
   layer, so this hides text from it. Fix: `font-feature-settings: "calt" 0` in slides, OG and the
   Mermaid SVG, and no `tnum`. A test asserts the text layer contains every slide word and no PUA
   characters.
3. **The PDF bytes are deterministic.** Chromium writes no `/ID`. With the dates stripped, the same
   spec gives **byte-identical PDFs and PNGs** across fresh browsers, including 3 concurrent ones. An
   approval bound to the SHA-256 of the media therefore survives the T-60 staging re-render. It changes
   when Chromium changes (a Playwright bump), which is a re-approval.
4. **The Mermaid bundle size drives memory, through the Playwright driver.** Injecting the 5.5 MB
   `mermaid.min.js` grew the Node driver from 130 to 418 MiB and kept it there until the driver exited.
   Capping the driver's V8 heap (`NODE_OPTIONS`) barely helped. **`@mermaid-js/tiny@12.3.0`** (the same
   release without mindmap, architecture and KaTeX; 2.85 MB) halves the driver's peak (484 → 257 MiB)
   and cuts a render from 0.85 to 0.58 s. Tiny is now the default; the full build is loaded only when a
   diagram needs it (`needs_full_bundle`).
5. **`page.evaluate(bundle)` doesn't work for Mermaid 12.** Playwright wraps string expressions in a
   strict-mode `eval`, so the bundle's top-level `var` never becomes global. `add_script_tag` (DOM
   insertion, no HTML parsing) works, and allows the CSP hash pin.
6. **`--jitless` is nearly free.** Mermaid time went 0.316 → 0.320 s for a small diagram and 0.51 →
   0.60 s for 60 nodes; a whole deck 0.518 → 0.530 s. It also removes WebAssembly. It is on by default.
7. **Mermaid 12 defaults needed tuning for slides:** the "neo" look, and edge-label plates at
   `opacity: 0.5` so edges strike through the labels. We set `look: classic` and trusted `themeCSS`
   (opaque plates, 2.5px strokes), scale arrowheads with the type size, and use a 26px label size for
   phone legibility.
8. **The worst-case layout test found real overflow:** 5 steps (+212px) and 6 sources (+268px) didn't
   fit at the 28px floor. Limits are now 4 steps and 5 sources (publisher ≤ 40, title ≤ 100). Both are
   far beyond what the 35-word lint allows.
9. **Plan rule conflict:** "the last slide is a recap" and "carousels get a Sources slide". The lint
   resolves it as: the recap closes the deck, and an optional sources slide may follow it, and only
   there.
10. **pypdf 7 details:** `remove_orphans` was renamed `remove_unreferenced`, and `PdfWriter`
    downgrades the header to `%PDF-1.3` unless you copy the reader's (`%PDF-1.4`). Both are handled.
11. **macOS memory accounting:** USS is denied for Chromium's sandboxed children, and RSS sums count
    the shared Chromium framework once per process. Telemetry therefore also reports macOS
    `phys_footprint` (via `proc_pid_rusage`; what Activity Monitor shows), Linux PSS, and cgroup v2
    `memory.peak`.

## Numbers (this Mac)

Apple Silicon (arm64, 10 cores, 16 GiB), macOS 27.3. 12-slide sample deck covering all 10 slide types,
one Mermaid diagram, tiny bundle, `--jitless`, sandbox on, 10 runs per scenario. Output: PDF 46.9 KB,
cover PNG 2160x2700 (161 KB).

| Scenario | Median | p90 | Notes |
|---|---|---|---|
| **Per job (production model: driver + Chromium launched per job)** | **0.82 s** | 0.83 s | cold first job 0.95 s |
| Long-lived driver, Chromium per job | 0.65 s | 0.66 s | saves the 0.16 s driver start |
| Warm browser, render only | 0.54 s | 0.57 s | |
| 3 decks at once, semaphore(2), per-job lifecycle | 1.90 s wall | | 0.63 s per deck; max in flight 2 |
| 6 decks at once, semaphore(2) | 3.15 s wall | | 0.53 s per deck; memory peak flat |
| OG card 1200x630 | 0.10 s | 0.10 s | warm |
| One diagram → sanitised SVG + 2x PNG | 0.44 s | 0.44 s | includes its own context + bundle |

Per-job stage medians (s): driver start 0.16, browser launch 0.08, Mermaid context + bundle 0.26,
Mermaid render 0.09, HTML build 0.001, set_content + fonts 0.06, fit check 0.001, cover PNG 0.09,
PDF 0.012, PDF finishing 0.007, teardown 0.06.

**Peak memory.** RSS sums are an upper bound (shared pages counted per process); footprint is the better
estimate of what a cgroup charges.

| Scenario | Python | Playwright driver (RSS / footprint) | Chromium tree (RSS / footprint) | Whole tree RSS |
|---|---|---|---|---|
| Idle Python with render stack imported | 109 MiB | - | - | 109 MiB |
| 1 job (per-job model) | 170 MiB | 250 / 206 MiB | 409 / 195 MiB (4 processes: browser 111, gpu 153, network 36, renderer 152) | 808 MiB |
| 2 jobs in flight (3 or 6 queued, semaphore 2) | 202-222 MiB | 476-499 / 388-411 MiB | 791 / 360 MiB (8 processes) | 1,466-1,502 MiB |
| 1 job with the **full** 5.5 MB bundle (before the change) | 183 MiB | 484 MiB RSS | 421 MiB RSS | about 1,090 MiB |

The 6-job peak equals the 3-job peak: the semaphore bounds memory, not the queue length.

### Extrapolation to the 2 GiB container (1 vCPU / 2 GiB ACA)

Inputs:

- **App process:** importing the plan's server stack (FastAPI, uvicorn, httpx, openai, anthropic,
  azure-identity/cosmos/storage-blob, markdown-it, nh3, Pillow, feedparser, bs4, playwright, pypdf, lxml)
  measured **212 MiB**. Add the measured render-driven growth of the Python process (about 110 MiB) and
  about 130 MiB of other runtime working set, for **450 MiB**.
- **Per job (driver + Chromium):** 659 MiB on the RSS upper bound and 401 MiB on the footprint
  estimate.
- **Linux uncertainty:** a 1.25x factor on the render part (allocator, SwiftShader GPU process,
  fontconfig).

| Concurrent render jobs | Pessimistic (RSS x 1.25) | Expected (footprint x 1.25) | Verdict |
|---|---|---|---|
| **1 (new default)** | 450 + 824 = **1,274 MiB (62%)** | 450 + 501 = 951 MiB (46%) | fits with about 770 MiB headroom |
| 1, diagram needs the full bundle | 450 + 1,131 = 1,581 MiB (77%) | not measured | fits |
| 2 | 450 + 1,648 = **2,098 MiB (102%)** | 450 + 1,003 = 1,453 MiB (71%) | **not safe until measured** |

Wall time on ACA: everything above ran across several fast cores. On 1 vCPU, expect roughly 2-4x
slower, about **2-3.5 s per 12-slide deck**. That is fine for previews and for staging at T-60 min.
A second concurrent job adds no throughput on 1 vCPU (it only time-slices), so concurrency 1 costs
nothing.

## Risks and mitigations

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| **Linux numbers unmeasured** (no Docker on this machine) | certain / medium | Run `Dockerfile.draft` with `--cpus=1 --memory=2g --memory-swap=2g`. `bench/measure.py` records cgroup `memory.peak` per scenario; it is already the plan's container spike. Keep concurrency at 1 until done |
| **No Chromium sandbox in ACA** (user namespaces usually blocked by the runtime's seccomp profile) | likely / high if exploited | The renderer refuses to fall back silently; `BW_RENDER_CHROMIUM_SANDBOX=0` is an explicit, reviewed decision. Without the sandbox, a V8 exploit reachable through the **JS-enabled Mermaid page** (AI-written diagram source can be prompt-injected) would run as the app's UID. It could then read the app's or driver's `/proc/<pid>/environ` (ACA `IDENTITY_HEADER`) and call the managed-identity endpoint. Compensating controls, in order: `--jitless` (on), V8 heap cap (on), pinned bundle + hash-locked CSP (on), Chromium patched by the weekly rebuild; at app start, read the identity env into the credential, `os.environ.pop` it before any driver starts, and `prctl(PR_SET_DUMPABLE, 0)` so same-UID processes cannot read `/proc/<app>/environ`; optionally allow only flowchart/sequence/class/state diagram types |
| Playwright driver inherits the app environment | certain / low | The driver is our own trusted code; Chromium gets a minimal env (tested). The env-pop above also removes secrets from the driver |
| Driver memory retained after a bundle injection | certain / medium | Per-job driver (memory returns when it exits); tiny bundle; full bundle only on demand |
| Browser download provenance (`cdn.playwright.dev`, no checksum we control) | low / high | Revision pinned via the Playwright version in `uv.lock`; optionally mirror the zip to ACR/Blob and verify a pinned SHA-256 |
| Base images not digest-pinned yet | certain / medium | TODO block in `Dockerfile.draft`; Renovate keeps digests current |
| Emoji / non-Latin glyphs outside Inter render as fallback or tofu | possible / low | Add `fonts-noto-color-emoji` (and measure image size) or lint emoji out of slide text |
| Dense or LR diagrams become unreadable on phones | likely / low | 26px labels, arrowheads scaled; editorial guidance: top-down, ≤ about 8 nodes. Follow-up: lint effective label size from the SVG viewBox and slide frame |
| Mermaid upgrade changes output or adds elements | certain on upgrade / low | Sanitiser drops unknown elements (fails safe, possibly ugly); golden SVG and visual review per upgrade; bundle SHA must be re-pinned deliberately |
| Re-enabling `calt`/`tnum` in a template | possible / high for the gate | Comment at the CSS rule plus the text-layer test (no PUA, all words present) |
| Studio preview line breaks differ in Safari (`text-wrap: balance/pretty`) | possible / low | Final review uses the Chromium PNG (the plan's publish page already shows the first PDF page). Run the preview-vs-PDF pixel-diff invariant in Chromium |
| Visual design is a placeholder | certain / low | Tokens and templates are brand-kit driven; replace with the owner's kit (owner action item 7). Short lint-compliant slides leave space; add density-aware type sizes then |

## Conditions for go

1. **One render job at a time** (`BW_RENDER_MAX_CONCURRENT_JOBS=1`, now the default). Allow 2 only if
   the container run shows cgroup `memory.peak` ≤ about 1.5 GiB for two concurrent jobs with the app
   loaded.
2. **`@mermaid-js/tiny` by default**, full Mermaid only for mindmap, architecture or KaTeX. Both are
   SHA-pinned; the Mermaid page's CSP pins the bundle hash.
3. **Driver and Chromium per job.** No long-lived Playwright driver: zero idle memory, and crash-only.
4. **Container spike** with this image and bench at 1 vCPU / 2 GiB: record the cgroup peaks and the
   sandbox result. If the sandbox is unavailable, adopt the compensating controls listed in the risk
   table, and record that decision in the plan.
5. **Keep `calt` off and `tnum` out**, and keep the text-layer test as a CI invariant.
6. **Digest-pin the base images** before the image ships.

## Recommended plan changes

- *Security model > Render isolation:* add the V8 flags `--jitless` and
  `--max-old-space-size=256`; DNS `MAP * ~NOTFOUND`; a dead-end proxy; a minimal Chromium environment
  with a per-job HOME; a Mermaid page CSP that allows only the pinned bundle hash; inert `DOMParser`
  extraction before sanitising; overflow fails closed.
- *Security model:* if the ACA sandbox is unavailable, pop the identity env vars after building the
  credential and set `PR_SET_DUMPABLE=0` on the app process.
- *Runtime:* "at most 1-2 pages" becomes "1 render job by default, 2 after measurement". Launch the
  Playwright driver per job too, not only Chromium. Vendor Mermaid as two pinned files (tiny + full)
  from a Node build stage.
- *Carousels:* the recap is the last content slide, and an optional Sources slide may follow it. Add
  the hard limits steps ≤ 4 and sources ≤ 5. PDF pages are 1080x1350 **pt**.
- *Verification:* add invariants for the text layer (no Private Use Area code points; every slide word
  present), deterministic PDF bytes per Chromium build, PNGs with no metadata chunks, and Chromium
  inheriting no app secrets.

## Test inventory (101 tests)

| File | Count | Covers |
|---|---|---|
| `test_carousel_pdf.py` | 9 | 12 pages of exactly 1080x1350 pt; 2x cover PNG; metadata exactly Title/Author; no links or active content; < 10 MB with 3 diagrams; byte-identical re-render; faithful text layer; top-level JSON API |
| `test_xss.py` | 12 | XSS corpus in every field: markup structure, no execution with JS on and no CSP, literal text in the PDF; invisible characters stripped; control characters rejected; brand colours cannot inject CSS |
| `test_network_isolation.py` | 7 | route guard aborts and records http/https/CSS/import/link/iframe loads; CSP alone; launch flags alone; positive control; flags present; jitless (no WebAssembly); static pages never run page script |
| `test_mermaid.py` | 17 | neutralisation units; hostile diagram end to end (no dialogs, no page errors, safe SVG, no loads); locked config holds when directives reach Mermaid; CSP pin refuses other scripts; SVG/PNG sizes and embedded font; invalid source; data: URI embedding; bundle integrity; tiny/full selection; mindmap via the full bundle |
| `test_svg_sanitize.py` | 18 | script, foreignObject, image, animate/set, iframe, feImage, unknown elements; `on*` and script URLs; links unwrapped; CSS `url()`/`@import`/escapes; DTD/entities/XXE/malformed/non-SVG/oversized rejected; checker semantics |
| `test_layout.py` | 7 | longest allowed text fits; OG fits; overflow fails closed; no text below 28px; WCAG AA pairs; contrast math; low-contrast kit rejected |
| `test_concurrency.py` | 2 | 3 decks behind semaphore(2) without errors (max in flight 2, identical PDFs); OG cards share the limiter |
| `test_og.py` | 3 | 1200x630 and 2x; PNGs carry no metadata chunks; OG limits |
| `test_browser_env.py` | 2 | Chromium inherits no app secrets; browser and temp home cleaned up |
| `test_settings.py` | 9 | defaults; bounds; env overrides; minimal env; limiter; stale-home sweeper; sandbox failure message; job timeout |
| `test_spec_and_lint.py` | 15 | invalid slides and decks rejected; normalisation; lint passes the sample; lint reports every plan rule |

---

## Reviewer notes

Independent review, 2026-11-25, same Mac. I re-ran everything from a clean shell, attacked the
isolation and output claims with probes, fixed what was clearly a bug (inside `spikes/render/` only),
and added a regression test for each fix. Nothing is committed.

**Bottom line: I agree with GO WITH CHANGES, with one more condition.** "Safely within 2 GiB" did not
hold for the default diagram-to-PNG path (`render_mermaid(..., png=True)`). About 2 KB of diagram source
took Chromium to **3.3 GiB** there, from a single job. That path now has a raster budget. I also fixed
six smaller defects, listed below. Each fix has a test that fails on the original code (checked by
running the new tests against a copy with the original files). The suite is now **149 tests** (101 + 48),
all passing, with ruff, `ruff format --check` and pyright (strict) clean.

### What I verified

- **The builder's command, from a clean environment** (`env -i` with only `HOME` and `PATH`): `uv sync`,
  `playwright install chromium --only-shell`, `npm ci --ignore-scripts`, then **101 passed in 16.4 s**.
  ruff, `ruff format --check` and pyright (strict) were clean. No skips or xfails, and no test that
  asserts nothing. After my changes I ran the same command again, with pytest three times in a row.
- **Benchmark** (`bench/measure.py --runs 10`, same machine). It reproduces within about 10%:

| Scenario | Builder | Reviewer, before fixes | Reviewer, after fixes |
|---|---|---|---|
| Per job (driver + Chromium per job), median / p90 | 0.82 / 0.83 s | 0.90 / 0.93 s | 0.83 / 0.84 s |
| Cold first job | 0.95 s | 0.91 s | 0.98 s |
| Long-lived driver / warm browser | 0.65 / 0.54 s | 0.67 / 0.56 s | 0.66 / 0.54 s |
| OG card / one diagram to SVG + 2x PNG | 0.10 / 0.44 s | 0.10 / 0.45 s | 0.10 / 0.43 s |
| 3 decks / 6 decks behind semaphore(2) | 1.90 / 3.15 s | 1.96 / 3.18 s | 1.83 / 3.14 s |
| Peak tree RSS, 1 job | 808 MiB | 766 MiB | 767 MiB |
| Chromium RSS / footprint, 1 job | 409 / 195 MiB | 396 / 181 MiB | 395 / 180 MiB |
| Driver RSS / footprint, 1 job | 250 / 206 MiB | 242 / 200 MiB | 251 / 207 MiB |
| Peak tree RSS, 2 jobs in flight | 1,466-1,502 MiB | 1,480-1,522 MiB | 1,507-1,541 MiB |
| PDF size (sample deck) | 46.9 KB | 46.9 KB | 47.1 KB (footer clip) |

- **"Importing the plan's server stack measured 212 MiB"** had no script or recorded output in the
  spike, yet the 2 GiB extrapolation rests on it. I measured it in a throwaway venv outside the repo.
  The venv held FastAPI 0.165.0, uvicorn 0.54.0, httpx 0.28.1, openai 3.59.0, anthropic 2.14.0,
  azure-identity 1.27.0, azure-cosmos 4.17.1, azure-storage-blob 12.31.1, markdown-it-py 4.3.0,
  mdit-py-plugins 0.7.0, nh3 0.3.8, Pillow 13.0.0, feedparser 6.0.14, beautifulsoup4 4.15.0 and this
  spike's pins. A fresh process measured **218 MiB** on the first, cold import (bytecode being compiled)
  and **160 MiB** warm. The image precompiles bytecode (`UV_COMPILE_BYTECODE=1`), so 212 MiB is a fair,
  slightly pessimistic input. The render package itself adds about 1.5 MiB.
- **"Idle Python with the render stack imported: 109 MiB"** measured 58-60 MiB here (the bench's
  `python_baseline_rss_mib`). This is not material: the extrapolation uses the larger number.
- **Spot checks that held:**
  - Diagram labels reach the PDF text layer, with no Private Use Area code points.
  - A deck title containing `/Producer` is not a false positive for the raw-bytes metadata check.
  - Front-matter `config:` is locked the same way as `%%{init}%%` when it reaches Mermaid (now tested).
  - No XSS payload executes.
  - A huge diagram on a *slide* stays cheap (394 MiB Chromium RSS), because the slide draws the SVG at
    frame size.

### Findings and fixes

| # | Severity | Finding (evidence) | Fix | Tests (added) |
|---|---|---|---|---|
| 1 | **High** | **Unbounded diagram raster: one job can exceed the whole container.** `render_mermaid` defaults to `png=True` and rasterised at the SVG's intrinsic size with no cap. A 2,075-character sequence diagram (60 participants, 60 messages; the spec allows 3,000 characters and `maxEdges` 200) is 12050x3171 px. Its 2x PNG is 24100x6342 (153 MP). Chromium peaked at **3,321 MiB RSS (3,876 MiB footprint; GPU process 2,590 MiB)**. Diagram source is AI-written and can be prompt-injected. In the single container, an OOM takes down the API, the scheduler and publishing too. | `check_raster_size()` refuses a raster over 12 MP or over 16,384 px per side with `OutputLimitError`, before any browser work. The SVG is still available. Measured memory against raster size: 4.4 MP gives 527 MiB, 13.3 MP gives 726 MiB, 27 MP gives 1,045 MiB and 49 MP gives 1,548 MiB of Chromium RSS. 12 MP sits below the largest point measured as safe. | `test_raster_budget_boundaries`, `test_oversized_diagram_is_refused_before_rasterising` |
| 2 | Medium | **SVG sanitiser and its "independent" checker both missed remote references in CSS.** Seven forms got `https://evil.example` through the sanitiser: (a) presentation attributes such as `fill`, `filter`, `mask`, `clip-path` and `marker-end` set to `url(https://...)`; (b) a local then a remote `url()` in one `style` declaration; (c) `image-set("https://...")` in a style attribute; (d) the same in a style element; (e) an unterminated `url(` at the end of a sheet; (f) the cleaner itself turning `ur\l(https://...)` into a live `url(https://...)` by deleting the escape *after* the URL rewrite; (g) CSS placed after a child element inside `<style>`, which Chromium does apply (verified). The checker passed five of the seven. I found no route to these through Mermaid 12.3.0: its grammars reject `(` in style values, and stylis turns `image-set` strings into `url()`, which was rewritten. But the published SVG file is fetched by anyone who opens it directly. | One `css_is_safe()` predicate, shared by the sanitiser and the checker, covers all seven: every `url(` must be a complete, local match; no `@import`, escapes or string-URL functions. Escapes are removed *before* the URL rewrite. Attributes are checked as CSS values. `<style>` is reduced to text before cleaning. A sheet that still fails is dropped. `sanitize_svg` now runs `assert_svg_safe` on its own output. **Regression check:** output is byte-identical to the old sanitiser on real Mermaid output for all 21 diagram types I rendered (flowchart through architecture). | `test_remote_references_in_every_css_form_are_removed` (x7), `test_checker_alone_rejects_every_css_bypass` (x7), `test_local_references_in_css_values_are_kept` |
| 3 | Medium | **WebRTC bypasses every network layer.** In a JS-enabled render context (route guard, dead-end proxy, `MAP * ~NOTFOUND` DNS) with no CSP, `RTCPeerConnection` sent STUN datagrams straight to an IP address: a UDP canary received 3-4 packets. Routes, proxy and DNS rules don't cover UDP, and CSP has no WebRTC directive that Chromium enforces. Exploiting this needs JS execution in the Mermaid page first, which the CSP hash pin and strict mode prevent. But "launch flags alone stop requests" was not true for UDP. | `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`: WebRTC may only use the (dead-end) proxy, so it gathers no candidates at all. | `test_webrtc_cannot_send_udp_from_a_js_page`, with a positive control on plain Chromium |
| 4 | Medium | **Invisible characters slipped past the confidentiality-gate normaliser.** Only category `Cf` was stripped. Nine other default-ignorable code points passed through: U+034F CGJ, VS1-VS16, VS17-VS256, Khmer U+17B4/U+17B5, the Mongolian variation selectors and the Hangul fillers. CGJ, VS1, VS17, Khmer and Mongolian draw `Cli<U+034F>ent` at **exactly the width of `Client`** in the slide font (measured). Yet `gate_normalise` returned `cli<U+034F>ent`, the PDF `/Title` kept the character and the text layer read "Cli ent". | Strip them all. ZWJ and VS16 are kept only inside emoji sequences (not after a letter, digit or space), and `gate_normalise` drops both. | `test_invisible_characters_cannot_split_a_blocklisted_name` (x9), `test_emoji_sequences_keep_their_joiners` |
| 5 | Medium | **The sandbox flag failed open.** Any `BW_RENDER_CHROMIUM_SANDBOX` value outside {1, true, yes, on} disabled Chromium's sandbox, including an empty value, `enabled` and the typo `ture`. The report calls `=0` "an explicit, reviewed decision". | Strict parsing: unset or empty means the default (sandbox on); 1/0, true/false, yes/no, on/off are accepted; anything else raises `ValueError`. | `test_sandbox_flag_accepts_only_known_values` (x7), `test_sandbox_flag_typo_is_an_error_not_a_silent_off` (x4) |
| 6 | Low | **The footer was never measured.** `.foot` had no `data-fit`. An unbreakable 48-character author name overflowed by 1,814 px and was clipped silently, with no `LayoutOverflowError`. This contradicts "every bounded box is measured". Realistic names wrap to two lines and fit. | `data-fit="footer"` plus a two-line `max-height`, so too much footer text fails closed. | `test_unbreakable_footer_text_fails_closed_instead_of_clipping` |
| 7 | Low | **The job timeout didn't cover driver start or browser launch,** although `settings.py` said "launch + all pages". With a hung launch, the original code waited until I killed the test: the only job slot (concurrency is 1) was held for good, so every later render would queue behind it. | The deadline now covers opening the session. It is lifted before teardown, so a deadline that lands during cleanup can't skip it (carousel and OG). | `test_job_timeout_also_bounds_a_hung_browser_launch` (carousel, OG) |

Also added (no code change needed; they lock in behaviour the report claimed but didn't test):
- `test_js_page_without_csp_cannot_reach_loopback_by_websocket_or_beacon`: Playwright's `route()` never
  sees WebSockets or beacons. Only the dead-end proxy stops them, so "the route guard aborts everything"
  was overstated. The docstring in `browser.py` now says so.
- `test_locked_config_holds_even_if_frontmatter_config_reaches_mermaid`.
- `test_sanitiser_alone_removes_links_mermaid_emits_in_strict_mode` (x3) and
  `test_link_that_reaches_mermaid_never_reaches_the_pdf` (see remaining issue 2).

Changed files: `src/render/{svg_sanitize,mermaid,browser,text,settings,carousel,og}.py`,
`src/render/templates/{_frame.html.j2,slides.css}`, and `tests/test_{svg_sanitize,network_isolation,
mermaid,spec_and_lint,settings,layout}.py`. Neither the builder's sections above nor its tests were
edited, so their "101 tests" counts describe the version before this review.

### Remaining issues (not fixed: they need a decision or are out of scope)

1. **The 28px type floor is not met for diagram labels.** Labels are set at 26px and then scaled to fit
   the frame. Effective size on the slide:

   | Diagram | Size | Scale | Effective label size |
   |---|---|---|---|
   | The sample deck's own diagram | 318x865 | 0.96 | **24.9px** |
   | 5-node LR flowchart | 1200x83 | 0.71 | **18.4px** |
   | 8-node TD flowchart | 444x821 | 1.01 | 26.2px |
   | 4-actor sequence | 850x371 | 1.00 | 25.9px |

   A 60-participant sequence diagram still renders, at about 1.8px. `test_no_text_is_smaller_than_28px`
   only walks HTML text nodes, so it can't see SVG labels. "A 28px type floor" is therefore true for HTML
   text only. Recommendation: at render time, compute the effective size as
   `26 x min(frame_w/svg_w, frame_h/svg_h)` and fail closed or lint below an agreed floor. Then either
   accept a documented lower floor for diagrams (24px, for example) or cap diagram size, for example top
   to bottom with about 6 nodes or fewer.
2. **The neutraliser works line by line.** It misses `;`-separated statements
   (`A --> B; click A "https://..."`) and the sequence-diagram forms `link A: label @ url` and
   `links A: {...}`. It emits no warning for them, and Mermaid's strict mode still renders them as real
   `<a xlink:href="https://...">`. The sanitiser unwraps them and the PDF carries no link (both now
   tested). This is a gap in editor warnings and defence in depth, not a leak. Recommendation: split on
   `;` outside quotes and brackets, and match the two sequence forms.
3. **Lint and normalisation gaps for diagrams and identity fields:**
   - Mermaid label words don't count toward the 35-word limit.
   - Domain-like tokens are not checked in diagram labels, the deck title (the LinkedIn document title)
     or the brand footer text.
   - Mermaid source is not put through the invisible-character policy (only NUL is rejected).
4. **The memory telemetry polls every 50 ms,** so it can miss short peaks (the 2x cover screenshot stage
   takes about 90 ms). On macOS, `proc_pid_rusage` with `RUSAGE_INFO_V4` gives
   `ri_lifetime_max_phys_footprint`, an exact per-process peak. The container's cgroup `memory.peak`
   stays the authoritative number.
5. **For the container spike** (condition 4 above), also measure:
   - a deck with three diagrams;
   - `render_mermaid(png=True)` at the 12 MP cap, to confirm the budget under cgroup accounting;
   - `--disable-gpu`. The GPU process was the largest Chromium process per job (153 MiB RSS) and made up
     most of the raster spike.
6. **Not verified, as the builder states:** Linux and cgroup numbers, whether ACA allows Chromium's
   sandbox, and `Dockerfile.draft`. I reviewed the Dockerfile by reading only; no Docker was available.

**Additional conditions for go:**

7. Keep the diagram raster budget (or an equivalent cap) wherever diagram PNGs are produced.
8. Decide the diagram type floor (remaining issue 1) before LinkedIn Studio (Phase 5).
