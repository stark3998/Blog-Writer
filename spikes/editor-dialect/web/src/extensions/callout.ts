/**
 * Callout containers: `:::note`, `:::tip`, `:::warning`.
 *
 * The tokenizer mirrors `mdit_py_plugins.container` (a port of
 * markdown-it-container) line for line, because the server renders the same
 * Markdown with it:
 *
 * - the opening line is up to 3 spaces, 3+ colons, then the callout type and
 *   nothing else (the server's `validate` is strict, so `:::note Title` is
 *   not a callout);
 * - the closing line is up to 3 spaces, at least as many colons as the
 *   opening, then only spaces;
 * - the closing line is searched for *before* the content is parsed, so a
 *   `:::` line inside a fenced code block closes the callout. The serializer
 *   therefore lengthens the fence (`::::note`) when the content has such a line;
 * - an unclosed callout runs to the end of its parent.
 */
import { type JSONContent, mergeAttributes, Node } from '@tiptap/core'

import { type CalloutType, CALLOUT_TYPES, isCalloutType } from '../dialect/constants.js'

const OPENING_LINE = /^( {0,3})(:{3,})([^\n]*)$/
const CLOSING_LINE = /^ {0,3}(:+) *$/
/**
 * An opening line anywhere after the first character. marked calls `start`
 * with `src.slice(1)` to find where a paragraph must stop, so only a match
 * right after a newline is a real line start.
 */
const OPENING_AFTER_NEWLINE = new RegExp(`\\n {0,3}:{3,}[ \\t]*(?:${CALLOUT_TYPES.join('|')})[ \\t]*(?=\\n|$)`)

/** Block content allowed inside a callout: no headings, no nested callouts, no footnotes. */
export const CALLOUT_CONTENT =
  '(paragraph | bulletList | orderedList | taskList | codeBlock | mermaid | blockquote | table | horizontalRule)+'

export interface CalloutToken {
  type: 'callout'
  raw: string
  calloutType: CalloutType
  tokens: unknown[]
}

/**
 * Length of the colon fence for a callout whose rendered content is `inner`:
 * three, or one more than the longest colon run that starts a line of the
 * content (a shorter or equal run would close the callout early).
 */
export function calloutFenceLength(inner: string): number {
  let longest = 0
  for (const match of inner.matchAll(/^ {0,3}(:{3,})/gm)) {
    longest = Math.max(longest, match[1].length)
  }

  return Math.max(3, longest + 1)
}

export const Callout = Node.create({
  name: 'callout',

  group: 'topBlock',

  content: CALLOUT_CONTENT,

  defining: true,

  addAttributes() {
    return {
      type: {
        default: 'note' satisfies CalloutType,
        parseHTML: element => {
          const value = element.getAttribute('data-callout') ?? ''
          return isCalloutType(value) ? value : 'note'
        },
        renderHTML: attributes => ({ 'data-callout': attributes.type as string }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'aside[data-callout]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    return [
      'aside',
      mergeAttributes({ class: `callout callout-${node.attrs.type as string}` }, HTMLAttributes),
      0,
    ]
  },

  markdownTokenName: 'callout',

  parseMarkdown: (token, helpers) => {
    const calloutToken = token as unknown as CalloutToken
    const parseBlocks = helpers.parseBlockChildren ?? helpers.parseChildren
    return helpers.createNode(
      'callout',
      { type: calloutToken.calloutType },
      parseBlocks((calloutToken.tokens ?? []) as Parameters<typeof parseBlocks>[0]),
    )
  },

  renderMarkdown: (node: JSONContent, helpers) => {
    const type = (node.attrs?.type as string | undefined) ?? 'note'
    const inner = helpers.renderChildren(node.content ?? [], '\n\n')
    const fence = ':'.repeat(calloutFenceLength(inner))
    return inner === '' ? `${fence}${type}\n${fence}` : `${fence}${type}\n${inner}\n${fence}`
  },

  markdownTokenizer: {
    name: 'callout',
    level: 'block',
    start: (src: string) => {
      const match = OPENING_AFTER_NEWLINE.exec(src)
      return match ? match.index + 1 : -1
    },
    tokenize: (src, _tokens, lexer) => {
      const firstBreak = src.indexOf('\n')
      const firstLine = firstBreak === -1 ? src : src.slice(0, firstBreak)
      const opening = OPENING_LINE.exec(firstLine)
      if (!opening) {
        return undefined
      }

      const calloutType = opening[3].trim()
      if (!isCalloutType(calloutType)) {
        return undefined
      }

      const markerLength = opening[2].length
      const contentStart = firstBreak === -1 ? src.length : firstBreak + 1
      let contentEnd = src.length
      let rawEnd = src.length
      let position = contentStart

      while (position < src.length) {
        const lineBreak = src.indexOf('\n', position)
        const lineEnd = lineBreak === -1 ? src.length : lineBreak
        const closing = CLOSING_LINE.exec(src.slice(position, lineEnd))
        if (closing && closing[1].length >= markerLength) {
          contentEnd = position
          rawEnd = lineBreak === -1 ? src.length : lineBreak + 1
          break
        }
        position = lineBreak === -1 ? src.length : lineBreak + 1
      }

      const content = src.slice(contentStart, contentEnd)

      return {
        type: 'callout',
        raw: src.slice(0, rawEnd),
        calloutType,
        tokens: content.trim() === '' ? [] : lexer.blockTokens(content),
      }
    },
  },
})
