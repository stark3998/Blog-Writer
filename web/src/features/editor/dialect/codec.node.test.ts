// @vitest-environment node
/**
 * The normalize worker has no DOM (no `window`, no `document`). This file runs
 * in plain Node to prove the codec never needs one: every corpus file
 * normalizes to its golden without jsdom.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterAll, expect, test } from 'vitest';

import { ArticleCodec } from './codec';

const corpusDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', '..', 'contracts', 'fixtures', 'corpus');
const manifest = JSON.parse(readFileSync(join(corpusDir, 'manifest.json'), 'utf8')) as { files: Record<string, unknown> };
const files = Object.keys(manifest.files).sort();

const codec = new ArticleCodec();
afterAll(() => {
  codec.destroy();
});

test('there is no DOM in this environment', () => {
  expect(typeof window).toBe('undefined');
  expect(typeof document).toBe('undefined');
});

test.each(files)('%s normalizes to its golden without a DOM', (file) => {
  const source = readFileSync(join(corpusDir, file), 'utf8');
  const golden = readFileSync(join(corpusDir, 'normalized', file), 'utf8');
  expect(codec.normalize(source)).toBe(golden);
  expect(codec.check(golden).lossless).toBe(true);
});
