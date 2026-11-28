# Shared contracts

Frozen on 2026-11-26 (v2 plan, Phase 1). These files are the single source of truth for the data and
rendering rules shared by the server (`server/`), the web app (`web/`) and the portfolio
(`../portfolio`). Change them only through the versioning rules below.

| Path | What | Version |
|---|---|---|
| `published-blogs.v2.schema.json` | JSON Schema 2020-12 for documents in the Cosmos DB containers `published-blogs` and `published-blogs-staging` | `schemaVersion` 2 |
| `sanitizer-profile.json` | HTML allowlist for `htmlContent`: tags, attributes, value patterns. Implemented with nh3 (server) and rehype-sanitize (portfolio) | `version` 1 |
| `fixtures/corpus/` | Article-dialect golden corpus: 22 files, `manifest.json`, reviewed TipTap output in `normalized/` | dialect v1 |
| `fixtures/xss/` | XSS corpus: `markdown/` (24 hostile articles for the renderer), `html/` (20 hostile fragments for the sanitizer) | |

The dialect itself is specified in `docs/v2/article-dialect.md`; the corpus is its executable form,
and when the two disagree the corpus wins. Fixtures come from the Phase 0 spike
(`spikes/editor-dialect/`), byte for byte.

## published-blogs v2

### Invariants

1. **`id == slug`, partition key `/id`.** The portfolio point-reads `item(slug, slug)`; the Terraform
   check `published_blogs_contract` keeps the partition key.
2. **Slugs are immutable; documents are never deleted.**
   - A rename writes the new document under the new slug, then overwrites the old one with a redirect
     stub: `status: "redirect"`, `redirectTo: <new slug>`.
   - Unpublishing overwrites the document with a tombstone: `status: "unpublished"`.
   - Stubs carry only `id`, `slug`, `schemaVersion`, `status`, `updatedAt` (and `redirectTo`). No
     content, so nothing stale or confidential stays readable by the portfolio build.
   - The writer keeps redirects one hop deep: when B is renamed to C, the stub A → B is rewritten to
     A → C.
3. **Timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`.** Fixed width, so string order (Cosmos `ORDER BY`,
   `<=`) is time order. `updatedAt >= publishedAt`. The v1 field `date` is the UTC date of
   `publishedAt`.
4. **Future-dated documents never render.** The writer publishes at the publication instant (the
   validator rejects a published document whose `publishedAt` is after now + 60 s), and readers
   still skip any document whose `publishedAt` is after the build time.
5. **Plain text.** `title`, `excerpt`, `seo.*`, `series.title` and `heroImage.alt` are one line with
   no `<`, no control characters and no leading or trailing whitespace. `tags` are slugs.
   Content-derived text (`toc[].text`, citation `title`/`publisher`, correction `note`) may contain
   `<`. Readers escape every field anyway.
6. **URLs are `https://` or site-relative** (one leading `/`), the sanitizer's `src` rule.
   `canonicalUrl` and `sourceUrl` are absolute `https://`.
7. **`htmlContent` is a sanitized fragment inside `sanitizer-profile.json`** (no `html`/`body`
   wrapper). `toc` lists exactly its h2/h3 headings that carry an id, in order. The fragment is
   self-contained, because the portfolio inserts it into its page: every element it opens is
   closed in order, it closes nothing it did not open, and it does not end inside a tag, comment or
   raw-text element (an unterminated `<img src=x onerror=...` would complete itself with the
   page's next end tag; a stray `</div>` would close the article container).
8. **Media are same-origin.** Every site-relative image (`heroImage`, `ogImage`, and every `<img src>`
   or `<a href>` under `/blog-media/`) is listed in `assets`. An asset is `path`
   `/blog-media/<id>/<file>` plus `blob` `<id>/<file>` (the same file) in the private Blob container
   `published`, plus the SHA-256 of its bytes. The portfolio copies each blob to
   `public/blog-media/` and checks the hash. SVG assets are served from the site's origin, so the
   renderer must sanitize them (no script, no `foreignObject`, no event handlers, no external refs).
9. **`ogImage` is a 1200x630 PNG.** LinkedIn and X cannot render SVG.
10. **Size.** A document is at most 2,000,000 bytes of UTF-8 JSON (Cosmos caps items at 2 MiB, system
    properties included); `mdxContent` is at most 512 KiB.

A published document carries every field the renderer produces (see `allOf[0]` in the schema):
lists may be empty but are present. `mdxContent`, `date`, `sourceUrl`, `sourceType`, `series`,
`canonicalUrl` and `seo` are optional. Cosmos system properties (`_rid`, `_self`, `_etag`,
`_attachments`, `_ts`, `_lsn`) are accepted on documents read back from the store; no other unknown
property is.

### Writing (server only)

`blogwriter.publish.contract.validate_published_doc(doc)` runs before every upsert to either
container; any error blocks the write. It runs the schema with format checking, then the invariants
JSON Schema cannot express: id/slug, redirect target, timestamps (real dates, not in the future,
ordered), corrections ordered and inside the publication window, series numbering, unique toc and
citation ids, asset locations, media listed in `assets`, toc equal to the headings, `htmlContent`
a self-contained fragment inside the sanitizer profile (an independent audit with Python's
`html.parser`), and the size limits. It raises `ContractUnavailableError` when these files are missing or malformed, so
publishing fails closed. The server finds this directory by walking up from its package, or from
`BW_PUBLISH_CONTRACTS_DIR` (set it in the container image).

### Reading (portfolio)

- Query only renderable documents:
  `WHERE (c.status = 'published' OR NOT IS_DEFINED(c.schemaVersion)) AND c.publishedAt <= @now`,
  with `@now` in the same fixed format. Legacy v1 documents (no `schemaVersion` and no `status`)
  count as published until the Phase 9 migration; `c.status = 'published'` alone is undefined for
  them and would drop every one. Their `publishedAt` (`isoformat()`: microseconds, `+00:00`) still
  compares correctly at second precision. Read v2 fields with a fallback to v1.
- **The writer must not write a redirect or unpublished stub before the portfolio reads v2 (PR B).**
  Today's reader has no `status` filter and point-reads every slug it lists. A listed stub has no
  `title`, `publishedAt` or `htmlContent`, so its page, feed and sitemap entries throw and the
  build fails (GitHub Pages keeps the last good build). Published v2 documents are safe for
  today's reader: they keep every field it reads (`id`, `slug`, `title`, `excerpt`, `sourceUrl`,
  `publishedAt`, `updatedAt`, `htmlContent`) with the v1 types.
- Validate each v2 document (one with `schemaVersion`) with `Ajv2020` plus `ajv-formats` (the
  schema compiles with `strict: true`; patterns assume Ajv's default `unicodeRegExp: true`). Legacy
  documents cannot pass it and keep the v1 reading path. Fail the build on a violation: GitHub
  Pages keeps serving the last good build, and the server's publish verification alerts.
- Redirect stubs become static pages at `/blog/<slug>` that redirect to `/blog/<redirectTo>` (meta
  refresh plus `rel=canonical`). Unpublished tombstones render nothing. Both stay out of listings,
  the sitemap and RSS. A missing or non-published redirect target is a 404.
- Render `htmlContent` through a rehype-sanitize schema generated from `sanitizer-profile.json`
  (`clobberPrefix: ''`), never `mdxContent`. Escape `<` in JSON-LD.

## Sanitizer profile

Data, not code: the nh3 implementation (server renderer, Phase 4) and the rehype-sanitize schema
(portfolio PR B) are generated from it or tested against it, and both run `fixtures/xss/` and the
corpus. The frozen profile is the spike's, with these decisions recorded:

- The spike's additions beyond "class pattern, id on h2/h3, img src/alt/width/height"
  (`docs/v2/spikes/editor-dialect.md`): footnote ids on `li`/`a`, `start` on `ol`, exact
  `text-align` styles on `th`/`td`, a forced disabled checkbox `input`, `data-diagram` (SHA-256) on
  `figure`, `title` on `a`/`img`, `rel="noopener noreferrer"` on every link.
- Site-relative links are allowed, not only site-relative images (the review's plan change).
- **New at freeze:** URL patterns exclude C0 controls and DEL (`\x00-\x1f\x7f`), as the review
  recommended.
- **`clobberPrefix` stays `''`** (no `h-` or `user-content-` id prefix). Heading ids are ASCII
  kebab-case, footnote ids are reserved, `toc` ids must equal the HTML ids, and the portfolio runs
  no inline script that reads globals by id. Revisit with a profile version bump if that changes.

All patterns (here and in the schema) are ECMA-262 regular expressions with the `u` flag. Python's
`re` differs on `$`, `\s`, `\S` and `.`: `blogwriter.publish.contract.python_regex` translates them
and refuses `\d`, `\w`, `\b`, `\p` and `\k`, so the contracts spell character classes out. A
differential check of all 43 patterns against 2,230 edge-case strings found no difference between
the two engines (2026-11-26).

## Fixtures

- Byte-exact goldens: `fixtures/.gitattributes` disables line-ending conversion. Never reformat them.
- `corpus/normalized/19-normalize-citations.md` shows TipTap writing the undefined `[^missing]` as
  `\[^missing\]`. Per the Phase 0 decision the Python normalizer reports an unresolved citation as a
  gate-5 error (`citation-undefined`) instead of escaping it.
- Linked images (`[![alt](src)](href)`) are not part of the dialect and get their own lint error,
  `linked-image` (Phase 0 decision); the corpus has no such case.

## Who consumes what

| Consumer | Files | Use |
|---|---|---|
| Server, `blogwriter.publish` | schema, profile | `validate_published_doc` before every upsert (Phase 1); nh3 renderer generated from the profile (Phase 4) |
| Server tests (`server/tests/publish`, CI job `server`) | everything here | schema and profile consistency, fixture inventory, the HTML audit against `fixtures/xss/html` |
| Server renderer, normalizer and gate 5 (Phase 4) | `fixtures/corpus`, `fixtures/xss` | snapshot goldens; the normalizer reproduces `corpus/normalized/` byte for byte; lint expectations from `manifest.json` |
| Web editor (`web/`) | `fixtures/corpus` | TipTap codec round trip and byte stability (vitest). It shows contract errors from the API; it does not read the schema |
| Portfolio (`../portfolio`) | schema, profile, `fixtures/xss`, `fixtures/corpus` | a copy pinned to a commit SHA of this repo: Ajv validation at build, rehype-sanitize schema and its XSS tests |

## Versioning

- **`schemaVersion` is the major version** and is part of the file name.
- **Compatible change** (a new optional field, a relaxed rule): edit this file in place, keep
  `schemaVersion: 2`, add a changelog line. The schema rejects unknown fields, so roll out in this
  order: contract PR, readers (portfolio) pick up the new copy, then the writer emits the field.
- **Breaking change** (remove, rename or retype a field; tighten a rule existing documents might
  break; change an invariant): add `published-blogs.v3.schema.json` with `schemaVersion: 3`. Readers
  accept both, the writer switches and re-renders every document, then v2 is retired.
- **Before tightening any rule**, validate every document in both containers with the new rule.
- **Sanitizer profile:** bump `version` on any change. The server refuses a version it does not
  implement (fail closed), so code, both implementations and the XSS corpus change in the same
  pull request. Loosening the allowlist needs a security review.
- **Fixtures:** a change to a golden is a dialect change. Regenerate deliberately and review the diff.
- Every contract change is reviewed for both sides (server and portfolio).

### Changelog

- 2026-11-26: published-blogs v2, sanitizer profile v1 and dialect corpus v1 frozen (Phase 1).
- 2026-11-26 (freeze review): invariant 7 spells out that `htmlContent` is self-contained, and the
  validator enforces it; the portfolio's query keeps legacy v1 documents; stubs wait for PR B.
