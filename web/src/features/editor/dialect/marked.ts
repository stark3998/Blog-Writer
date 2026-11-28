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
 * - `link` refuses a link whose text holds another link: CommonMark lets the
 *   inner link win and keeps the outer brackets as text, marked kept both
 *   (and the editor's schema cannot hold a link inside a link, so such a
 *   source failed to open).
 * - `inlineText` decodes every HTML5 named and numeric character reference,
 *   as CommonMark requires. TipTap only decodes `&lt; &gt; &quot; &amp;` and
 *   numeric references, so the decoded text is re-encoded with `&amp;` for
 *   TipTap's own decoder to restore.
 *
 * A dedicated instance (not the global `marked`) also keeps TipTap's
 * tokenizer registrations from leaking between editors.
 */
import { decodeHTMLStrict } from 'entities';
import { Marked, type Token, Tokenizer, type Tokens } from 'marked';

/** CommonMark ends an ATX `#` run only at a space, a tab or the end of the line. */
const ATX_HEADING_START = /^ {0,3}#{1,6}(?:[ \t\n]|$)/;

/** True when `tokens`, at any depth, hold a link. */
function containsLink(tokens: readonly Token[] | undefined): boolean {
  return (tokens ?? []).some((token) => token.type === 'link' || ('tokens' in token && containsLink(token.tokens)));
}

export function createDialectMarked(): Marked {
  const instance = new Marked({ gfm: true, breaks: false, pedantic: false });

  instance.use({
    tokenizer: {
      // Returning `undefined` (not `false`) means "no match", with no fallback to the default.
      html: () => undefined,
      tag: () => undefined,
      url: () => undefined,
      del: () => undefined,
      // marked accepts any Unicode whitespace after the `#` run, so `##` +
      // NBSP became a heading the server renders as a paragraph. `false` falls
      // back to marked's own heading tokenizer.
      heading: (src: string) => (ATX_HEADING_START.test(src) ? false : undefined),
      link(src: string): Tokens.Link | Tokens.Image | undefined {
        const token = Tokenizer.prototype.link.call(this, src);
        // No match here; the outer `[` then becomes text and the inner link is found on its own.
        return token?.type === 'link' && containsLink(token.tokens) ? undefined : token;
      },
      inlineText(src: string): Tokens.Text | undefined {
        const match = this.rules.inline.text.exec(src);
        if (!match) {
          return undefined;
        }

        const raw = match[0];
        const escaped = this.lexer.state.inRawBlock;
        const text = raw.includes('&') ? decodeHTMLStrict(raw).replace(/&/g, '&amp;') : raw;

        return { type: 'text', raw, text, escaped };
      },
    },
  });

  return instance;
}
