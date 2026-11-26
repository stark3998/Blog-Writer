/**
 * Targeted parity cases between TipTap (marked + dialect tokenizers) and the
 * markdown-it oracle (the server's reading), plus the known divergences.
 *
 * "Parity" means: TipTap's normalization of the snippet is read by markdown-it
 * as the same document as the snippet itself.
 */
import type { JSONContent } from '@tiptap/core'
import { afterAll, describe, expect, test } from 'vitest'

import { ArticleCodec } from '../src/index.js'
import { compareMarkdown, render, tokenize } from './helpers/oracle.js'

const codec = new ArticleCodec()
afterAll(() => codec.destroy())

function types(doc: JSONContent): string[] {
  return (doc.content ?? []).map(node => node.type ?? '?')
}

function parity(source: string) {
  const normalized = codec.normalize(source)
  return { normalized, comparison: compareMarkdown(source, normalized), doc: codec.parse(source).doc }
}

describe('callouts mirror mdit_py_plugins.container', () => {
  test.each([
    ['basic', ':::note\ntext\n:::\n', ['callout']],
    ['spaces around the type', ':::  tip  \ntext\n:::\n', ['callout']],
    ['longer closing fence', ':::warning\ntext\n:::::\n', ['callout']],
    ['unclosed runs to the end', ':::note\ntext\n\nmore\n', ['callout']],
    ['interrupts a paragraph', 'para\n:::note\ntext\n:::\n', ['paragraph', 'callout']],
    ['a title is not allowed (strict validate)', ':::note Title\ntext\n:::\n', ['paragraph']],
    ['unknown type is text', ':::danger\ntext\n:::\n', ['paragraph']],
    ['indented 4 spaces is code', '    :::note\n    text\n', ['codeBlock']],
    ['shorter inner fence is content', '::::note\n:::\n::::\n', ['callout']],
    ['::: inside a fence closes the callout early', ':::note\n```\n:::\n```\n:::\n', ['callout', 'codeBlock']],
  ])('%s', (_name, source, expected) => {
    const { comparison, doc } = parity(source)
    expect(types(doc)).toEqual(expected)
    expect(comparison.differences).toEqual([])
  })

  test('a callout whose content has a ::: line is written with a longer fence', () => {
    const doc = {
      type: 'doc',
      content: [{ type: 'callout', attrs: { type: 'note' }, content: [{ type: 'codeBlock', content: [{ type: 'text', text: ':::\n::::' }] }] }],
    }
    expect(codec.serialize({ frontmatter: null, doc })).toBe(':::::note\n```\n:::\n::::\n```\n:::::\n')
  })

  test('a callout nested in a list item is lifted (schema: callouts are top-level)', () => {
    const { doc } = parity('- item\n\n  :::note\n  inside\n  :::\n')
    expect(JSON.stringify(doc)).not.toContain('"callout"')
  })
})

describe('citations mirror mdit_py_plugins.footnote', () => {
  test.each([
    ['defined reference', 'Claim.[^a]\n\n[^a]: Source.\n'],
    ['undefined reference stays text', 'Claim.[^a]\n'],
    ['inline link wins', '[^a](https://example.com)\n\n[^a]: Source.\n'],
    ['labels may hold punctuation', 'Claim.[^a.b_c:1]\n\n[^a.b_c:1]: Source.\n'],
    ['definition before reference', '[^a]: Source.\n\nClaim.[^a]\n'],
    ['definition interrupts a paragraph', 'Claim.[^a]\n[^a]: Source.\n'],
    ['lazy continuation line', 'Claim.[^a]\n\n[^a]: Source\ncontinued.\n'],
    ['empty first line, indented text', 'Claim.[^a]\n\n[^a]:\n    Source.\n'],
    ['duplicate definition: last wins', 'Claim.[^a]\n\n[^a]: First.\n[^a]: Second.\n'],
    ['definition inside a callout', ':::note\nClaim.[^a]\n\n[^a]: Source.\n:::\n'],
    ['unreferenced definition is kept (not rendered)', 'Text.\n\n[^a]: Unused.\n'],
  ])('%s', (_name, source) => {
    const { comparison } = parity(source)
    expect(comparison.differences).toEqual([])
  })

  test('the last duplicate definition is the one kept', () => {
    const { normalized } = parity('Claim.[^a]\n\n[^a]: First.\n[^a]: Second.\n')
    expect(normalized).toBe('Claim.[^a]\n\n[^a]: Second.\n')
  })

  test('empty definitions have no trailing space', () => {
    expect(parity('Claim.[^a]\n\n[^a]:\n').normalized).toBe('Claim.[^a]\n\n[^a]:\n')
  })
})

describe('mermaid fences', () => {
  test.each([
    ['lowercase', '```mermaid\ngraph TD\n```\n', 'mermaid'],
    ['any case, extra words', '```Mermaid title="x"\ngraph TD\n```\n', 'mermaid'],
    ['tilde fence', '~~~mermaid\ngraph TD\n~~~\n', 'mermaid'],
    ['indented code is not a diagram', '    graph TD\n', 'codeBlock'],
  ])('%s', (_name, source, type) => {
    const { comparison, doc } = parity(source)
    expect(types(doc)).toEqual([type])
    expect(comparison.differences).toEqual([])
  })
})

describe('raw HTML is never interpreted', () => {
  test.each([
    ['img onerror', '<img src=x onerror=alert(1)>\n'],
    ['script block', '<script>alert(1)</script>\n'],
    ['inline tags keep inner Markdown', 'a <b>*em*</b> b\n'],
    ['comment', 'a <!-- hidden --> b\n'],
    ['javascript link stays a link (sanitizer strips it)', '[x](javascript:alert(1))\n'],
  ])('%s', (_name, source) => {
    const { comparison, doc } = parity(source)
    expect(comparison.differences).toEqual([])
    expect(JSON.stringify(doc)).not.toMatch(/"type":"(html|htmlBlock)"/)
  })

  test('HTML-looking text is escaped on output', () => {
    expect(parity('<img src=x onerror=alert(1)>\n').normalized).toBe('\\<img src=x onerror=alert(1)>\n')
  })
})

describe('other CommonMark details', () => {
  test.each([
    ['code span strips one space each side', '` a `\n'],
    ['code span keeps inner spaces', '`  a  `\n'],
    ['entities are decoded', '&copy; &eacute; &#35; &#x26; &amp;lt;\n'],
    ['unescaped pipe in code splits a table cell (GFM)', '| a |\n| - |\n| `x|y` |\n'],
    ['ordered list delimiter change starts a new list', '1. a\n2) b\n'],
    ['table interrupts a paragraph', 'para\n| a |\n| - |\n'],
    ['setext heading', 'Title\n=====\n'],
    ['hard break with spaces', 'a  \nb\n'],
    ['bare URL is text (no linkify)', 'see https://example.com\n'],
    ['strikethrough is text', '~~gone~~\n'],
    ['image inside text', 'an ![icon](/i.png) inline\n'],
  ])('%s', (_name, source) => {
    expect(parity(source).comparison.differences).toEqual([])
  })

  // Reviewer: marked accepted any Unicode whitespace after the # run, so these
  // paragraphs were normalized into real h2/h3 headings that no lint rule caught.
  test.each([
    ['NBSP after ##', '##\u00A0not a heading\n'],
    ['ideographic space after ###', '###\u3000not a heading\n'],
    ['NBSP after #', '#\u00A0not a heading\n'],
  ])('%s is a paragraph, as in CommonMark', (_name, source) => {
    const { normalized, comparison, doc } = parity(source)
    expect(types(doc)).toEqual(['paragraph'])
    expect(comparison.differences).toEqual([])
    expect(normalized.startsWith('\\#')).toBe(true)
  })

  test.each([
    ['backtick in the only word', '~~~a`b\n# not a heading\n- nor a list\n~~~\n', ''],
    ['backtick in a later word', '~~~python `x`\nprint(1)\n~~~\n', 'python'],
  ])('a tilde fence whose info string holds a backtick stays one code block (%s)', (_name, source, info) => {
    // Reviewer: the info string used to be copied onto a backtick fence, which then was
    // no fence at all, so the code was read as Markdown (a heading, a list, ...).
    const { normalized } = parity(source)
    const before = tokenize(source)
    const after = tokenize(normalized)
    expect(after.map(token => token.type)).toEqual(['fence'])
    expect(after[0].content).toBe(before[0].content)
    expect(after[0].info).toBe(info)
    expect(codec.normalize(normalized)).toBe(normalized)
  })
})

/**
 * Known divergences, kept as tests so that a dependency upgrade that fixes
 * (or changes) one is noticed. All involve input the linter rejects.
 */
describe('known divergences (input outside the dialect)', () => {
  test('marked ends a paragraph at an HTML-block-looking line; markdown-it (html: false) does not', () => {
    expect(parity('text\n<div>\nmore\n').comparison.equivalent).toBe(false)
  })

  test('block content in a footnote definition is text in TipTap, blocks in markdown-it', () => {
    expect(parity('Claim.[^a]\n\n[^a]: - item\n').comparison.equivalent).toBe(false)
  })

  test('a #-run + Unicode whitespace line interrupts a paragraph in marked, not in CommonMark', () => {
    // Reviewer: on its own line this is now a paragraph on both sides (see "other CommonMark
    // details"); marked's paragraph-interrupt rule still splits the paragraph before it.
    const { comparison, doc } = parity('para\n##\u00A0not a heading\n')
    expect(types(doc)).toEqual(['paragraph', 'paragraph'])
    expect(comparison.equivalent).toBe(false)
  })

  test('a linked image loses its link in the editor (the linter reports inline-image)', () => {
    const { normalized, comparison } = parity('[![Alt](/blog-media/a/b.png)](https://example.com)\n')
    expect(normalized).toBe('![Alt](/blog-media/a/b.png)\n')
    expect(comparison.equivalent).toBe(false)
  })

  test('numeric references to C1 controls: TipTap maps to Windows-1252, markdown-it to U+FFFD', () => {
    expect(parity('&#128;\n').comparison.equivalent).toBe(false)
  })

  test('a citation inside link text: marked keeps the link, markdown-it drops it', () => {
    expect(parity('[see [^a]](https://example.com)\n\n[^a]: Source.\n').comparison.equivalent).toBe(false)
  })
})

describe('adjacent lists of the same kind', () => {
  const paragraph = (text: string) => ({ type: 'paragraph', content: [{ type: 'text', text }] })
  const item = (type: string, text: string) => ({ type, ...(type === 'taskItem' ? { attrs: { checked: false } } : {}), content: [paragraph(text)] })
  const list = (type: string, text: string) => ({ type, content: [item(type === 'taskList' ? 'taskItem' : 'listItem', text)] })

  test('alternate markers keep them apart (lossless)', () => {
    const doc = {
      type: 'doc',
      content: [list('bulletList', 'a'), list('bulletList', 'b'), list('taskList', 'c'), list('orderedList', 'd'), list('orderedList', 'e')],
    }
    const result = codec.check({ frontmatter: null, doc })
    expect(result.markdown).toBe('- a\n\n* b\n\n- [ ] c\n\n1. d\n\n1) e\n')
    expect(result.lossless).toBe(true)
    expect(types(result.reparsed.doc)).toEqual(['bulletList', 'bulletList', 'taskList', 'orderedList', 'orderedList'])
  })

  test('a list after a paragraph uses the canonical marker again', () => {
    const doc = { type: 'doc', content: [list('bulletList', 'a'), paragraph('p'), list('bulletList', 'b')] }
    expect(codec.serialize({ frontmatter: null, doc })).toBe('- a\n\np\n\n- b\n')
  })
})

describe('documents Markdown cannot represent (detected by ArticleCodec.check)', () => {
  test('a code span with a backslash before a pipe in a table cell loses the backslash', () => {
    const cell = (type: string, content: JSONContent[]) => ({ type, content: [{ type: 'paragraph', content }] })
    const doc = {
      type: 'doc',
      content: [
        {
          type: 'table',
          content: [
            { type: 'tableRow', content: [cell('tableHeader', [{ type: 'text', text: 'h' }])] },
            { type: 'tableRow', content: [cell('tableCell', [{ type: 'text', text: 'a\\|b', marks: [{ type: 'code' }] }])] },
          ],
        },
      ],
    }
    expect(codec.check({ frontmatter: null, doc }).lossless).toBe(false)
  })

  test('bold covering part of an inline code span cannot be expressed', () => {
    const doc = {
      type: 'doc',
      content: [{ type: 'paragraph', content: [
        { type: 'text', text: 'ab', marks: [{ type: 'bold' }, { type: 'code' }] },
        { type: 'text', text: 'cd', marks: [{ type: 'code' }] },
      ] }],
    }
    expect(codec.check({ frontmatter: null, doc }).lossless).toBe(false)
  })
})

test('the oracle renders like the server would (sanity)', () => {
  const html = render('Claim.[^a]\n\n:::note\n- [x] done\n:::\n\n[^a]: Source.\n')
  expect(html).toContain('<div class="note">')
  expect(html).toContain('class="task-list-item-checkbox" checked="checked" disabled="disabled" type="checkbox"')
  expect(html).toContain('<section class="footnotes">')
})
