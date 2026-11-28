import { useCallback, useSyncExternalStore } from 'react';

/** Matches Tailwind's `md` breakpoint (48rem), so CSS and JS agree. */
export const DESKTOP_QUERY = '(min-width: 48rem)';
export const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';
export const STANDALONE_QUERY = '(display-mode: standalone)';

/** Subscribes to a media query. Re-renders only when its answer changes. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener('change', onChange);
      return () => {
        list.removeEventListener('change', onChange);
      };
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}
