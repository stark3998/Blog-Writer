import type { Node as ProseMirrorNode } from '@tiptap/pm/model';

/**
 * Facts about the document in the editor: words, reading time and the outline. Words are
 * counted the way the published page counts them (`domain.compose.word_stats`): prose only,
 * outside code blocks, diagrams and the sources list, at 230 words a minute.
 */

export const WORDS_PER_MINUTE = 230;

const SKIPPED_BLOCKS: ReadonlySet<string> = new Set(['codeBlock', 'mermaid', 'footnotes']);

function leafText(leaf: ProseMirrorNode): string {
  switch (leaf.type.name) {
    case 'image':
      return typeof leaf.attrs.alt === 'string' ? leaf.attrs.alt : '';
    case 'hardBreak':
      return ' ';
    default:
      return '';
  }
}

export function countWords(doc: ProseMirrorNode): number {
  let words = 0;
  doc.descendants((node) => {
    if (SKIPPED_BLOCKS.has(node.type.name)) return false;
    if (node.isTextblock) {
      const text = node.textBetween(0, node.content.size, ' ', leafText);
      words += text.split(/\s+/).filter((word) => word !== '').length;
      return false;
    }
    return true;
  });
  return words;
}

export function readingMinutes(words: number): number {
  return words === 0 ? 0 : Math.max(1, Math.ceil(words / WORDS_PER_MINUTE));
}

export interface OutlineEntry {
  readonly level: number;
  readonly text: string;
  /** The heading's position in the document. */
  readonly pos: number;
}

/** The document's headings, in order (the dialect keeps them at the top level). */
export function outlineOf(doc: ProseMirrorNode): OutlineEntry[] {
  const entries: OutlineEntry[] = [];
  doc.forEach((node, offset) => {
    if (node.type.name !== 'heading') return;
    const level = typeof node.attrs.level === 'number' ? node.attrs.level : 2;
    entries.push({ level, text: node.textContent.trim(), pos: offset });
  });
  return entries;
}

export function sameOutline(a: readonly OutlineEntry[], b: readonly OutlineEntry[] | null): boolean {
  if (a.length !== b?.length) return false;
  return a.every((entry, index) => {
    const other = b[index];
    return other?.level === entry.level && other.text === entry.text && other.pos === entry.pos;
  });
}

/** The citation ids the document uses, in order of first use. */
export function citationIds(doc: ProseMirrorNode): string[] {
  const ids: string[] = [];
  doc.descendants((node) => {
    if (node.type.name === 'citation' && typeof node.attrs.id === 'string' && !ids.includes(node.attrs.id)) {
      ids.push(node.attrs.id);
    }
    return true;
  });
  return ids;
}
