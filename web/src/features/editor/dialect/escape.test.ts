// @vitest-environment node
/** Escaping unit cases (ported from the spike). The property round trips are in properties.test.ts. */
import { describe, expect, test } from 'vitest';

import {
  bracketsAsReferences,
  codeFenceFor,
  escapeHeadingEnd,
  escapeInlineText,
  escapeLineStart,
  fenceInfo,
  formatLinkDestination,
  formatLinkTitle,
  shortestUnusedBacktickRun,
} from './escape';

describe('escapeInlineText', () => {
  const plain = { lineStart: false, headingEnd: false };

  test.each([
    ['a*b', 'a\\*b'],
    ['snake_case', 'snake_case'],
    ['_lead and trail_', '\\_lead and trail\\_'],
    ['[sic]', '\\[sic\\]'],
    ['a\\b', 'a\\\\b'],
    ['`x`', '\\`x\\`'],
    ['a < b > c', 'a < b > c'],
    ['<T> and </p> and <!x>', '\\<T> and \\</p> and \\<!x>'],
    ['AT&T', 'AT&T'],
    ['&copy; &#169; &#xA9;', '\\&copy; \\&#169; \\&#xA9;'],
    ['# mid-line', '# mid-line'],
    ['日本_語', '日本_語'],
    ['a\nb', 'a b'],
    ['a \r\n\n b', 'a b'],
    ['tab\there', 'tab here'],
  ])('%j -> %j', (input, expected) => {
    expect(escapeInlineText(input, plain)).toBe(expected);
  });

  test.each([
    ['# heading', '\\# heading'],
    ['#hashtag', '#hashtag'],
    ['> quote', '\\> quote'],
    ['- item', '\\- item'],
    ['--flag', '\\--flag'],
    ['+ item', '\\+ item'],
    ['+1 for this', '+1 for this'],
    ['1. item', '1\\. item'],
    ['2024) year', '2024\\) year'],
    ['3.14 is pi', '3.14 is pi'],
    ['=== underline', '\\=== underline'],
    [':::note', '\\:::note'],
    [':-- delimiter', '\\:-- delimiter'],
    ['| cell', '\\| cell'],
    ['~~~ fence', '\\~~~ fence'],
    ['   leading spaces', 'leading spaces'],
  ])('line start %j -> %j', (input, expected) => {
    expect(escapeInlineText(input, { lineStart: true, headingEnd: false })).toBe(expected);
  });

  test('block edges drop the Unicode whitespace parsers trim', () => {
    expect(escapeInlineText('\u00A0 text \u3000', { lineStart: true, headingEnd: false, blockStart: true, blockEnd: true })).toBe(
      'text',
    );
  });

  test('line-start rules apply to already-escaped text only at position 0', () => {
    expect(escapeLineStart('a # b')).toBe('a # b');
  });

  test.each([
    ['Item #', 'Item \\#'],
    ['Ends ##', 'Ends \\##'],
    ['#', '\\#'],
    ['C#', 'C#'],
    ['Issue #42', 'Issue #42'],
  ])('heading end %j -> %j', (input, expected) => {
    expect(escapeHeadingEnd(input)).toBe(expected);
  });

  test.each([
    ['https://example.com/a_(b)', 'https://example.com/a_(b)'],
    ['/blog-media/x/threat model.pdf', '</blog-media/x/threat model.pdf>'],
    ['https://example.com/a)b', '<https://example.com/a)b>'],
    ['https://example.com/<x>', '<https://example.com/\\<x\\>>'],
    ['https://example.com/a\r\nb', '<https://example.com/a%0D%0Ab>'],
    ['', '<>'],
  ])('link destination %j -> %j', (input, expected) => {
    expect(formatLinkDestination(input)).toBe(expected);
  });
});

describe('other writers', () => {
  test('link titles are double-quoted, escaped and on one line', () => {
    expect(formatLinkTitle('say "hi"\\')).toBe('"say \\"hi\\"\\\\"');
    expect(formatLinkTitle('one\n\n# two')).toBe('"one # two"');
  });

  test('brackets in link text become character references', () => {
    expect(bracketsAsReferences('\\[^x\\] and \\*')).toBe('&#91;^x&#93; and \\*');
  });

  test.each([
    ['', 1],
    ['a`b', 2],
    ['a`b``c', 3],
    ['``', 1],
  ])('shortest unused backtick run in %j is %i', (text, length) => {
    expect(shortestUnusedBacktickRun(text)).toBe(length);
  });

  test.each([
    ['plain', '```'],
    ['```\ninside', '````'],
    ['   ````indented', '`````'],
    ['    ````` code-indented lines cannot close a fence', '```'],
  ])('code fence for %j is %j', (text, fence) => {
    expect(codeFenceFor(text)).toBe(fence);
  });

  test.each([
    ['ts', 'ts'],
    ['a`b', ''],
    ['python `x`', 'python'],
    ['ts\n# Injected', 'ts # Injected'],
  ])('fence info %j -> %j', (info, expected) => {
    expect(fenceInfo(info)).toBe(expected);
  });
});
