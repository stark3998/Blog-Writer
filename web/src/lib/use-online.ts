import { useSyncExternalStore } from 'react';

function subscribe(onChange: () => void): () => void {
  window.addEventListener('online', onChange);
  window.addEventListener('offline', onChange);
  return () => {
    window.removeEventListener('online', onChange);
    window.removeEventListener('offline', onChange);
  };
}

const read = () => navigator.onLine;

/**
 * Whether the device thinks it has a connection (`navigator.onLine`), re-rendering when that
 * changes. False means certainly offline: TanStack Query pauses reads and writes then, so screens
 * say so instead of showing a skeleton or "Saving…" forever. True only means "try": a request can
 * still fail, and the screen handles that too.
 */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, read, () => true);
}
