// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { clampPageSize, DEFAULT_PAGE_SIZE, flattenPages, MAX_PAGE_SIZE, nextCursorOf } from './pagination';

describe('cursor pagination (contract 1.5)', () => {
  it('asks for a page size the server accepts', () => {
    expect(clampPageSize(undefined)).toBe(DEFAULT_PAGE_SIZE);
    expect(clampPageSize(20)).toBe(20);
    expect(clampPageSize(0)).toBe(1);
    expect(clampPageSize(500)).toBe(MAX_PAGE_SIZE);
    expect(clampPageSize(12.7)).toBe(12);
    expect(clampPageSize(Number.NaN)).toBe(DEFAULT_PAGE_SIZE);
  });

  it('reads the next cursor, and stops on null, empty or missing ones', () => {
    expect(nextCursorOf({ items: [], nextCursor: 'MjAyNg' })).toBe('MjAyNg');
    expect(nextCursorOf({ items: [], nextCursor: null })).toBeUndefined();
    expect(nextCursorOf({ items: [], nextCursor: '' })).toBeUndefined();
    expect(nextCursorOf({ items: [] })).toBeUndefined();
    expect(nextCursorOf(undefined)).toBeUndefined();
  });

  it('flattens loaded pages into one list in order', () => {
    const pages = [
      { items: [{ item: 'a', etag: '"1"' }], nextCursor: 'x' },
      { items: [{ item: 'b', etag: '"2"' }, { item: 'c', etag: '"3"' }], nextCursor: null },
    ];
    expect(flattenPages({ pages, pageParams: [undefined, 'x'] }).map((entry) => entry.item)).toEqual(['a', 'b', 'c']);
    expect(flattenPages({ pages: [], pageParams: [] })).toEqual([]);
  });
});
