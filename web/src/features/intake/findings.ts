import type { Finding } from './types';

/**
 * Local findings as the intake screens show them: labels per code, spans converted to the
 * browser's string offsets, the text split into marked and unmarked runs, and the placeholder
 * masking the server applies before the anonymizer (used when no proposal comes back).
 *
 * Pure functions, no DOM. The texts they take live in component state only (contract 1.9 rule 9).
 */

/** The codes of local findings (`blogwriter.stories.intake`, `blogwriter.domain.confidentiality`). */
export const FINDING_CODES = {
  blocklisted: 'blocklisted-term',
  blocklistNotConfigured: 'blocklist-not-configured',
  email: 'email-address',
  phone: 'phone-number',
  url: 'url',
  ip: 'ip-address',
  handle: 'handle',
  domain: 'domain-name',
  money: 'money',
  figure: 'exact-figure',
  name: 'possible-name',
} as const;

const LABELS: Readonly<Record<string, string>> = {
  [FINDING_CODES.blocklisted]: 'Blocklisted term',
  [FINDING_CODES.blocklistNotConfigured]: 'Blocklist not set up',
  [FINDING_CODES.email]: 'E-mail address',
  [FINDING_CODES.phone]: 'Phone number',
  [FINDING_CODES.url]: 'Link',
  [FINDING_CODES.ip]: 'IP address',
  [FINDING_CODES.handle]: 'Handle',
  [FINDING_CODES.domain]: 'Web address',
  [FINDING_CODES.money]: 'Money amount',
  [FINDING_CODES.figure]: 'Exact figure',
  [FINDING_CODES.name]: 'Possible name',
};

/** What a finding is about, in two or three words ("E-mail address", "Exact figure"). */
export function findingLabel(code: string): string {
  return LABELS[code] ?? 'Detail to check';
}

/**
 * The server's placeholders (`stories.intake.mask_text`). A blocklisted term's category is not
 * sent to the browser, so its placeholder is the generic one here.
 */
const PLACEHOLDERS: Readonly<Record<string, string>> = {
  [FINDING_CODES.blocklisted]: '[redacted]',
  [FINDING_CODES.email]: '[email address]',
  [FINDING_CODES.phone]: '[phone number]',
  [FINDING_CODES.url]: '[link]',
  [FINDING_CODES.ip]: '[IP address]',
  [FINDING_CODES.handle]: '[handle]',
};

/** `block` findings must go before anything is saved; `warn` and `info` are for the owner to judge. */
export function isBlocking(finding: Pick<Finding, 'severity'>): boolean {
  return finding.severity === 'block';
}

/** A span in the browser's string offsets (UTF-16 code units), `end` exclusive. */
export interface TextSpan {
  readonly start: number;
  readonly end: number;
}

/**
 * The server indexes text by code point (Python strings); JavaScript strings index UTF-16 code
 * units, so an emoji or another character outside the BMP shifts every later offset by one.
 * Returns the UTF-16 offset of each code point (plus the end), or `null` when the two agree
 * (no surrogates: the common case).
 */
export function codePointOffsets(text: string): readonly number[] | null {
  if (!/[\uD800-\uDFFF]/.test(text)) return null;
  const offsets: number[] = [];
  let index = 0;
  while (index < text.length) {
    offsets.push(index);
    const code = text.codePointAt(index) ?? 0;
    index += code > 0xffff ? 2 : 1;
  }
  offsets.push(text.length);
  return offsets;
}

function isOffset(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

/**
 * A finding's span in `text`, in UTF-16 offsets; `undefined` when the finding is about the whole
 * text (no span) or its span does not fit `text` (it indexes another version of it).
 */
export function findingSpan(
  text: string,
  finding: Pick<Finding, 'start' | 'end'>,
  offsets: readonly number[] | null = codePointOffsets(text),
): TextSpan | undefined {
  const { start, end } = finding;
  if (!isOffset(start) || !isOffset(end) || end <= start) return undefined;
  if (offsets === null) return end <= text.length ? { start, end } : undefined;
  const from = offsets[start];
  const to = offsets[end];
  return from === undefined || to === undefined ? undefined : { start: from, end: to };
}

/** A finding with its number (reading order, 1-based) and its span in `text`. */
export interface MarkedFinding {
  readonly finding: Finding;
  readonly number: number;
  /** `undefined` for a finding about the whole text. */
  readonly span: TextSpan | undefined;
}

/**
 * The findings of `text` in reading order, numbered for the marks and the list beside them:
 * findings about the whole text first, then by position, blocking before the rest on a tie.
 */
export function markFindings(text: string, findings: readonly Finding[]): MarkedFinding[] {
  const offsets = codePointOffsets(text);
  const spanned = findings.map((finding) => ({ finding, span: findingSpan(text, finding, offsets) }));
  spanned.sort((a, b) => {
    const at = a.span?.start ?? -1;
    const bt = b.span?.start ?? -1;
    if (at !== bt) return at - bt;
    return Number(isBlocking(b.finding)) - Number(isBlocking(a.finding));
  });
  return spanned.map((entry, index) => ({ ...entry, number: index + 1 }));
}

/** A run of `text`: plain, or the text a finding points at. */
export interface TextSegment {
  readonly text: string;
  readonly mark?: MarkedFinding;
}

/**
 * Splits `text` into plain runs and marked runs, in order. Overlapping spans (two blocklist terms
 * inside each other) are clipped so every character is shown once.
 */
export function segmentText(text: string, marks: readonly MarkedFinding[]): TextSegment[] {
  const placed = marks
    .filter((mark): mark is MarkedFinding & { span: TextSpan } => mark.span !== undefined)
    .sort((a, b) => a.span.start - b.span.start);
  const segments: TextSegment[] = [];
  let position = 0;
  for (const mark of placed) {
    const start = Math.max(mark.span.start, position);
    const end = Math.min(mark.span.end, text.length);
    if (end <= start) continue;
    if (start > position) segments.push({ text: text.slice(position, start) });
    segments.push({ text: text.slice(start, end), mark });
    position = end;
  }
  if (position < text.length) segments.push({ text: text.slice(position) });
  return segments;
}

/** The text a finding points at, for a list entry ("“jane@example.com”"); cut to `max` characters. */
export function findingExcerpt(text: string, span: TextSpan | undefined, max = 60): string | undefined {
  if (!span) return undefined;
  const excerpt = text.slice(span.start, span.end).replace(/\s+/g, ' ').trim();
  if (excerpt === '') return undefined;
  return excerpt.length > max ? `${excerpt.slice(0, max - 1)}…` : excerpt;
}

/**
 * `text` with every blocking span replaced by a bracketed placeholder (`[email address]`,
 * `[redacted]`), as the server masks the text before the anonymizer sees it. Overlapping spans
 * merge. Used to start the owner's wording when the anonymizer proposed nothing.
 */
export function maskBlocking(text: string, findings: readonly Finding[]): string {
  const offsets = codePointOffsets(text);
  const spans = findings
    .filter(isBlocking)
    .flatMap((finding) => {
      const span = findingSpan(text, finding, offsets);
      if (!span) return [];
      const label = PLACEHOLDERS[finding.code] ?? '[redacted]';
      return [{ ...span, label, term: finding.code === FINDING_CODES.blocklisted }];
    })
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const merged: { start: number; end: number; label: string; term: boolean }[] = [];
  for (const span of spans) {
    const previous = merged.at(-1);
    if (previous && span.start < previous.end) {
      previous.end = Math.max(previous.end, span.end);
      if (span.term && !previous.term) {
        previous.label = span.label;
        previous.term = true;
      }
      continue;
    }
    merged.push({ ...span });
  }
  let result = '';
  let position = 0;
  for (const span of merged) {
    result += text.slice(position, span.start) + span.label;
    position = span.end;
  }
  return result + text.slice(position);
}

/**
 * How many details redaction flagged in the raw text: what an interview answer reports as
 * `redactionFindings` (a count, never the spans; the server caps it at 1000).
 */
export function redactionCount(findings: readonly Finding[]): number {
  return Math.min(1000, findings.filter((finding) => isOffset(finding.start) && isOffset(finding.end)).length);
}

export interface FindingCounts {
  readonly blocking: number;
  readonly other: number;
}

export function countFindings(findings: readonly Finding[]): FindingCounts {
  const blocking = findings.filter(isBlocking).length;
  return { blocking, other: findings.length - blocking };
}

function details(count: number): string {
  return count === 1 ? '1 detail' : `${String(count)} details`;
}

/** "2 details to fix and 1 to check.", "Nothing to fix.", for a summary line. */
export function describeFindings(findings: readonly Finding[]): string {
  const { blocking, other } = countFindings(findings);
  if (blocking === 0 && other === 0) return 'Nothing to fix.';
  if (blocking === 0) return `${details(other)} to check.`;
  if (other === 0) return `${details(blocking)} to fix.`;
  return `${details(blocking)} to fix and ${String(other)} to check.`;
}
