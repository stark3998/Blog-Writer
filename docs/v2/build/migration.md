# Runbook: migrating v1 data into v2 (`python -m blogwriter.backups.migrate_v1`)

Plan section "Migration (Phase 9)". Code: `server/src/blogwriter/backups/migrate_v1/`, tests:
`server/tests/backups/test_migrate_*.py`.

The tool does two things:

1. **Export.** It copies every v1 container except the session and OAuth-state containers to
   Blob storage as JSON Lines: `backups/v1-export/<UTC date>/<container>.jsonl`.
2. **Convert.** It turns v1 posts, drafts, feeds, crawled articles, keyword lists and voice
   profiles into v2 documents. Each one is validated before anything is written, and writes are
   idempotent by id.

It runs as a dry run unless you pass `--apply`.

## Safety rules (enforced by the code and its tests)

- **v1 is read-only.** The tool reaches v1's Cosmos account only through list and query calls.
  It has no code path that creates, replaces, upserts or deletes anything there. The v1 account
  is shared with another application, so use its **read-only** key or a data-plane reader role.
- **Session and state containers are never read**: `linkedin-sessions`, `linkedin-oauth-states`,
  `twitter-sessions`, `twitter-oauth-states`, `medium-sessions`, and any container whose name
  contains `session`, `state`, `oauth`, `token`, `secret` or `credential`. They hold access
  tokens. `--only` refuses them (exit code 2).
- **No content in the output.** The report holds container names, counts, document references
  and error codes. References are ids or slugs, or a short hash for anything that could be text
  (keyword topics, odd ids). Titles, bodies, URLs and keys are never printed or logged.
- **The key stays secret.** `BW_MIGRATE_V1_KEY` is a `SecretStr`. It goes only to the Cosmos
  client and never appears in output or error messages (a test checks this). Set it in the
  environment for one shell session. Never pass it on the command line.
- **Nothing is deleted** in v1 or v2. Deleting the v1 containers is a separate cutover step,
  done with Terraform or the Azure CLI and never by the app.
- **v2 changes win.** A re-run never overwrites a document that changed in v2 since the
  migration wrote it (see "Re-running").

## Before you start

| Need | How |
|---|---|
| v1 read access | `BW_MIGRATE_V1_ENDPOINT=https://<v1 account>.documents.azure.com:443/` plus either `BW_MIGRATE_V1_KEY` (the account's read-only key: `az cosmosdb keys list --type read-only-keys`) or a `DefaultAzureCredential` sign-in (`az login`) with a data-plane reader role on the v1 account. `BW_MIGRATE_V1_DATABASE` defaults to `blog-writer`. |
| v2 store | `BW_COSMOS_ENDPOINT` (and `BW_COSMOS_DATABASE` if it is not `blog-writer`), signed in with Cosmos DB Built-in Data Contributor on v2. A dry run without it compares against an empty store and says so. |
| v2 Blob | `BW_STORAGE_BLOB_ENDPOINT` with Storage Blob Data Contributor on the `backups`, `published` and `media` containers. Needed only for `--apply`. |
| Contracts | Run from a checkout of this repo (the validator finds `contracts/` itself), or set `BW_PUBLISH_CONTRACTS_DIR`. Without the contract files, published posts fail closed with `contract_unavailable`. |
| v2 settings | Save the pillars and format packs in v2 first if they differ from the defaults. Migrated posts get pillar and format ids from those settings, and the brand kit's colours and name go on the generated cards. |
| Where | A workstation or a one-off job. It is a CLI and is not part of the app. Memory peaks at about one v1 container (the `images` export is the largest). |

```bash
cd server
export BW_MIGRATE_V1_ENDPOINT=...   # v1 account
read -rs BW_MIGRATE_V1_KEY && export BW_MIGRATE_V1_KEY   # typed, not echoed, not in history
export BW_COSMOS_ENDPOINT=...       # v2 account
export BW_STORAGE_BLOB_ENDPOINT=... # v2 storage
```

## Steps

1. **Dry run**: `uv run python -m blogwriter.backups.migrate_v1`. It reads v1 and converts and
   validates everything, but writes nothing. Check the per-container counts and every `FAIL`
   line.
2. **Fix failures.** Each `FAIL <container> <ref>: <code> at <path>` names one document. Common
   codes:
   - `invalid_slug`, `id_slug_mismatch`: the post cannot keep its URL. Fix it in v1, or accept
     that it is not migrated.
   - `title_missing`, `v1_markdown_missing`: there is nothing to render.
   - `html-outside-profile`, `toc-mismatch` and other contract codes come from
     `validate_published_doc`. Report them: they mean a converter bug, not bad data.

   To change pillars or formats, use `--pillar <id>` (the fallback when no keyword matches),
   `--format <id>` (for every post), or `--overrides overrides.json`
   (`{"<slug or v1 draft id>": {"pillar": "identity", "format": "case-study"}}`).
3. **Published posts first**: `uv run python -m blogwriter.backups.migrate_v1 --apply --only
   published-blogs`.
   **The portfolio's post-count guard needs this before its first v2 build.** Portfolio PR A
   fails the build when the post count drops sharply. A build pointed at the v2 account before
   this step would find an empty `published-blogs` and stop. Also check that portfolio PR B
   (which downloads `assets[]` into `public/blog-media/`) is live. Without it, the posts still
   render but the migrated images under `/blog-media/` return 404.
4. **Everything else**: `uv run python -m blogwriter.backups.migrate_v1 --apply`. This exports
   every container and converts the rest. The exports land in
   `backups/v1-export/<UTC date>/`, so re-running on the same day replaces that day's files.
5. **Verify**:
   - the counts in v2 (`published-blogs`, `articles`, `sources`);
   - a portfolio preview build from v2;
   - in the app: Studio lists the migrated drafts, Settings > Sources lists the feeds (paused
     where noted), and the kb and voice seeds wait as proposals.
6. **Cutover** (plan Phase 9): delete the v1 containers with Terraform or the Azure CLI, rotate
   every key v1 held, then unset `BW_MIGRATE_V1_*` and close the shell.

Exit codes: `0` everything valid, `1` some documents failed (the others were handled), `2`
configuration or usage error.

## What becomes what

| v1 container | v2 | Id |
|---|---|---|
| `published-blogs` | `published-blogs` document, contract v2, `status: published` | same id and slug |
| `drafts` | `articles` (version 1) + `article-versions` (`<articleId>-v00001`, source `restore`) | `derived_id("art", "v1", <v1 id>)` |
| `feed-sources` | `sources` (type `feed`) | `derived_id("feed", "v1", <v1 id>)` |
| `crawled-articles` | `signals` (hash only) | `Signal.id_for(<source id>, url_sha256(url))` |
| `keywords` | `settings`, kind `kb_seed`, `status: proposed` | `derived_id("kbseed", "v1", <topic>)` |
| `voice-profiles` | `stories`, kind `voice_seed`, `status: proposed` | `derived_id("voiceseed", "v1", <v1 id>)` |
| `images` | blobs, through the posts and drafts that show them | content-addressed |
| everything else (`prompts`, `templates`, `version-history`, `comments`, `seo-tracking`, `post-analytics`, `scheduled-publishes`, `crawl-jobs`, `user-profiles`, ...) | export only | |

### Published posts

- **Content.** The v1 `mdxContent` is re-rendered with the v2 renderer and sanitizer. The v1
  `htmlContent`, a whole HTML page with its "frontmatter banner", is not used.
- **Body cleanup** (never inside code blocks or inline code):
  - MDX `import`/`export` lines and JSX tags are removed. Their text stays.
  - `<img>` tags become Markdown images.
  - An `h1` repeating the title is dropped. Other `h1`s become `h2`, and `h5`/`h6` become `h4`.
  - `data:` placeholder images are dropped.
  - The body is normalized with `blogwriter.publish.normalize` when that reports no errors.
- **Images.** `/api/images/<id>` (relative, or on any host) becomes
  `/blog-media/<slug>/<first 16 hex of sha256>.<ext>`. The bytes are copied unchanged from v1's
  `images` container to Blob `published/<slug>/...` and listed in `assets[]` with their full
  sha256.
  - Only PNG, JPEG, WebP and GIF that Pillow can decode are published. SVG and anything else
    is dropped, because it would be served from the portfolio's origin.
  - Missing images are dropped and noted as `image_missing`.
  - External `https://` images stay where they are. Other schemes are dropped.
- **Hero and OG.** The v1 hero, which is the image alone on the body's first line, becomes
  `heroImage`, and a centred 1200x630 PNG crop of it becomes `ogImage`. A post without a hero
  gets a generated 1200x630 title card (brand colours, Inter) as both. That card is
  deterministic, so re-runs produce the same bytes.
- **Fields**:
  - `publishedAt` and `updatedAt` are the v1 values at second precision (`updatedAt` is never
    earlier than `publishedAt`), and `date` is the UTC date of `publishedAt`.
  - `tags` become slugs (`"Cloud Security"` → `cloud-security`, at most 10).
  - Title and excerpt become one plain line (`<` → `‹`, cut at a word boundary). A missing
    excerpt is taken from the first paragraph.
  - `sourceUrl` is kept only when it is https (`http` is upgraded). `sourceType` is kept when
    it is a valid token. `corrections` is empty.
- **Pillar.** A keyword guess over the title, tags and body (`identity`: Entra, Okta, SSO,
  OIDC...; `governance`: NIST, RMF, compliance...; `agents`: agent, MCP, LLM, AI...). It uses
  only pillar ids present in v2 settings. With no hit, it falls back to `--pillar`, else the
  owner's first pillar.
- **Format.** `github` sources become `deep-explainer`; everything else becomes `news-analysis`.
  The override order is `--overrides`, then `--format`, then this guess.
- **Ownership.** Migrated posts are not linked to an article. Their slugs stay reserved, so a
  v2 article that wants one gets `slug_taken`, as `Article.owns_slug` describes.

### Drafts

| v1 draft | v2 status | Why |
|---|---|---|
| went live (`publishedSlug` or `publishedAt` set, or its slug is a v1 published post) | `archived` | the migrated published-blogs document is the live copy |
| `origin: rss_crawl`, never published | `archived` | v2 never drafts from feeds without the owner |
| any other | `in_review` | the owner's own draft, editable in Studio |

- **Meta.** It comes from the v1 fields, falling back to the v1 frontmatter. A missing slug is
  derived from the title. `updatedBy` is `system`, and `version` is 1.
- **Markdown.** The body is cleaned like a published post and normalized when possible.
- **Images.** v1 images move to Blob `media` at `articles/<articleId>/<sha256>.<ext>` (GIF
  becomes PNG). The body references them as `/api/media/...`, and they are listed in `media`
  with role `image`.
- **Dropped**: `userId` and `origin`.

### Feeds, signals, seeds

- **Feed sources.** The title, feed URL and site URL are kept (`http` is upgraded to `https`).
  An enabled feed is `active`, a disabled one `paused`. A feed without a feed URL (v1
  `feedType: html`) is migrated `paused` with `lastError: v1_no_feed_url` and its site URL.
  - Dropped: `autoPublishBlog`, `autoPublishLinkedIn`, `autoPublishTwitter`,
    `crawlIntervalMinutes`, `maxArticleAgeDays`, `maxArticlesToGenerate`, `topics` (they only
    feed the pillar guess) and `feedType`.
- **Crawled articles** become signals holding only the normalized URL hash. They are marked
  triaged, so the inbox does not refill.
  - Skipped: articles of deleted feeds (`orphan`), articles older than 90 days (`stale`, the
    container TTL), and missing or non-http URLs.
  - No title or URL text is stored.
- **Keywords and voice profiles** become proposals with `status: proposed`, which means
  pending owner approval. Nothing uses them until the owner accepts them:
  - a `kb_seed` holds the topic, the deduplicated keywords and a suggested pillar;
  - a `voice_seed` holds the name, description, tone, style notes and sample text (`userId`
    is dropped).

## Re-running

Every v2 id is derived from the v1 id, and conversion is deterministic, so a re-run is safe:

- A missing document is **created**.
- An identical document is **left alone** (`unchanged`).
- A document the migration wrote earlier is **replaced** with an If-Match on its etag. This is
  how changes made in v1 since the last run come through. Each domain document carries
  `migratedFrom: {source, container, id, updatedAt}`. While its `updatedAt` still equals the
  migration's stamp, the document counts as the migration's.
- Anything changed in v2 is **skipped**:
  - `changed_in_v2`: the owner edited the article, source or seed;
  - `v2_stub`: a published-blogs redirect or unpublished stub;
  - `slug_owned_by_v2`: a v2 article publishes that slug.
- Blobs are content-addressed. An existing name already holds the same bytes, so it is never
  rewritten.

## Known limits

- Mermaid fences in v1 posts render as source code (as they did in v1). Republishing from v2
  renders the diagrams.
- Raw HTML other than `<img>` is shown as text by the v2 renderer, and the lint reports it.
- The pillar and format guesses are heuristics. Review the dry-run output and use
  `--overrides`.
