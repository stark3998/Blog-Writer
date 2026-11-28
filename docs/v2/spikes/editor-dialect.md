# Spike report: article dialect + TipTap v3 round trip

Date: 2026-11-25 · Plan sections: "Data > Article dialect spec", "Frontend (web/) > Editor" · Code: `spikes/editor-dialect/` · Spec: `docs/v2/article-dialect.md`

## Verdict: GO WITH CHANGES

"TipTap + markdown-it-py share one dialect" holds, with evidence:

- **Byte-stable round trip.** All 22 corpus files go parse -> serialize -> parse -> serialize through a real headless TipTap v3 editor (jsdom, schema content checks on) and are byte-identical after the first normalization; the re-parsed documents are identical too.
- **Canonical files are fixed points.** The 17 canonical files (every construct, all the edge cases, a 1,514-word article) come back byte-for-byte unchanged.
- **The first normalization only changes syntax.** The 5 non-canonical files change only in spec-allowed categories. A markdown-it token-stream oracle (same version and plugin set as the server) reads source and output as the same document. The Python suite then renders TipTap's output with the real server renderer and gets the same HTML, TOC, citations, diagrams and word count as the original, for all 22 files.
- **Escaping holds under random input.** 11 fast-check properties pass, one per inline context (paragraph, after a hard break, heading, list item, table cell, inline code, link text and destination, image alt, callout, emphasis, citation definition). They run 1,500 times each by default and passed 20,000 runs on three seeds locally: TipTap and markdown-it read back exactly the text that was written.
- **The sanitizer holds.** 24 hostile-Markdown and 20 hostile-HTML cases come out inside the profile (checked by a separate stdlib HTML audit), with reviewed goldens.

It is not a plain GO because:

1. Stock `@tiptap/markdown` 3.35.0 is **not** round-trip safe for this dialect. It needed 26 targeted fixes (listed under "Patches"). All are extension overrides or a private `marked` instance; there is no fork. One fix replaces a private method on the manager instance, so upgrades must be gated on the corpus suite.
2. The server pipeline's "canonical Markdown normalization (v0)" needs a **Python twin of the TipTap serializer**. The spike proves the rules and ships the goldens (`corpus/normalized/`) a port must match byte-for-byte, but does not build that port.
3. A few divergences remain for input **outside** the dialect (raw HTML lines, block content in citations, a citation inside link text, and others). The linter catches each one, so it must run on every save and as a publish gate.

## What was built

| Path | Contents |
|---|---|
| `docs/v2/article-dialect.md` | One-page dialect spec: constructs, forbidden list with lint codes, canonical style, escaping, allowed normalizations, HTML contract |
| `spikes/editor-dialect/corpus/` | 22 golden files and `manifest.json`; `normalized/` holds the reviewed TipTap output |
| `spikes/editor-dialect/web/` | TipTap v3 dialect: `articleExtensions()`, `ArticleCodec` (parse/serialize/normalize/`check`), `DialectMarkdown`, callout, citation and footnotes, mermaid, overrides, private marked, escaper, frontmatter, `scripts/licenses.mjs`. 273 vitest tests, `tsc` strict clean |
| `spikes/editor-dialect/py/` | `article_render`: renderer (markdown-it-py), nh3 sanitizer, linter (gate 5 dialect conformance), frontmatter twin, slugger. 257 pytest tests, ruff and mypy `--strict` clean |
| `spikes/editor-dialect/xss/` | XSS corpus (24 Markdown, 20 HTML), shared with the future `rehype-sanitize` twin |
| `spikes/editor-dialect/sanitizer-profile.json` | The shared sanitizer profile as data, golden-tested |

## Versions and licenses

Nothing paid. No TipTap Pro packages, AI Toolkit, Collaboration or Cloud. `@tiptap/markdown` is MIT in v3.

| Package (pinned) | License | Role |
|---|---|---|
| `@tiptap/core`, `pm`, `starter-kit`, `markdown`, `extension-{table,list,image,link,code,code-block,heading,paragraph,document,hard-break,text}`, `extensions` 3.35.0 | MIT | editor |
| `marked` 17.0.6 (TipTap's tokenizer; private instance) | MIT | editor |
| `entities` 9.0.0 | BSD-2-Clause | editor (HTML5 entity decoding) |
| `yaml` 2.9.1 | ISC | editor (frontmatter) |
| vitest 5.1.2, jsdom 31.0.1, fast-check 4.11.0, typescript 6.0.3 (Apache-2.0), diff 9.0.0 (BSD-3) | MIT unless noted | web dev |
| markdown-it 14.1.0 + markdown-it-footnote 4.0.0 + markdown-it-container 4.0.0 | MIT | web dev only: the parity oracle, pinned to the version markdown-it-py ports |
| lightningcss 1.33.0 (via vite) | **MPL-2.0** (weak copyleft, free) | web dev, transitive, never shipped |
| markdown-it-py 4.3.0, mdit-py-plugins 0.7.0, mdurl 0.1.2 | MIT | server |
| nh3 0.3.8 (ammonia) | MIT | server |
| Pygments 2.21.0 | BSD-2-Clause | server |
| PyYAML 6.0.4 | MIT | server |
| pytest 9.2.0, ruff 0.17.6, mypy 2.4.1, types-pyyaml, types-pygments | MIT / Apache-2.0 | py dev |

`node scripts/licenses.mjs --prod`: 48 production packages, 46 MIT, 1 ISC, 1 BSD-2-Clause, 0 outside the allowlist. `npm audit --omit=dev`: 0 vulnerabilities. Full tree: the dev-only oracle `markdown-it@14.1.0` carries the GHSA advisories fixed in 14.3.3 (ReDoS, smartquotes, linkify and link-reference quadratic paths). It stays pinned for parity with markdown-it-py's 14.1.0 port; see "Gaps".

## Results per corpus file

TipTap columns: **golden** (first normalization equals the reviewed `normalized/` file), **stable** (bytes and document identical on the second pass), **fixed** (canonical file unchanged), **oracle** (markdown-it reads source and output as the same document; categories found). Server columns: **HTML** (sanitized HTML and metadata match goldens and pass the audit), **parity** (TipTap's output renders the same as the source), **lint** (errors found equal the manifest).

| File | Covers | golden | stable | fixed | oracle | HTML | parity | lint |
|---|---|---|---|---|---|---|---|---|
| 01-frontmatter-and-paragraphs | frontmatter, paragraphs, punctuation | pass | pass | pass | pass | pass | pass | clean |
| 02-headings | h2-h4, trailing `?!:#`, `C#`, unicode, duplicate ids | pass | pass | pass | pass | pass | pass | clean |
| 03-inline-escapes | every escaping rule, line-start syntax, entity-like text | pass | pass | pass | pass | pass | pass | clean |
| 04-emphasis | nesting, intraword, punctuation flanking, code and links in emphasis | pass | pass | pass | pass | pass | pass | clean |
| 05-links-and-images | titles, parentheses in URLs, `<dest>`, fragment/site-relative/mailto, images | pass | pass | pass | pass | pass | pass | clean |
| 06-lists | nesting x3, start numbers, code/quote/table in items, 10+ items | pass | pass | pass | pass | pass | pass | clean |
| 07-task-lists | tasks, nested tasks, blocks in task items | pass | pass | pass | pass | pass | pass | clean |
| 08-code-blocks | longer fences, `:::`/`[^x]`/`<script>` inside code, tabs, trailing spaces, empty block, code-span edges | pass | pass | pass | pass | pass | pass | clean |
| 09-mermaid | flowchart, sequence, `A:::class` mermaid syntax | pass | pass | pass | pass | pass | pass | clean |
| 10-tables | alignment, `\|` in code and text, empty cells, unicode | pass | pass | pass | pass | pass | pass | clean |
| 11-callouts | 3 types, lists/tasks/quote/table/code/image inside, `::::` fence, consecutive | pass | pass | pass | pass | pass | pass | clean |
| 12-citations | repeated, in list/table/callout, definitions, link beats citation | pass | pass | pass | pass | pass | pass | clean |
| 13-blockquotes-and-rules | multi-paragraph, nested, list/code in quote, `---` | pass | pass | pass | pass | pass | pass | clean |
| 14-hard-breaks | breaks in paragraph, list, task, quote, callout; syntax after a break | pass | pass | pass | pass | pass | pass | clean |
| 15-unicode | CJK, RTL, ZWJ emoji, combining marks, NBSP, typography | pass | pass | pass | pass | pass | pass | clean |
| 16-long-lines | 1,467-character paragraph, long URL, long inline code, long cell | pass | pass | pass | pass | pass | pass | clean |
| 17-full-article | 1,514 words, series frontmatter, every construct | pass | pass | pass | pass | pass | pass | clean |
| 18-normalize-syntax | setext, `_`, `*`/`+` bullets, soft wraps, tilde/indented code, autolinks, reference links, entities, closed ATX, padded table, `[X]`, `1.` x3, `***`/`___`, `Mermaid` | pass | pass | n/a | pass: autolink, bullet-marker, code-fence-style, emphasis-delimiter, escape-or-entity, heading-style, mermaid-info, ordered-numbering, soft-break, thematic-break | pass | pass | heading-level (2x h1, expected) |
| 19-normalize-citations | out-of-order and unused definitions, lazy line, undefined label | pass | pass | n/a | pass: escape-or-entity, soft-break | pass | pass | citation-undefined (expected) |
| 20-normalize-frontmatter | key order, quoting, block tags, flow series | pass | pass | n/a | pass: frontmatter-style | pass | pass | clean |
| 21-normalize-lists | loose list, `1)`, `*` with wide spacing, lazy line | pass | pass | n/a | pass: bullet-marker, list-looseness, ordered-delimiter, soft-break | pass | pass | clean |
| 22-tolerated-but-linted | h1, h5, inline image, raw HTML block/inline/comment | pass | pass | n/a | pass: escape-or-entity | pass | pass | heading-level, inline-image, raw-html (expected) |

Frontmatter: Python's `dump_frontmatter` output is byte-identical to TipTap's for every corpus file.

## Normalization diffs (every first-normalization change)

Generated by `npm run report` (`web/reports/normalization.json`). Canonical files 01-17: no diff.

`18-normalize-syntax.md` (10 categories, all syntax-only; condensed, with `/` joining parallel hunks and `␠` marking a trailing space):

```diff
-Setext Heading Level 2
------------------------
+## Setext Heading Level 2
-Setext Heading Level 1 Is Still Parsed
-=======================================
+# Setext Heading Level 1 Is Still Parsed
-Alternate _emphasis_ with underscores and __strong underscores__.
+Alternate *emphasis* with underscores and **strong underscores**.
-* star bullet            /  -+ plus bullet
+- star bullet            /  +- plus bullet
-Soft-wrapped paragraph that continues
-on the next line and
-   on a third, indented line.
+Soft-wrapped paragraph that continues on the next line and on a third, indented line.
-Two-space hard break␠␠
+Two-space hard break\
-~~~python ... ~~~
+```python ... ```
-    indented code block
+```
+indented code block
+```
-Autolinks: <https://example.com/auto> and <hello@example.com>.
+Autolinks: [https://example.com/auto](https://example.com/auto) and [hello@example.com](mailto:hello@example.com).
-Reference link: [the spec][spec] and a collapsed one: [spec][].
-[spec]: https://spec.commonmark.org/0.31.2/ "CommonMark spec"
+Reference link: [the spec](https://spec.commonmark.org/0.31.2/ "CommonMark spec") and a collapsed one: [spec](https://spec.commonmark.org/0.31.2/ "CommonMark spec").
-Entities: &copy; &mdash; &#8212; &#x2014; &amp; &nbsp;end.
+Entities: © — — — &  end.            (the last gap is a literal U+00A0)
-Unnecessary escapes: \. \! \( \) \: \@ \- \# \> in the middle.
+Unnecessary escapes: . ! ( ) : @ - # > in the middle.
-### Closed ATX heading ###
+### Closed ATX heading
-#    Heading with extra spaces
+# Heading with extra spaces
-| a  |   b |
-|----|----:|
-| 1  |   2 |
+| a | b |
+| --- | ---: |
+| 1 | 2 |
-- [X] uppercase X task
+- [x] uppercase X task
-1. all ones / 1. numbering
+2. all ones / 3. numbering
-Trailing spaces at the end of a paragraph␠␠␠
+Trailing spaces at the end of a paragraph
-*** / ___
+--- / ---
-```Mermaid
+```mermaid
-(two extra blank lines)
```

`19-normalize-citations.md`: definitions moved to the end in first-citation order (`b`, `a`, `c`) with the unused `[^unused]` last; the lazy continuation line joined (`Source B, defined in the middle, continued on a lazy line.`); undefined `[^missing]` written as `\[^missing\]` (markdown-it reads it as text either way).

`20-normalize-frontmatter.md`: keys reordered `title, slug, excerpt, pillar, format, tags, series`; `'single'` and bare strings double-quoted; block-list tags to `[identity, entra]`; flow-mapping series to a block mapping; a blank line inserted before the body.

`21-normalize-lists.md`: blank lines between single-paragraph items dropped (loose to tight; the HTML loses its `<p>` wrappers, the only rendered change any normalization makes); `1)` to `1.`; `*   wide` to `- wide`; lazy continuation joined into the item.

`22-tolerated-but-linted.md`: raw HTML kept as literal text and escaped (`\<div class="raw">...\</div>`, `\<b>`, `\<!-- a comment -->`). Rendering is unchanged: the server renders it as escaped text, and the linter rejects it.

## Patches to stock `@tiptap/markdown` 3.35.0 (all needed for the corpus to pass)

Parsing (parity with markdown-it, `html: false`):
1. Raw HTML was turned into rich content through DOMParser in the browser, and kept literal on the server: `<b>*x*</b>` became bold `*x*`, and `<script>` vanished. Fix: a private marked instance whose `html`/`tag` tokenizers never match, so HTML stays literal text.
2. Bare URLs were autolinked (marked GFM `url`). Disabled.
3. `~~x~~` was tokenized as `del` and silently dropped (no Strike extension). Disabled, so it is literal.
4. Only `&lt; &gt; &quot; &amp;` and numeric references were decoded, so `&copy;` became `&amp;copy;`. Fix: full HTML5 decoding (`entities`).
5. The ordered-list tokenizer folded tables and blocks into the item text and ignored a `.`/`)` delimiter change. Disabled; marked's CommonMark list tokenizer is used.
6. The task-list tokenizer read only an item's first line, so hard breaks and continuation lines fell out of the item. Disabled; tasks are built from marked's GFM list tokens. Mixed lists bypass the same first-line parser.
7. Block-level `text` tokens (tight list items after a code block) kept raw Markdown. Fix: parsed into a paragraph.
8. Fences indented 1-3 spaces were dropped entirely. Fixed.
9. Images were block nodes plus "unwrap image-only paragraphs", so an inline image broke the schema, and alt kept raw Markdown (`Alt *text*`). Fix: an inline image node (as in CommonMark) whose alt is the plain text.
10. Heading levels outside `levels` became paragraphs. Fix: all levels kept; the linter rejects them instead.
11. The table tokenizer "repaired" unescaped `|` inside code spans, which markdown-it treats as cell separators. Disabled.
12. The `&nbsp;`-paragraph convention dropped content and created empty paragraphs from blank-line runs. Removed: soft breaks become spaces, tabs become spaces, empty paragraphs (including TipTap's trailing node) are dropped.
13. Block-extension `start()` was probed mid-line by marked (`src.slice(1)`), so `x[^a]:` split a paragraph. Fix: the callout and definition `start()` only match after a newline.

Serializing:

14. Text was escaped with entities (`AT&amp;T`, `a &gt; b`) plus a blanket `\*\_\[\]\~`, with no line-start handling: `1.`, `#`, `>`, `-`, `+`, `=`, `:::`, `|` or `~~~` at the start of a line, or after a hard break, became block syntax. Fix: the context-aware escaper in the spec (property-tested).
15. Hard break was written as two trailing spaces. Now a backslash.
16. A code block containing ```` ``` ```` was corrupted (fence always ```` ``` ````). Now a longer fence.
17. Code spans: edge spaces were moved outside the backticks, and whitespace-only code lost its mark. Fixed with padding and private-use placeholders.
18. The code mark's `excludes: '_'` made `` **`x`** `` and `` [`x`](u) `` schema-invalid. Now `excludes: ''`, priority 50 (always innermost).
19. Link and image destinations and titles were written raw (spaces, parentheses, quotes). Now formatted as CommonMark requires.
20. Brackets in link text and alt are written as `&#91;`/`&#93;`, because marked unescapes `\[` there before tokenizing, which recreated citations and nested links.
21. Tables were padded to column width (whole-table diffs on every edit), started with a blank line, and wrote multi-paragraph cells with raw `<br>`. Now compact, with pipes in code spans escaped too.
22. List items indented empty lines (trailing whitespace) and left continuation lines unindented. The item renderer is rewritten, with CommonMark-safe separators (tight where CommonMark allows). An empty item is written `- &nbsp;`, because a bare nested `-` would turn its parent paragraph into a setext heading. A trailing hard break, which CommonMark reads as a literal backslash, is dropped.
23. Adjacent lists of the same kind merged on reload. They now alternate `-`/`*` and `.`/`)`.
24. Empty headings were dropped. Now `##`.
25. Unicode whitespace at block start and end is now trimmed, because the parsers strip it.
26. `MarkdownManager` mutated the global `marked`. Now a private instance per editor.

Fragility: fixes 14, 17 and 20 replace the manager's private `encodeTextForMarkdown` on the instance; several renderers rely on `context.meta.markText` and `parentAttrs`. Pin TipTap exactly, and let Renovate bump it only when the corpus, parity and property suites pass.

## Gaps and known divergences

- **No Python normalizer yet.** The pipeline's "canonical Markdown normalization (v0)" runs server-side. Port the serializer rules (escaping, list markers and separators, fences, tables, callout fence length, footnote order, frontmatter) to a markdown-it-py token renderer. Accept it only when it reproduces `corpus/normalized/` byte-for-byte and matches `ArticleCodec` on the property generators. The Python frontmatter dumper already matches.
- **Divergences on input outside the dialect** (kept as tests in `web/test/parity.test.ts`):
  - marked ends a paragraph at an HTML-block-looking line; caught by `raw-html`.
  - Block content in a citation definition; caught by `citation-definition-blocks`.
  - `[text [^ref]](url)` is a link in marked and not in markdown-it; caught by `citation-in-link`.
  - `#` followed by NBSP is a heading in marked but not in CommonMark; the canonical writer escapes it.
  - `&#128;`-`&#159;` decode as Windows-1252 in TipTap and as U+FFFD in markdown-it.
  - A mixed task list is one list on the server but two in the editor; caught by `mixed-task-list`.
- **Editor states Markdown cannot hold**, such as bold over part of a code span, a code span with `\|` inside a table cell, or emphasis that must open after a letter and close before punctuation. `ArticleCodec.check()` re-parses and reports `lossless: false`; the editor should warn before saving.
- **Structure limits by design.** Callouts are top-level only (a callout inside a list item is unwrapped in the editor). Citation definitions are single-paragraph text. Images are inline nodes; the editor should present image-only paragraphs as figures.
- **Link destinations containing entity references** (`?a=1&amp;b=2`) stay raw in TipTap (markdown-it decodes them). The rendered output is identical, but the editor shows `&amp;`.
- **Heading ids** are ASCII slugs (`section` fallback for all-CJK headings), not prefixed (`clobberPrefix ''` per plan). Slugs like `location` or `forms` are allowed. That is low risk with no inline scripts on the portfolio, but consider an `h-` prefix before the contract freezes.
- **Sanitizer profile additions** beyond "class pattern, id on h2/h3, img src/alt/width/height". Each is needed by the renderer and constrained by an exact rule:
  - `id` on footnote `li`/`a` (`fnN`, `fnrefN:M`)
  - `start` on `ol` (digits)
  - `style` on `th`/`td` (exactly `text-align:left|center|right`)
  - a forced-disabled checkbox `input`
  - `data-diagram` (sha256) on `figure`
  - `title` on `a`/`img`
  - nh3 adds `rel="noopener noreferrer"` to every link, in-page anchors included.
- **DoS.** The JS markdown-it advisories (≤14.3.2) may apply to markdown-it-py's 14.1.0 port. Simple probes, including a long multi-line link reference definition, were linear up to 8,000 lines. Run `pip-audit` in CI, keep the 512 KB input cap (enforced in `render_article`) and run rendering inside the job runner's timeout.
- **TypeScript** 6.0.3 is used (7.0.2 is current), to keep tooling stable during the spike.

## Security results

- `render_article` with `html: False` never emits author markup (the raw renderer output is audited too). Every hostile case is neutralized: `<script>`, `<img onerror>`, `<svg onload>`, `javascript:` in several encodings (case, entities, tab, ZWJ, NBSP), `vbscript:`, `data:` links and images (markdown-it allows `data:image/png`; nh3 strips it), protocol-relative `//` and `/\` links, `http:`, relative URLs, injection through link titles, alt, fence info, mermaid source, callout params, footnote labels, heading text, forms, `style`, comments, and entity-encoded markup.
- Feeding raw HTML straight to the sanitizer also neutralizes mXSS classics (`<math><mtext><table><mglyph><style>`, `<noscript>`, comment confusion), `iframe`/`object`/`embed`/`base`/`meta`/`link`/`template`, `formaction`, non-checkbox inputs, foreign classes and ids, and out-of-range dimensions and `start`. The output is idempotent.
- The audit uses Python's own `html.parser`, not ammonia, so the sanitizer is checked by an independent parser.

## Performance (M-series laptop)

| Step | Time |
|---|---|
| TipTap `ArticleCodec` create | 9 ms |
| Normalize the 10 KB full article (parse + serialize) | 2.1 ms |
| Server render + sanitize | 3.7 ms |
| Lint | 7.9 ms |

## Recommended plan changes

1. In "Data > Article dialect spec", record:
   - list marker alternation for adjacent lists
   - brackets as `&#91;`/`&#93;` in link text and alt
   - images as inline nodes
   - single-paragraph citation definitions ordered by first citation
   - the forbidden list with its lint codes
   - the allowed normalization categories
2. Add a Phase 1 or Phase 4 task: a Python canonical normalizer (markdown-it-py tokens to canonical Markdown) that must reproduce `corpus/normalized/` byte-for-byte. Pipeline normalization (v0) uses it.
3. Treat the TipTap pin as a gated dependency. Renovate bumps of `@tiptap/*` and `marked` merge only when the corpus, parity and property suites pass. The Markdown layer depends on internals: private `encodeTextForMarkdown`, `meta.markText` and `parentAttrs`.
4. Run gate 5 (dialect lint, `article_render.lint`) on every editor save as well as at publish, with `source_ids` from the brief. Block publishing on errors.
5. Before saving, call `ArticleCodec.check()` in the editor and warn on `lossless: false`.
6. Generate the `rehype-sanitize` schema from `sanitizer-profile.json`, and run `xss/` and the corpus goldens against it in portfolio PR B. Decide on an `id` prefix before the contract freeze.
7. Keep the shared fixtures (`corpus/`, `xss/`, `sanitizer-profile.json`) in one place that both CI jobs and the portfolio test can read.
8. Add `pip-audit`, the 512 KB input cap and a render timeout to the server job.

## How to reproduce

```sh
cd spikes/editor-dialect/web && npm ci && npm run typecheck && npm test && npm run report
cd ../py && uv sync --locked && uv run pytest && uv run ruff check src tests && uv run mypy src
```

`npm run golden` and `UPDATE_SNAPSHOTS=1 uv run pytest` regenerate the goldens after a deliberate change; review the diff before committing.

## Reviewer notes (independent review, 2026-11-25)

**Verdict: agree with GO WITH CHANGES.** The central claim holds and reproduces: one dialect, byte-stable TipTap round trip, canonical fixed points, server parity, a sound sanitizer. The review found 10 bugs, fixed all of them inside the spike, and left 1 pipeline gap and 3 robustness issues open. No golden changed: `corpus/`, `corpus/normalized/`, `py/tests/snapshots/`, `xss/` and `sanitizer-profile.json` are byte-identical. Nothing was committed.

### What was re-run

- **Builder's code as delivered, from a clean shell** (fresh `npm ci`, fresh `.venv`, exact test command): web 6 files / 273 passed; `tsc` clean; license gate 48 production packages, 0 outside the allowlist; `npm audit --omit=dev` 0. Python: 257 passed; `ruff check` and `ruff format --check` clean; `mypy src` clean. All as claimed.
- **Property tests:** 20,000 runs on seeds 20261125, 7 and 42 passed, before and after the review changes (about 18 s per seed).
- **Timings** (no benchmark script ships with the spike; measured ad hoc on the same machine):

  | Step | Claimed | Measured |
  |---|---|---|
  | `ArticleCodec` create | 9 ms | 8.3 ms |
  | Normalize the 10 KB article | 2.1 ms | 1.75 ms |
  | Server render + sanitize | 3.7 ms | 3.55 ms |
  | Lint | 7.9 ms | 7.93 ms |

- **After the review**, the same command plus `mypy src tests`: web 7 files / 310 passed; Python 276 passed and 1 strict xfail (a documented gap); ruff, ruff format and mypy strict clean on `src` and `tests`; license gate unchanged.

### Claims that did not hold exactly

- "11 properties, 1,500 runs each": the link property runs 500 (`numRuns / 3`).
- "mypy strict clean": only `src` was checked. `tests/test_lint.py` had 3 errors (fixed).
- "The linter catches each [divergence]": not true for `##` + NBSP (fixed), for refused link schemes (fixed), or for linked images (reported only as `inline-image`).
- "Linear up to 8,000 lines": confirmed linear on the server (2.0x per doubling), but slow at the 512 KB cap (see open issue 3). TipTap had a quadratic path (fixed) and still has others in stock marked (open issue 2).
- The profile allows site-relative `/path` **links**. The plan's profile lists site-relative **images** only, and the "Sanitizer profile additions" list does not mention links. Record it as a plan change.

### Bugs found and fixed

1. **Text could create Markdown blocks (high).**
   - Line endings inside a text node were written raw, so `foo\n# x` became a paragraph plus an h1. The same input could produce a list, a fence or a `[^x]:` definition.
   - Affected contexts: paragraphs, headings, list and task items, quotes, callouts, table cells, footnote definitions, link and bold text, and inline code.
   - Reachable through `tr.insertText` and `insertContent(..., {preserveWhitespace})`, which is the path a planned "Selection AI → Accept" button would take.
   - Never tested before: the property generator stripped `\n` from every input.
   - Fix: `escapeInlineText` writes a run of line endings as one space. In inline code each line ending becomes a space (`encodeDialectText`; `DialectCode` pads on the folded text).
   - `check()` still reports `lossless: false`, so the editor still warns.
2. **Attributes could break out of their syntax (medium).**
   - A CR in a link destination (pasted `href="…&#13;…"`) was written raw.
   - A link title with a blank line split the paragraph.
   - A code-block language containing a backtick produced an invalid fence, so the code was read as Markdown. Sources: a valid `~~~a`b` tilde fence (normalization changed meaning and was not byte-stable) or a pasted `class="language-a`b"`.
   - A citation id containing whitespace or `]` (pasted `data-citation`) broke out of `[^…]`.
   - Fixes: `%0D` encoding, one-line titles, `fenceInfo()`, `citationLabel()`.
3. **Quadratic parse in the editor (medium).**
   - Citation's inline `start()` rescanned the rest of the source on every text step.
   - Cost: 16 KB of `[` took 1.1 s, 64 KB took 18 s, and 512 KB would take about 20 minutes, freezing the browser tab. Stock marked parses 32 KB in 12 ms.
   - Fix: only the first `[` is inspected (marked's text rule already stops before each `[`). 512 KB now takes about 0.3 s, with a regression test.
4. **NBSP headings (medium).**
   - marked treats `#` followed by any Unicode whitespace as a heading; CommonMark accepts only a space or tab.
   - A paragraph `##`+NBSP (or U+3000) was normalized into a real h2/h3, and no lint rule fired.
   - Fix: a `heading` tokenizer guard in `marked.ts`.
5. **Linter silent on refused schemes (medium).**
   - markdown-it refuses `javascript:`, `vbscript:`, `file:` and non-image `data:` targets, so they render as literal text and `link-target`/`image-src` never ran. Spec section 4 lists them as errors.
   - Fix: `_check_refused_links`, which parses with a permissive `validateLink`.
   - This was not an XSS: the sanitized HTML was always safe.
6. **Unsanitized citation URLs (low-medium).**
   - `RenderResult.citations[].url`, which feeds `published-blogs.citations[]`, bypassed nh3, so `http:`, `//host` and relative URLs got through.
   - Fix: the profile's href rule applies. The field now holds the first allowed link, or `None`.
7. **Uncaught `RecursionError` (low-medium).** About 3 KB of `series: [[[[…`, under the 8 KB frontmatter cap, crashed `render_article` and `lint_article`. It is now a `FrontmatterError`.
8. **Frontmatter twin mismatch (low).** `yaml` resolves `!!binary` and `!!timestamp` even with the failsafe schema, while PyYAML keeps them as strings, so the editor rejected frontmatter the server accepted. Fix: `resolveKnownTags: false`, with a twin test on each side.
9. **Lint size limit (low).** Only `render_article` enforced the 512 KB limit. `lint_article`, run on every save, parsed any size four times; it now returns `source-too-large`. It also parses the definitions probe once instead of twice.
10. **License gate (low).** `MIT AND GPL-3.0` passed because any one part matched the allowlist. `AND` now requires every part.

### Tests made stronger

- The property alphabet now includes LF, CR and CRLF for all 11 properties. Mutation check: with the fold disabled, 9 of 11 properties and 15 hardening tests fail.
- The link property compares markdown-it's href with `normalizeLink(href)`; it was `toBeTruthy()`.
- Both semantic comparisons were too lenient, and both are tightened with tests of their own:
  - The JS oracle collapsed all Unicode whitespace, so an NBSP-to-space change passed.
  - The Python `_equivalent_html` did the same and also dropped spaces next to inline tags, so `<em>a</em> b` equalled `<em>a</em>b`.
  - The corpus still passes under the stricter checks, so the builder's results stand.
- New tests:
  - `web/test/serializer-hardening.test.ts` (28): JSON, `insertText`, `insertContent` and pasted-HTML paths, plus the performance guard.
  - Parity: NBSP headings, tilde-fence info strings, and linked images as a known divergence. The NBSP divergence test now covers only the case still open.
  - Frontmatter tags, oracle sensitivity, refused-link lint cases, citation metadata, server-side proof of the parity fixes, deep YAML and the size limit.

### Open issues

1. **Normalization hides undefined citations from the lint gate (high for the pipeline).**
   - TipTap writes an undefined `[^label]` as `\[^label\]`, which lints clean and publishes as literal text. The linter cannot tell it apart from deliberate prose like the regex in `03-inline-escapes.md`.
   - The plan normalizes drafts *before* the gates, so a hallucinated citation would never trip `citation-undefined`.
   - Kept as a strict xfail (`test_undefined_citation_survives_normalization`).
   - Fix in the normalizer: return unresolved labels as a diagnostic (or keep undefined citations as nodes written raw), and fail gate 5 on it. Also lint the raw draft.
2. **Editor robustness on hostile input (medium).**
   - Stock marked has no nesting limit. The codec throws `RangeError` (stack overflow) on about 8 KB of `>`, 2,000 stacked `:::note` lines, or 300 list levels. markdown-it caps nesting at 20.
   - Super-linear paths remain in stock marked: 128 KB of `[a](` takes 5.3 s, and `*a` repeated takes 3.7 s.
   - Recommendation:
     - Normalize in a Web Worker with a timeout.
     - Refuse to open more than about 128 KB in the editor.
     - Pre-check nesting depth.
     - Map `RangeError` to a typed error.
3. **Server cost at the 512 KB cap (medium).** Linear but slow: 512 KB of `[` takes 5.0 s to render and 19.1 s to lint; a long link-reference definition takes 3.3 s and 11.6 s. Run render and lint off the FastAPI event loop (thread or process pool) with a timeout. Consider a lower cap: a 1,500-word article is 10 KB.
4. **Linked images (low).** `[![a](src)](href)` loses its link in the editor, and the linter reports only `inline-image`. Either allow linked images (a link mark on the image node) or give them their own lint code.
5. **`para\n##`+NBSP (low).** marked's paragraph-interrupt rule still splits the paragraph; the text is preserved. Kept as a known-divergence test.
6. **AI "Accept" design.** Insert *parsed* dialect Markdown as nodes, not raw text. The serializer now neutralizes stray line endings, but a multi-paragraph proposal inserted as text would flatten into one paragraph (`check()` reports `lossless: false`).
7. **Minor:**
   - `check()` compares frontmatter with `JSON.stringify`, so key order matters.
   - jsdom 31.0.1 needs Node ^24.15.0 (`EBADENGINE` on 24.14.1), but `package.json` says `>=24`. Pin the CI Node version.
   - The oracle reports fence-info whitespace (`   js  ` → `js`) as a semantic change; the corpus does not hit it.
   - The citation metadata URL is now the first *allowed* link.

### Security assessment

`html: false`, the nh3 allowlist with exact value rules, and the independent `html.parser` audit hold up. The review found no script-execution path. Extra probes were all blocked by the profile: mixed-case and entity-encoded schemes, tab/LF/CR in URLs, `/\host` and `//host`, `srcset`, and `data:image/svg+xml`. One nit: a raw C0 control character inside an otherwise valid `https:` href (`https://x/&#1;y`) survives `sanitize_html`. It is harmless, because the scheme is fixed and Markdown input is percent-encoded by markdown-it first. Still, the twin's regex should exclude `\x00-\x1f\x7f` when the profile is frozen. What the review did find were integrity gaps: Markdown structure injected through the serializer, metadata that skipped the sanitizer, and a lint gate that missed refused links or could be bypassed by normalization.
