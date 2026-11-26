import { useEffect } from 'react';

/** Cmd+K on Apple platforms, Ctrl+K elsewhere (both are accepted everywhere). */
export function isCommandPaletteShortcut(event: KeyboardEvent): boolean {
  return (
    !event.isComposing &&
    (event.metaKey || event.ctrlKey) &&
    !event.altKey &&
    !event.shiftKey &&
    event.key.toLowerCase() === 'k'
  );
}

export function useCommandPaletteShortcut(onToggle: () => void): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isCommandPaletteShortcut(event)) return;
      event.preventDefault();
      onToggle();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onToggle]);
}
