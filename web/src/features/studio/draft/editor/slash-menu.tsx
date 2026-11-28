import type { Editor } from '@tiptap/core';
import { useEditorState } from '@tiptap/react';
import { useEffect, useEffectEvent, useId, useMemo, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '@/lib/cn';

import type { SlashKeys } from './bus';
import { BLOCK_COMMANDS, commandAvailable, filterCommands, type BlockCommand, type BlockCommandContext } from './commands';
import { dismissSlash, NO_SLASH, slashMatch, type SlashMatch } from './slash';

function sameMatch(a: SlashMatch, b: SlashMatch | null): boolean {
  return b !== null && a.active === b.active && a.from === b.from && a.to === b.to && a.query === b.query;
}

const MENU_WIDTH = 288;
const MENU_ROOM = 340;

function menuPosition(editor: Editor, from: number): CSSProperties {
  try {
    const coords = editor.view.coordsAtPos(from);
    const left = Math.max(8, Math.min(coords.left, window.innerWidth - MENU_WIDTH - 8));
    return coords.bottom + MENU_ROOM > window.innerHeight && coords.top > MENU_ROOM
      ? { left, bottom: window.innerHeight - coords.top + 6 }
      : { left, top: coords.bottom + 6 };
  } catch {
    return { left: 8, top: 8 };
  }
}

export interface SlashMenuProps {
  editor: Editor;
  slashKeys: SlashKeys;
  commands: BlockCommandContext;
}

/**
 * The block menu that a `/` opens in the text. Focus stays in the text: the menu is a listbox
 * the text box points at (`aria-controls`, `aria-activedescendant`), driven by Arrow keys,
 * Enter or Tab, and Escape.
 */
export function SlashMenu({ editor, slashKeys, commands }: SlashMenuProps) {
  const match = useEditorState({
    editor,
    selector: ({ editor: current }) => (current.isEditable ? slashMatch(current.state) : NO_SLASH),
    equalityFn: sameMatch,
  });
  const listId = useId();
  const key = `${String(match.from)}:${match.query}`;
  const [cursor, setCursor] = useState({ key: '', index: 0 });
  const [, setScrolls] = useState(0);
  const items = useMemo(
    () => (match.active ? filterCommands(BLOCK_COMMANDS, match.query).filter((command) => commandAvailable(command, editor)) : []),
    [match, editor],
  );
  const index = cursor.key === key ? Math.min(cursor.index, Math.max(0, items.length - 1)) : 0;
  const optionId = (position: number) => `${listId}-option-${String(position)}`;

  const choose = (command: BlockCommand) => {
    editor.chain().focus().deleteRange({ from: match.from, to: match.to }).run();
    command.run(editor, commands);
  };

  const onKey = useEffectEvent((event: KeyboardEvent): boolean => {
    if (items.length === 0) return false;
    switch (event.key) {
      case 'ArrowDown':
        setCursor({ key, index: (index + 1) % items.length });
        return true;
      case 'ArrowUp':
        setCursor({ key, index: (index - 1 + items.length) % items.length });
        return true;
      case 'Enter':
      case 'Tab': {
        const command = items[index];
        if (command) choose(command);
        return true;
      }
      default:
        return false;
    }
  });

  useEffect(() => {
    if (!match.active) return undefined;
    slashKeys.setHandler((event) => onKey(event));
    return () => {
      slashKeys.setHandler(null);
    };
  }, [slashKeys, match.active]);

  // The text box points at the menu while it is open.
  const activeId = items.length > 0 ? optionId(index) : undefined;
  useEffect(() => {
    if (!match.active) return undefined;
    const dom = editor.view.dom;
    dom.setAttribute('aria-controls', listId);
    if (activeId) dom.setAttribute('aria-activedescendant', activeId);
    else dom.removeAttribute('aria-activedescendant');
    return () => {
      dom.removeAttribute('aria-controls');
      dom.removeAttribute('aria-activedescendant');
    };
  }, [editor, match.active, listId, activeId]);

  // Follow the text when the page scrolls; close when the text loses focus.
  useEffect(() => {
    if (!match.active) return undefined;
    const onScroll = () => {
      setScrolls((count) => count + 1);
    };
    const onBlur = () => {
      if (!editor.isDestroyed) editor.view.dispatch(dismissSlash(editor.state));
    };
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    editor.on('blur', onBlur);
    return () => {
      window.removeEventListener('scroll', onScroll, { capture: true });
      editor.off('blur', onBlur);
    };
  }, [editor, match.active]);

  if (!match.active) return null;

  return createPortal(
    <div
      role="listbox"
      id={listId}
      aria-label="Insert a block"
      style={menuPosition(editor, match.from)}
      className="fixed z-50 max-h-80 w-72 overflow-y-auto rounded-lg border border-line bg-raised p-1 text-ink shadow-overlay"
    >
      {items.length === 0 ? (
        <p className="px-2.5 py-2 text-sm text-ink-muted">{`No block matches “${match.query}”.`}</p>
      ) : (
        items.map((command, position) => {
          const Icon = command.icon;
          const active = position === index;
          return (
            <div
              key={command.id}
              id={optionId(position)}
              role="option"
              aria-selected={active}
              onMouseDown={(event) => {
                event.preventDefault();
                choose(command);
              }}
              onMouseMove={() => {
                if (!active) setCursor({ key, index: position });
              }}
              className={cn(
                'flex cursor-default select-none items-center gap-3 rounded-md px-2.5 py-2 text-sm pointer-coarse:py-3',
                active && 'bg-wash-strong',
              )}
            >
              <Icon aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
              <span className="grid">
                <span className="font-medium">{command.label}</span>
                <span className="text-xs text-ink-subtle">{command.hint}</span>
              </span>
            </div>
          );
        })
      )}
    </div>,
    document.body,
  );
}
