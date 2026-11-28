# Build: article renderer, sanitizer, linter and canonical normalizer (`blogwriter.publish`)

Plan sections: "Article pipeline, gates, publishing" (pipeline step "canonical Markdown
normalization (v0)", gate 5, Publish steps 1-2) and "Data" > "Article dialect spec". Source: the
Phase 0 editor-dialect spike (`spikes/editor-dialect/py`, report
`docs/v2/spikes/editor-dialect.md`), ported to the server package, plus the Python canonical
normalizer the spike left open. Binding inputs: `docs/v2/article-dialect.md` (frozen dialect),
`contracts/sanitizer-profile.json` and `contracts/fixtures/{corpus,xss}`. Status: built and
tested; not wired into `blogwriter.main` (there is nothing to wire: no routes, no lifecycle, no
settings).

## Files

| Path (`server/src/blogwriter/publish/`) | Contents |
|---|---|
| `dialect.py` | Dialect constants (callout types, heading levels, slug and language patterns), the 512 KB cap (`MAX_SOURCE_BYTES`, `SourceTooLargeError`, `check_source_size`), `normalize_source`, `info_language`, JavaScript `trim` helpers |
| `parser.py` | The shared markdown-it-py configuration: `dialect_parser(...)` (cached, never mutate) and `new_dialect_markdown(...)` |
| `renderer.py` | `render_article`, `RenderedArticle`, `TocEntry`, `Heading`, `Citation`, `diagram_id`, `DiagramUrlError`, `create_markdown` |
| `profile.py` | Loads `contracts/sanitizer-profile.json` through the contract's `SanitizerProfile` (`load_profile`, `default_profile_rules`, `ProfileRules`) |
| `sanitizer.py` | nh3 built from the profile: `sanitize_html`, `attribute_filter`, `build_cleaner`, `URL_SCHEMES` |
| `frontmatter.py` | Split, strict parse (YAML node graph, strings only), validation, canonical dump |
| `slug.py` | Heading ids: `slugify`, `Slugger` |
| `lint.py` | Gate 5 dialect conformance: `lint_article` -> `list[Finding]` |
| `normalize.py` | The canonical normalizer: `normalize_markdown` -> `NormalizeResult(text, errors)` |
| `escape.py`, `tree.py`, `writer.py` | Normalizer internals: the editor's escaping rules, its document model, its writer (mark-boundary algorithm) |

`contract.py` and `contracts/**` are untouched. Tests: `server/tests/publish/test_dialect_*.py`
plus goldens in `server/tests/publish/snapshots/{corpus,xss,xss-html}`.

## Rendering (`render_article`)

```python
from blogwriter.publish.renderer import render_article, diagram_id

result = render_article(markdown)                       # sync, CPU-bound: use asyncio.to_thread
result.html            # sanitized htmlContent fragment (passes contract.audit_html)
result.toc             # (TocEntry(level, id, text), ...) = h2/h3 with ids, in order
result.headings        # every heading (Heading(level, text, id | None))
result.citations       # Citation(number, id, text, url | None), numbered by first citation
result.word_count, result.reading_time_minutes          # 230 words per minute, at least 1
result.frontmatter, result.title, result.excerpt        # None without frontmatter
result.mermaid_sources # source of every Mermaid fence, in order
```

- `MarkdownIt("commonmark", {"html": False})` + GFM tables + task lists (`mdit_py_plugins`) +
  `[^src-id]` footnote citations (no inline footnotes) + strict `:::note|tip|warning` callouts.
  Pygments token classes on `<pre class="highlight"><code class="language-x">`; h2/h3 get ASCII
  kebab-case ids (de-duplicated `-1`, `-2`, never `fnN`/`fnrefN`).
- **Mermaid.** Without a hook a fence renders as
  `<figure class="mermaid" data-diagram="<sha256>"><pre class="mermaid-source"><code>…` (escaped
  source; the spike's placeholder). With `diagram_url=callable` it renders
  `<figure class="mermaid" data-diagram="<sha256>"><img src="<url>" alt="…">`; the alt text is the
  diagram's Mermaid `accTitle:` or "Diagram". The URL must pass the profile's `src` rule (https or
  site-relative), else `DiagramUrlError`. `image_sizes` (`{src: (w, h)}`) adds `width`/`height` to
  images and diagrams.
- **Linked images** (`[![alt](src)](href)`) are outside the dialect: the image loses the link and
  surrounding link text keeps it (the editor's reading); the linter reports `linked-image`.
- `citations[].url` is the first link of the definition that the profile's `href` rule allows
  (this metadata skips nh3).
- Errors: `SourceTooLargeError` (> 512 KB), `FrontmatterError`, `DiagramUrlError`,
  `ContractUnavailableError` (profile missing or malformed: fail closed).

Publishing flow for diagrams (the hook is sync, `blogwriter.render` is async): render once,
render each `mermaid_sources` entry with `render.render_mermaid_svg`, upload it as
`published/<slug>/diagram-<diagram_id(src)[:12]>.svg`, then render again with
`diagram_url=lambda src: urls[src]` and `image_sizes`.

The HTML of all 22 corpus files and 24 XSS Markdown cases, and the sanitized output of the 20 XSS
HTML cases, are byte-identical to the spike's reviewed goldens under the frozen profile (which
adds `version` and the C0/DEL URL exclusion).

## Sanitizer (`sanitize_html`)

Built from `contracts/sanitizer-profile.json` only: allowed tags, attributes per tag, class/id/URL/
value patterns (ECMA-262 patterns translated by `contract.python_regex`), exact `text-align`
styles, the forced disabled checkbox, `rel="noopener noreferrer"` on every link, and the tags
dropped with their content. nh3's scheme allowlist (`https`, `mailto`) is checked against the
profile's URL patterns by a test. The profile is loaded with the contract's own checks (known
keys, `version` 1, every attribute has a rule) and cached per process; `BW_PUBLISH_CONTRACTS_DIR`
applies as for the contract.

## Linter (`lint_article`, gate 5 dialect conformance)

`lint_article(source, *, require_frontmatter=True, source_ids=None) -> list[Finding]`, sorted by
line. `Finding(code, severity, line, message)`; `code` and `line` are safe to log, `message` may
quote text. Errors block publishing; warnings do not.

Errors: `source-too-large`, `frontmatter-missing`, `frontmatter-invalid`, `nesting-too-deep` (new:
markdown-it silently drops content beyond 20 levels), `thematic-break-first`,
`forbidden-character`, `control-character`, `raw-html`, `heading-level`, `heading-nested`,
`heading-empty`, `mermaid-empty`, `callout-invalid`, `callout-nested`, `mixed-task-list`,
`link-target`, `image-src`, `image-alt`, `inline-image`, `linked-image` (new, Phase 0 decision),
`citation-undefined`, `citation-in-link`, `citation-duplicate`, `citation-id`,
`citation-unknown-source` (with `source_ids`), `citation-definition-blocks`. Warnings:
`setext-heading`, `indented-code`, `code-info`, `citation-unused`, `citation-placement`. The
spike's lint has no standards-ID check, so none was ported.

## Canonical normalizer (`normalize_markdown`)

`normalize_markdown(text) -> NormalizeResult(text, errors)`; `result.ok` is `not errors`.

- Reproduces `contracts/fixtures/corpus/normalized/<name>` byte for byte for all 22 files; every
  canonical file is a fixed point. Method: parse with the server's parser, build the editor's
  document model (text runs with mark sets plus atoms), apply the editor's normalizations (soft
  breaks, nested callouts lifted, definitions moved to the end in first-citation order with the
  last duplicate winning, empty paragraphs, trailing hard breaks, adjacent-list marker
  alternation), and write it back with a port of `@tiptap/markdown`'s mark-boundary algorithm and
  the dialect's node writers.
- **Twin check.** Against the real TipTap codec (the spike compiled to a scratch directory): 0
  differences on 2,100 generated valid dialect articles; on 311 hand-written edge cases the only
  differences are the documented ones below or inputs TipTap rejects. 132 of those cases are pinned
  in `test_dialect_normalize.py` with TipTap's output.
- **Where the server's reading wins** (TipTap's tokenizer reads these differently; all outside the
  dialect, so the published page is what markdown-it renders): entity references in link
  destinations are decoded (same page); a setext underline in a list item makes a heading; an
  ordered list starting above 1 does not interrupt a paragraph; `#` plus NBSP does not interrupt a
  paragraph; a raw-HTML-looking line does not end a paragraph; `\\|` in a table row escapes the
  pipe (so the row may not be a table); an empty first list item stays an item; a citation inside
  link text drops the link; block content in a definition is flattened; `&#128;`-`&#159;` decode to
  U+FFFD; nested emphasis of one kind collapses; a heading starting a list item stays a heading;
  ordered task items keep their checkbox. List items that start with a non-paragraph block get the
  editor's empty first paragraph (`- &nbsp;`), which also keeps `- ---` from reading as a rule.
- **Errors normalization would hide** (`HIDDEN_BY_NORMALIZATION`): an undefined `[^label]` is
  written `\[^label\]` exactly as the editor writes it (so `normalized/19` matches) and reported as
  `citation-undefined`; likewise `raw-html`, `callout-nested`, `callout-invalid`,
  `citation-duplicate`, `citation-definition-blocks`, `citation-in-link`, `linked-image`,
  `mixed-task-list`. They are lint findings of the **input** (input line numbers).
- **Fail closed**, text returned unchanged: invalid frontmatter (`frontmatter-invalid`), nesting
  beyond markdown-it's limit (`nesting-too-deep`), a body starting with a thematic break without
  frontmatter (`thematic-break-first`: its canonical `---` would read as frontmatter), canonical
  text above 512 KB (`source-too-large`). Input above 512 KB raises `SourceTooLargeError`.
- **Idempotent by construction.** A few inputs have no one-pass fixed point (emphasis whose
  delimiters cannot open or close where they land, next to punctuation, a citation or an image;
  TipTap's writer has the same limit and its own round trip is unstable on them). The pass is
  repeated until the text stops changing (at most 4 extra passes) and `normalize-unstable` is
  reported; without a fixed point the input comes back unchanged with that error.

### Gate 5

```python
result = await asyncio.to_thread(normalize_markdown, draft)      # store result.text and errors
findings = [*result.errors, *lint_article(result.text, source_ids=brief_source_ids)]
passes = not any(f.severity == "error" for f in findings)
```

`result.errors` describe the draft that was normalized and cannot be recomputed from
`result.text` (normalizing canonical text again reports nothing): persist them with the draft
until a human has reviewed it, or lint the raw draft as well. Phase 0 open issue 1 ("normalization
hides undefined citations from the lint gate") is closed this way; its strict xfail is now a
passing test.

## Cost

Normal articles (10 KB): normalize 5-17 ms, render + sanitize 4 ms, lint 8 ms. Hostile input at
the 512 KB cap is linear but slow (markdown-it-py dominates): render up to ~2 s, lint up to ~8 s,
normalize up to ~15 s (it lints the input and runs two passes). Run all three off the event loop
(`asyncio.to_thread`) inside the job runner's timeout; a thread cannot be cancelled, so a process
pool is the option if a hard limit is needed.

## Tests (`server/tests/publish`)

| File | What |
|---|---|
| `test_dialect_render.py` | corpus HTML and metadata goldens, contract audit, toc = audited headings, editor and Python normalizations render the same, diagram hook, linked images, limits |
| `test_dialect_xss.py` | 24 hostile Markdown and 20 hostile HTML cases: goldens, audit, no author markup before sanitizing, idempotent sanitizer, URL rules, C0/DEL |
| `test_dialect_sanitizer.py` | every value rule, Pygments classes, forced attributes, profile = contract profile, scheme allowlist, fail-closed loading |
| `test_dialect_lint.py` | manifest lint errors per corpus file, every error code, warnings, line numbers, the closed citation gap |
| `test_dialect_frontmatter.py`, `test_dialect_slug.py` | spike twins, canonical frontmatter equals the editor's for every corpus file |
| `test_dialect_normalize.py` | 22 goldens, fixed points, errors, 132 editor-twin cases, server-reading cases, fail-closed paths, seeded idempotence (valid and hostile input) and render-equivalence properties |
| `test_dialect_escape.py` | escaping unit cases (twins of `escape.test.ts`) and round-trip properties per inline context |

Regenerate goldens deliberately with `UPDATE_SNAPSHOTS=1` and review the diff.
