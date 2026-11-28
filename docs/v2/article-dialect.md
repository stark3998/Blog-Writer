# Article Markdown dialect

Status: **frozen** (v1, 2026-11-26). The shared fixtures live in `contracts/fixtures/`. Changes need a contract version bump.

Origin: the Phase 0 editor spike. Reference implementations and the golden corpus live in `spikes/editor-dialect/`: TipTap v3 (`web/`), markdown-it-py + nh3 (`py/`), corpus (`corpus/`), XSS corpus (`xss/`). When this page and the corpus disagree, the corpus wins and this page gets fixed.

One dialect, read identically by the editor (TipTap v3 + `@tiptap/markdown`), the server renderer (markdown-it-py `commonmark` + `html: False`) and every producer (Claude drafts, revisions, imports). It is **CommonMark 0.31** plus **GFM tables and task lists**, ```` ```mermaid ```` fences, `:::note|tip|warning` callouts and `[^src-id]` citations. Nothing else.

## 1. File

- UTF-8, LF line endings, no BOM, exactly one trailing newline. No tabs or trailing whitespace outside code. Never two blank lines in a row outside code.
- Optional frontmatter (required for publishable articles), then one blank line, then the body.
- The body must not start with `---` (it would read as frontmatter). Body size limit: 512 KB.
- Forbidden characters outside code: C0/C1 controls, U+2028/U+2029, private-use U+E010-U+E03F (serializer placeholders).

## 2. Frontmatter

| Key | Type | Rule |
|---|---|---|
| `title` | string | plain text, 1-150 chars, no `<`, no control chars or line breaks, no edge whitespace |
| `slug` | slug | `^[a-z0-9]+(-[a-z0-9]+)*$`, max 80, immutable once published |
| `excerpt` | string | plain text, 1-400 chars, same rules as `title` |
| `pillar` | slug | a pillar id from Library |
| `format` | slug | a format-pack id (`case-study`, `framework`, `deep-explainer`, `news-analysis`, ...) |
| `tags` | slug list | 0-10 unique slugs, max 40 chars each |
| `series` | mapping, optional | `id` (slug), `title` (plain text), `part` (1-9999), `total` (optional, >= `part`) |

Parsed with the YAML *failsafe* schema (every scalar is a string, so `no`, `2024-01-01` and `1.0` never change type, and explicit tags such as `!!binary` or `!!timestamp` are read as plain strings on both sides), then validated; unknown keys, duplicate keys, aliases and nesting too deep to parse are errors. Canonical form (key order fixed, free text double-quoted with JSON-style escapes, slugs bare, tags as a flow list, `series` as a block mapping, `total` omitted when unknown):

```yaml
---
title: "Least-privilege tool calls for AI agents: a field guide"
slug: least-privilege-tool-calls-for-ai-agents
excerpt: "How to give an agent exactly the access a single tool call needs."
pillar: agents
format: deep-explainer
tags: [agents, identity, mcp]
series:
  id: agent-identity-in-practice
  title: "Agent identity in practice"
  part: 2
  total: 4
---
```

## 3. Body constructs (canonical syntax)

| Construct | Canonical syntax | Rules |
|---|---|---|
| Paragraph | one line, no hard wrap | soft breaks normalize to a space; Unicode whitespace at block start/end is dropped |
| Heading | `## Text` | ATX only, levels 2-4 (title is the h1), top level only, never empty, no closing `#`s |
| Emphasis | `*em*`, `**strong**`, `***both***` | asterisks only (intraword emphasis works) |
| Inline code | `` `code` `` | shortest unused backtick run; one space of padding when the code starts/ends with a backtick, or with a space on both sides |
| Link | `[text](https://...)`, `[text](dest "title")` | targets: `https:`, `mailto:`, `#fragment`, site-relative `/path` (not `//`); `<dest>` when it has spaces or unbalanced parentheses (CR/LF inside are written `%0D`/`%0A`); the title is one line; no reference links, no autolinks, bare URLs are text |
| Image | `![alt](src)` / `![alt](src "title")` | alone in its paragraph; alt required; `src` https or site-relative (`/blog-media/<slug>/...`) |
| Hard break | `\` at end of line | not in headings, table cells or citations; never at the end of a block (dropped) |
| Bullet list | `- item` | marker `-`, content indented to the content column; a bullet/task list directly after another uses `*` (CommonMark would merge them); an empty item is `- &nbsp;` |
| Ordered list | `1. item` | numbered sequentially from the list's start; delimiter `.`, or `)` for a list directly after another ordered list |
| Task list | `- [ ] todo`, `- [x] done` | lowercase `x`; a list is all tasks or no tasks |
| List tightness | no blank line between items | a blank line only where CommonMark needs one (second paragraph in an item, block after a nested list/quote/table) |
| Blockquote | `> text`, `>` on blank lines | nested quotes `> >` |
| Code block | ```` ```lang ```` ... ```` ``` ```` | backtick fences only, one longer than any backtick run at a line start inside; info string is one lowercase language (`[a-z0-9][a-z0-9+#._-]*`); a backtick fence cannot carry a backtick, so the writer drops the info words from the first one that holds a backtick (`~~~a`b` becomes a plain ```` ``` ````) |
| Mermaid | ```` ```mermaid ```` | rendered by the asset pipeline (strict mode) |
| Thematic break | `---` | |
| Table | `\| a \| b \|` / `\| --- \| :---: \|` | header row required; no column padding; delimiters `---`, `:---`, `---:`, `:---:`; inline content only; every `\|` in a cell escaped, inside code spans too; empty cell `\| \|` |
| Callout | `:::note` ... `:::` | types `note`, `tip`, `warning`; no title; top level only, not nested; may hold paragraphs, lists, quotes, code, mermaid, tables, rules (no headings); fence lengthened to `::::` (or more) when the content has a line starting with `:::` |
| Citation | `claim.[^src-id]` | label = brief source id `^[a-z0-9]+(-[a-z0-9]+)*$` (max 64); must have a definition; not inside link text; an editor id holding whitespace or `]` is written with `-` in their place (reference and definition alike) |
| Citation definition | `[^src-id]: Publisher. *Title*. [site](https://...)` | one line of inline text; all definitions at the end, one per line, no blank lines between, in order of first citation, then unreferenced ones; the last duplicate wins (and is an error) |

## 4. Not allowed (linter error codes)

Raw HTML of any kind, including comments (`raw-html`; it renders as literal text) · MDX/JSX/ESM · h1, h5, h6 (`heading-level`) · headings inside lists, quotes or callouts (`heading-nested`) · setext headings, indented code (normalized; warnings `setext-heading`, `indented-code`) · strikethrough, underline, highlight, sub/superscript, math, emoji shortcodes, wiki links, attribute blocks, definition lists, GitHub alerts (`> [!NOTE]`) - all render as literal text · reference links and link definitions (normalized away) · `http:`, `javascript:`, `data:`, protocol-relative and relative links or images (`link-target`, `image-src`) · inline images (`inline-image`), missing alt (`image-alt`) · callouts with titles, unknown types, nested or not top level (`callout-invalid`, `callout-nested`) · mixed task/plain lists (`mixed-task-list`) · undefined, duplicate, malformed, unknown-source or in-link citations (`citation-undefined`, `citation-duplicate`, `citation-id`, `citation-unknown-source`, `citation-in-link`) · definitions with more than one paragraph or block content (`citation-definition-blocks`) · inline footnotes `^[...]` (literal text) · empty mermaid fences.

## 5. Escaping (canonical writer)

- Always escape `\`, `` ` ``, `*`, `[`, `]` in text. Escape `_` unless it sits between two letters/digits (`snake_case` stays bare).
- Escape `<` before a letter, `/`, `!` or `?` (would start an autolink or tag); escape `&` before something entity-like (`&copy;`, `&#35;`).
- At the start of a line (a block's first text, or after a hard break): escape `#` run + whitespace, `>`, `-` before space/`-`/`|`/`:`, `+` before space, `=`, `N.`/`N)` + space, `:` before `:` or `-`, `|`, `~~`. (Only a space or tab after the `#` run makes a heading, as in CommonMark; the `#` is still escaped before any whitespace.)
- In headings, escape a trailing `#` run that follows whitespace (`Item \#`).
- Inside link text and image alt, write brackets as `&#91;` / `&#93;` (marked unescapes `\[` there before parsing).
- Prose tabs become spaces. A line ending inside a text node (edited, pasted or generated text; parsing never produces one) is written as a space, a run of them with surrounding blanks as one space; inside inline code each line ending becomes a space. Text can therefore never start a new Markdown line. Everything else is written literally in UTF-8. The only entity references in canonical output are `&#91;`/`&#93;` (link text, alt) and `&nbsp;` (an empty list item).

## 6. Normalization

Any producer may emit non-canonical but valid CommonMark; the normalizer rewrites it to the canonical form above and must not change meaning. Allowed normalization categories (the corpus test asserts nothing else changes): `emphasis-delimiter` (`_`/`__`), `bullet-marker`, `ordered-delimiter`, `ordered-numbering`, `heading-style` (setext, closing `#`s), `code-fence-style` (tilde, indented, fence length), `thematic-break`, `autolink` (to explicit links), reference links inlined, `soft-break` (joined lines), hard-break style, `escape-or-entity` (escapes added/removed, entities decoded), `list-looseness` (blank lines between single-paragraph items dropped), `callout-marker`, `mermaid-info` (`Mermaid title=x` to `mermaid`), `image-alt-markup` (alt reduced to its plain text), table padding, citation definitions moved/reordered, whitespace, `frontmatter-style`. Normalization runs in the pipeline after drafting and on every editor save; the result must be byte-stable (normalizing twice changes nothing).

## 7. Rendering contract (server HTML, then nh3 with `sanitizer-profile.json`)

`<h2 id>`/`<h3 id>` (ASCII slugs, de-duplicated `-1`, `-2`; also the TOC) · `<pre class="highlight"><code class="language-x">` with Pygments token classes · `<figure class="mermaid" data-diagram="<sha256>">` wrapping the escaped source (replaced by the rendered SVG at publish) · `<aside class="callout callout-note|tip|warning">` · `<ul class="contains-task-list">` / `<li class="task-list-item">` / disabled `<input type="checkbox">` · citations as `<sup class="footnote-ref"><a href="#fnN" id="fnrefN">[N]</a></sup>` with `<section class="footnotes">` at the end (numbered by first citation) · `<th|td style="text-align:...">` · images get `width`/`height` from the asset manifest. Links keep only https/mailto/#/site-relative targets and get `rel="noopener noreferrer"`.
