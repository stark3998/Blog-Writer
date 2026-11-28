/**
 * The article editor's extension set: StarterKit minus everything the dialect
 * replaces or forbids, plus the dialect nodes and the dialect Markdown codec.
 *
 * MIT packages only: @tiptap/core, starter-kit, extension-*, markdown.
 */
import type { AnyExtension } from '@tiptap/core'
import { TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import { StarterKit } from '@tiptap/starter-kit'

import { DialectMarkdown } from './dialect/extension.js'
import { Callout } from './extensions/callout.js'
import { Citation, FootnoteDefinition, Footnotes } from './extensions/citation.js'
import { Mermaid } from './extensions/mermaid.js'
import {
  DialectBulletList,
  DialectCode,
  DialectCodeBlock,
  DialectDocument,
  DialectHardBreak,
  DialectHeading,
  DialectImage,
  DialectLink,
  DialectListItem,
  DialectOrderedList,
  DialectParagraph,
  DialectTable,
  DialectTaskItem,
  DialectTaskList,
  DialectText,
} from './extensions/overrides.js'

export function articleExtensions(): AnyExtension[] {
  return [
    StarterKit.configure({
      // Replaced by dialect overrides.
      document: false,
      text: false,
      paragraph: false,
      heading: false,
      codeBlock: false,
      code: false,
      hardBreak: false,
      link: false,
      bulletList: false,
      orderedList: false,
      listItem: false,
      // Not part of the dialect.
      strike: false,
      underline: false,
      // The footnotes section is always last; never append a paragraph after it.
      trailingNode: { notAfter: ['footnotes'] },
    }),
    DialectDocument,
    DialectText,
    DialectParagraph,
    DialectHeading,
    DialectCodeBlock,
    DialectCode,
    DialectHardBreak,
    DialectBulletList,
    DialectOrderedList,
    DialectListItem,
    DialectLink.configure({ openOnClick: false, autolink: false, linkOnPaste: true }),
    DialectImage.configure({ inline: true, allowBase64: false }),
    DialectTable.configure({ resizable: false }),
    TableRow,
    TableHeader,
    TableCell,
    DialectTaskList,
    DialectTaskItem.configure({ nested: true }),
    Callout,
    Mermaid,
    Citation,
    Footnotes,
    FootnoteDefinition,
    DialectMarkdown,
  ]
}
