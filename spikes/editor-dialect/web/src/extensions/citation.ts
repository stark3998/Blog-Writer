/**
 * Citations: `[^src-id]` footnote references bound to brief source ids, plus
 * the footnote definitions (`[^src-id]: ...`) that hold the source text.
 *
 * Schema:
 * - `citation`: inline atom with an `id` attribute.
 * - `footnotes`: one container at the very end of the document holding every
 *   `footnoteDefinition`, ordered by first reference (see transforms.ts).
 * - `footnoteDefinition`: `id` attribute plus single-paragraph inline text.
 *
 * Parsing mirrors `mdit_py_plugins.footnote` (port of markdown-it-footnote):
 * - a reference is `[^label]` with no space or newline in the label;
 * - an inline link wins over a reference: `[^x](https://...)` is a link;
 * - a reference only counts when a definition with the same label exists;
 *   undefined references are turned back into text after parsing;
 * - a definition starts a line (up to 3 spaces), may interrupt a paragraph,
 *   and continues over lazy paragraph-continuation lines.
 */
import { type JSONContent, mergeAttributes, Node } from '@tiptap/core'

const REFERENCE = /^\[\^([^\] \n]+)\]/
const DEFINITION_FIRST_LINE = /^ {0,3}\[\^([^\] \n]+)\]:[ \t]*([^\n]*)(?:\n|$)/
/** A definition line after a newline (see the note on `start` in callout.ts). */
const DEFINITION_AFTER_NEWLINE = /\n {0,3}\[\^[^\] \n]+\]:/
/**
 * The tail of an inline link (`(destination "title")`) right after `]`.
 * Approximates CommonMark's link destination/title grammar closely enough to
 * decide whether markdown-it would read `[^x](...)` as a link instead.
 */
const INLINE_LINK_TAIL =
  /^\([ \t]*(?:<[^<>\n]*>|(?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))*)(?:[ \t]+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?[ \t]*\)/

/**
 * The `[^label]` text for a citation or definition id. Parsed ids never hold
 * whitespace or `]`, but ids from pasted HTML (`data-citation`) or JSON can;
 * written raw they would break out of the brackets (a newline even starts a
 * new block). Those characters become `-`, the same way for references and
 * definitions so they stay paired; the linter reports the id (`citation-id`).
 */
export function citationLabel(id: unknown): string {
  return String(id ?? '').replace(/[\]\s]/g, '-')
}

export interface CitationToken {
  type: 'citation'
  raw: string
  label: string
}

export interface FootnoteDefinitionToken {
  type: 'footnoteDefinition'
  raw: string
  label: string
  text: string
  tokens: unknown[]
}

export const Citation = Node.create({
  name: 'citation',

  group: 'inline',

  inline: true,

  atom: true,

  selectable: true,

  addAttributes() {
    return {
      id: {
        default: null,
        parseHTML: element => element.getAttribute('data-citation'),
        renderHTML: attributes => ({ 'data-citation': attributes.id as string }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'sup[data-citation]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['sup', mergeAttributes({ class: 'citation' }, HTMLAttributes), `[${node.attrs.id as string}]`]
  },

  renderText({ node }) {
    return `[^${node.attrs.id as string}]`
  },

  markdownTokenName: 'citation',

  parseMarkdown: (token, helpers) =>
    helpers.createNode('citation', { id: (token as unknown as CitationToken).label }),

  renderMarkdown: (node: JSONContent) => `[^${citationLabel(node.attrs?.id)}]`,

  markdownTokenizer: {
    name: 'citation',
    level: 'inline',
    // marked calls `start` on the rest of the paragraph before every text
    // step, only to clip the text tokenizer, and that tokenizer already stops
    // before each `[`. So only the first `[` matters: scanning the whole rest
    // for `[^` made text with many `[` quadratic (64 KB of `[` took 18 s to
    // load; 512 KB now takes ~0.3 s).
    start: (src: string) => {
      const index = src.indexOf('[')
      return index !== -1 && src[index + 1] === '^' ? index : -1
    },
    tokenize: src => {
      const match = REFERENCE.exec(src)
      if (!match) {
        return undefined
      }

      if (INLINE_LINK_TAIL.test(src.slice(match[0].length))) {
        return undefined
      }

      return { type: 'citation', raw: match[0], label: match[1] }
    },
  },
})

export const FootnoteDefinition = Node.create({
  name: 'footnoteDefinition',

  content: 'text*',

  defining: true,

  addAttributes() {
    return {
      id: {
        default: null,
        parseHTML: element => element.getAttribute('data-footnote'),
        renderHTML: attributes => ({ 'data-footnote': attributes.id as string }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'li[data-footnote]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['li', mergeAttributes(HTMLAttributes), 0]
  },

  markdownTokenName: 'footnoteDefinition',

  parseMarkdown: (token, helpers) => {
    const definition = token as unknown as FootnoteDefinitionToken
    return helpers.createNode(
      'footnoteDefinition',
      { id: definition.label },
      helpers.parseInline((definition.tokens ?? []) as Parameters<typeof helpers.parseInline>[0]),
    )
  },

  renderMarkdown: (node: JSONContent, helpers) => {
    const text = helpers.renderChildren(node.content ?? [])
    const label = `[^${citationLabel(node.attrs?.id)}]:`
    return text === '' ? label : `${label} ${text}`
  },

  markdownTokenizer: {
    name: 'footnoteDefinition',
    level: 'block',
    start: (src: string) => {
      const match = DEFINITION_AFTER_NEWLINE.exec(src)
      return match ? match.index + 1 : -1
    },
    tokenize: (src, _tokens, lexer) => {
      const first = DEFINITION_FIRST_LINE.exec(src)
      if (!first) {
        return undefined
      }

      const lines = [first[2]]
      const firstLineEmpty = first[2].trim() === ''
      let consumed = first[0].length

      // Lazy continuation lines belong to the definition's paragraph. When the
      // first line is empty, markdown-it only takes lines indented 4+ spaces.
      while (consumed < src.length) {
        const lineBreak = src.indexOf('\n', consumed)
        const line = lineBreak === -1 ? src.slice(consumed) : src.slice(consumed, lineBreak)
        if (line.trim() === '') {
          break
        }
        if (firstLineEmpty && lines.length === 1 && !/^ {4}/.test(line)) {
          break
        }
        if (!(lexer.isParagraphContinuation?.(line) ?? true)) {
          break
        }
        lines.push(line.replace(/^[ \t]+/, ''))
        consumed = lineBreak === -1 ? src.length : lineBreak + 1
      }

      const text = lines.join('\n').trim()

      return {
        type: 'footnoteDefinition',
        raw: src.slice(0, consumed),
        label: first[1],
        text,
        tokens: text === '' ? [] : lexer.inlineTokens(text),
      }
    },
  },
})

export const Footnotes = Node.create({
  name: 'footnotes',

  content: 'footnoteDefinition+',

  isolating: true,

  defining: true,

  parseHTML() {
    return [{ tag: 'section[data-footnotes]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['section', mergeAttributes({ class: 'footnotes', 'data-footnotes': '' }, HTMLAttributes), ['ol', 0]]
  },

  renderMarkdown: (node: JSONContent, helpers) => helpers.renderChildren(node.content ?? [], '\n'),
})
