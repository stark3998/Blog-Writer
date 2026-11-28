// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api/errors';

import { brandColors, hex } from '../testing/fixtures';
import { checkPairs, contrastRatio, formatRatio, normalizeHex } from './contrast';
import { itemErrors, normalizePath, serverFieldErrors } from './errors';
import { formatShare, fromDateInput, listOf, plural, toDateInput } from './format';
import { canMove, reviewDecisions, reviewTarget } from './machines';
import { normalizeHashtag, normalizeHost, parsePercent, percentText, slugify, uniqueSlug, wholeError } from './rules';
import { charCount, markSegments, spanText } from './spans';

function problem(code: string, extensions: Record<string, unknown>): ApiError {
  return new ApiError({ kind: 'client', status: 422, problem: { code, extensions } });
}

describe('contrast', () => {
  it('normalizes what the owner types into the stored form', () => {
    expect(normalizeHex('0e2a24')).toBe(hex('0E2A24'));
    expect(normalizeHex(` ${hex('0e2a24')} `)).toBe(hex('0E2A24'));
    expect(normalizeHex(hex('abc'))).toBe(hex('AABBCC'));
    expect(normalizeHex('12345')).toBeUndefined();
    expect(normalizeHex('blue')).toBeUndefined();
  });

  it('measures WCAG contrast like the renderer', () => {
    expect(contrastRatio(hex('000000'), hex('FFFFFF'))).toBeCloseTo(21, 5);
    expect(contrastRatio(hex('FFFFFF'), hex('FFFFFF'))).toBe(1);
    // Rounded down, so a value shown as 4.50 always passes.
    expect(formatRatio(4.4999)).toBe('4.49:1');
  });

  it('passes the renderer’s placeholder palette and fails a pale paper under muted text', () => {
    const colors = brandColors();
    expect(checkPairs(colors).every((pair) => pair.passes === true)).toBe(true);
    const pale = checkPairs({ ...colors, muted: hex('9AA8A2') });
    expect(pale.filter((pair) => pair.passes === false).map((pair) => pair.id)).toEqual(['muted:paper']);
    const incomplete = checkPairs({ ...colors, ink: '#12' });
    expect(incomplete.find((pair) => pair.id === 'ink:paper')?.passes).toBeUndefined();
  });
});

describe('spans', () => {
  it('marks code-point spans, so an emoji before a match does not shift it', () => {
    const text = '🚀 Let us delve in';
    const start = Array.from(text).indexOf('d');
    const segments = markSegments(text, [{ start, end: start + 5 }]);
    expect(segments.map((segment) => [segment.text, segment.spans.length > 0])).toEqual([
      ['🚀 Let us ', false],
      ['delve', true],
      [' in', false],
    ]);
    expect(spanText(text, { start, end: start + 5 })).toBe('delve');
    expect(charCount('🚀ab')).toBe(3);
  });

  it('ignores spans without offsets and clips spans past the text', () => {
    expect(markSegments('abc', [{ start: null, end: null }])).toEqual([{ text: 'abc', spans: [] }]);
    expect(markSegments('abc', [{ start: 1, end: 99 }]).map((segment) => segment.text)).toEqual(['a', 'bc']);
  });
});

describe('rules', () => {
  it('reads hashtags with or without #', () => {
    expect(normalizeHashtag('  #ZeroTrust ')).toBe('ZeroTrust');
    expect(normalizeHashtag('##AI')).toBe('AI');
  });

  it('keeps only the site of a pasted address', () => {
    expect(normalizeHost('https://Learn.Microsoft.com/en-us/entra/?view=1')).toBe('learn.microsoft.com');
    expect(normalizeHost('nist.gov/itl/ai-risk-management-framework')).toBe('nist.gov');
    expect(normalizeHost('owasp.org:443')).toBe('owasp.org');
  });

  it('makes slugs and keeps them unique', () => {
    expect(slugify('AI agents & security')).toBe('ai-agents-security');
    expect(slugify('Économie numérique')).toBe('economie-numerique');
    expect(uniqueSlug('framework', ['framework', 'framework-2'])).toBe('framework-3');
  });

  it('reads percentages and whole numbers as typed', () => {
    expect(parsePercent('33')).toBeCloseTo(0.33);
    expect(parsePercent('33.5%')).toBeCloseTo(0.335);
    expect(parsePercent('abc')).toBeUndefined();
    expect(percentText(0.333)).toBe('33.3');
    expect(wholeError('', 1, 5)).toBe('Enter a whole number.');
    expect(wholeError('9', 1, 5)).toBe('Between 1 and 5.');
    expect(wholeError('3', 1, 5)).toBeUndefined();
  });
});

describe('server errors', () => {
  it('maps validation messages and blocklist findings to request paths', () => {
    const validation = problem('validation_failed', {
      errors: [{ loc: ['body', 'pillars', 0, 'hashtags', 1], msg: 'String should match pattern' }],
    });
    expect(serverFieldErrors(validation)).toEqual({ 'pillars.0.hashtags.1': 'String should match pattern' });
    const confidential = problem('confidential_text', {
      findings: [{ field: 'metrics[0].label', code: 'blocklist', severity: 'block', message: 'Matches a blocklisted name.' }],
    });
    expect(serverFieldErrors(confidential)).toEqual({ 'metrics.0.label': 'Matches a blocklisted name.' });
    expect(normalizePath('a[1].b[22]')).toBe('a.1.b.22');
    expect(itemErrors({ 'hashtags.2': 'Bad', 'hashtags.0.x': 'Worse', name: 'n' }, 'hashtags')).toEqual({ 2: 'Bad', 0: 'Worse' });
  });
});

describe('machines', () => {
  it('offers the review decisions the machine allows', () => {
    expect(reviewDecisions(undefined, 'proposed')).toEqual(['approve', 'reject']);
    expect(reviewDecisions(undefined, 'approved')).toEqual(['archive']);
    expect(reviewDecisions(undefined, 'rejected')).toEqual(['restore']);
    expect(reviewDecisions(undefined, 'archived')).toEqual(['restore']);
    expect(reviewTarget('archived', 'restore')).toBe('approved');
    expect(reviewTarget('rejected', 'restore')).toBe('proposed');
  });

  it('follows the server’s table over the fallback', () => {
    const table = { review: { proposed: ['approved'] } };
    expect(reviewDecisions(table, 'proposed')).toEqual(['approve']);
    expect(canMove(table, 'voice_sample', 'active', 'excluded')).toBe(true);
    expect(canMove(table, 'voice_sample', 'active', 'deleted')).toBe(false);
  });
});

describe('format', () => {
  it('writes shares, counts, lists and date inputs', () => {
    expect(formatShare(0.33)).toBe('33%');
    expect(plural(1, 'story', 'stories')).toBe('1 story');
    expect(plural(3, 'story', 'stories')).toBe('3 stories');
    expect(listOf(['ideas', 'posts', 'feeds'])).toBe('ideas, posts and feeds');
    expect(toDateInput('2026-11-03T00:00:00.000000Z')).toBe('2026-11-03');
    expect(fromDateInput('2026-11-03')).toBe('2026-11-03T00:00:00.000000Z');
    expect(fromDateInput('')).toBeNull();
  });
});
