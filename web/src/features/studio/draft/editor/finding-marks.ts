import { Extension, type Editor } from '@tiptap/core';
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

import type { DocRange } from '../model/spans';
import type { Severity } from '../model/types';

import { FINDING_MARK_CLASS, FLASH_CLASS } from './prose';

/**
 * Proof marks in the text: the live checks' findings underlined where they are, and a
 * highlighter flash on the span the owner jumped to from a finding. Both are decorations: they
 * never change the document, follow it as it is edited, and never reach the Markdown.
 */

export interface MarkedFinding extends DocRange {
  readonly severity: Severity;
  readonly id: string;
}

const marksKey = new PluginKey<DecorationSet>('draftFindingMarks');
const flashKey = new PluginKey<DecorationSet>('draftFlash');

interface MarksMeta {
  readonly findings: readonly MarkedFinding[];
}

interface FlashMeta {
  readonly range: DocRange | null;
}

function clampRange(range: DocRange, size: number): DocRange | null {
  const from = Math.max(0, Math.min(range.from, size));
  const to = Math.max(from, Math.min(range.to, size));
  if (to > from) return { from, to };
  // An empty span still gets one character, so it can be seen.
  return from < size ? { from, to: from + 1 } : null;
}

function decorationsFor(doc: Parameters<typeof DecorationSet.create>[0], findings: readonly MarkedFinding[]): DecorationSet {
  const size = doc.content.size;
  const decorations = findings.flatMap((finding) => {
    const range = clampRange(finding, size);
    return range
      ? [Decoration.inline(range.from, range.to, { class: FINDING_MARK_CLASS[finding.severity], 'data-finding': finding.id })]
      : [];
  });
  return DecorationSet.create(doc, decorations);
}

export const FindingMarks = Extension.create({
  name: 'draftFindingMarks',

  addProseMirrorPlugins() {
    return [
      new Plugin<DecorationSet>({
        key: marksKey,
        state: {
          init: () => DecorationSet.empty,
          apply(tr, set) {
            const meta = tr.getMeta(marksKey) as MarksMeta | undefined;
            if (meta) return decorationsFor(tr.doc, meta.findings);
            return tr.docChanged ? set.map(tr.mapping, tr.doc) : set;
          },
        },
        props: {
          decorations: (state) => marksKey.getState(state),
        },
      }),
      new Plugin<DecorationSet>({
        key: flashKey,
        state: {
          init: () => DecorationSet.empty,
          apply(tr, set) {
            const meta = tr.getMeta(flashKey) as FlashMeta | undefined;
            if (meta) {
              const range = meta.range ? clampRange(meta.range, tr.doc.content.size) : null;
              return range
                ? DecorationSet.create(tr.doc, [Decoration.inline(range.from, range.to, { class: FLASH_CLASS })])
                : DecorationSet.empty;
            }
            // Typing ends the flash: it marked where to look, not something to keep.
            return tr.docChanged ? DecorationSet.empty : set;
          },
        },
        props: {
          decorations: (state) => flashKey.getState(state),
        },
      }),
    ];
  },
});

/** Replaces the underlined findings (an empty list clears them). */
export function setFindingMarks(editor: Editor, findings: readonly MarkedFinding[]): void {
  if (editor.isDestroyed) return;
  const meta: MarksMeta = { findings };
  editor.view.dispatch(editor.state.tr.setMeta(marksKey, meta).setMeta('addToHistory', false));
}

const FLASH_MS = 2600;

/**
 * Selects `range`, scrolls it into view, marks it like a highlighter for a moment and moves
 * focus into the text, so keyboard and screen-reader users land on it too.
 */
export function revealRange(editor: Editor, range: DocRange): void {
  if (editor.isDestroyed) return;
  const size = editor.state.doc.content.size;
  const target = clampRange(range, size);
  if (!target) return;
  const flash: FlashMeta = { range: target };
  const tr = editor.state.tr
    .setSelection(TextSelection.create(editor.state.doc, target.from, target.to))
    .setMeta(flashKey, flash)
    .setMeta('addToHistory', false)
    .scrollIntoView();
  editor.view.dispatch(tr);
  if (editor.isEditable) editor.view.focus();
  // ProseMirror scrolls only a focused view; a read-only one is scrolled here.
  try {
    const { node } = editor.view.domAtPos(target.from);
    const element = node instanceof Element ? node : node.parentElement;
    element?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  } catch {
    // No layout (a detached view): nothing to scroll.
  }
  window.setTimeout(() => {
    if (editor.isDestroyed) return;
    const clear: FlashMeta = { range: null };
    editor.view.dispatch(editor.state.tr.setMeta(flashKey, clear).setMeta('addToHistory', false));
  }, FLASH_MS);
}
