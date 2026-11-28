import type { InfiniteData } from '@tanstack/react-query';

/**
 * Cursor pagination (contract 1.5): `?limit=` (1-100, default 50) and `?cursor=` (opaque), and
 * `{items: [{item, etag}], nextCursor}` back, `nextCursor` null on the last page.
 */

/** The page size the server uses when none is given. */
export const DEFAULT_PAGE_SIZE = 50;
/** The largest page the server returns. */
export const MAX_PAGE_SIZE = 100;

/** The entries of one page type (`Versioned<T>` for the server's lists). */
export type PageEntry<TPage> = TPage extends { readonly items: readonly (infer E)[] } ? E : never;

/** A page size the server accepts: a whole number from 1 to 100. */
export function clampPageSize(limit: number | undefined): number {
  if (limit === undefined || !Number.isFinite(limit)) return DEFAULT_PAGE_SIZE;
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.trunc(limit)));
}

/** The cursor of the page after this one, or `undefined` on the last page (TanStack's "no more"). */
export function nextCursorOf(page: unknown): string | undefined {
  if (typeof page !== 'object' || page === null || !('nextCursor' in page)) return undefined;
  const cursor = page.nextCursor;
  return typeof cursor === 'string' && cursor !== '' ? cursor : undefined;
}

/** Every entry of every loaded page, in order: what a list shows. */
export function flattenPages<TPage>(data: InfiniteData<TPage>): PageEntry<TPage>[] {
  const entries: PageEntry<TPage>[] = [];
  for (const page of data.pages) {
    if (typeof page !== 'object' || page === null || !('items' in page) || !Array.isArray(page.items)) continue;
    entries.push(...(page.items as PageEntry<TPage>[]));
  }
  return entries;
}
