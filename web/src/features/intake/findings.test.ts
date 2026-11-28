// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  codePointOffsets,
  describeFindings,
  findingExcerpt,
  findingLabel,
  findingSpan,
  markFindings,
  maskBlocking,
  redactionCount,
  segmentText,
} from './findings';
import { findingAt, MESSAGES, RAW_ANSWER, RAW_FINDINGS } from './testing/fixtures';
import type { Finding } from './types';

function finding(start: number | null, end: number | null, overrides: Partial<Finding> = {}): Finding {
  return { code: 'email-address', severity: 'block', message: MESSAGES.email, start, end, overridable: false, ...overrides };
}

describe('code-point spans', () => {
  it('maps the server’s code-point offsets onto JavaScript’s UTF-16 offsets', () => {
    // The emoji is one code point (Python) but two UTF-16 units (JavaScript).
    const text = 'Ship it 🚀 then mail jane@example.com today';
    const start = text.indexOf('jane');
    const codePointStart = Array.from(text.slice(0, start)).length;
    const span = findingSpan(text, finding(codePointStart, codePointStart + 'jane@example.com'.length));
    expect(span).toEqual({ start, end: start + 'jane@example.com'.length });
    expect(text.slice(span?.start, span?.end)).toBe('jane@example.com');
  });

  it('needs no table for text without surrogates, and rejects spans that do not fit', () => {
    expect(codePointOffsets('plain text')).toBeNull();
    expect(findingSpan('short', finding(2, 40))).toBeUndefined();
    expect(findingSpan('short', finding(3, 3))).toBeUndefined();
    expect(findingSpan('short', finding(null, null))).toBeUndefined();
    expect(findingSpan('a🚀b', finding(1, 2))).toEqual({ start: 1, end: 3 });
    expect(findingSpan('a🚀b', finding(1, 9))).toBeUndefined();
  });
});

describe('marks and segments', () => {
  it('numbers findings in reading order, whole-text findings first and blocking first on a tie', () => {
    const whole = finding(null, null, { code: 'blocklist-not-configured' });
    const warn = finding(0, 4, { code: 'possible-name', severity: 'warn' });
    const block = finding(0, 4);
    const marks = markFindings('Jane wrote', [warn, block, whole]);
    expect(marks.map((mark) => [mark.number, mark.finding.code])).toEqual([
      [1, 'blocklist-not-configured'],
      [2, 'email-address'],
      [3, 'possible-name'],
    ]);
  });

  it('splits the text into plain and marked runs, every character once', () => {
    const marks = markFindings(RAW_ANSWER, RAW_FINDINGS);
    const segments = segmentText(RAW_ANSWER, marks);
    expect(segments.map((segment) => segment.text).join('')).toBe(RAW_ANSWER);
    expect(segments.filter((segment) => segment.mark).map((segment) => segment.text)).toEqual([
      '2,137',
      'Northwind Bank',
      'jane@northwind.example',
    ]);
    // Overlapping spans are clipped, not repeated.
    const overlapping = markFindings('Contoso Bank', [finding(0, 7), finding(0, 12)]);
    expect(segmentText('Contoso Bank', overlapping).map((segment) => segment.text)).toEqual(['Contoso', ' Bank']);
  });

  it('quotes the words a finding points at, cut to a length', () => {
    const marks = markFindings(RAW_ANSWER, RAW_FINDINGS);
    expect(findingExcerpt(RAW_ANSWER, marks[2]?.span)).toBe('jane@northwind.example');
    expect(findingExcerpt('a'.repeat(100), { start: 0, end: 100 }, 10)).toBe(`${'a'.repeat(9)}…`);
    expect(findingExcerpt(RAW_ANSWER, undefined)).toBeUndefined();
  });
});

describe('maskBlocking', () => {
  it('replaces blocking details with the server’s placeholders and leaves the rest', () => {
    expect(maskBlocking(RAW_ANSWER, RAW_FINDINGS)).toBe(
      'We moved 2,137 apps for Northwind Bank. Ask [email address] for the runbook.',
    );
  });

  it('merges overlapping spans and lets a blocklisted term name the placeholder', () => {
    const text = 'Mail ops@contoso.example now';
    const email = findingAt(text, 'ops@contoso.example', 'email-address', 'block', MESSAGES.email);
    const term = findingAt(text, 'contoso', 'blocklisted-term', 'block', MESSAGES.blocklisted);
    expect(maskBlocking(text, [email, term])).toBe('Mail [redacted] now');
    expect(maskBlocking('a🚀 b@c.example', [finding(3, 14)])).toBe('a🚀 [email address]');
  });
});

describe('counts and words', () => {
  it('counts flagged spans for redactionFindings and describes the findings', () => {
    expect(redactionCount(RAW_FINDINGS)).toBe(3);
    expect(redactionCount([finding(null, null)])).toBe(0);
    expect(describeFindings([])).toBe('Nothing to fix.');
    expect(describeFindings(RAW_FINDINGS)).toBe('1 detail to fix and 2 to check.');
    expect(describeFindings(RAW_FINDINGS.slice(0, 1))).toBe('1 detail to check.');
    expect(describeFindings(RAW_FINDINGS.slice(2))).toBe('1 detail to fix.');
    expect(findingLabel('exact-figure')).toBe('Exact figure');
    expect(findingLabel('something-new')).toBe('Detail to check');
  });
});
