/**
 * Reviewer tests: editor states that never come from parsing Markdown.
 *
 * Parsed documents never hold a line ending inside a text node or an
 * attribute, but edited ones can: `tr.insertText` (an "accept AI proposal"
 * button), `insertContent` with `preserveWhitespace`, pasted HTML attributes
 * (`data-citation`, `class="language-..."`, `href="...&#13;..."`) or JSON. The
 * serializer must never let such a value start a new Markdown line, where it
 * would become a heading, a list item, a fence or a citation definition.
 *
 * For each case: markdown-it (the server's reading) and TipTap see exactly the
 * blocks the document has, and the text comes back with line endings as spaces.
 */
import { Editor, type JSONContent } from '@tiptap/core'
import { afterAll, describe, expect, test } from 'vitest'

import { ArticleCodec, articleExtensions } from '../src/index.js'
import { tokenize } from './helpers/oracle.js'

const codec = new ArticleCodec()
afterAll(() => codec.destroy())

const INJECTED = '\n# Injected heading\n- injected item\n\n[^x]: injected definition\n```\nfence'

function serialize(doc: JSONContent): string {
  return codec.serialize({ frontmatter: null, doc })
}

/** Top-level block types as markdown-it reads them (footnote tokens do not set `level`, so track depth). */
function serverBlocks(markdown: string): string[] {
  const types: string[] = []
  let depth = 0
  for (const token of tokenize(markdown)) {
    if (token.nesting === -1) {
      depth -= 1
      continue
    }
    if (depth === 0 && token.type !== 'inline') {
      types.push(token.type)
    }
    depth += token.nesting
  }
  return types
}

/** Every block token markdown-it reads, nested ones included (an injected heading inside a list item counts). */
function allBlocks(markdown: string): string[] {
  return tokenize(markdown)
    .filter(token => token.type !== 'inline' && token.nesting !== -1)
    .map(token => token.type)
}

/** The same JSON with every line ending in a string replaced by a space: the structure the document really has. */
function withoutLineEndings(node: JSONContent): JSONContent {
  return JSON.parse(JSON.stringify(node).replace(/\\r\\n|\\r|\\n/g, ' ')) as JSONContent
}

function editorBlocks(markdown: string): string[] {
  return (codec.parse(markdown).doc.content ?? []).map(node => node.type ?? '?')
}

function allText(doc: JSONContent): string {
  if (doc.type === 'text') {
    return doc.text ?? ''
  }
  return (doc.content ?? []).map(allText).join('')
}

const paragraph = (...content: JSONContent[]): JSONContent => ({ type: 'paragraph', content })
const text = (value: string, marks?: JSONContent['marks']): JSONContent => ({ type: 'text', text: value, ...(marks ? { marks } : {}) })

describe('line endings inside text nodes cannot create blocks', () => {
  test.each([
    ['LF', '\n'],
    ['CR', '\r'],
    ['CRLF', '\r\n'],
  ])('%s in a paragraph', (_name, eol) => {
    const doc = { type: 'doc', content: [paragraph(text(`Before${INJECTED.replaceAll('\n', eol)} after`))] }
    const markdown = serialize(doc)
    expect(markdown).not.toMatch(/\r/)
    expect(markdown.trimEnd().split('\n')).toHaveLength(1)
    expect(serverBlocks(markdown)).toEqual(['paragraph_open'])
    expect(editorBlocks(markdown)).toEqual(['paragraph'])
    expect(allText(codec.parse(markdown).doc)).toBe('Before # Injected heading - injected item [^x]: injected definition ``` fence after')
    // The visible line breaks are gone, so the editor must still warn before saving.
    expect(codec.check({ frontmatter: null, doc }).lossless).toBe(false)
  })

  test.each<[string, JSONContent, string[], string[]]>([
    ['heading', { type: 'heading', attrs: { level: 2 }, content: [text(`Title${INJECTED}`)] }, ['heading_open'], ['heading']],
    [
      'list item',
      { type: 'bulletList', content: [{ type: 'listItem', content: [paragraph(text(`Item${INJECTED}`))] }] },
      ['bullet_list_open'],
      ['bulletList'],
    ],
    [
      'task item',
      { type: 'taskList', content: [{ type: 'taskItem', attrs: { checked: false }, content: [paragraph(text(`Task${INJECTED}`))] }] },
      ['bullet_list_open'],
      ['taskList'],
    ],
    ['blockquote', { type: 'blockquote', content: [paragraph(text(`Quote${INJECTED}`))] }, ['blockquote_open'], ['blockquote']],
    ['callout', { type: 'callout', attrs: { type: 'tip' }, content: [paragraph(text(`Tip${INJECTED}`))] }, ['container_tip_open'], ['callout']],
    [
      'table cell',
      {
        type: 'table',
        content: [
          { type: 'tableRow', content: [{ type: 'tableHeader', content: [paragraph(text('h'))] }] },
          { type: 'tableRow', content: [{ type: 'tableCell', content: [paragraph(text(`Cell${INJECTED}`))] }] },
        ],
      },
      ['table_open'],
      ['table'],
    ],
    ['inline code', paragraph(text('a '), text(`code${INJECTED}`, [{ type: 'code' }])), ['paragraph_open'], ['paragraph']],
    ['bold text', paragraph(text('a '), text(`bold${INJECTED}`, [{ type: 'bold' }])), ['paragraph_open'], ['paragraph']],
    ['link text', paragraph(text(`link${INJECTED}`, [{ type: 'link', attrs: { href: 'https://example.com' } }])), ['paragraph_open'], ['paragraph']],
  ])('%s', (_name, block, server, editor) => {
    const markdown = serialize({ type: 'doc', content: [block] })
    expect(serverBlocks(markdown)).toEqual(server)
    expect(allBlocks(markdown)).toEqual(allBlocks(serialize({ type: 'doc', content: [withoutLineEndings(block)] })))
    expect(editorBlocks(markdown)).toEqual(editor)
    expect(markdown).not.toMatch(/^# Injected/m)
    expect(markdown).not.toMatch(/^\[\^x\]:/m)
  })

  test('in a footnote definition', () => {
    const doc = {
      type: 'doc',
      content: [
        paragraph(text('Claim.'), { type: 'citation', attrs: { id: 'src' } }),
        { type: 'footnotes', content: [{ type: 'footnoteDefinition', attrs: { id: 'src' }, content: [text(`Source${INJECTED}`)] }] },
      ],
    }
    const markdown = serialize(doc)
    expect(serverBlocks(markdown)).toEqual(['paragraph_open', 'footnote_block_open'])
    expect(editorBlocks(markdown)).toEqual(['paragraph', 'footnotes'])
  })

  test('inline code keeps its edge spaces when its edges were line endings', () => {
    const markdown = serialize({ type: 'doc', content: [paragraph(text('a '), text('\nx\n', [{ type: 'code' }]), text(' b'))] })
    const code = tokenize(markdown).flatMap(token => token.children ?? []).filter(token => token.type === 'code_inline')
    expect(code.map(token => token.content)).toEqual([' x '])
  })
})

describe('attributes cannot break out of their syntax', () => {
  test.each([
    ['CR', 'https://example.com/a\r# Injected', '%0D'],
    ['LF', 'https://example.com/a\n# Injected', '%0A'],
  ])('a %s in a link destination is percent-encoded', (_name, href, encoded) => {
    const markdown = serialize({ type: 'doc', content: [paragraph(text('see', [{ type: 'link', attrs: { href } }]))] })
    expect(markdown).toContain(encoded)
    expect(serverBlocks(markdown)).toEqual(['paragraph_open'])
    const link = tokenize(markdown)[1].children?.find(token => token.type === 'link_open')
    expect(link?.attrGet('href')).toBe(`https://example.com/a${encoded}#%20Injected`)
  })

  test('a link title is written on one line', () => {
    const markdown = serialize({
      type: 'doc',
      content: [paragraph(text('see', [{ type: 'link', attrs: { href: 'https://example.com', title: 'one\n\n# two' } }]))],
    })
    expect(markdown).toBe('[see](https://example.com "one # two")\n')
    expect(tokenize(markdown)[1].children?.find(token => token.type === 'link_open')?.attrGet('title')).toBe('one # two')
  })

  test.each([
    ['a backtick', 'a`b', ''],
    ['a backtick after the language', 'ts `x`', 'ts'],
    ['a line break', 'ts\n# Injected', 'ts # Injected'],
  ])('a code block language with %s keeps the fence intact', (_name, language, info) => {
    const markdown = serialize({ type: 'doc', content: [{ type: 'codeBlock', attrs: { language }, content: [text('# not a heading\n- nor a list')] }] })
    const tokens = tokenize(markdown)
    expect(tokens.map(token => token.type)).toEqual(['fence'])
    expect(tokens[0].info).toBe(info)
    expect(tokens[0].content).toBe('# not a heading\n- nor a list\n')
    expect(editorBlocks(markdown)).toEqual(['codeBlock'])
  })

  test.each([
    ['a newline', 'x\n\n# Injected', '[^x--#-Injected]'],
    ['a bracket and a link', 'x](https://evil.example) [y', '[^x-(https://evil.example)-[y]'],
    ['a space', 'two words', '[^two-words]'],
  ])('a citation id with %s stays one reference, paired with its definition', (_name, id, label) => {
    const doc = {
      type: 'doc',
      content: [
        paragraph(text('Claim.'), { type: 'citation', attrs: { id } }),
        { type: 'footnotes', content: [{ type: 'footnoteDefinition', attrs: { id }, content: [text('Source.')] }] },
      ],
    }
    const markdown = serialize(doc)
    expect(markdown).toBe(`Claim.${label}\n\n${label}: Source.\n`)
    expect(serverBlocks(markdown)).toEqual(['paragraph_open', 'footnote_block_open'])
    const refs = tokenize(markdown)[1].children?.filter(token => token.type === 'footnote_ref') ?? []
    expect(refs).toHaveLength(1)
    expect(tokenize(markdown).some(token => token.type === 'link_open')).toBe(false)
  })
})

describe('real editor input paths', () => {
  const editor = new Editor({ element: null, extensions: articleExtensions() })
  afterAll(() => editor.destroy())

  test('tr.insertText with line endings (an "accept proposal" button)', () => {
    editor.commands.setContent('<p>Start</p>')
    editor.commands.command(({ tr }) => {
      tr.insertText(' and\n- injected item\n\n[^x]: injected definition', 6)
      return true
    })
    const markdown = editor.getMarkdown()
    expect(serverBlocks(markdown)).toEqual(['paragraph_open'])
    expect(markdown).toBe('Start and - injected item \\[^x\\]: injected definition\n')
  })

  test('insertContent with preserveWhitespace', () => {
    editor.commands.setContent('<p>Start</p>')
    editor.commands.insertContentAt(6, ' then\n## Injected', { parseOptions: { preserveWhitespace: 'full' } })
    expect(serverBlocks(editor.getMarkdown())).toEqual(['paragraph_open'])
  })

  test('pasted HTML: data-citation, a language class with a backtick, an href with &#13;', () => {
    editor.commands.setContent(
      '<p>Claim<sup data-citation="x&#10;&#10;# Injected">[x]</sup> <a href="https://example.com/a&#13;# Injected">link</a></p>' +
        '<pre><code class="language-a`b"># not a heading</code></pre>',
    )
    const markdown = editor.getMarkdown()
    expect(serverBlocks(markdown)).toEqual(['paragraph_open', 'fence'])
    const tokens = tokenize(markdown)
    expect(tokens.find(token => token.type === 'fence')?.content).toBe('# not a heading\n')
    expect(tokens[1].children?.filter(token => token.type === 'link_open').map(token => token.attrGet('href'))).toEqual([
      'https://example.com/a%0D#%20Injected',
    ])
  })
})

describe('parsing cost', () => {
  // The citation tokenizer's start() scanned the rest of the source on every
  // text step: 16 KB of "[" took 1.1 s, 64 KB took 18 s (quadratic). Now ~20 ms.
  test('64 KB of brackets parses in linear time', () => {
    const source = `${'['.repeat(64 * 1024)}\n`
    const started = performance.now()
    const markdown = codec.normalize(source)
    expect(performance.now() - started).toBeLessThan(3000)
    expect(markdown).toBe(`${'\\['.repeat(64 * 1024)}\n`)
  })

  test('citations are still found after many brackets', () => {
    expect(codec.normalize(`${'['.repeat(500)} claim[^a]\n\n[^a]: Source.\n`)).toBe(`${'\\['.repeat(500)} claim[^a]\n\n[^a]: Source.\n`)
  })
})
