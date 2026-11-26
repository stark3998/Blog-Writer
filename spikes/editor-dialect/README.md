# Spike: article dialect + TipTap v3 round trip

Phase 0 spike for Blog Writer v2. Spec: `docs/v2/article-dialect.md`. Findings and verdict: `docs/v2/spikes/editor-dialect.md`.

| Path | What |
|---|---|
| `corpus/` | Golden corpus (22 files) and `manifest.json` (canonical or not, expected normalization categories, expected lint errors) |
| `corpus/normalized/` | Reviewed TipTap output for every corpus file (the first normalization) |
| `xss/markdown/`, `xss/html/` | XSS corpus: hostile Markdown for the renderer, hostile HTML for the sanitizer. Shared with the future `rehype-sanitize` twin |
| `sanitizer-profile.json` | The shared sanitizer profile as data (generated and checked by the Python tests) |
| `web/` | TipTap v3 editor dialect: extensions, Markdown codec, vitest suites |
| `py/` | markdown-it-py renderer, nh3 sanitizer, dialect linter, pytest suites (uv project) |

## Run

```sh
cd web
npm ci
npm test                 # vitest: corpus round trip, parity oracle, property-based escaping, editor commands
npm run typecheck
npm run golden           # regenerate corpus/normalized/ after a deliberate change, then review the diff
npm run report           # write web/reports/normalization.json (every first-normalization diff)
node scripts/licenses.mjs --prod

cd ../py
uv sync --locked
uv run pytest            # UPDATE_SNAPSHOTS=1 to regenerate goldens, then review the diff
uv run ruff check src tests && uv run mypy src
```

Property-based tests are deterministic by default; explore further with `FC_SEED=7 FC_RUNS=20000 npm test -- test/escape.test.ts`.

## Graduating into v2

- `web/src/` becomes the editor's Markdown layer in `web/` (keep `articleExtensions()`, `ArticleCodec`, `DialectMarkdown`; swap `DialectCodeBlock` for a `CodeBlockLowlight` extension with the same `parseMarkdown`/`renderMarkdown`).
- `py/src/article_render/` becomes `server/publish` (renderer, sanitizer) and `server/content/gates` (linter).
- `corpus/`, `xss/` and `sanitizer-profile.json` move to a shared test-fixtures folder used by both CI jobs and by the portfolio's `rehype-sanitize` tests.
