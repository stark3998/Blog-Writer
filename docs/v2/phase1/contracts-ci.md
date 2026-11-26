# Phase 1: shared contracts and CI

Date: 2026-11-26. Plan sections: "Data" (published-blogs v2 contract, shared sanitizer profile,
article dialect spec), "Portfolio work", "CI/CD and operations".

## What was built

| Path | What |
|---|---|
| `contracts/published-blogs.v2.schema.json` | JSON Schema 2020-12: the v1 fields plus `schemaVersion`, `status`, `redirectTo`, `pillar`, `format`, `series`, `heroImage`, `ogImage`, `assets`, `readingTimeMinutes`, `wordCount`, `toc`, `citations`, `corrections`, `canonicalUrl`, `seo` |
| `contracts/sanitizer-profile.json` | The spike profile plus `version: 1` and URL patterns that exclude C0 controls and DEL |
| `contracts/fixtures/` | Spike corpus (22 files, manifest, `normalized/`) and XSS cases (24 Markdown, 20 HTML), byte-identical; `.gitattributes` turns off line-ending conversion |
| `contracts/README.md` | Invariants, reading rules for the portfolio, profile decisions, consumers, versioning |
| `server/src/blogwriter/publish/contract.py` | `validate_published_doc(doc, *, now=None) -> list[ContractError]`, `audit_html`, `python_regex`, `PublishedContract`, `ContractUnavailableError` |
| `server/tests/publish/` | 297 tests: good samples, 114 bad-document cases with exact `(code, path)` sets, the HTML audit, pattern translation, contract-file consistency, fail-closed loading |
| `.github/workflows/ci.yml` | `server` job (uv 0.12.5, `uv sync --locked`, ruff, ruff format, pyright, pytest) and a `web` job (Node 24.22.3, `npm ci`, lint, typecheck, test, build) that a `web-present` job skips until `web/package-lock.json` exists |

## Decisions worth knowing

- `redirectTo` is a slug, not a URL: no open redirect, and the portfolio builds `/blog/<slug>`.
  Redirect and unpublished stubs carry no content (a closed property set).
- Timestamps are `YYYY-MM-DDTHH:MM:SSZ`: fixed width, so Cosmos string order is time order.
  `published-in-future` and `updated-in-future` allow 60 s of clock skew.
- A published document requires every field the renderer produces. Optional: `mdxContent`, `date`
  (must equal the UTC date of `publishedAt`), `sourceUrl`, `sourceType`, `series`, `canonicalUrl`,
  `seo`.
- `tags` are slugs (the dialect's frontmatter rule), which is stricter than "no `<`".
- Every site-relative image (hero, OG, `<img>`, `<a>` under `/blog-media/`) must be listed in
  `assets`, which closes the v1 `/api/images` 404 class. The OG image must be a PNG.
- The validator audits `htmlContent` against the profile with Python's `html.parser`, independently
  of nh3. That audit also flags numeric character references Python drops but browsers keep
  (`&#1;`), a parser differential found while testing.
- Contract patterns are ECMA-262 (`u` flag). `python_regex` translates `$`, `\s`, `\S` and `.` for
  Python and refuses `\d`, `\w`, `\b`, `\p` and `\k`. Python's `$` would otherwise accept a trailing
  newline (`"slug\n"`).
- `clobberPrefix` stays `''`, as in the plan; the rationale is in the README.
- CI: pushes run on `main` only and branches run through pull requests, so there are no duplicate
  runs. The server job uses uv-managed CPython only, because the runner's system 3.12.3 predates the
  `html.parser` security fixes the audit relies on. uv's checksum is required (setup-uv bundles it
  for 0.12.5).

## Checks run

- My paths: `ruff check`, `ruff format --check` and strict `pyright` are clean; `pytest tests/publish`
  passes 297 tests.
- Whole server (`uv sync --locked && ruff check . && ruff format --check . && pyright && pytest -q`):
  the sync is OK. Other agents' in-progress files fail, none of them mine:
  - ruff: `tests/db/test_query.py`, S608 and formatting.
  - pyright: 4 errors in `tests/core/test_app.py` and `tests/core/test_logging.py`.
  - pytest: 1354 passed, 1 failed (`tests/db/test_query.py`).
- One-off cross-checks, not committed:
  - The audit finds nothing in any of the spike's 66 sanitized or rendered goldens, and catches all
    19 hostile `xss/html` cases.
  - Ajv 8.20.0 and ajv-formats 3.0.1 compile the schema with `strict: true` and no warnings. They
    give the same schema-stage verdict as Python on 115 of 116 documents. The other one (Feb 30) is
    rejected by Ajv's `date-time` format and by the Python `invalid-timestamp` invariant.
  - A differential check of all 43 patterns against 2,230 edge-case strings found no difference
    between Python and JavaScript in 95,890 checks.
- Workflow:
  - The YAML parses.
  - The three actions are pinned to commit SHAs (the tags are lightweight; verified with `gh api`).
  - Every input was checked against `action.yml` at that SHA.
  - actionlint did not run. The npm package has no CLI, and downloading the release binary was
    blocked by the permission system.
- Web (another agent's path; read-only checks only): typecheck passes. Lint fails because there is
  no `eslint.config.*`, and test fails because `vitest run` finds no test files. I did not run
  `npm run build`, because it would rewrite that agent's `web/dist`.

## Needs and follow-ups

1. The container image must ship `contracts/` (walking up from the package finds it) or set
   `BW_PUBLISH_CONTRACTS_DIR`. Without it, validation raises `ContractUnavailableError` and
   publishing fails closed.
2. Web: the CI web job fails until `web/` has an ESLint flat config and at least one test. Its
   `engines` (`>=24`) should say `>=24.15`, matching CI's Node 24.22.3 and jsdom's requirement.
3. `docs/v2/article-dialect.md` still says "draft v1": flip it to frozen and point it at
   `contracts/fixtures/` (not my path).
4. `ContractSettings` (`BW_PUBLISH_CONTRACTS_DIR`) should move into the publish package's settings
   class once one exists.
5. Phase 4 renderer: generate the nh3 profile and compare it with `contracts/sanitizer-profile.json`,
   which now has `version` and the C0/DEL rule, so the spike's generator needs both. The renderer's
   corpus goldens could also move to `contracts/fixtures/rendered/` for the portfolio's
   rehype-sanitize tests.
6. Path-filtered workflows never report on pull requests that touch none of the paths, so do not
   make these jobs required checks without a fallback. Still to add from the plan: pip-audit, the
   Docker build, Trivy and Playwright.
7. `spikes/editor-dialect/` still holds the originals of the fixtures and profile. Delete them at
   cutover, or point the spike at `contracts/`.

## Freeze review (2026-11-26)

Changes made by the independent review:

- **`htmlContent` must be self-contained** (README invariant 7). The portfolio inserts it into its
  page, so the audit now parses it followed by an end tag, in one pass, and reports:
  - markup that swallows that tag. `<p>a</p><img src=x onerror=alert(1)//` used to pass, because
    the patched `html.parser` silently drops a start tag left open at the end of input; on the page
    it would complete itself with `</div>` and run;
  - stray and misnested end tags (`</div>` closes the article container);
  - unclosed elements (an unclosed `<a>` is re-opened around the rest of the page).
  All 66 nh3 goldens of the spike still pass. Open elements are counted, so the check stays linear
  (a list scan took 21 s on a 1.3 MB nested fragment).
- Tag and attribute names from the document are shortened to 40 characters in findings, and
  schema messages that quote unexpected keys to 200 characters (a 5,000-character key gave a
  5,081-character message).
- README, portfolio reading: the query keeps legacy v1 documents
  (`OR NOT IS_DEFINED(c.schemaVersion)`; `c.status = 'published'` alone drops them), Ajv validates
  only v2 documents, and stubs wait for portfolio PR B (today's reader throws on a document
  without `htmlContent` or `title`).
- CI: `infra/v2/data/*.tf` joins the path filters (the db tests read `main.tf`); each main commit
  gets its own concurrency group (in a shared group a newer run replaces a pending one);
  `PYTHONASYNCIODEBUG=1`, as the plan specifies.
- `server/tests/publish`: 329 tests. The whole server is green: ruff, format, pyright, and pytest
  with and without asyncio debug.

Verified without changes: the action SHAs equal their tags (`gh api`), every `with:` input exists at
that SHA, setup-uv bundles the uv 0.12.5 checksum, Node 24.22.3 is the newest 24.x, the fixtures are
byte-identical to the spike, and the profile differs from the spike's only by `version` and the
C0/DEL rule.

Open:
- `CosmosStore` does not pass `enable_compact_utf8_item_writes=True`, so the SDK escapes non-ASCII
  in item bodies and the bytes sent can exceed the UTF-8 size both size checks measure.
- "Blocking calls fail the build" also needs a test hook that turns asyncio slow-callback logs into
  failures (shared test config, not this pipeline's paths).
- actionlint has still not run on `ci.yml`.
