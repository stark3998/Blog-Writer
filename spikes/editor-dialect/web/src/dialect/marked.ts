/**
 * A private `marked` instance configured for the article dialect.
 *
 * `@tiptap/markdown` uses marked as its tokenizer. Out of the box marked
 * speaks GFM with raw HTML, bare-URL autolinks and `~~strike~~`, none of which
 * exist in the dialect (the server renders with markdown-it `commonmark`,
 * `html: false`, plus tables and task lists). The overrides below switch those
 * features off so that both sides read the same syntax:
 *
 * - `html` / `tag` never match: raw HTML stays literal text, as with
 *   markdown-it `html: false`.
 * - `url` never matches: bare URLs are plain text (markdown-it `linkify: false`).
 * - `del` never matches: `~~` is plain text (strikethrough is not enabled).
 * - `heading` needs a space, tab or line end after the `#` run, as in CommonMark.
 * - `inlineText` decodes every HTML5 named/numeric character reference, as
 *   CommonMark requires. TipTap only decodes `&lt; &gt; &quot; &amp;` and
 *   numeric references, so the decoded text is re-encoded with `&amp;` for
 *   TipTap's own decoder to restore.
 *
 * A dedicated instance (not the global `marked`) also keeps TipTap's
 * tokenizer registrations from leaking between editors.
 */
import { decodeHTMLStrict } from 'entities'
import { Marked, type Tokens } from 'marked'

export function createDialectMarked(): Marked {
  const instance = new Marked({ gfm: true, breaks: false, pedantic: false })

  instance.use({
    tokenizer: {
      // Returning `undefined` (not `false`) means "no match" with no fallback to the default.
      html: () => undefined,
      tag: () => undefined,
      url: () => undefined,
      del: () => undefined,
      // CommonMark ends an ATX `#` run only at a space, a tab or the line end;
      // marked accepts any Unicode whitespace, so `##` + NBSP became a heading
      // the server renders as a paragraph. `false` falls back to marked's own
      // heading tokenizer.
      heading: (src: string) => (/^ {0,3}#{1,6}(?:[ \t\n]|$)/.test(src) ? false : undefined),
      inlineText(src: string): Tokens.Text | undefined {
        const match = this.rules.inline.text.exec(src)
        if (!match) {
          return undefined
        }

        const raw = match[0]
        const escaped = this.lexer.state.inRawBlock
        const text = raw.includes('&') ? decodeHTMLStrict(raw).replace(/&/g, '&amp;') : raw

        return { type: 'text', raw, text, escaped }
      },
    },
  })

  return instance
}
