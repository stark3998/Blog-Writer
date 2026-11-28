/**
 * Post text as the composer measures it. The server's lint (`linkedin.lint`) is the gate; these
 * helpers mirror it so the preview and the counter answer as you type, and the server's numbers
 * replace them when its lint returns.
 *
 * Offsets from the server (`foldIndex`, finding `start`/`end`) are **code-point** indexes
 * (Python strings); JavaScript strings index UTF-16 code units. Convert with `utf16Offset`.
 */

/** LinkedIn's documented commentary limit, counted on the escaped payload in UTF-16 units. */
export const MAX_PAYLOAD_UNITS = 3000;

/** How the feed truncates a post before "…see more" (`linkedin.lint`). */
export interface FoldModel {
  /** At most this many characters show before the fold. */
  readonly maxChars: number;
  /** At most this many rendered lines show (blank lines count). */
  readonly maxLines: number;
  /** A rendered line wraps after about this many characters. */
  readonly lineChars: number;
}

/** The phone feed, exactly as the server's lint models it (140 characters or 3 lines, 48 per line). */
export const MOBILE_FOLD: FoldModel = { maxChars: 140, maxLines: 3, lineChars: 48 };

/**
 * The desktop feed column (about 555 px of 14 px text). LinkedIn documents no number for it; the
 * lint checks the phone fold, which is the stricter one.
 */
export const DESKTOP_FOLD: FoldModel = { maxChars: 210, maxLines: 3, lineChars: 72 };

/**
 * Where "…see more" falls, as a code-point index into `display` (a port of
 * `linkedin.lint.fold_index`: the same walk, so the phone fold equals the server's).
 */
export function foldIndex(display: string, model: FoldModel = MOBILE_FOLD): number {
  const chars = Array.from(display);
  const limit = Math.min(model.maxChars, chars.length);
  let lines = 0;
  let column = 0;
  for (let index = 0; index < limit; index += 1) {
    if (chars[index] === '\n') {
      lines += 1;
      column = 0;
    } else {
      column += 1;
      if (column > model.lineChars) {
        lines += 1;
        column = 1;
      }
    }
    if (lines >= model.maxLines) return index;
  }
  return limit;
}

/** The UTF-16 offset of code point `index` in `text` (clamped to the text). */
export function utf16Offset(text: string, index: number): number {
  if (index <= 0) return 0;
  let offset = 0;
  let count = 0;
  for (const char of text) {
    if (count >= index) break;
    offset += char.length;
    count += 1;
  }
  return offset;
}

/** The number of code points in `text` (what the server's offsets count). */
export function codePointLength(text: string): number {
  return Array.from(text).length;
}

/** The little-text reserved characters (`linkedin.littletext.RESERVED_CHARACTERS`): each goes out escaped. */
const RESERVED = new Set(['|', '{', '}', '@', '[', ']', '(', ')', '<', '>', '#', '\\', '*', '_', '~']);

/**
 * A hashtag as the serializer recognises one: `#` at the start or after a space, tab or newline;
 * the tag in letters, marks and digits; then optional closing punctuation and a space or the end.
 */
const HASHTAG = /(^|[ \t\n])#([\p{L}\p{Nd}][\p{L}\p{M}\p{Nd}]*)(?=[.,;:!?)\]}"'’”]*(?:[ \t\n]|$))/gu;

function fold(tag: string): string {
  return tag.toLocaleLowerCase('en');
}

/** The approved hashtags that appear in the text as real hashtags, in the approved spelling. */
export function hashtagsInText(display: string, approved: readonly string[]): string[] {
  const byFold = new Map(approved.map((tag) => [fold(tag), tag]));
  const found: string[] = [];
  for (const match of display.matchAll(HASHTAG)) {
    const tag = byFold.get(fold(match[2] ?? ''));
    if (tag !== undefined && !found.includes(tag)) found.push(tag);
  }
  return found;
}

/** A run of the text by UTF-16 offsets (`end` exclusive). */
export interface TextSpan {
  readonly start: number;
  readonly end: number;
}

/** Where the approved hashtags sit in the text (the `#` included): they go out as real hashtags. */
export function hashtagSpans(display: string, approved: readonly string[]): TextSpan[] {
  const folds = new Set(approved.map(fold));
  const spans: TextSpan[] = [];
  for (const match of display.matchAll(HASHTAG)) {
    const tag = match[2] ?? '';
    if (!folds.has(fold(tag))) continue;
    const start = match.index + (match[1]?.length ?? 0);
    spans.push({ start, end: start + 1 + tag.length });
  }
  return spans;
}

/**
 * The escaped payload's length in UTF-16 units, estimated from the display text: every reserved
 * character gains a backslash except the `#` of an approved hashtag. The server's lint returns
 * the exact number (`payloadUnits`); this one answers between keystrokes.
 */
export function estimatePayloadUnits(display: string, approved: readonly string[]): number {
  let units = display.length;
  for (const char of display) if (RESERVED.has(char)) units += 1;
  const approvedFolds = new Set(approved.map(fold));
  for (const match of display.matchAll(HASHTAG)) {
    if (approvedFolds.has(fold(match[2] ?? ''))) units -= 1;
  }
  return units;
}

/** The first paragraph (up to the first blank line): where the hook lives. */
export function firstParagraph(display: string): string {
  const index = display.indexOf('\n\n');
  return (index === -1 ? display : display.slice(0, index)).trim();
}

/**
 * `display` with its hook replaced (`linkedin.posts.derive.replace_hook`): the first paragraph
 * becomes `hook`. Without a blank line the first line is replaced, so the body is never lost.
 */
export function replaceHook(display: string, hook: string): string {
  const head = hook.trim();
  const paragraph = display.indexOf('\n\n');
  if (paragraph !== -1) return head + display.slice(paragraph);
  const line = display.indexOf('\n');
  if (line !== -1) return head + display.slice(line);
  return head;
}

/** True when a line holds nothing but hashtags. */
function isHashtagLine(line: string): boolean {
  const words = line.trim().split(/\s+/).filter(Boolean);
  return words.length > 0 && words.every((word) => /^#[\p{L}\p{Nd}][\p{L}\p{M}\p{Nd}]*$/u.test(word));
}

/** `display` with `#tag` added: to the closing hashtag line, or as a new last paragraph. */
export function addHashtag(display: string, tag: string): string {
  const trimmed = display.replace(/\s+$/u, '');
  const token = `#${tag}`;
  if (trimmed === '') return token;
  const lastBreak = trimmed.lastIndexOf('\n');
  const lastLine = trimmed.slice(lastBreak + 1);
  if (isHashtagLine(lastLine)) return `${trimmed} ${token}`;
  return `${trimmed}\n\n${token}`;
}

/** `display` without the hashtag `#tag` (any case); an emptied hashtag line goes too. */
export function removeHashtag(display: string, tag: string): string {
  const escaped = tag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`(^|[ \\t])#${escaped}(?=$|[ \\t.,;:!?])`, 'giu');
  const lines = display.split('\n').map((line) => {
    const replaced = line.replace(pattern, '$1');
    if (replaced === line) return line;
    // Only lines that lost a tag are tidied: double spaces, a space before punctuation, the ends.
    return replaced.replace(/[ \t]{2,}/g, ' ').replace(/ ([,.;:!?])/g, '$1').trim();
  });
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').replace(/\s+$/u, '');
}

/** Words as the carousel rules count them (runs of non-space). */
export function wordCount(text: string | null | undefined): number {
  if (!text) return 0;
  return text.trim().split(/\s+/u).filter(Boolean).length;
}

/** "2,431" in the owner's locale. */
export function formatCount(value: number): string {
  return new Intl.NumberFormat(undefined).format(value);
}
