# Editor: the article dialect for TipTap

The Markdown layer of the Studio editor, ported from the Phase 0 spike
(`spikes/editor-dialect/web`). It reads and writes the article dialect
(`docs/v2/article-dialect.md`) through TipTap v3. There is no editor UI here yet.

Import everything from `@/features/editor`.

## API

| Export | What it does |
|---|---|
| `ArticleCodec` | A headless TipTap editor that runs parse and serialize. `parse(md)` returns `{ frontmatter, doc }` (TipTap JSON). `serialize(article)` returns canonical Markdown. `normalize(md)` is `serialize(parse(md))`. `check(article \| md)` returns `{ markdown, lossless, warnings, reparsed }`. Call `destroy()` when done. |
| `createArticleExtensions({ lowlight? })` | The extension set for the visible editor: StarterKit minus what the dialect replaces, the dialect overrides, callout, citation and footnotes, mermaid, and `DialectMarkdown`. Pass `createLowlight(common)` to highlight code blocks; the Markdown is the same either way. |
| `normalizeInWorker(md, { timeoutMs })`, `parseInWorker(md, { timeoutMs })` | Run the codec in a module Web Worker. Use these for anything that comes from outside the editor: loading an article, server drafts, pastes. Sources over 128 KB are refused before anything is sent. On timeout (default 5 s) the worker is terminated and the call rejects with `timeout`; the next call starts a fresh worker. `disposeDialectWorker()` stops the shared worker. `DialectWorkerClient` gives you your own worker. |
| `DialectError` (`code`), `FrontmatterError` (`issues`) | The only errors these APIs throw. Codes: `too-large`, `too-deep`, `invalid-frontmatter`, `invalid-content`, `timeout`, `worker-unavailable`, `worker-failed`, `cancelled`, `internal`. Messages never quote the article. |
| `EDITOR_MAX_BYTES` (128 KB, UTF-8), `MAX_NESTING_DEPTH` (32), `utf8ByteLength`, `assertEditableSource` | Input limits. The server accepts 512 KB; the editor opens at most 128 KB. |
| `parseFrontmatter`, `validateFrontmatter`, `serializeFrontmatter`, `splitFrontmatter`, `sameFrontmatter` | Frontmatter in canonical form. YAML is parsed with the failsafe schema, like the server's twin. |
| `Callout`, `Citation`, `Footnotes`, `FootnoteDefinition`, `Mermaid`, `DialectMarkdown`, `DialectMarkdownManager` | The building blocks, for custom node views. |

`check()` warnings are `{ code: 'content-changed' | 'frontmatter-changed', message, blocks?: { from, to } }`.
`blocks` holds indexes into the checked `doc.content`, so the UI can highlight them. Call `check()`
before every save and warn when `lossless` is false. These states cannot be written exactly in
Markdown:

- a line break inside a text node (`insertText` of multi-line text): saved as a space;
- bold or italic over part of inline code;
- overlapping bold and italic: TipTap writes `<em>` there, which the dialect reads as text;
- a `\|` inside inline code in a table cell.

Insert AI proposals as parsed Markdown, not as raw text.

Suggested flows:

- **Load**: `parseInWorker(md)`, then `editor.commands.setContent(article.doc)`.
- **Save**: `codec.check({ frontmatter, doc: editor.getJSON() })`. On `lossless: false`, warn and
  show the warnings. Then send `markdown`. The server lints it (gate 5) and normalizes it again.

The mermaid node stores the diagram source only. The UI asks the server to render it.

## Pins and why

All of these are exact pins in `web/package.json`:

- `@tiptap/*` 3.35.0 and `marked` 17.0.6. Stock `@tiptap/markdown` is not round-trip safe for this
  dialect: the spike needed 26 fixes (`docs/v2/spikes/editor-dialect.md`, "Patches"). This port
  depends on TipTap internals:
  - the private `MarkdownManager.encodeTextForMarkdown`, replaced on the instance;
  - `RenderContext.meta.markText` and `meta.parentAttrs`;
  - the parse helpers `parseBlockChildren` and `isParagraphContinuation`;
  - marked's `Tokenizer.prototype.link`.

  `@tiptap/markdown` resolves `marked` `^17.0.1`, so the `package-lock.json` must keep exactly one
  `marked@17.0.6`.
- `entities` 9.0.0 (HTML5 entity decoding), `yaml` 2.9.1 (frontmatter), and `lowlight` 3.3.0
  (highlighting only).

This port fixes these issues on top of the spike:

- An empty source, frontmatter alone, or a lone citation definition threw `invalid-content`. These
  now parse.
- A link inside link text (`[a [b](x) c](y)`, valid CommonMark) threw `invalid-content`. Now the
  inner link wins, as in CommonMark; this was checked against the spike's markdown-it oracle.
- The codec needed a DOM. It now runs in a worker, and `codec.node.test.ts` proves it.
- `serialize` refuses frontmatter it could not read back.
- Nesting is pre-checked, and stack overflows are mapped to `too-deep`.
- YAML warnings are no longer printed (they quote the source).

## Updating TipTap or marked safely

1. Bump every `@tiptap/*` package and `marked` together, to exact versions. Check that
   `npm ls marked` shows one copy.
2. From `web/`, run:
   - `npx eslint src/features/editor --max-warnings 0`
   - `npx tsc -b`
   - `npx vitest run src/features/editor`
3. The corpus goldens in `contracts/fixtures/corpus/normalized` must stay byte-identical. Never
   regenerate a golden to make a test pass: a golden change is a dialect change, and it follows
   the contract rules in `contracts/README.md` (the server's normalizer must match it too).
4. Run the property suite harder, for example:
   `BW_PROPERTY_SEED=7 BW_PROPERTY_RUNS=20000 npx vitest run src/features/editor/dialect/properties.test.ts`.
   Use a few seeds; about 20 s each.
5. The spike's markdown-it oracle (server parity) is not in `web/`, because `markdown-it` and
   `fast-check` are not dependencies. Until they are, run the spike's suite
   (`spikes/editor-dialect/web/test`) against this folder before merging a bump. The parity cases
   here pin the outputs that the oracle accepted.
6. Run `npm run build`. `scripts/verify-dist.mjs` must still pass. The editor bundles contain the
   text-only hosts `prosemirror.net` (ProseMirror error text) and `github.com` (highlight.js
   warning text); `scripts/verify-dist.mjs` allowlists both as text-only hosts.

## Tests

| File | What it covers |
|---|---|
| `dialect/corpus.test.ts` | All 22 corpus files: golden, byte-stable, fixed points, hygiene, `check()`, lowlight variant |
| `dialect/codec.node.test.ts` | The corpus without a DOM (the worker's environment) |
| `dialect/parity.test.ts` | Parity cases and known divergences, pinned to the outputs the oracle accepted |
| `dialect/properties.test.ts` | 11 seeded escaping round trips (the generator is in `testing/random.ts`) |
| `dialect/hostile.test.ts` | Injected line breaks and attributes, real editor input paths, parse cost, limits, typed errors |
| `dialect/escape.test.ts`, `frontmatter.test.ts`, `limits.test.ts`, `codec.test.ts`, `editor.test.ts` | Units, API and editor commands |
| `normalize-client.test.ts`, `normalize-worker.test.ts` | The worker client (with a fake worker) and the worker entry |
