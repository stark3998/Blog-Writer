/**
 * Golden-corpus round trip through a real (headless) TipTap editor, against
 * the shared fixtures in contracts/fixtures/corpus (the dialect's executable
 * spec; the server's normalizer must reproduce the same goldens).
 *
 * For every corpus file:
 * 1. first = normalize(source) equals the reviewed golden in normalized/;
 * 2. normalize(first) equals first byte for byte, and parse(first) equals
 *    parse(second): no drift after the first normalization;
 * 3. canonical files are fixed points (first === source);
 * 4. canonical output hygiene: LF, one final newline, no trailing whitespace,
 *    tabs or double blank lines outside code, no private-use placeholders;
 * 5. check() finds the golden lossless, and the highlighting editor (lowlight)
 *    writes the same Markdown as the headless codec.
 *
 * The goldens were verified in the Phase 0 spike against a markdown-it
 * oracle (the server's parser): the first normalization changes syntax only,
 * in the categories `manifest.json` lists. Matching them byte for byte keeps
 * that guarantee; `docs/v2/spikes/editor-dialect.md` has the per-file table.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { Editor } from '@tiptap/core';
import { common, createLowlight } from 'lowlight';
import { afterAll, describe, expect, test } from 'vitest';

import { ArticleCodec } from './codec';
import { splitFrontmatter } from './frontmatter';
import { createArticleExtensions } from './kit';

interface ManifestEntry {
  canonical: boolean;
  covers?: string[];
  normalizations?: string[];
  lintErrors?: string[];
}

interface Manifest {
  allowedNormalizations: string[];
  files: Record<string, ManifestEntry>;
}

// Resolved from this file: web/src/features/editor/dialect -> repository root. (Not `new URL(path,
// import.meta.url)`: Vite rewrites that pattern into a served asset URL in the jsdom environment.)
const corpusDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', '..', 'contracts', 'fixtures', 'corpus');
const read = (name: string) => readFileSync(join(corpusDir, name), 'utf8');
const manifest = JSON.parse(read('manifest.json')) as Manifest;
const files = Object.keys(manifest.files).sort();

const codec = new ArticleCodec();
const highlighting = new Editor({
  element: null,
  extensions: createArticleExtensions({ lowlight: createLowlight(common) }),
  enableContentCheck: true,
});

afterAll(() => {
  codec.destroy();
  highlighting.destroy();
});

/**
 * Line numbers (0-based) of fenced code in canonical Markdown, fence lines
 * included. Canonical code is always a backtick fence, possibly inside list
 * indentation or `>` quote markers.
 */
function codeLines(body: string): Set<number> {
  const lines = new Set<number>();
  let open: string | null = null;
  body.split('\n').forEach((line, index) => {
    const rest = line.replace(/^(?:[ ]*>[ ]?)*[ ]*/, '');
    if (open === null) {
      const fence = /^(`{3,})[^`]*$/.exec(rest);
      if (fence?.[1]) {
        open = fence[1];
        lines.add(index);
      }
      return;
    }
    lines.add(index);
    if (rest.startsWith(open) && /^`+$/.test(rest)) {
      open = null;
    }
  });
  return lines;
}

test('the corpus has every file in the manifest and a golden for each', () => {
  expect(files.length).toBeGreaterThanOrEqual(15);
  const onDisk = readdirSync(corpusDir).filter((name) => name.endsWith('.md')).sort();
  expect(onDisk).toEqual(files);
  const goldens = readdirSync(join(corpusDir, 'normalized')).filter((name) => name.endsWith('.md')).sort();
  expect(goldens).toEqual(files);
  for (const entry of Object.values(manifest.files)) {
    for (const category of entry.normalizations ?? []) {
      expect(manifest.allowedNormalizations).toContain(category);
    }
  }
});

describe.each(files)('%s', (file) => {
  const entry = manifest.files[file];
  const source = read(file);
  const golden = read(`normalized/${file}`);

  test('first normalization matches the reviewed golden', () => {
    expect(codec.normalize(source)).toBe(golden);
  });

  test('parse -> serialize -> parse -> serialize is byte-stable', () => {
    const second = codec.normalize(golden);
    expect(second).toBe(golden);
    expect(codec.parse(second)).toEqual(codec.parse(golden));
  });

  if (entry?.canonical) {
    test('canonical source is a fixed point', () => {
      expect(golden).toBe(source);
    });
  } else {
    test('non-canonical source changes on the first normalization', () => {
      expect(golden).not.toBe(source);
      expect(entry?.normalizations?.length ?? 0).toBeGreaterThan(0);
    });
  }

  test('canonical output hygiene', () => {
    expect(golden.endsWith('\n')).toBe(true);
    expect(golden.endsWith('\n\n')).toBe(false);
    expect(golden).not.toMatch(/\r/);
    expect(golden).not.toMatch(/[\uE010-\uE03F]/u);

    const { body } = splitFrontmatter(golden);
    const code = codeLines(body);
    let previousBlank = false;
    body.split('\n').forEach((line, index) => {
      if (code.has(index)) {
        previousBlank = false;
        return;
      }
      expect(line, `line ${index + 1}`).not.toMatch(/[ \t]$/);
      expect(line, `line ${index + 1}`).not.toMatch(/\t/);
      expect(previousBlank && line === '', `double blank line at ${index + 1}`).toBe(false);
      previousBlank = line === '';
    });
  });

  test('check() finds the golden lossless', () => {
    const result = codec.check(golden);
    expect(result.warnings).toEqual([]);
    expect(result.lossless).toBe(true);
    expect(result.markdown).toBe(golden);
  });

  test('the highlighting editor writes the same Markdown', () => {
    const article = codec.parse(golden);
    highlighting.commands.setContent(article.doc, { emitUpdate: false, errorOnInvalidContent: true });
    expect(highlighting.getMarkdown()).toBe(splitFrontmatter(golden).body);
  });
});
