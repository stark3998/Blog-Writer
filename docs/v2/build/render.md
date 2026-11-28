# Build: `blogwriter.render` (carousels, image cards, OG images, Mermaid)

Plan sections: "Security model" > "Render isolation", "LinkedIn Studio" > "Carousels", "Publish".
Source: the Phase 0 render spike (`spikes/render`, report `docs/v2/spikes/render.md`), ported to
the server package. Status: built and tested (227 tests; 166 run without a browser, 61 need
Chrome Headless Shell and skip cleanly without it). Not wired into `blogwriter.main` yet.

For the sample deck the production package writes a PDF **byte-identical** to the reviewed spike
(SHA-256 `8f86e5f7...`), so the spike's measurements and review still describe this code.

## Files

| Path | Contents |
|---|---|
| `server/src/blogwriter/render/__init__.py` | Public API (below). The module is also a lifecycle (`startup`/`shutdown`) |
| `service.py` | `Renderer` (one isolated job per call), `RenderedDeck`, `MermaidDiagram`, `RenderStatus`, module-level functions, the default renderer |
| `lifecycle.py` | `RenderLifecycle`, `startup(app)`, `shutdown(app)`, `get_renderer` (FastAPI dependency) |
| `settings.py` | `RenderSettings` (`BW_RENDER_*`), `MERMAID_TINY_SHA256`, `MERMAID_VERSION` |
| `brand.py` | `RenderBrand`, `BrandColors` (WCAG AA enforced), `FontChoice` |
| `spec.py`, `text.py`, `lint.py` | Slide models and `CarouselSpec`, `OgImageSpec`; plain-text normalisation; `lint_carousel` -> `RenderWarning` |
| `browser.py`, `chromium.py` | Per-job driver + Chromium, isolation, limiter, stale-home sweeper; headless-shell discovery |
| `mermaid.py`, `svg_sanitize.py`, `_xml.py` | Mermaid in its own JS context, locked config, label measurement, raster budget; allowlist SVG sanitiser and checker; typed lxml facade |
| `html.py`, `templates/` | Jinja2 (autoescape, `StrictUndefined`): deck, image card, OG, raster page; CSS |
| `pdf.py`, `headshot.py`, `fonts.py`, `fonts/` | PDF finishing and text layer; headshot re-encoding; Inter 4.1 (OFL, SHA-256 manifest) |
| `telemetry.py`, `_logging.py` | Stage timer, memory sampler (process tree, cgroup `memory.peak`); log fields |
| `testing.py` | Test helpers (sample brand, XSS corpus, PNG/PDF/markup inspection) |
| `server/vendor/` | npm project pinning `@mermaid-js/tiny` 12.3.0 (`node_modules/` is gitignored) |
| `server/tests/render/` | 17 test files (see "Tests") |

## Public API

All functions are `async`. Each call that needs Chromium is one **job**: it waits for a slot
(`BW_RENDER_CONCURRENCY`, default 1), starts its own Playwright driver and Chromium, renders,
tears both down and logs one `render_job` line. The module-level functions use the process-wide
renderer installed by the lifecycle (or a lazily created one); `Renderer` has the same methods.

| Call | Returns | Notes |
|---|---|---|
| `render_carousel(spec, brand, *, preview_scale=1)` | `RenderedDeck` | `pdf` (one 1080x1350 pt page per slide, metadata exactly Title/Author, <= 10 MB, no links or active content), `pages` (PNG per page, 1080x1350 or 2160x2700), `text` (PDF text layer per page), `sha256` (hex of `pdf`), `warnings` (`RenderWarning`: lint rules, diagram source changes, small diagram labels) |
| `carousel_preview_html(spec, brand)` | `str` | The exact HTML `render_carousel` prints. Needs Chromium only for diagram slides |
| `render_image_card(slide, brand, *, lang="en")` | PNG 1080x1350 | Any slide type; no slide number |
| `render_og_image(title, subtitle, pillar_label, brand, *, lang="en")` | PNG 1200x630 | `subtitle`/`pillar_label` may be `None`; bad text raises `pydantic.ValidationError` |
| `render_mermaid_svg(source, *, brand=None, font_size=16)` | `str` | Sanitised SVG, Inter embedded, explicit size |
| `render_mermaid_png(source, width, *, brand=None, font_size=16)` | PNG | Exactly `width` px wide |
| `render_mermaid(source, *, brand=None, font_size=16, png_width=None)` | `MermaidDiagram` | SVG, size, neutralisation warnings (for the editor), optional PNG |

Errors (all `RenderError`): `RenderUnavailable` (no Chromium; suggest 503), `RenderTimeout`
(504), `AssetIntegrityError` (500), and the input errors (422): `LayoutOverflowError` (text does
not fit; never clipped), `DiagramLabelError` (labels below 18 px on the slide), `MermaidError`
(invalid or unsupported diagram, with the slide number), `OutputLimitError` (PDF > 10 MB, raster
budget, broken PDF invariants), `HeadshotError`, `UnsafeSvgError`. Messages hold no slide text
except Mermaid's parse error excerpt; they are never logged.

### `RenderBrand` (the integrator maps the knowledge-base brand kit onto it)

| Field | Limits | Used for |
|---|---|---|
| `name_mark` (required) | plain text, <= 48 | cover byline, slide footer, OG byline, PDF `/Author` |
| `headline` | <= 64 | under the name on the cover |
| `disclaimer` | <= 40 | employer-policy footer on every slide and card |
| `site_name` | <= 40 | OG images only (carousels carry no domain-like tokens) |
| `colors: BrandColors` | `#RRGGBB` only; every text pair >= 4.5:1 or the brand is rejected | all templates and diagrams |
| `fonts: "inter"` | only bundled, licence-checked faces | Inter Display headings, Inter text |
| `headshot: bytes` | PNG/JPEG/WebP, <= 5 MB, <= 25 MP; base64 in JSON | cover byline and OG; re-encoded to a 256 px square PNG with no metadata |

A kit font other than Inter maps to `"inter"` until that face is added to `render/fonts` with its
licence and manifest entry.

### Specs and rules

`CarouselSpec{title <= 100, lang, slides 1-20}`; slide types cover, statement, list, steps,
framework (2x2, layered), diagram (Mermaid <= 3,000 chars, alt text), stat, quote, recap,
sources. Hard limits (spec): <= 4 steps, <= 5 sources, field lengths sized to fit. Editorial
rules (`lint_carousel`, block approval, not rendering): title <= 58, 8-12 slides, cover first
with a hook <= 10 words, <= 35 words per slide, the recap is the last content slide and an
optional sources slide may follow it, no domain-like tokens. Layout: body text >= 28 px, slide
numbers, fit checks on every box (`LayoutOverflowError`), diagram labels warn < 22 px and block
< 18 px effective size.

## Wiring

```python
from blogwriter import render

create_app(routers=[...], lifecycles=[db, jobs_lifecycle, ..., render])  # or render.RenderLifecycle(settings)
deck = await render.render_carousel(spec, brand)                          # anywhere after startup
renderer = Depends(render.get_renderer)                                  # in routes, if preferred
```

Start-up verifies the fonts, templates and the Mermaid bundle's SHA-256 (**a missing or
mismatched bundle aborts the boot** with an actionable `AssetIntegrityError`), sweeps per-job
homes left by a SIGKILL, and looks for Chrome Headless Shell. Without it a `render_unavailable`
warning is logged, the app runs, and render calls raise `RenderUnavailable`. Nothing long-lived
exists to shut down.

**Preview iframe.** Serve `carousel_preview_html` from an API route with
`Content-Security-Policy: render.PREVIEW_CSP` and load it in
`<iframe sandbox src="...">` (no `allow-scripts`, no `allow-same-origin`). Do not use `srcdoc`:
it inherits the SPA's CSP, which forbids the inline `<style>` the document needs.

## Settings (`BW_RENDER_*`)

| Variable | Default | Notes |
|---|---|---|
| `BW_RENDER_CONCURRENCY` | `1` | 1-2. Raise to 2 only after the container shows cgroup `memory.peak` <= ~1.5 GiB for two jobs |
| `BW_RENDER_CHROMIUM_SANDBOX` | on | 1/0, true/false, yes/no, on/off; anything else fails start-up (a typo never disables it) |
| `BW_RENDER_MERMAID_JS` | `server/vendor/node_modules/@mermaid-js/tiny/dist/mermaid.tiny.js` | Images copy the file and set this (the default only exists in a checkout) |
| `BW_RENDER_ACTION_TIMEOUT_MS` / `_JOB_TIMEOUT_S` / `_MERMAID_TIMEOUT_S` | 20000 / 90 / 15 | The job deadline covers driver start and launch, and is lifted before teardown |
| `BW_RENDER_JS_HEAP_LIMIT_MB` | 256 | V8 old space per renderer |
| `BW_RENDER_MAX_PDF_BYTES` | 10 MB | may only be lowered |
| `BW_RENDER_MEMORY_SAMPLE_INTERVAL_S` | 0.1 | 0 = cgroup figures only |
| `BW_RENDER_STALE_HOME_MAX_AGE_S` | 3600 | start-up sweep |

Playwright's own `PLAYWRIGHT_BROWSERS_PATH` decides where the browser is looked up.

## Isolation (as in the spike, plus the driver)

Per job: the Playwright **driver and Chromium** get only `PATH, HOME, TMPDIR, LANG, TZ` (+ XDG on
Linux) with a throwaway home deleted afterwards; the driver also gets `PLAYWRIGHT_BROWSERS_PATH`
and Playwright's three `PW_*` names. No `IDENTITY_*`, `AZURE_*` or `BW_*` variable reaches either
(tested by reading the live processes' environments). The driver's environment is set through a
context-scoped replacement of `playwright._impl.transport.get_driver_env` (no public hook
exists); if a Playwright upgrade removes it, rendering switches off instead of leaking.

Chromium: own sandbox, `--js-flags=--max-old-space-size=256 --jitless`, `MAP * ~NOTFOUND`,
dead-end proxy, WebRTC confined to the proxy; every context routes `**/*` to an aborting guard;
JavaScript off everywhere except the Mermaid context; documents are `set_content` with `data:`
assets only and a `default-src 'none'` CSP. Mermaid: `@mermaid-js/tiny` only, injected with
`add_script_tag` under a CSP that allows only its hash; `securityLevel: strict`,
`htmlLabels: false`, locked keys; source neutralisation; inert `DOMParser` extraction; lxml
allowlist sanitiser plus an independent checker on the finished SVG; rasters <= 12 MP and
16,384 px per side, checked before rasterising. Mindmap, architecture and KaTeX diagrams need
the full Mermaid build and are refused up front.

## Telemetry

One `render_job` line per job (fields registered in `render/_logging.py`): `render_job` (random
id), `render_kind`, `outcome` (ok/error/timeout/cancelled), `duration_ms`, `slides`,
`output_bytes`, `warnings`, `blocked_requests`, `stages` (`name:ms`), process-tree memory peaks
(`python_rss_mib`, `driver_rss_mib`, `chromium_rss_mib`, `chromium_unique_mib`, `tree_rss_mib`,
`chromium_processes`), and cgroup v2 `cgroup_current_mib`, **`cgroup_peak_mib`**,
`cgroup_max_mib`; on failure `error_type` and `error_frames`. Also `render_ready` /
`render_unavailable` at start-up, `render_request_blocked` (scheme and resource type only) and
`render_isolation_event` (something ran in the Mermaid page). No content, hosts or text.

## Container image (for the Dockerfile owner)

- `playwright install --with-deps --only-shell chromium` with `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`.
- A Node build stage: `npm ci --ignore-scripts` in `server/vendor`, check
  `c41f5236...a2a9a  node_modules/@mermaid-js/tiny/dist/mermaid.tiny.js` with `sha256sum -c`,
  copy only that file (for example to `/opt/vendor/mermaid.tiny.js`) and set
  `BW_RENDER_MERMAID_JS` to it. Package data (fonts, templates) ships in the wheel.
- `tini` as PID 1 (reaps Chromium), non-root user. If ACA refuses Chromium's sandbox, apply the
  spike's compensating controls before setting `BW_RENDER_CHROMIUM_SANDBOX=0`.

## Changes from the spike

- **Brand separated from the spec** (`RenderBrand` parameter) with name mark, headshot and font
  choice; OG images gained a subtitle and the headshot; image cards (no slide number).
- **Driver environment confined** too (the spike confined Chromium only).
- **Page previews** for every page instead of a 2x cover: each slide is scrolled into the
  viewport and captured (never a beyond-viewport capture of the whole deck).
- **Only the tiny Mermaid build** (the full build and its auto-selection are gone).
- **Diagram label floor implemented** (spike remaining issue 1): measured from the rendered SVG,
  not assumed. Sequence diagrams ignored the 26 px theme size (Mermaid defaulted them to 16 px);
  the locked config now sets the top-level `fontSize`. Flowchart output is unchanged.
- **Density steps**: at their maximum lengths the list, steps, layered framework and sources
  slides overflowed by 15-56 px for some word orders (the spike's single filler happened to
  fit). Their body text now steps down once (never below 28 px) above a length threshold; tested
  across word rotations in both regimes.
- PDF finishing and Pillow work run in worker threads; the PDF text layer fails the render if it
  holds Private Use Area code points; per-job homes are created and deleted off the event loop.

## Tests

`uv run pytest tests/render` (CI runs it with `PYTHONASYNCIODEBUG=1`). Tests marked `chromium`
skip with the reason when Chrome Headless Shell (`uv run playwright install --only-shell
chromium`, or `PLAYWRIGHT_BROWSERS_PATH`) or the vendored bundle (`npm ci --ignore-scripts` in
`server/vendor`) is missing. With both: 227 passed in about 60 s on an M-series Mac.

## Follow-ups

- CI: add `npm ci --ignore-scripts` (server/vendor) and the headless-shell install to the server
  job, or the 61 Chromium tests stay skipped there.
- Container spike: measure cgroup `memory.peak` per job in ACA at 1 vCPU / 2 GiB (now logged per
  job), and whether Chromium's sandbox is allowed.
- Spike remaining issues 2-3 still open: `;`-separated and sequence `link(s)` statements are not
  neutralised (the sanitiser removes the links), and diagram labels, the deck title and brand
  text are not linted for domain-like tokens.
- Emoji and non-Latin glyphs fall back to system fonts (none in the slim image): add a fallback
  font or lint them out.


## Azure Container Apps result (2026-11-27)

The first live render in `ca-blog-writer-v2` failed at `browser_launch`: Chromium "could not start
its sandbox" (ACA does not grant the user namespaces it needs). Production therefore runs with
`BW_RENDER_CHROMIUM_SANDBOX=0` (Terraform `render_chromium_sandbox = false`) and these
compensating controls:

- **Non-dumpable app process** (`render/hardening.py`): `RenderLifecycle.startup` calls
  `prctl(PR_SET_DUMPABLE, 0)` before any driver starts whenever the sandbox is off. Same-uid
  processes (a compromised renderer) then cannot ptrace the app or read its
  `/proc/<pid>/{mem,environ}`, where the managed-identity endpoint secret and tokens live, and no
  core dumps are written. On Linux a failure raises, so rendering never runs unsandboxed without it.
- **Unchanged layers**: driver and Chromium get only the minimal environment (no `IDENTITY_*`,
  `AZURE_*`, `BW_*`), a throwaway home, no network (dead-end proxy, `MAP * ~NOTFOUND`, aborting
  route guard), `--jitless` with a 256 MB heap, JavaScript off outside the Mermaid context,
  `data:`-only documents under `default-src 'none'`, render concurrency 1.
