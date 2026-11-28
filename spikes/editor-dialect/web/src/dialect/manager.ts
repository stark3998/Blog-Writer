/**
 * MarkdownManager for the article dialect.
 *
 * Wraps `@tiptap/markdown`'s MarkdownManager with:
 * - a private marked instance (raw HTML, bare URLs and `~~` disabled,
 *   full entity decoding; see marked.ts);
 * - JSON normalization after parsing and before serializing (transforms.ts);
 * - context-aware text escaping (escape.ts) in place of TipTap's
 *   "encode `& < >` as entities, escape every `*_[]~`" approach;
 * - inline-code edge whitespace preservation (see JS_WHITESPACE in constants.ts).
 */
import type { AnyExtension, JSONContent } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'
import type { marked } from 'marked'

import { JS_WHITESPACE, WHITESPACE_PLACEHOLDER_BASE } from './constants.js'
import { bracketsAsReferences, escapeInlineText } from './escape.js'
import { createDialectMarked } from './marked.js'
import { normalizeParsedDocument, prepareForSerialization } from './transforms.js'

type EncodeText = (text: string, node: JSONContent, parentNode?: JSONContent) => string

const CODE_PARENT_TYPES = new Set(['codeBlock', 'mermaid'])

export class DialectMarkdownManager extends MarkdownManager {
  constructor(options: { extensions: AnyExtension[] }) {
    super({
      // A Marked instance has the same surface TipTap uses (Lexer, defaults, use, lexer, setOptions).
      marked: createDialectMarked() as unknown as typeof marked,
      indentation: { style: 'space', size: 2 },
      extensions: options.extensions,
    })

    // `encodeTextForMarkdown` is private in the TypeScript declaration, so it
    // cannot be overridden with `override`; replace it on the instance.
    const target = this as unknown as { encodeTextForMarkdown: EncodeText }
    target.encodeTextForMarkdown = encodeDialectText
  }

  override parse(markdown: string): JSONContent {
    return normalizeParsedDocument(super.parse(normalizeSource(markdown)))
  }

  override serialize(doc: JSONContent): string {
    const markdown = restoreCodeWhitespace(super.serialize(prepareForSerialization(doc)))
    const body = markdown.replace(/\n+$/, '')
    return body === '' ? '' : `${body}\n`
  }
}

/** CRLF/CR to LF and no byte-order mark: the dialect is UTF-8 with `\n` line endings. */
export function normalizeSource(source: string): string {
  return source.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n')
}

export function encodeDialectText(text: string, node: JSONContent, parentNode?: JSONContent): string {
  if (parentNode?.type && CODE_PARENT_TYPES.has(parentNode.type)) {
    return text
  }

  if (node.marks?.some(mark => mark.type === 'code')) {
    // A line ending would end the Markdown line mid-span (and `# `, `- `, ... on
    // the next line would start a block); inside a code span it reads as a space.
    const oneLine = text.replace(/\r\n?|\n/g, ' ')
    // Keep edge whitespace inside the backticks; DialectCode adds the padding CommonMark strips.
    return oneLine.replace(/^\s+|\s+$/g, run => Array.from(run, protectWhitespace).join(''))
  }

  const siblings = parentNode?.content ?? []
  const index = siblings.indexOf(node)
  const previous = index > 0 ? siblings[index - 1] : undefined
  // markdown-it parses a footnote definition's text as block content, so it
  // starts a line just like a paragraph does.
  const startsLines = parentNode?.type === 'paragraph' || parentNode?.type === 'footnoteDefinition'
  const textblock = startsLines || parentNode?.type === 'heading'
  const blockStart = textblock && index <= 0
  const blockEnd = textblock && index === siblings.length - 1
  const lineStart = startsLines && (index <= 0 || previous?.type === 'hardBreak')
  const headingEnd = parentNode?.type === 'heading' && index === siblings.length - 1

  const escaped = escapeInlineText(text, { lineStart, headingEnd, blockStart, blockEnd })
  return node.marks?.some(mark => mark.type === 'link') ? bracketsAsReferences(escaped) : escaped
}

const PLACEHOLDER_PATTERN = new RegExp(
  `[${String.fromCharCode(WHITESPACE_PLACEHOLDER_BASE)}-${String.fromCharCode(WHITESPACE_PLACEHOLDER_BASE + JS_WHITESPACE.length - 1)}]`,
  'g',
)

function protectWhitespace(ch: string): string {
  const index = (JS_WHITESPACE as readonly string[]).indexOf(ch)
  return index === -1 ? ch : String.fromCharCode(WHITESPACE_PLACEHOLDER_BASE + index)
}

function restoreCodeWhitespace(markdown: string): string {
  return markdown.replace(PLACEHOLDER_PATTERN, ch => JS_WHITESPACE[ch.charCodeAt(0) - WHITESPACE_PLACEHOLDER_BASE])
}
