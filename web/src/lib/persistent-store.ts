/**
 * A tiny external store for one UI preference, persisted in localStorage.
 *
 * - Reads go to storage every time, so the value is always current and tests
 *   never see stale module state. Values are primitives, so snapshots compare
 *   by value for useSyncExternalStore.
 * - Other tabs are followed through the `storage` event.
 * - When storage is unavailable (private mode, policy), the store keeps the
 *   value in memory for the session instead of failing.
 */
export interface PersistentStore<T> {
  readonly get: () => T;
  readonly set: (value: T) => void;
  readonly subscribe: (listener: () => void) => () => void;
}

export interface PersistentStoreOptions<T> {
  key: string;
  fallback: T;
  /** Returns the parsed value, or undefined when the stored text is not valid. */
  parse: (raw: string) => T | undefined;
  serialize: (value: T) => string;
}

function storage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export function createPersistentStore<T>(options: PersistentStoreOptions<T>): PersistentStore<T> {
  const { key, fallback, parse, serialize } = options;
  const listeners = new Set<() => void>();
  let memory: T | undefined;

  const readRaw = (): string | null => {
    try {
      return storage()?.getItem(key) ?? null;
    } catch {
      return null;
    }
  };

  const read = (): T => {
    if (memory !== undefined) return memory;
    const raw = readRaw();
    if (raw === null) return fallback;
    return parse(raw) ?? fallback;
  };

  const notify = () => {
    for (const listener of listeners) listener();
  };

  const onStorage = (event: StorageEvent) => {
    if (event.key === key || event.key === null) notify();
  };

  return {
    get: read,
    set: (value) => {
      try {
        const target = storage();
        if (!target) throw new Error('storage unavailable');
        target.setItem(key, serialize(value));
        memory = undefined;
      } catch {
        memory = value;
      }
      notify();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      if (listeners.size === 1) window.addEventListener('storage', onStorage);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener('storage', onStorage);
      };
    },
  };
}
