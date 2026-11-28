/**
 * Mermaid diagrams: a fenced code block whose info string starts with
 * `mermaid`. A separate node type (not a code block language) so the editor
 * can render it live (strict mode) while the source stays plain text.
 *
 * It claims the same marked `code` token as the code block, with a higher
 * priority, and declines anything that is not a mermaid fence.
 */
import { type JSONContent, Node } from '@tiptap/core'

import { MERMAID_LANGUAGE } from '../dialect/constants.js'
import { codeFenceFor } from '../dialect/escape.js'
import { textContent } from '../dialect/json.js'

/** First word of a fenced code block info string, lowercased (the CommonMark "language"). */
export function infoLanguage(info: string | null | undefined): string {
  return (info ?? '').trim().split(/\s+/)[0]?.toLowerCase() ?? ''
}

export const Mermaid = Node.create({
  name: 'mermaid',

  group: 'block',

  content: 'text*',

  marks: '',

  code: true,

  defining: true,

  // Higher than CodeBlock (100) so this parser sees `code` tokens first.
  priority: 200,

  parseHTML() {
    return [{ tag: 'pre[data-mermaid]', preserveWhitespace: 'full' }]
  },

  renderHTML() {
    return ['pre', { 'data-mermaid': '', class: 'mermaid-source' }, ['code', 0]]
  },

  markdownTokenName: 'code',

  parseMarkdown: (token, helpers) => {
    const fenced = token.codeBlockStyle !== 'indented'
    if (!fenced || infoLanguage(token.lang as string | undefined) !== MERMAID_LANGUAGE) {
      return []
    }

    const text = (token.text as string | undefined) ?? ''
    return helpers.createNode('mermaid', undefined, text ? [helpers.createTextNode(text)] : [])
  },

  renderMarkdown: (node: JSONContent) => {
    const text = textContent(node)
    const fence = codeFenceFor(text)
    return text === '' ? `${fence}${MERMAID_LANGUAGE}\n${fence}` : `${fence}${MERMAID_LANGUAGE}\n${text}\n${fence}`
  },
})
