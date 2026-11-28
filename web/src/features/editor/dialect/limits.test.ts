// @vitest-environment node
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, test } from 'vitest';

import { DialectError, toDialectError } from './errors';
import {
  assertEditableSize,
  assertEditableSource,
  EDITOR_MAX_BYTES,
  estimateNestingDepth,
  MAX_NESTING_DEPTH,
  utf8ByteLength,
} from './limits';
import { createDialectMarked } from './marked';

const corpusDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', '..', 'contracts', 'fixtures', 'corpus');
const manifest = JSON.parse(readFileSync(join(corpusDir, 'manifest.json'), 'utf8')) as { files: Record<string, unknown> };

function errorCode(action: () => void): string | undefined {
  try {
    action();
  } catch (error) {
    return error instanceof DialectError ? error.code : 'not a DialectError';
  }
  return undefined;
}

describe('size', () => {
  test('utf8ByteLength counts UTF-8 bytes', () => {
    expect(utf8ByteLength('')).toBe(0);
    expect(utf8ByteLength('abc')).toBe(3);
    expect(utf8ByteLength('é')).toBe(2);
    expect(utf8ByteLength('日')).toBe(3);
    expect(utf8ByteLength('😀')).toBe(4);
  });

  test('the limit is 128 KB of UTF-8', () => {
    expect(EDITOR_MAX_BYTES).toBe(131_072);
    expect(errorCode(() => { assertEditableSize('a'.repeat(EDITOR_MAX_BYTES)); })).toBeUndefined();
    expect(errorCode(() => { assertEditableSize('a'.repeat(EDITOR_MAX_BYTES + 1)); })).toBe('too-large');
    expect(errorCode(() => { assertEditableSize('😀'.repeat(EDITOR_MAX_BYTES / 4)); })).toBeUndefined();
    expect(errorCode(() => { assertEditableSize(`${'😀'.repeat(EDITOR_MAX_BYTES / 4)}a`); })).toBe('too-large');
  });
});

describe('estimateNestingDepth', () => {
  test.each(Object.keys(manifest.files).sort())('corpus file %s is shallow', (file) => {
    expect(estimateNestingDepth(readFileSync(join(corpusDir, file), 'utf8'))).toBeLessThanOrEqual(6);
  });

  test.each([
    ['plain text', 'Just a paragraph.\n', 0],
    ['a list', '- a\n- b\n', 1],
    ['a nested list', '- a\n  - b\n    - c\n', 3],
    ['nested on one line', '- - - x\n', 3],
    ['an ordered list in a list', '1. a\n   - b\n', 2],
    ['quotes', '> a\n> > b\n> > > c\n', 3],
    ['a list in a quote', '> - a\n>   - b\n', 3],
    ['a callout', ':::note\ntext\n:::\n', 1],
    ['a list in a callout', ':::tip\n- a\n  - b\n:::\n', 3],
    ['consecutive callouts', ':::note\na\n:::\n\n:::tip\nb\n:::\n', 1],
    ['a callout closed by a longer fence', ':::note\n:::note\n:::::\nafter\n', 2],
  ])('%s', (_name, source, depth) => {
    expect(estimateNestingDepth(source)).toBe(depth);
  });

  test.each([
    ['a long thematic break', `${'- '.repeat(80)}\n`],
    ['a long starred break', `${'* '.repeat(80)}\n`],
    ['deeply indented code in a fence', `\`\`\`yaml\n${Array.from({ length: 60 }, (_, i) => `${'  '.repeat(i)}- key: value`).join('\n')}\n\`\`\`\n`],
    ['REPL prompts in a fence', `\`\`\`\n${'>>> print(1)\n'.repeat(50)}\`\`\`\n`],
    ['an email thread in a fence', `~~~\n${Array.from({ length: 40 }, (_, i) => `${'>'.repeat(i)} reply`).join('\n')}\n~~~\n`],
    ['callout lines inside a fence', `\`\`\`markdown\n${':::note\n'.repeat(50)}\`\`\`\n`],
  ])('%s is not mistaken for deep nesting', (_name, source) => {
    expect(estimateNestingDepth(source)).toBeLessThanOrEqual(MAX_NESTING_DEPTH);
  });

  test('a callout closing line inside a fence closes the callout and the fence (as the tokenizer does)', () => {
    const source = `:::note\n\`\`\`\n:::\n${'- '.repeat(1)}item\n`;
    expect(estimateNestingDepth(source)).toBe(1);
  });

  test.each([
    ['many quote markers', `${'>'.repeat(40)} deep\n`],
    ['spaced quote markers', `${'> '.repeat(40)}deep\n`],
    ['a deep bullet list', Array.from({ length: 40 }, (_, level) => `${'  '.repeat(level)}- item`).join('\n')],
    ['stacked callouts', ':::note\n'.repeat(40)],
    ['callouts that never close each other', Array.from({ length: 40 }, (_, i) => `${':'.repeat(40 - i)}note`).join('\n')],
  ])('%s is too deep', (_name, source) => {
    expect(estimateNestingDepth(source)).toBeGreaterThan(MAX_NESTING_DEPTH);
    expect(errorCode(() => { assertEditableSource(source); })).toBe('too-deep');
  });

  test('stops early on huge lines', () => {
    const started = performance.now();
    expect(estimateNestingDepth('>'.repeat(EDITOR_MAX_BYTES))).toBe(MAX_NESTING_DEPTH + 1);
    expect(estimateNestingDepth(`${'- '.repeat(EDITOR_MAX_BYTES / 2)}x`)).toBeGreaterThan(MAX_NESTING_DEPTH);
    expect(performance.now() - started).toBeLessThan(500);
  });
});

describe('without the pre-check', () => {
  test('stock marked really overflows the stack on deep nesting, and that maps to too-deep', () => {
    // Why the pre-check exists: marked recurses once per nesting level.
    let thrown: unknown;
    try {
      createDialectMarked().lexer(`${'>'.repeat(100_000)} deep\n`);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(RangeError);
    expect(toDialectError(thrown).code).toBe('too-deep');
  });
});
