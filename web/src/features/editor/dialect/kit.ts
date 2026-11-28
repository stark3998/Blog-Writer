/**
 * The article editor's extension set: StarterKit minus everything the dialect
 * replaces or forbids, plus the dialect nodes and the dialect Markdown codec.
 *
 * MIT packages only: @tiptap/core, starter-kit, extension-*, markdown.
 */
import type { AnyExtension } from '@tiptap/core';
import { TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { StarterKit } from '@tiptap/starter-kit';
import type { createLowlight } from 'lowlight';

import { Callout } from './extensions/callout';
import { Citation, FootnoteDefinition, Footnotes } from './extensions/citation';
import { Mermaid } from './extensions/mermaid';
import {
  DialectBulletList,
  DialectCode,
  DialectCodeBlock,
  DialectCodeBlockLowlight,
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
} from './extensions/overrides';
import { DialectMarkdown } from './markdown-extension';

/** A lowlight instance (`createLowlight(common)` from `lowlight`). */
export type Lowlight = ReturnType<typeof createLowlight>;

export interface ArticleExtensionsOptions {
  /**
   * Syntax highlighting for code blocks in the visible editor. Omit it for
   * headless use (the codec, the normalize worker): the Markdown is the same
   * either way, and highlight.js grammars stay out of those bundles.
   */
  lowlight?: Lowlight;
}

export function createArticleExtensions(options: ArticleExtensionsOptions = {}): AnyExtension[] {
  const codeBlock = options.lowlight
    ? DialectCodeBlockLowlight.configure({ lowlight: options.lowlight })
    : DialectCodeBlock;

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
    codeBlock,
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
  ];
}
