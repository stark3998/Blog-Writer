import type { Node as ProseMirrorNode } from '@tiptap/pm/model';

/**
 * Findings point at character offsets of the Markdown they were made from (the body the gates
 * or the live checks read). The editor holds a document, not Markdown, so a span is found by
 * the text a reader sees: the snippet's visible text, matched in the document's text blocks,
 * preferring the occurrence whose surroundings match and whose place in the article matches.
 */

export interface DocRange {
  readonly from: number;
  readonly to: number;
}

/** One stand-in character for every inline leaf (image, citation, hard break): offsets stay positions. */
const LEAF = '\uFFFC';

/** Strips Markdown syntax from a snippet, leaving the text a reader sees. */
export function visibleText(markdown: string): string {
  return markdown
    .replace(/\[\^[^\]\s]+\](?::[ \t]*)?/g, '')
    .replace(/!\[([^\]]*)\]\((?:[^()\s]|\([^()]*\))*(?:\s+"[^"]*")?\)/g, '$1')
    .replace(/\[([^\]]*)\]\((?:[^()\s]|\([^()]*\))*(?:\s+"[^"]*")?\)/g, '$1')
    .replace(/^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+(?:\[[ xX]\][ \t]+)?|\d+[.)][ \t]+|:{3,}\w*)/gm, '')
    .replace(/^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$/gm, ' ')
    .replace(/\\([\\`*_{}[\]()#+\-.!|<>&~:])/g, '$1')
    .replace(/[*`]/g, '')
    .replace(/\|/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

interface TextBlock {
  /** Position of the block node. Its text starts at `pos + 1`. */
  readonly pos: number;
  readonly text: string;
}

function textBlocks(doc: ProseMirrorNode): TextBlock[] {
  const blocks: TextBlock[] = [];
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true;
    blocks.push({ pos, text: node.textBetween(0, node.content.size, undefined, () => LEAF) });
    return false;
  });
  return blocks;
}

function commonSuffix(a: string, b: string): number {
  let length = 0;
  while (length < a.length && length < b.length && a[a.length - 1 - length] === b[b.length - 1 - length]) {
    length += 1;
  }
  return length;
}

function squash(text: string): string {
  return text.replace(/\s+/g, ' ');
}

/** Every place `needle` occurs in the document's text blocks. */
function occurrences(blocks: readonly TextBlock[], needle: string): { block: TextBlock; index: number }[] {
  const found: { block: TextBlock; index: number }[] = [];
  if (needle === '') return found;
  for (const block of blocks) {
    let index = block.text.indexOf(needle);
    while (index !== -1) {
      found.push({ block, index });
      index = block.text.indexOf(needle, index + 1);
    }
  }
  return found;
}

/**
 * Where `source.slice(start, end)` lies in `doc`, or `null` when the document has no text
 * blocks at all. When the snippet's text cannot be found, the text block at the same place in
 * the article is returned instead, so "Show in text" always lands somewhere sensible.
 */
export function locateSpan(doc: ProseMirrorNode, source: string, start: number, end: number): DocRange | null {
  const blocks = textBlocks(doc);
  if (blocks.length === 0) return null;
  const from = Math.max(0, Math.min(start, source.length));
  const to = Math.max(from, Math.min(end, source.length));
  const size = Math.max(1, doc.content.size);
  const approx = (from / Math.max(1, source.length)) * size;

  const visible = visibleText(source.slice(from, to));
  const context = visibleText(source.slice(Math.max(0, from - 120), from)).slice(-32);
  for (const needle of [visible, visible.slice(0, 40), visible.split(' ').slice(0, 3).join(' ')]) {
    if (needle.length < 2 && visible.length >= 2) continue;
    const candidates = occurrences(blocks, needle);
    let best: { range: DocRange; score: number } | undefined;
    for (const { block, index } of candidates) {
      const position = block.pos + 1 + index;
      const before = squash(block.text.slice(0, index)).replace(new RegExp(LEAF, 'g'), ' ');
      const score = commonSuffix(squash(context), before.trimEnd()) * 1000 - Math.abs(position - approx);
      if (!best || score > best.score) best = { range: { from: position, to: position + needle.length }, score };
    }
    if (best) return best.range;
  }

  // The block at the same place in the article.
  let nearest = blocks[0];
  for (const block of blocks) {
    if (block.pos <= approx) nearest = block;
    else break;
  }
  if (!nearest) return null;
  const node = doc.nodeAt(nearest.pos);
  const length = node ? node.content.size : 0;
  return { from: nearest.pos + 1, to: nearest.pos + 1 + length };
}
