/** The oracle must notice semantic changes, or the corpus test proves nothing. */
import { describe, expect, test } from 'vitest'

import { compareMarkdown } from './helpers/oracle.js'

describe('compareMarkdown detects semantic changes', () => {
  test.each([
    ['text', 'a b\n', 'a c\n'],
    ['emphasis removed', '*a*\n', 'a\n'],
    ['emphasis kind', '*a*\n', '**a**\n'],
    ['link target', '[a](https://x.example)\n', '[a](https://y.example)\n'],
    ['heading level', '## a\n', '### a\n'],
    ['list to paragraph', '- a\n', 'a\n'],
    ['list start', '3. a\n', '4. a\n'],
    ['code language', '```ts\nx\n```\n', '```js\nx\n```\n'],
    ['code content', '```\nx \n```\n', '```\nx\n```\n'],
    ['inline code content', '` a  b `\n', '`a b`\n'],
    ['callout type', ':::note\na\n:::\n', ':::tip\na\n:::\n'],
    ['citation number', 'a[^x] b[^y]\n\n[^x]: X\n[^y]: Y\n', 'a[^y] b[^x]\n\n[^x]: X\n[^y]: Y\n'],
    ['task state', '- [ ] a\n', '- [x] a\n'],
    ['table alignment', '| a |\n| - |\n', '| a |\n| :-: |\n'],
    ['image alt', '![a](/x.png)\n', '![b](/x.png)\n'],
    ['hard break removed', 'a\\\nb\n', 'a b\n'],
    ['frontmatter presence', '---\ntitle: x\n---\n\na\n', 'a\n'],
    ['NBSP to space', 'a\u00A0b\n', 'a b\n'],
    ['ideographic space to space', 'a\u3000b\n', 'a b\n'],
  ])('%s', (_name, before, after) => {
    expect(compareMarkdown(before, after).equivalent).toBe(false)
  })
})

describe('compareMarkdown accepts syntax-only changes and names them', () => {
  test.each([
    ['_a_', '*a*', 'emphasis-delimiter'],
    ['* a', '- a', 'bullet-marker'],
    ['1) a', '1. a', 'ordered-delimiter'],
    ['1. a\n1. b', '1. a\n2. b', 'ordered-numbering'],
    ['a\n===', '# a', 'heading-style'],
    ['~~~\nx\n~~~', '```\nx\n```', 'code-fence-style'],
    ['    x', '```\nx\n```', 'code-fence-style'],
    ['a\n\n***', 'a\n\n---', 'thematic-break'],
    ['<https://x.example>', '[https://x.example](https://x.example)', 'autolink'],
    ['a\nb', 'a b', 'soft-break'],
    ['&copy;', '©', 'escape-or-entity'],
    ['- a\n\n- b', '- a\n- b', 'list-looseness'],
    ['::::note\na\n::::', ':::note\na\n:::', 'callout-marker'],
    ['```Mermaid\nx\n```', '```mermaid\nx\n```', 'mermaid-info'],
    ['![*a*](/x.png)', '![a](/x.png)', 'image-alt-markup'],
  ])('%j -> %j is %s', (before, after, category) => {
    const result = compareMarkdown(`${before}\n`, `${after}\n`)
    expect(result.differences).toEqual([])
    expect(result.categories).toContain(category)
  })
})
