import type { Editor, JSONContent } from '@tiptap/core';
import type { Node as ProseMirrorNode } from '@tiptap/pm/model';
import { NodeSelection, type Transaction } from '@tiptap/pm/state';

import type { DialectService } from '../environment';
import type { DocRange } from '../model/spans';
import type { BriefCitation } from '../model/types';

/**
 * Pieces of Markdown in and out of the editor: the selection as Markdown (for selection AI),
 * and Markdown from outside (an AI proposal, a paste) parsed in the worker and put in place.
 *
 * A fragment parsed on its own would lose its citations: `[^id]` is only a citation when a
 * definition exists. So the article's definitions are appended before parsing and taken off
 * again; definitions the fragment brings are added to the sources list.
 */

export interface ParsedFragment {
  /** Body blocks, in order. */
  readonly blocks: readonly JSONContent[];
  /** Citation definitions (`footnoteDefinition`) the fragment held. */
  readonly definitions: readonly JSONContent[];
}

function footnotesOf(doc: ProseMirrorNode): { node: ProseMirrorNode; pos: number } | undefined {
  let found: { node: ProseMirrorNode; pos: number } | undefined;
  doc.forEach((node, offset) => {
    if (node.type.name === 'footnotes') found = { node, pos: offset };
  });
  return found;
}

/** The article's citation definitions as Markdown lines (`[^id]: …`), or an empty string. */
export function definitionsMarkdown(editor: Editor): string {
  const footnotes = footnotesOf(editor.state.doc);
  const manager = editor.markdown;
  if (!footnotes || !manager) return '';
  return manager.serialize({ type: 'doc', content: [footnotes.node.toJSON() as JSONContent] }).trim();
}

function isEmptyParagraph(node: JSONContent): boolean {
  return node.type === 'paragraph' && (node.content ?? []).length === 0;
}

/**
 * Parses Markdown from outside the editor (in the worker, with its timeout). A leading `---`
 * would read as frontmatter, so such a fragment starts with a blank line.
 */
export async function parseFragment(
  dialect: DialectService,
  markdown: string,
  definitions: string,
  timeoutMs: number,
): Promise<ParsedFragment> {
  const body = /^---[ \t]*(?:\n|$)/.test(markdown) ? `\n${markdown}` : markdown;
  const source = definitions === '' ? body : `${body.trimEnd()}\n\n${definitions}\n`;
  const parsed = await dialect.parse(source, { timeoutMs });
  const blocks: JSONContent[] = [];
  const found: JSONContent[] = [];
  for (const node of parsed.doc.content ?? []) {
    if (node.type === 'footnotes') found.push(...(node.content ?? []));
    else if (!isEmptyParagraph(node)) blocks.push(node);
  }
  return { blocks, definitions: found };
}

function definitionId(node: JSONContent | ProseMirrorNode): string {
  const attrs = 'attrs' in node ? (node.attrs as Record<string, unknown> | undefined) : undefined;
  const id = attrs?.id;
  return typeof id === 'string' ? id : '';
}

/**
 * Adds the definitions the document does not have yet to its sources list (creating the list
 * at the very end when there is none). Runs inside a command, on the transaction's document.
 */
export function mergeDefinitions(tr: Transaction, definitions: readonly JSONContent[]): boolean {
  if (definitions.length === 0) return true;
  const { schema } = tr.doc.type;
  const footnotes = footnotesOf(tr.doc);
  const known = new Set<string>();
  footnotes?.node.forEach((child) => known.add(definitionId(child)));
  const missing = definitions.filter((definition) => {
    const id = definitionId(definition);
    if (id === '' || known.has(id)) return false;
    known.add(id);
    return true;
  });
  if (missing.length === 0) return true;
  const nodes = missing.map((definition) => schema.nodeFromJSON(definition));
  const footnotesType = schema.nodes.footnotes;
  if (footnotes) {
    tr.insert(footnotes.pos + footnotes.node.nodeSize - 1, nodes);
  } else if (footnotesType) {
    tr.insert(tr.doc.content.size, footnotesType.create(null, nodes));
  }
  return true;
}

/**
 * Puts a parsed fragment in place of `range`. A single paragraph going into text keeps to the
 * line (its inline content is inserted, the paragraph is not split); anything else goes in as
 * blocks. One transaction, so one undo step.
 */
export function insertFragment(editor: Editor, range: DocRange, fragment: ParsedFragment, inline: boolean): boolean {
  const [first] = fragment.blocks;
  const asInline = inline && fragment.blocks.length === 1 && first?.type === 'paragraph';
  const content = asInline ? (first.content ?? []) : [...fragment.blocks];
  return editor
    .chain()
    .focus()
    .insertContentAt(range, content, { updateSelection: true })
    .command(({ tr }) => mergeDefinitions(tr, fragment.definitions))
    .run();
}

/** The selection the AI rewrites: its range, its Markdown and whether it sits inside one line of text. */
export interface CapturedSelection {
  readonly range: DocRange;
  readonly markdown: string;
  readonly inline: boolean;
}

export type CaptureProblem = 'empty' | 'code' | 'sources';

function isCodeBlock(node: ProseMirrorNode): boolean {
  return node.type.spec.code === true;
}

/**
 * The current selection as Markdown. A selection inside one paragraph or heading is sent as
 * it is; one that spans blocks is widened to whole top-level blocks (the proposal replaces
 * those blocks). Code, diagrams and the sources list are not rewritten.
 */
export function captureSelection(editor: Editor): CapturedSelection | CaptureProblem {
  const { state } = editor;
  const { selection } = state;
  const manager = editor.markdown;
  if (selection.empty || !manager) return 'empty';
  const { $from, $to } = selection;

  if (!(selection instanceof NodeSelection) && $from.sameParent($to) && $from.parent.isTextblock) {
    if (isCodeBlock($from.parent)) return 'code';
    if ($from.node(1).type.name === 'footnotes') return 'sources';
    // Within one text block the slice is the selected inline nodes themselves.
    const inlineContent = (state.doc.slice(selection.from, selection.to).content.toJSON() ?? []) as JSONContent[];
    const markdown = manager.serialize({ type: 'doc', content: [{ type: 'paragraph', content: inlineContent }] }).trim();
    return markdown === '' ? 'empty' : { range: { from: selection.from, to: selection.to }, markdown, inline: true };
  }

  const start = $from.depth === 0 ? selection.from : $from.before(1);
  const end = $to.depth === 0 ? selection.to : $to.after(1);
  const blocks: ProseMirrorNode[] = [];
  state.doc.nodesBetween(start, end, (node, pos) => {
    if (pos >= start && pos < end) blocks.push(node);
    return false;
  });
  if (blocks.some((node) => node.type.name === 'footnotes')) return 'sources';
  if (blocks.length === 0) return 'empty';
  if (blocks.every(isCodeBlock)) return 'code';
  const markdown = manager.serialize({ type: 'doc', content: blocks.map((node) => node.toJSON() as JSONContent) }).trim();
  return markdown === '' ? 'empty' : { range: { from: start, to: end }, markdown, inline: false };
}

/** A citation definition for a brief source, in the dialect's form: `Publisher. *Title*. [site](url)`. */
export function citationDefinition(citation: BriefCitation): JSONContent {
  const content: JSONContent[] = [];
  const publisher = citation.publisher?.trim();
  if (publisher) content.push({ type: 'text', text: `${publisher}. ` });
  content.push({ type: 'text', text: citation.title.trim() || citation.domain, marks: [{ type: 'italic' }] });
  content.push({ type: 'text', text: '. ' });
  content.push({ type: 'text', text: citation.domain, marks: [{ type: 'link', attrs: { href: citation.url } }] });
  return { type: 'footnoteDefinition', attrs: { id: citation.id }, content };
}

/** Inserts a citation of `citation` at the cursor and adds its definition when the article has none. */
export function insertCitation(editor: Editor, citation: BriefCitation): boolean {
  return editor
    .chain()
    .focus()
    .insertContent({ type: 'citation', attrs: { id: citation.id } })
    .command(({ tr }) => mergeDefinitions(tr, [citationDefinition(citation)]))
    .run();
}
