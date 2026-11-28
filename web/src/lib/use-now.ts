import { useSyncExternalStore } from 'react';

/**
 * The current time for labels such as "Due in 25 min", "the slot passed 5 minutes ago" and "day
 * 52 of 60", shared by every component that shows one. Reading `Date.now()` in render would make
 * components impure; this store reads it outside render instead.
 *
 * It ticks every 15 seconds while something on screen uses it, and at once when the page comes
 * back to the foreground (a phone that slept, or one opened from a push). A component that
 * mounts reads a fresh time; with nothing on screen the clock stops.
 */

const TICK_MS = 15_000;

const listeners = new Set<() => void>();
let current: number | undefined;
let timer: ReturnType<typeof setInterval> | undefined;

function tick(): void {
  current = Date.now();
  for (const listener of listeners) listener();
}

function onVisibility(): void {
  if (document.visibilityState === 'visible') tick();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // A component that mounts now reads a fresh time: React reads the snapshot again after this.
  current = Date.now();
  if (timer === undefined) {
    timer = setInterval(tick, TICK_MS);
    document.addEventListener('visibilitychange', onVisibility);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
      document.removeEventListener('visibilitychange', onVisibility);
      current = undefined;
    }
  };
}

function getSnapshot(): number {
  current ??= Date.now();
  return current;
}

/** Epoch milliseconds, refreshed every 15 seconds (and on return to the page) while mounted. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
