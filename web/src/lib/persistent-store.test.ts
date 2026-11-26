import { afterEach, describe, expect, it, vi } from 'vitest';

import { createPersistentStore } from './persistent-store';

const makeStore = () =>
  createPersistentStore<'a' | 'b'>({
    key: 'test:value',
    fallback: 'a',
    parse: (raw) => (raw === 'a' || raw === 'b' ? raw : undefined),
    serialize: (value) => value,
  });

describe('createPersistentStore', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns the fallback until something is saved', () => {
    expect(makeStore().get()).toBe('a');
  });

  it('saves to localStorage and notifies subscribers', () => {
    const store = makeStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set('b');
    expect(window.localStorage.getItem('test:value')).toBe('b');
    expect(store.get()).toBe('b');
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    store.set('a');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('ignores stored values it cannot parse', () => {
    window.localStorage.setItem('test:value', 'tampered');
    expect(makeStore().get()).toBe('a');
  });

  it('follows changes from other tabs, including a cleared storage', () => {
    const store = makeStore();
    const listener = vi.fn();
    store.subscribe(listener);
    window.dispatchEvent(new StorageEvent('storage', { key: 'test:value' }));
    window.dispatchEvent(new StorageEvent('storage', { key: null }));
    window.dispatchEvent(new StorageEvent('storage', { key: 'other:key' }));
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('keeps working in memory when storage refuses writes', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    const store = makeStore();
    store.set('b');
    expect(store.get()).toBe('b');
  });

  it('falls back when storage cannot be read', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    expect(makeStore().get()).toBe('a');
  });
});
