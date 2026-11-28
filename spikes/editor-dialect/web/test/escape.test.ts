/**
 * Escaping: unit cases plus property-based round trips.
 *
 * Property: for arbitrary text placed in each inline context the dialect
 * allows, serialize -> parse returns the same text in TipTap, and the
 * markdown-it oracle (the server's reading) sees the same text too.
 */
import type { JSONContent } from '@tiptap/core'
import fc from 'fast-check'
import type { Token } from 'markdown-it'
import { afterAll, describe, expect, test } from 'vitest'

import { ArticleCodec } from '../src/index.js'
import { escapeHeadingEnd, escapeInlineText, escapeLineStart, formatLinkDestination } from '../src/dialect/escape.js'
import { createOracle, tokenize } from './helpers/oracle.js'

const codec = new ArticleCodec()
const serverOracle = createOracle()
afterAll(() => codec.destroy())

describe('escapeInlineText', () => {
  const plain = { lineStart: false, headingEnd: false }

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
  ])('%j -> %j', (input, expected) => {
    expect(escapeInlineText(input, plain)).toBe(expected)
  })

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
    expect(escapeInlineText(input, { lineStart: true, headingEnd: false })).toBe(expected)
  })

  test('line-start rules apply to already-escaped text only at position 0', () => {
    expect(escapeLineStart('a # b')).toBe('a # b')
  })

  test.each([
    ['Item #', 'Item \\#'],
    ['Ends ##', 'Ends \\##'],
    ['#', '\\#'],
    ['C#', 'C#'],
    ['Issue #42', 'Issue #42'],
  ])('heading end %j -> %j', (input, expected) => {
    expect(escapeHeadingEnd(input)).toBe(expected)
  })

  test.each([
    ['https://example.com/a_(b)', 'https://example.com/a_(b)'],
    ['/blog-media/x/threat model.pdf', '</blog-media/x/threat model.pdf>'],
    ['https://example.com/a)b', '<https://example.com/a)b>'],
    ['', '<>'],
  ])('link destination %j -> %j', (input, expected) => {
    expect(formatLinkDestination(input)).toBe(expected)
  })
})

// --- Property-based round trips ------------------------------------------

const ALPHABET = [
  ...'\\`*_[]<>&#-+=:|~!()^.,;?"\'{}$%@/',
  ...'0123456789',
  ...'abcxyzABC',
  ' ', '\u00A0', '\u3000', '\t', '\u200B',
  'é', '日', '😀', 'ß',
  '&amp;', '&copy;', '&#35;', '&#x26;', '1.', '1)', '---', '===', ':::', '```', '~~~', '[^x]', '](', '![', '<a', '</', '<!--', '\\\n',
  // Reviewer: line endings were stripped from every generated text, so a text node holding
  // one (insertText, pasted or generated text) was never exercised; it used to start a block.
  '\n', '\r', '\r\n',
]

const textArbitrary = fc
  .array(fc.constantFrom(...ALPHABET), { minLength: 1, maxLength: 24 })
  .map(parts => parts.join(''))
  .filter(text => /[^ \t\r\n]/.test(text))

// Deterministic by default; FC_SEED / FC_RUNS explore further (e.g. FC_SEED=7 FC_RUNS=20000).
const RUNS = { numRuns: Number(process.env.FC_RUNS ?? 1500), seed: Number(process.env.FC_SEED ?? 20261125) }

/** Text of a paragraph-like block as the oracle reads it (breaks as "\n"). */
function oracleInlineText(children: Token[]): string {
  return children
    .map(child => {
      switch (child.type) {
        case 'text':
        case 'text_special':
        case 'code_inline':
          return child.content
        case 'softbreak':
        case 'hardbreak':
          return '\n'
        default:
          return ''
      }
    })
    .join('')
}

function firstInline(markdown: string, predicate: (token: Token, index: number, tokens: Token[]) => boolean = token => token.type === 'inline'): Token[] {
  const tokens = tokenize(markdown)
  const inline = tokens.find(predicate)
  if (!inline?.children) {
    throw new Error(`no inline token in ${JSON.stringify(markdown)}`)
  }
  return inline.children
}

function tiptapText(doc: JSONContent): string {
  const out: string[] = []
  const visit = (node: JSONContent) => {
    if (node.type === 'text') {
      out.push(node.text ?? '')
    } else if (node.type === 'hardBreak') {
      out.push('\n')
    }
    for (const child of node.content ?? []) {
      visit(child)
    }
  }
  visit(doc)
  return out.join('')
}

function serialize(doc: JSONContent): string {
  return codec.serialize({ frontmatter: null, doc })
}

/**
 * Parsers trim Unicode whitespace (JS trim) at block boundaries and spaces
 * after a hard break; the serializer writes tabs in prose as spaces, and a run
 * of line endings (with the blanks around it) as one space.
 */
function prose(text: string): string {
  return text.replace(/[ \t]*(?:(?:\r\n?|\n)[ \t]*)+/g, ' ').replace(/\t/g, ' ')
}

function paragraphExpectation(text: string): string {
  return prose(text).trim()
}

describe('escaping round trips (property-based)', () => {
  test('paragraph text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = paragraphExpectation(text)
        fc.pre(expected !== '')
        const markdown = serialize({ type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] })
        expect(tiptapText(codec.parse(markdown).doc)).toBe(expected)
        expect(oracleInlineText(firstInline(markdown))).toBe(expected)
      }),
      RUNS,
    )
  })

  test('text on both sides of a hard break', () => {
    fc.assert(
      fc.property(textArbitrary, textArbitrary, (before, after) => {
        const left = prose(before).trimStart()
        const right = prose(after).replace(/^ +/, '').trimEnd()
        fc.pre(left.trim() !== '' && right !== '')
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'paragraph', content: [{ type: 'text', text: before }, { type: 'hardBreak' }, { type: 'text', text: after }] }],
        })
        const expected = `${left}\n${right}`
        expect(tiptapText(codec.parse(markdown).doc)).toBe(expected)
        expect(oracleInlineText(firstInline(markdown))).toBe(expected)
      }),
      RUNS,
    )
  })

  test('heading text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = prose(text).trim()
        fc.pre(expected !== '')
        const markdown = serialize({ type: 'doc', content: [{ type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text }] }] })
        expect(tiptapText(codec.parse(markdown).doc)).toBe(expected)
        expect(oracleInlineText(firstInline(markdown))).toBe(expected)
      }),
      RUNS,
    )
  })

  test('list item text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = paragraphExpectation(text)
        fc.pre(expected !== '')
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'bulletList', content: [{ type: 'listItem', content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] }] }],
        })
        expect(tiptapText(codec.parse(markdown).doc)).toBe(expected)
        expect(oracleInlineText(firstInline(markdown))).toBe(expected)
      }),
      RUNS,
    )
  })

  test('table cell text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = prose(text).trim()
        fc.pre(expected !== '' && expected === prose(text))
        const cell = (type: string, value: string) => ({ type, content: [{ type: 'paragraph', content: [{ type: 'text', text: value }] }] })
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'table', content: [{ type: 'tableRow', content: [cell('tableHeader', 'h')] }, { type: 'tableRow', content: [cell('tableCell', text)] }] }],
        })
        expect(tiptapText(codec.parse(markdown).doc)).toBe(`h${expected}`)
        const cellTokens = firstInline(markdown, (token, index, tokens) => token.type === 'inline' && tokens[index - 1]?.type === 'td_open')
        expect(oracleInlineText(cellTokens)).toBe(expected)
      }),
      RUNS,
    )
  })

  test('inline code content', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        // Inside a code span a line ending reads as a space.
        const expected = text.replace(/\r\n?|\n/g, ' ')
        fc.pre(!/^ +$/.test(expected))
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'paragraph', content: [{ type: 'text', text: 'x ' }, { type: 'text', text, marks: [{ type: 'code' }] }, { type: 'text', text: ' y' }] }],
        })
        expect(tiptapText(codec.parse(markdown).doc)).toBe(`x ${expected} y`)
        const code = firstInline(markdown).filter(token => token.type === 'code_inline')
        expect(code.map(token => token.content)).toEqual([expected])
      }),
      RUNS,
    )
  })

  test('link text and destination', () => {
    fc.assert(
      fc.property(textArbitrary, fc.webUrl(), (text, href) => {
        const expected = prose(text).trim()
        fc.pre(expected !== '' && expected === prose(text))
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'paragraph', content: [{ type: 'text', text: 'see ' }, { type: 'text', text, marks: [{ type: 'link', attrs: { href } }] }] }],
        })
        const parsed = codec.parse(markdown).doc
        const link = parsed.content?.[0]?.content?.find(node => node.marks?.some(mark => mark.type === 'link'))
        expect(link?.marks?.find(mark => mark.type === 'link')?.attrs?.href).toBe(href)
        expect(tiptapText(parsed)).toBe(`see ${expected}`)
        const children = firstInline(markdown)
        // Reviewer: was toBeTruthy(); the server must read the same destination.
        expect(children.find(token => token.type === 'link_open')?.attrGet('href')).toBe(serverOracle.normalizeLink(href))
        expect(oracleInlineText(children)).toBe(`see ${expected}`)
      }),
      { ...RUNS, numRuns: Math.ceil(RUNS.numRuns / 3) },
    )
  })

  test('image alt text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = text.replace(/\s+/g, ' ').trim()
        fc.pre(expected !== '')
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'paragraph', content: [{ type: 'image', attrs: { src: '/blog-media/a/b.png', alt: text } }] }],
        })
        const image = codec.parse(markdown).doc.content?.[0]?.content?.[0]
        expect(image?.attrs?.alt).toBe(expected)
        const token = firstInline(markdown).find(child => child.type === 'image')
        expect(oracleInlineText(token?.children ?? [])).toBe(expected)
      }),
      RUNS,
    )
  })

  test('callout paragraph text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = paragraphExpectation(text)
        fc.pre(expected !== '')
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'callout', attrs: { type: 'note' }, content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] }],
        })
        const doc = codec.parse(markdown).doc
        expect(doc.content?.[0]?.type).toBe('callout')
        expect(tiptapText(doc)).toBe(expected)
        const tokens = tokenize(markdown)
        expect(tokens[0]?.type).toBe('container_note_open')
        expect(oracleInlineText(tokens.find(token => token.type === 'inline')?.children ?? [])).toBe(expected)
      }),
      RUNS,
    )
  })
  test('bold and italic text between words', () => {
    const word = fc.stringMatching(/^[a-z0-9]{1,6}$/)
    fc.assert(
      fc.property(word, textArbitrary, word, fc.constantFrom('bold', 'italic'), (head, middle, tail, mark) => {
        const inner = `${head}${prose(middle).trim()}${tail}`
        const markdown = serialize({
          type: 'doc',
          content: [{ type: 'paragraph', content: [{ type: 'text', text: 'a ' }, { type: 'text', text: inner, marks: [{ type: mark }] }, { type: 'text', text: ' z' }] }],
        })
        const parsed = codec.parse(markdown).doc.content?.[0]?.content ?? []
        expect(parsed.map(node => [node.text, node.marks?.map(m => m.type) ?? []])).toEqual([
          ['a ', []],
          [inner, [mark]],
          [' z', []],
        ])
        const children = firstInline(markdown)
        const open = mark === 'bold' ? 'strong_open' : 'em_open'
        expect(children.filter(token => token.type === open)).toHaveLength(1)
        expect(oracleInlineText(children)).toBe(`a ${inner} z`)
      }),
      RUNS,
    )
  })

  test('footnote definition text', () => {
    fc.assert(
      fc.property(textArbitrary, text => {
        const expected = prose(text).trim()
        fc.pre(expected !== '' && expected === prose(text))
        const markdown = serialize({
          type: 'doc',
          content: [
            { type: 'paragraph', content: [{ type: 'text', text: 'Claim.' }, { type: 'citation', attrs: { id: 'src-1' } }] },
            { type: 'footnotes', content: [{ type: 'footnoteDefinition', attrs: { id: 'src-1' }, content: [{ type: 'text', text }] }] },
          ],
        })
        const doc = codec.parse(markdown).doc
        const definition = doc.content?.[1]?.content?.[0]
        expect(definition?.type).toBe('footnoteDefinition')
        expect(tiptapText(definition ?? {})).toBe(expected)
        const tokens = tokenize(markdown)
        const index = tokens.findIndex(token => token.type === 'footnote_open')
        const inline = tokens.slice(index).find(token => token.type === 'inline')
        expect(oracleInlineText(inline?.children ?? [])).toBe(expected)
      }),
      RUNS,
    )
  })
})

