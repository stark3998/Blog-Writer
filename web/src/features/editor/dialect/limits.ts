/**
 * Input limits for the editor. Stock `marked` has no nesting limit and a few
 * super-linear paths, so hostile input can freeze a tab or overflow the stack
 * (`docs/v2/spikes/editor-dialect.md`, open issue 2). The editor therefore:
 *
 * - opens at most `EDITOR_MAX_BYTES` of UTF-8 (the server accepts 512 KB);
 * - refuses sources that nest lists, quotes or callouts deeper than
 *   `MAX_NESTING_DEPTH`, estimated line by line before anything is parsed;
 * - normalizes in a Web Worker with a timeout (`normalize-client.ts`).
 */
import { CALLOUT_TYPES } from './constants';
import { DialectError } from './errors';

/** The largest article source the editor opens: 128 KB of UTF-8. */
export const EDITOR_MAX_BYTES = 128 * 1024;

/**
 * The deepest nesting the editor parses. Real articles stay under 6; the
 * server's markdown-it (commonmark preset, maxNesting 20) stops rendering at
 * about 10 list levels anyway. Stock `marked` overflows the stack at about
 * 300 list levels.
 */
export const MAX_NESTING_DEPTH = 32;

/** UTF-8 length of `text` in bytes. */
export function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

/** Throws `too-large` when `markdown` is over `EDITOR_MAX_BYTES` of UTF-8. */
export function assertEditableSize(markdown: string): void {
  // Every UTF-16 code unit takes at least one UTF-8 byte, so a longer string never fits.
  if (markdown.length > EDITOR_MAX_BYTES || utf8ByteLength(markdown) > EDITOR_MAX_BYTES) {
    throw new DialectError(
      'too-large',
      `The article is larger than ${EDITOR_MAX_BYTES / 1024} KB, the most the editor opens.`,
    );
  }
}

/** Throws `too-large` or `too-deep` for a source the editor must not parse. */
export function assertEditableSource(markdown: string): void {
  assertEditableSize(markdown);
  if (estimateNestingDepth(markdown) > MAX_NESTING_DEPTH) {
    throw new DialectError(
      'too-deep',
      `The article nests lists, quotes or callouts more than ${MAX_NESTING_DEPTH} levels deep, more than the editor opens.`,
    );
  }
}

/** A container marker: a quote `>`, a bullet or an ordered-list marker (sticky: matched at `lastIndex`). */
const CONTAINER_MARKER = />|[-+*](?=[ \t]|$)|[0-9]{1,9}[.)](?=[ \t]|$)/y;
const THEMATIC_BREAK = /^([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const FENCE_OPENING = /^(`{3,}|~{3,})/;
const FENCE_ONLY = /^(?:`+|~+)$/;
const CALLOUT_OPENING = new RegExp(`^ {0,3}(:{3,})[ \\t]*(?:${CALLOUT_TYPES.join('|')})[ \\t]*$`);
const CALLOUT_CLOSING = /^ {0,3}(:{3,}) *$/;

interface LinePrefix {
  /** Container markers at the start of the line, plus its indentation in pairs of columns. */
  depth: number;
  /** The rest of the line after the markers. */
  rest: string;
}

function isBlank(ch: string | undefined): boolean {
  return ch === ' ' || ch === '\t';
}

function linePrefix(line: string, limit: number): LinePrefix {
  // Indentation anywhere in the prefix nests lists: before the first marker
  // and after a marker beyond the one space that belongs to it
  // (`>   - b` is a list nested in a list inside a quote).
  let index = 0;
  let columns = 0;
  const skipBlanks = (afterMarker: boolean) => {
    let first = afterMarker;
    while (isBlank(line[index])) {
      columns += (line[index] === '\t' ? 4 : 1) - (first ? 1 : 0);
      first = false;
      index += 1;
    }
  };

  skipBlanks(false);

  // `- - -` and `* * *` are thematic breaks, not nested lists.
  const first = line[index];
  if ((first === '-' || first === '*' || first === '_') && THEMATIC_BREAK.test(line.slice(index))) {
    return { depth: 0, rest: '' };
  }

  let markers = 0;
  // Stop counting past the limit: the caller only needs to know the line is too deep.
  while (markers <= limit) {
    CONTAINER_MARKER.lastIndex = index;
    if (!CONTAINER_MARKER.test(line)) {
      break;
    }
    markers += 1;
    index = CONTAINER_MARKER.lastIndex;
    skipBlanks(true);
  }

  return { depth: markers === 0 ? 0 : markers + Math.floor(columns / 2), rest: line.slice(index) };
}

/**
 * A cheap upper estimate of how deeply `markdown` nests blocks, without
 * parsing it: for each line, the quote and list markers at its start plus its
 * indentation (two columns per level), plus the callouts still open (closed
 * the way the callout tokenizer closes them). Lines inside fenced code are not
 * counted. Legitimate articles score far below `MAX_NESTING_DEPTH`. Returns as
 * soon as the estimate exceeds `limit`.
 */
export function estimateNestingDepth(markdown: string, limit = MAX_NESTING_DEPTH): number {
  const openCallouts: number[] = [];
  let fence: { marker: string; calloutDepth: number } | null = null;
  let deepest = 0;

  for (const line of markdown.split('\n')) {
    const { depth, rest } = linePrefix(line, limit);

    // A callout's closing line is found before its content is parsed, so it
    // closes the callout even inside a code fence (and ends that fence).
    const closing = openCallouts.length > 0 ? CALLOUT_CLOSING.exec(rest) : null;
    const colons = closing?.[1]?.length ?? 0;
    const outermost = closing ? openCallouts.findIndex((length) => length <= colons) : -1;
    if (outermost !== -1) {
      openCallouts.length = outermost;
      if (fence && fence.calloutDepth > outermost) {
        fence = null;
      }
      continue;
    }

    if (fence) {
      const trimmed = rest.trimEnd();
      if (trimmed.startsWith(fence.marker) && FENCE_ONLY.test(trimmed)) {
        fence = null;
      }
      continue;
    }

    const fenceOpening = FENCE_OPENING.exec(rest)?.[1];
    if (fenceOpening) {
      fence = { marker: fenceOpening, calloutDepth: openCallouts.length };
    } else {
      const calloutFence = CALLOUT_OPENING.exec(rest)?.[1];
      if (calloutFence) {
        openCallouts.push(calloutFence.length);
      }
    }

    deepest = Math.max(deepest, depth + openCallouts.length);
    if (deepest > limit) {
      return deepest;
    }
  }

  return deepest;
}
