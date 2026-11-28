/**
 * Shared constants of the Blog Writer v2 article dialect (`docs/v2/article-dialect.md`).
 *
 * The server's renderer and linter hold the same values; the shared corpus in
 * `contracts/fixtures/corpus` is how the two sides are kept in step.
 */

/** Callout container types (`:::note`, `:::tip`, `:::warning`). */
export const CALLOUT_TYPES = ['note', 'tip', 'warning'] as const;
export type CalloutType = (typeof CALLOUT_TYPES)[number];

export function isCalloutType(value: string): value is CalloutType {
  return (CALLOUT_TYPES as readonly string[]).includes(value);
}

/** Citation ids are brief source ids: lowercase kebab-case, at most 64 characters. */
export const CITATION_ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const CITATION_ID_MAX_LENGTH = 64;

/** Slug-like frontmatter values (slug, pillar, format, tags, series.id). */
export const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** The info string that turns a fenced code block into a Mermaid diagram. */
export const MERMAID_LANGUAGE = 'mermaid';

/** Heading levels allowed in an article body (the title is the page's h1). */
export const BODY_HEADING_LEVELS = [2, 3, 4] as const;

/**
 * JavaScript whitespace characters (what `\s` and `String#trim` match). Inside
 * an inline code span, leading and trailing ones are swapped for private-use
 * stand-ins while TipTap's mark serializer runs (it would otherwise move them
 * outside the backticks) and swapped back afterwards.
 */
export const JS_WHITESPACE = [
  '\t',
  '\n',
  '\u000B',
  '\f',
  '\r',
  ' ',
  '\u00A0',
  '\u1680',
  '\u2000',
  '\u2001',
  '\u2002',
  '\u2003',
  '\u2004',
  '\u2005',
  '\u2006',
  '\u2007',
  '\u2008',
  '\u2009',
  '\u200A',
  '\u2028',
  '\u2029',
  '\u202F',
  '\u205F',
  '\u3000',
  '\uFEFF',
] as const;

/** The private-use stand-in for `JS_WHITESPACE[i]` is U+E020 + i. */
export const WHITESPACE_PLACEHOLDER_BASE = 0xe020;

/**
 * Private-use character standing in for a `|` inside inline code in a table
 * cell while the cell is rendered; replaced by `\|` afterwards (GFM requires
 * pipes inside code spans in tables to be escaped too).
 */
export const CODE_PIPE_PLACEHOLDER = '\uE011';
