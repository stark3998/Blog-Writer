import { Extension } from '@tiptap/core';
import { Plugin, PluginKey, type EditorState } from '@tiptap/pm/state';

import type { SlashKeys } from './bus';

/**
 * The slash menu's trigger: a `/` typed at the start of a text block or after a space, with up
 * to 24 letters after it, opens the menu; Escape closes it until that slash is gone. The
 * plugin only tracks where the slash is; the menu itself is React (`slash-menu.tsx`).
 */

export interface SlashMatch {
  readonly active: boolean;
  /** Position of the slash. */
  readonly from: number;
  /** The cursor (end of the query). */
  readonly to: number;
  /** What was typed after the slash. */
  readonly query: string;
}

export const NO_SLASH: SlashMatch = { active: false, from: 0, to: 0, query: '' };

interface SlashState {
  readonly match: SlashMatch;
  /** The slash the owner dismissed with Escape: it stays closed while it is there. */
  readonly dismissedAt: number | null;
}

export const slashKey = new PluginKey<SlashState>('draftSlash');

const TRIGGER = /(?:^|\s)\/([a-z0-9-]{0,24})$/i;

function findSlash(state: EditorState): Omit<SlashMatch, 'active'> | null {
  const { selection } = state;
  if (!selection.empty) return null;
  const { $from } = selection;
  const parent = $from.parent;
  if (!parent.isTextblock || parent.type.spec.code === true) return null;
  const before = parent.textBetween(0, $from.parentOffset, undefined, '\uFFFC');
  const match = TRIGGER.exec(before);
  if (!match) return null;
  const query = match[1] ?? '';
  return { from: $from.pos - query.length - 1, to: $from.pos, query };
}

export function slashMatch(state: EditorState): SlashMatch {
  return slashKey.getState(state)?.match ?? NO_SLASH;
}

/** Closes the menu for the current slash (Escape, or a click elsewhere). */
export function dismissSlash(state: EditorState) {
  return state.tr.setMeta(slashKey, { dismiss: true }).setMeta('addToHistory', false);
}

export const SlashCommand = Extension.create<{ keys: SlashKeys | null }>({
  name: 'draftSlash',

  addOptions() {
    return { keys: null };
  },

  addProseMirrorPlugins() {
    const keys = this.options.keys;
    return [
      new Plugin<SlashState>({
        key: slashKey,
        state: {
          init: () => ({ match: NO_SLASH, dismissedAt: null }),
          apply(tr, previous, _oldState, newState) {
            const found = findSlash(newState);
            const meta = tr.getMeta(slashKey) as { dismiss?: boolean } | undefined;
            if (!found) return { match: NO_SLASH, dismissedAt: null };
            if (meta?.dismiss || previous.dismissedAt === found.from) {
              return { match: NO_SLASH, dismissedAt: found.from };
            }
            return { match: { active: true, ...found }, dismissedAt: null };
          },
        },
        props: {
          handleKeyDown(view, event) {
            if (!view.editable || !slashMatch(view.state).active) return false;
            if (event.key === 'Escape') {
              view.dispatch(dismissSlash(view.state));
              return true;
            }
            return keys?.keyDown(event) ?? false;
          },
        },
      }),
    ];
  },
});
