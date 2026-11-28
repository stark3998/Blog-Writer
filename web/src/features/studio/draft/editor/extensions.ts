import { Extension, type AnyExtension } from '@tiptap/core';
import { Placeholder, Selection as SelectionHighlight } from '@tiptap/extensions';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { ReactNodeViewRenderer } from '@tiptap/react';

import { Callout, Citation, createArticleExtensions, DialectError, Mermaid } from '@/features/editor';

import type { DialectService } from '../environment';

import { draftLowlight, type EditorBus, type SlashKeys } from './bus';
import { FindingMarks } from './finding-marks';
import { definitionsMarkdown, insertFragment, parseFragment } from './fragments';
import { CalloutNodeView, CitationNodeView, MermaidNodeView } from './node-views';
import { SlashCommand } from './slash';

/**
 * The Draft tab's editor: the article dialect (`@/features/editor`, unchanged: same schema and
 * Markdown) with the tab's views and behaviour on top: React views for diagrams, callouts and
 * citations, the slash menu, proof marks, Markdown pastes through the worker and shortcuts.
 */

export interface MermaidViewOptions {
  /** Ask the server to render the diagram (off for the live pipeline text, which changes every moment). */
  renderDiagrams: boolean;
}

const MermaidWithView = Mermaid.extend<MermaidViewOptions>({
  addOptions() {
    return { renderDiagrams: true };
  },
  addNodeView() {
    return ReactNodeViewRenderer(MermaidNodeView);
  },
});

const CalloutWithView = Callout.extend({
  addNodeView() {
    return ReactNodeViewRenderer(CalloutNodeView);
  },
});

const CitationWithView = Citation.extend({
  addNodeView() {
    return ReactNodeViewRenderer(CitationNodeView, { as: 'span' });
  },
});

/** Mod-K opens the link field, Mod-Shift-E the selection AI. */
const DraftShortcuts = Extension.create<{ bus: EditorBus | null }>({
  name: 'draftShortcuts',
  addOptions() {
    return { bus: null };
  },
  addKeyboardShortcuts() {
    const bus = this.options.bus;
    return {
      'Mod-k': () => {
        if (!this.editor.isEditable || !bus) return false;
        bus.emit('link', undefined);
        return true;
      },
      'Mod-Shift-e': () => {
        if (!this.editor.isEditable || !bus) return false;
        bus.emit('ai', undefined);
        return true;
      },
    };
  },
});

/** Text that reads as Markdown (a heading, a list, a fence, emphasis, a link or a citation). */
export function looksLikeMarkdown(text: string): boolean {
  if (/^(?:#{1,6}[ \t]|[-*+][ \t]|\d{1,9}[.)][ \t]|>[ \t]?|```|~~~|:::|\|.*\|[ \t]*$)/m.test(text)) return true;
  return /\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^)\s]+\)|`[^`\n]+`|\[\^[a-z0-9-]+\]/i.test(text);
}

interface MarkdownPasteOptions {
  bus: EditorBus | null;
  dialect: DialectService | null;
  parseTimeoutMs: number;
}

/**
 * Pasted Markdown goes through the worker like any text from outside the editor, then in as
 * blocks (rich HTML pastes are read by the schema as usual; pasted or dropped images open the
 * upload dialog).
 */
const MarkdownPaste = Extension.create<MarkdownPasteOptions>({
  name: 'draftMarkdownPaste',

  addOptions() {
    return { bus: null, dialect: null, parseTimeoutMs: 5000 };
  },

  addProseMirrorPlugins() {
    const { bus, dialect, parseTimeoutMs } = this.options;
    const editor = this.editor;
    const images = (files: FileList | null | undefined): File[] =>
      [...(files ?? [])].filter((file) => file.type.startsWith('image/'));
    return [
      new Plugin({
        key: new PluginKey('draftMarkdownPaste'),
        props: {
          handlePaste(view, event) {
            if (!view.editable || !bus || !dialect) return false;
            const data = event.clipboardData;
            if (!data) return false;
            const files = images(data.files);
            if (files.length > 0) {
              bus.emit('images', files);
              return true;
            }
            if (data.types.includes('text/html')) return false;
            const text = data.getData('text/plain');
            if (view.state.selection.$from.parent.type.spec.code === true || !looksLikeMarkdown(text)) return false;
            const before = view.state.doc;
            const { from, to } = view.state.selection;
            const inline = view.state.selection.$from.parent.isTextblock && view.state.selection.$from.sameParent(view.state.selection.$to);
            void parseFragment(dialect, text, definitionsMarkdown(editor), parseTimeoutMs).then(
              (fragment) => {
                if (editor.isDestroyed || !editor.isEditable) return;
                const range = editor.state.doc.eq(before) ? { from, to } : { from: editor.state.selection.from, to: editor.state.selection.to };
                insertFragment(editor, range, fragment, inline);
              },
              (error: unknown) => {
                bus.emit(
                  'problem',
                  error instanceof DialectError && error.code === 'too-large'
                    ? 'That paste is too large for the editor (over 128 KB).'
                    : 'That paste couldn’t be read as Markdown. Paste it as plain text instead.',
                );
              },
            );
            return true;
          },
          handleDrop(view, event) {
            if (!view.editable || !bus) return false;
            const files = images(event.dataTransfer?.files);
            if (files.length === 0) return false;
            event.preventDefault();
            bus.emit('images', files);
            return true;
          },
        },
      }),
    ];
  },
});

export interface DraftExtensionOptions {
  readonly bus: EditorBus | null;
  readonly slashKeys: SlashKeys | null;
  readonly dialect: DialectService | null;
  readonly parseTimeoutMs: number;
  readonly renderDiagrams: boolean;
}

export function createDraftExtensions(options: DraftExtensionOptions): AnyExtension[] {
  const views: Readonly<Record<string, AnyExtension>> = {
    mermaid: MermaidWithView.configure({ renderDiagrams: options.renderDiagrams }),
    callout: CalloutWithView,
    citation: CitationWithView,
  };
  const article = createArticleExtensions({ lowlight: draftLowlight() }).map((extension) => views[extension.name] ?? extension);
  return [
    ...article,
    Placeholder.configure({
      showOnlyCurrent: true,
      placeholder: ({ editor, node }) => {
        if (editor.isEmpty) return 'Start writing, or type / for headings, lists, diagrams and citations.';
        return node.type.name === 'paragraph' ? 'Type / to add a block' : '';
      },
    }),
    SelectionHighlight.configure({ className: 'bw-selection' }),
    SlashCommand.configure({ keys: options.slashKeys }),
    FindingMarks,
    MarkdownPaste.configure({ bus: options.bus, dialect: options.dialect, parseTimeoutMs: options.parseTimeoutMs }),
    DraftShortcuts.configure({ bus: options.bus }),
  ];
}
