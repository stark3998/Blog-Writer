import type { Editor, JSONContent } from '@tiptap/core';
import {
  BookMarked,
  Code,
  Heading2,
  Heading3,
  Heading4,
  ImagePlus,
  Info,
  Lightbulb,
  List,
  ListChecks,
  ListOrdered,
  Minus,
  Pilcrow,
  Table,
  TextQuote,
  TriangleAlert,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

import type { CalloutType } from '@/features/editor';

/**
 * The blocks the owner can insert, shared by the slash menu and the toolbar's Insert menu.
 * Headings and callouts are top-level only in the dialect, so they are offered only there.
 */

export interface BlockCommandContext {
  readonly openImageDialog: () => void;
  readonly openCitationPicker: () => void;
}

export interface BlockCommand {
  readonly id: string;
  readonly label: string;
  /** What it inserts, in a few words. */
  readonly hint: string;
  readonly keywords: readonly string[];
  readonly icon: LucideIcon;
  /** Only in a top-level paragraph (headings, callouts). */
  readonly topLevelOnly?: boolean;
  readonly run: (editor: Editor, context: BlockCommandContext) => void;
}

/** A starting diagram (an empty Mermaid fence is a lint error). */
export const STARTER_DIAGRAM = 'flowchart LR\n  idea[Idea] --> draft[Draft]';

/**
 * Puts a block where the owner expects it: in place of an empty line (the slash menu leaves
 * one), else after the current block (`topLevel`: after the current top-level block), so text
 * is never split around it.
 */
export function insertBlock(editor: Editor, content: JSONContent, { topLevel = false }: { topLevel?: boolean } = {}): boolean {
  const { $from } = editor.state.selection;
  const parent = $from.parent;
  if (parent.isTextblock && parent.content.size === 0 && (!topLevel || $from.depth <= 1)) {
    return editor.chain().focus().insertContent(content).run();
  }
  const depth = topLevel ? Math.min(1, $from.depth) : $from.depth;
  const position = depth === 0 ? editor.state.selection.to : $from.after(depth);
  return editor.chain().focus().insertContentAt(position, content).run();
}

function insertCallout(type: CalloutType) {
  return (editor: Editor) => {
    insertBlock(editor, { type: 'callout', attrs: { type }, content: [{ type: 'paragraph' }] }, { topLevel: true });
  };
}

export const BLOCK_COMMANDS: readonly BlockCommand[] = [
  {
    id: 'paragraph',
    label: 'Text',
    hint: 'A plain paragraph',
    keywords: ['paragraph', 'plain', 'body'],
    icon: Pilcrow,
    run: (editor) => editor.chain().focus().setParagraph().run(),
  },
  {
    id: 'heading-2',
    label: 'Section heading',
    hint: 'Heading level 2',
    keywords: ['h2', 'heading', 'section', 'title'],
    icon: Heading2,
    topLevelOnly: true,
    run: (editor) => editor.chain().focus().setHeading({ level: 2 }).run(),
  },
  {
    id: 'heading-3',
    label: 'Subheading',
    hint: 'Heading level 3',
    keywords: ['h3', 'heading', 'subheading'],
    icon: Heading3,
    topLevelOnly: true,
    run: (editor) => editor.chain().focus().setHeading({ level: 3 }).run(),
  },
  {
    id: 'heading-4',
    label: 'Minor heading',
    hint: 'Heading level 4',
    keywords: ['h4', 'heading'],
    icon: Heading4,
    topLevelOnly: true,
    run: (editor) => editor.chain().focus().setHeading({ level: 4 }).run(),
  },
  {
    id: 'bullet-list',
    label: 'Bulleted list',
    hint: 'A list with bullets',
    keywords: ['ul', 'bullets', 'unordered'],
    icon: List,
    run: (editor) => editor.chain().focus().toggleBulletList().run(),
  },
  {
    id: 'ordered-list',
    label: 'Numbered list',
    hint: 'A list with numbers',
    keywords: ['ol', 'numbers', 'ordered', 'steps'],
    icon: ListOrdered,
    run: (editor) => editor.chain().focus().toggleOrderedList().run(),
  },
  {
    id: 'checklist',
    label: 'Checklist',
    hint: 'A list of tasks to tick',
    keywords: ['tasks', 'todo', 'checkbox'],
    icon: ListChecks,
    run: (editor) => editor.chain().focus().toggleTaskList().run(),
  },
  {
    id: 'quote',
    label: 'Quote',
    hint: 'A block quotation',
    keywords: ['blockquote', 'citation'],
    icon: TextQuote,
    run: (editor) => editor.chain().focus().toggleBlockquote().run(),
  },
  {
    id: 'code',
    label: 'Code block',
    hint: 'Code with highlighting',
    keywords: ['code', 'snippet', 'pre'],
    icon: Code,
    run: (editor) => editor.chain().focus().toggleCodeBlock().run(),
  },
  {
    id: 'diagram',
    label: 'Diagram',
    hint: 'Mermaid, rendered by the server',
    keywords: ['mermaid', 'flowchart', 'chart', 'sequence'],
    icon: Workflow,
    run: (editor) => insertBlock(editor, { type: 'mermaid', content: [{ type: 'text', text: STARTER_DIAGRAM }] }),
  },
  {
    id: 'callout-note',
    label: 'Note',
    hint: 'A note callout',
    keywords: ['callout', 'aside', 'info'],
    icon: Info,
    topLevelOnly: true,
    run: insertCallout('note'),
  },
  {
    id: 'callout-tip',
    label: 'Tip',
    hint: 'A tip callout',
    keywords: ['callout', 'hint', 'advice'],
    icon: Lightbulb,
    topLevelOnly: true,
    run: insertCallout('tip'),
  },
  {
    id: 'callout-warning',
    label: 'Warning',
    hint: 'A warning callout',
    keywords: ['callout', 'caution', 'danger'],
    icon: TriangleAlert,
    topLevelOnly: true,
    run: insertCallout('warning'),
  },
  {
    id: 'table',
    label: 'Table',
    hint: 'Three columns with a header row',
    keywords: ['grid', 'columns', 'rows'],
    icon: Table,
    run: (editor) => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
  },
  {
    id: 'divider',
    label: 'Divider',
    hint: 'A thematic break',
    keywords: ['rule', 'hr', 'separator', 'line'],
    icon: Minus,
    run: (editor) => editor.chain().focus().setHorizontalRule().run(),
  },
  {
    id: 'image',
    label: 'Image',
    hint: 'Upload a PNG, JPEG or WebP',
    keywords: ['picture', 'photo', 'screenshot', 'upload'],
    icon: ImagePlus,
    run: (_editor, context) => {
      context.openImageDialog();
    },
  },
  {
    id: 'citation',
    label: 'Citation',
    hint: 'Cite a source from the brief',
    keywords: ['source', 'reference', 'footnote', 'cite'],
    icon: BookMarked,
    run: (_editor, context) => {
      context.openCitationPicker();
    },
  },
];

/** True when the cursor sits in a top-level text block (where headings and callouts may go). */
export function atTopLevel(editor: Editor): boolean {
  return editor.state.selection.$from.depth <= 1;
}

/** True when the cursor is in a code block or a diagram source, where blocks cannot go. */
export function inCode(editor: Editor): boolean {
  return editor.state.selection.$from.parent.type.spec.code === true;
}

/** True when the cursor is in the sources list (citation definitions hold one line of text). */
export function inSources(editor: Editor): boolean {
  const { $from } = editor.state.selection;
  return $from.depth >= 1 && $from.node(1).type.name === 'footnotes';
}

export function commandAvailable(command: BlockCommand, editor: Editor): boolean {
  if (!editor.isEditable || inCode(editor) || inSources(editor)) return false;
  return !command.topLevelOnly || atTopLevel(editor);
}

/** Commands matching what was typed after the slash, best matches first. */
export function filterCommands(commands: readonly BlockCommand[], query: string): BlockCommand[] {
  const needle = query.trim().toLowerCase();
  if (needle === '') return [...commands];
  const scored = commands.flatMap((command) => {
    const label = command.label.toLowerCase();
    if (label.startsWith(needle)) return [{ command, score: 0 }];
    if (label.includes(needle)) return [{ command, score: 1 }];
    if (command.keywords.some((keyword) => keyword.startsWith(needle))) return [{ command, score: 2 }];
    if (command.id.includes(needle)) return [{ command, score: 3 }];
    return [];
  });
  return scored.sort((a, b) => a.score - b.score).map(({ command }) => command);
}
