/**
 * Golden-corpus round trip through a real (headless, jsdom) TipTap v3 editor.
 *
 * For every corpus file:
 * 1. first = serialize(parse(source)) must equal the reviewed golden in
 *    corpus/normalized/ (regenerate with `npm run golden`, then review the diff);
 * 2. serialize(parse(first)) must equal first, byte for byte, and the parsed
 *    documents must be identical (no drift after the first normalization);
 * 3. canonical files must be fixed points (first === source);
 * 4. the first normalization may only change syntax: the markdown-it oracle
 *    must read source and first as the same document, and the syntax-only
 *    differences it reports must be exactly the categories the manifest lists
 *    (all of which the dialect spec allows);
 * 5. canonical output hygiene: LF, one final newline, no trailing whitespace
 *    or tabs outside code, no private-use placeholders.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { structuredPatch } from 'diff'
import { afterAll, describe, expect, test } from 'vitest'

import { ArticleCodec } from '../src/index.js'
import { splitFrontmatter } from '../src/dialect/frontmatter.js'
import { compareMarkdown, tokenize } from './helpers/oracle.js'

interface ManifestEntry {
  canonical: boolean
  covers?: string[]
  normalizations?: string[]
  lintErrors?: string[]
}

interface Manifest {
  allowedNormalizations: string[]
  files: Record<string, ManifestEntry>
}

const here = dirname(fileURLToPath(import.meta.url))
const corpusDir = join(here, '..', '..', 'corpus')
const normalizedDir = join(corpusDir, 'normalized')
const reportDir = join(here, '..', 'reports')
const manifest = JSON.parse(readFileSync(join(corpusDir, 'manifest.json'), 'utf8')) as Manifest
const files = Object.keys(manifest.files).sort()
const updateGolden = process.env.UPDATE_GOLDEN === '1'

const codec = new ArticleCodec()
const report: Record<string, unknown> = {}

afterAll(() => {
  codec.destroy()
  if (process.env.WRITE_REPORT === '1') {
    mkdirSync(reportDir, { recursive: true })
    writeFileSync(join(reportDir, 'normalization.json'), `${JSON.stringify(report, null, 2)}\n`)
  }
})

test('the corpus has at least 15 files and every file is in the manifest', () => {
  expect(files.length).toBeGreaterThanOrEqual(15)
  for (const file of files) {
    expect(existsSync(join(corpusDir, file)), file).toBe(true)
  }
})

describe.each(files)('%s', file => {
  const entry = manifest.files[file]
  const source = readFileSync(join(corpusDir, file), 'utf8')
  const first = codec.normalize(source)
  const second = codec.normalize(first)
  const comparison = compareMarkdown(source, first)

  report[file] = {
    canonical: entry.canonical,
    changed: first !== source,
    stable: first === second,
    categories: comparison.categories,
    semanticDifferences: comparison.differences,
    hunks: structuredPatch(file, file, source, first, '', '', { context: 0 }).hunks.map(hunk => ({
      oldStart: hunk.oldStart,
      newStart: hunk.newStart,
      lines: hunk.lines,
    })),
  }

  test('first normalization matches the reviewed golden', () => {
    const golden = join(normalizedDir, file)
    if (updateGolden) {
      mkdirSync(normalizedDir, { recursive: true })
      writeFileSync(golden, first)
    }
    expect(first).toBe(readFileSync(golden, 'utf8'))
  })

  test('parse -> serialize -> parse -> serialize is byte-stable', () => {
    expect(second).toBe(first)
    expect(codec.parse(second)).toEqual(codec.parse(first))
  })

  if (entry.canonical) {
    test('canonical source is a fixed point', () => {
      expect(first).toBe(source)
    })
  }

  test('first normalization changes syntax only, in the categories the spec allows', () => {
    expect(comparison.differences).toEqual([])
    for (const category of comparison.categories) {
      expect(manifest.allowedNormalizations).toContain(category)
    }
    expect(comparison.categories).toEqual([...(entry.normalizations ?? [])].sort())
  })

  test('canonical output hygiene', () => {
    expect(first.endsWith('\n')).toBe(true)
    expect(first.endsWith('\n\n')).toBe(false)
    expect(first).not.toMatch(/\r/)
    expect(first).not.toMatch(/[\uE010-\uE03F]/u)

    const { body } = splitFrontmatter(first)
    const codeLines = new Set<number>()
    for (const token of tokenize(body)) {
      if ((token.type === 'fence' || token.type === 'code_block') && token.map) {
        for (let line = token.map[0]; line < token.map[1]; line += 1) {
          codeLines.add(line)
        }
      }
    }

    let previousBlank = false
    body.split('\n').forEach((line, index) => {
      if (codeLines.has(index)) {
        previousBlank = false
        return
      }
      expect(line, `line ${index + 1}`).not.toMatch(/[ \t]$/)
      expect(line, `line ${index + 1}`).not.toMatch(/\t/)
      expect(previousBlank && line === '', `double blank line at ${index + 1}`).toBe(false)
      previousBlank = line === ''
    })
  })
})
