import type { Editor } from '@tiptap/core';
import { createLowlight, common } from 'lowlight';

import { ArticleCodec, type Lowlight } from '@/features/editor';

/**
 * Plumbing between the editor's ProseMirror plugins and the React around them: plugins raise
 * events (a keyboard shortcut, pasted images), React components listen. Created once per editor.
 */

export interface EditorEvents {
  /** Mod-K: open the link field. */
  readonly link: undefined;
  /** Mod-Shift-E: rewrite the selection with AI. */
  readonly ai: undefined;
  /** Images pasted or dropped into the text. */
  readonly images: readonly File[];
  /** A paste the editor could not take, in the owner's words. */
  readonly problem: string;
}

type Listener<T> = (payload: T) => void;

export class EditorBus {
  private readonly listeners = new Map<keyof EditorEvents, Set<Listener<never>>>();

  on<K extends keyof EditorEvents>(type: K, listener: Listener<EditorEvents[K]>): () => void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
    };
  }

  emit<K extends keyof EditorEvents>(type: K, payload: EditorEvents[K]): void {
    for (const listener of this.listeners.get(type) ?? []) {
      (listener as Listener<EditorEvents[K]>)(payload);
    }
  }
}

/**
 * The slash menu's keyboard: while the menu is open, the plugin hands it Arrow, Enter and Tab
 * before ProseMirror sees them.
 */
export class SlashKeys {
  private handler: ((event: KeyboardEvent) => boolean) | null = null;

  setHandler(handler: ((event: KeyboardEvent) => boolean) | null): void {
    this.handler = handler;
  }

  keyDown(event: KeyboardEvent): boolean {
    return this.handler?.(event) ?? false;
  }
}

let lowlight: Lowlight | undefined;

/** One lowlight instance with the common grammars, for every editor on the page. */
export function draftLowlight(): Lowlight {
  lowlight ??= createLowlight(common);
  return lowlight;
}

let codec: ArticleCodec | undefined;

/**
 * The headless codec that checks a save is lossless (`ArticleCodec.check`). It parses only the
 * Markdown the editor itself just wrote, so it runs on the main thread; Markdown from outside
 * the editor always goes through the worker.
 */
export function draftCodec(): ArticleCodec {
  codec ??= new ArticleCodec();
  return codec;
}

/** Re-renders node views and menus that read `editor.isEditable` (it changes without a transaction). */
export function announceEditable(editor: Editor): void {
  if (editor.isDestroyed) return;
  editor.view.dispatch(editor.state.tr.setMeta('draftEditable', editor.isEditable).setMeta('addToHistory', false));
}
