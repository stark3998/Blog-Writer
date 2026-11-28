import { useRef, type RefObject } from 'react';

/**
 * Where focus goes when a dialog closes. Radix gives it back to the element that opened the
 * dialog, but an action often removes that element (after "Approve", the article is approved
 * and the button is gone). Then focus goes to `fallback` (the article's status), never to the
 * page body.
 */
export function useReturnFocus(fallback: RefObject<HTMLElement | null> | undefined) {
  const opener = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: () => {
      // Radix asks before it moves focus in, so this is still the element that opened it.
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    },
    onCloseAutoFocus: (event: Event) => {
      const target = opener.current?.isConnected ? opener.current : fallback?.current;
      opener.current = null;
      if (!target) return;
      event.preventDefault();
      target.focus({ preventScroll: true });
    },
  };
}
