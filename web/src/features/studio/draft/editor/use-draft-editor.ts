import { useEditor, type Editor, type UseEditorOptions } from '@tiptap/react';
import { useCallback, useState } from 'react';

import { useDraftEnvironment } from '../environment';

import { announceEditable, EditorBus, SlashKeys } from './bus';
import { createDraftExtensions } from './extensions';
import { MANUSCRIPT_CLASS } from './prose';

export interface DraftEditor {
  readonly editor: Editor;
  readonly bus: EditorBus;
  readonly slashKeys: SlashKeys;
  /** Lets the owner type (or not); re-renders what reads `editor.isEditable`. */
  readonly setEditable: (editable: boolean) => void;
}

/**
 * Whether the text can be edited. TipTap compares the options it was given with its own on
 * every render and re-applies them when they differ; reading `editable` from here keeps the
 * two equal after `setEditable`, so nothing is re-applied.
 */
class EditableFlag {
  private value = false;

  readonly get = (): boolean => this.value;

  set(value: boolean): void {
    this.value = value;
  }
}

export interface DraftEditorOptions {
  /** The editor's accessible name. */
  readonly label: string;
  /** The owner's editor (slash menu, pastes, shortcuts, diagram renders), or a read-only view of the live pipeline text. */
  readonly kind: 'manuscript' | 'live';
}

/**
 * One TipTap editor for the Draft tab, created once per mount. It starts read-only and empty;
 * the session loads the article into it and decides when the owner may edit. Options never
 * change after creation (so TipTap never re-applies them), and no style tag is injected.
 */
export function useDraftEditor({ label, kind }: DraftEditorOptions): DraftEditor {
  const env = useDraftEnvironment();
  const [bus] = useState(() => new EditorBus());
  const [slashKeys] = useState(() => new SlashKeys());
  const [flag] = useState(() => new EditableFlag());
  const [options] = useState<UseEditorOptions>(() => {
    const owner = kind === 'manuscript';
    return {
      extensions: createDraftExtensions({
        bus: owner ? bus : null,
        slashKeys: owner ? slashKeys : null,
        dialect: owner ? env.dialect : null,
        parseTimeoutMs: env.timings.parseTimeoutMs,
        renderDiagrams: owner,
      }),
      // JSON, not HTML: nothing is parsed before the article arrives.
      content: { type: 'doc', content: [{ type: 'paragraph' }] },
      injectCSS: false,
      enableContentCheck: true,
      immediatelyRender: true,
      shouldRerenderOnTransaction: false,
      editorProps: {
        attributes: {
          class: MANUSCRIPT_CLASS,
          role: 'textbox',
          'aria-multiline': 'true',
          'aria-label': label,
          spellcheck: 'true',
        },
      },
      get editable() {
        return flag.get();
      },
    };
  });
  const editor = useEditor(options);
  const setEditable = useCallback(
    (editable: boolean) => {
      if (editor.isDestroyed || editor.isEditable === editable) return;
      flag.set(editable);
      editor.setEditable(editable, false);
      announceEditable(editor);
    },
    [editor, flag],
  );
  return { editor, bus, slashKeys, setEditable };
}
