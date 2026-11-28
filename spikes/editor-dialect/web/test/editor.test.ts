/**
 * Documents built through editor commands (what a user does) serialize to the
 * dialect and survive a reload: getMarkdown() -> parse -> same document.
 */
import { Editor } from '@tiptap/core'
import { afterEach, beforeEach, expect, test } from 'vitest'

import { ArticleCodec, articleExtensions } from '../src/index.js'

let editor: Editor
let codec: ArticleCodec

beforeEach(() => {
  editor = new Editor({ element: document.createElement('div'), extensions: articleExtensions(), enableContentCheck: true })
  codec = new ArticleCodec()
})

afterEach(() => {
  editor.destroy()
  codec.destroy()
})

function reloadIsLossless(): string {
  const result = codec.check({ frontmatter: null, doc: editor.getJSON() })
  expect(result.lossless, result.markdown).toBe(true)
  expect(editor.getMarkdown()).toBe(result.markdown)
  return result.markdown
}

test('typing, marks and links', () => {
  editor
    .chain()
    .setContent('<p></p>')
    .insertContent('Plain, ')
    .toggleBold()
    .insertContent('bold')
    .toggleBold()
    .insertContent(' and ')
    .toggleCode()
    .insertContent(' code with edge spaces ')
    .toggleCode()
    .insertContent(' then ')
    .insertContent({ type: 'text', text: 'a link', marks: [{ type: 'link', attrs: { href: 'https://example.com/a b' } }] })
    .run()

  // CommonMark strips one space on each side of a code span, so edge spaces get one more of padding.
  expect(reloadIsLossless()).toBe('Plain, **bold** and `  code with edge spaces  ` then [a link](<https://example.com/a b>)\n')
})

test('headings, lists, tasks, quotes and code blocks', () => {
  editor.commands.setContent(
    '<h2>Section #</h2><p>1. not a list</p><ul><li><p>bullet</p></li></ul><ol start="3"><li><p>three</p></li></ol>' +
      '<blockquote><p>quote</p></blockquote><pre><code class="language-ts">const a = "```"</code></pre>',
  )
  editor.commands.insertContentAt(editor.state.doc.content.size, {
    type: 'taskList',
    content: [{ type: 'taskItem', attrs: { checked: true }, content: [{ type: 'paragraph', content: [{ type: 'text', text: 'done' }] }] }],
  })

  expect(reloadIsLossless()).toBe(
    '## Section \\#\n\n1\\. not a list\n\n- bullet\n\n3. three\n\n> quote\n\n```ts\nconst a = "```"\n```\n\n- [x] done\n',
  )
})

test('callouts, mermaid, citations and footnotes', () => {
  editor.commands.setContent({
    type: 'doc',
    content: [
      {
        type: 'callout',
        attrs: { type: 'warning' },
        content: [{ type: 'paragraph', content: [{ type: 'text', text: 'Rotate keys.' }, { type: 'citation', attrs: { id: 'nist-ai-rmf' } }] }],
      },
      { type: 'mermaid', content: [{ type: 'text', text: 'graph TD\n  A:::x --> B' }] },
      {
        type: 'footnotes',
        content: [{ type: 'footnoteDefinition', attrs: { id: 'nist-ai-rmf' }, content: [{ type: 'text', text: 'NIST. AI RMF 1.0.' }] }],
      },
    ],
  })

  expect(reloadIsLossless()).toBe(
    ':::warning\nRotate keys.[^nist-ai-rmf]\n:::\n\n```mermaid\ngraph TD\n  A:::x --> B\n```\n\n[^nist-ai-rmf]: NIST. AI RMF 1.0.\n',
  )
})

test('a table built with insertTable', () => {
  editor.commands.setContent('<p>intro</p>')
  editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true })
  editor.commands.insertContent('a | b')

  const markdown = reloadIsLossless()
  expect(markdown).toBe('intro\n\n| a \\| b | |\n| --- | --- |\n| | |\n')
})

test('the footnotes section is re-ordered by first citation', () => {
  editor.commands.setContent({
    type: 'doc',
    content: [
      { type: 'paragraph', content: [{ type: 'text', text: 'One.' }, { type: 'citation', attrs: { id: 'b' } }, { type: 'text', text: ' Two.' }, { type: 'citation', attrs: { id: 'a' } }] },
      {
        type: 'footnotes',
        content: [
          { type: 'footnoteDefinition', attrs: { id: 'a' }, content: [{ type: 'text', text: 'Source A.' }] },
          { type: 'footnoteDefinition', attrs: { id: 'b' }, content: [{ type: 'text', text: 'Source B.' }] },
        ],
      },
    ],
  })

  expect(editor.getMarkdown()).toBe('One.[^b] Two.[^a]\n\n[^b]: Source B.\n[^a]: Source A.\n')
})

test('an empty nested list item and a trailing hard break survive a reload', () => {
  editor.commands.setContent({
    type: 'doc',
    content: [
      {
        type: 'bulletList',
        content: [
          {
            type: 'listItem',
            content: [
              { type: 'paragraph', content: [{ type: 'text', text: 'parent' }] },
              { type: 'bulletList', content: [{ type: 'listItem', content: [{ type: 'paragraph' }] }] },
            ],
          },
        ],
      },
      { type: 'paragraph', content: [{ type: 'text', text: 'ends with a break' }, { type: 'hardBreak' }] },
    ],
  })

  expect(reloadIsLossless()).toBe('- parent\n  - &nbsp;\n\nends with a break\n')
})
