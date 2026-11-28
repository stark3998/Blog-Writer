import { useMutation, useQueryClient, type InfiniteData, type QueryClient, type QueryKey } from '@tanstack/react-query';

import { apiRequest } from '@/lib/api/request';
import type { components } from '@/lib/api/schema';
import { toastError } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

/**
 * The Engage panel's data (contract W-B5): open items (other members' text, 48 hours), the
 * watchlist and what the owner posted. Items have no detail route: the open list is their cache,
 * and every write puts its answer back into it. Request bodies that carry other members' text or
 * a screenshot are never kept in the mutation cache (`gcTime: 0`).
 */

export type EngagementItem = components['schemas']['EngagementItem'];
export type ItemEntry = components['schemas']['Versioned_EngagementItem_'];
export type ItemsPage = components['schemas']['Page_EngagementItem_'];
export type Angle = components['schemas']['Angle'];
export type Finding = components['schemas']['Finding'];
export type ItemKind = components['schemas']['ItemKind'];
export type WatchEntry = components['schemas']['WatchEntry'];
export type WatchEntryVersioned = components['schemas']['Versioned_WatchEntry_'];
export type WatchPage = components['schemas']['Page_WatchEntry_'];
export type WatchIn = components['schemas']['WatchIn'];
export type EngagementRecord = components['schemas']['EngagementRecord'];
export type ExtractOut = components['schemas']['ExtractOut'];

export const engageKeys = {
  /** `GET /api/engagement/items?status=open` (an `entity.changed` for `engagement_item` refreshes it). */
  items: queryKeys.engagementItems.list({ status: 'open' }),
  /** `GET /api/engagement/watchlist` (every voice, active and paused). */
  watchlist: [...queryKeys.engagementItems.all, 'watchlist', {}] as const,
  /** `GET /api/engagement/records`. */
  records: [...queryKeys.engagementItems.all, 'records', {}] as const,
};

/** Screenshots the server reads (`POST /api/engagement/extract`). */
export const SCREENSHOT_TYPES: readonly string[] = ['image/png', 'image/jpeg', 'image/webp'];
/** The server's cap (`ai.types.MAX_IMAGE_BYTES`). */
export const SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024;

// ------------------------------------------------------------------------------------------------
// Cache helpers for paged lists
// ------------------------------------------------------------------------------------------------

interface Entry {
  readonly item: { readonly id: string };
}

interface PageOf<E extends Entry> {
  readonly items: readonly E[];
  readonly nextCursor?: string | null;
}

/** Replaces an entry where it is, or puts it first (a new item: the list is newest first). */
export function upsertEntry<E extends Entry, P extends PageOf<E>>(
  data: InfiniteData<P> | undefined,
  entry: E,
): InfiniteData<P> | undefined {
  if (!data) return data;
  const found = data.pages.some((page) => page.items.some((current) => current.item.id === entry.item.id));
  const pages = data.pages.map((page, index) => {
    if (found) return { ...page, items: page.items.map((current) => (current.item.id === entry.item.id ? entry : current)) };
    return index === 0 ? { ...page, items: [entry, ...page.items] } : page;
  });
  return { ...data, pages };
}

/** Removes an entry from every loaded page. */
export function removeEntry<E extends Entry, P extends PageOf<E>>(
  data: InfiniteData<P> | undefined,
  id: string,
): InfiniteData<P> | undefined {
  if (!data) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, items: page.items.filter((entry) => entry.item.id !== id) })) };
}

function setItems(client: QueryClient, update: (data: InfiniteData<ItemsPage> | undefined) => InfiniteData<ItemsPage> | undefined) {
  client.setQueryData<InfiniteData<ItemsPage>>(engageKeys.items, update);
}

/** Puts a write's answer into the open list. */
export function rememberItem(client: QueryClient, entry: ItemEntry): void {
  setItems(client, (data) => upsertEntry(data, entry));
}

/** Drops an item from the open list (finalized, dismissed or expired). */
export function forgetItem(client: QueryClient, id: string): void {
  setItems(client, (data) => removeEntry<ItemEntry, ItemsPage>(data, id));
}

// ------------------------------------------------------------------------------------------------
// Reads
// ------------------------------------------------------------------------------------------------

/** Open items within their 48 hours, newest first. */
export function useOpenItems() {
  return useApiInfiniteQuery({
    queryKey: engageKeys.items,
    path: '/api/engagement/items',
    init: { params: { query: { status: 'open' } } },
    pageSize: 20,
  });
}

/** The whole watchlist (it is short: 5 to 10 voices, paused ones included). */
export function useWatchlist() {
  return useApiInfiniteQuery({ queryKey: engageKeys.watchlist, path: '/api/engagement/watchlist', pageSize: 100 });
}

/** What the owner posted, newest first. */
export function useRecords() {
  return useApiInfiniteQuery({ queryKey: engageKeys.records, path: '/api/engagement/records', pageSize: 20 });
}

// ------------------------------------------------------------------------------------------------
// Items
// ------------------------------------------------------------------------------------------------

/** `POST /api/engagement/items`: keeps other members' text for 48 hours. */
export function useCreateItem() {
  const client = useQueryClient();
  return useApiMutation('post', '/api/engagement/items', {
    gcTime: 0,
    errorToast: 'Couldn’t save their text.',
    handles: ['validation_failed'],
    onSuccess: (entry) => {
      rememberItem(client, entry);
    },
  });
}

/** Problem codes the angles step explains in place (with a way on) instead of a toast. */
export const ANGLE_PROBLEMS: readonly string[] = [
  'ai_use_not_confirmed',
  'no_owner_material',
  'blocklist_not_configured',
  'confidential_text',
  'expired',
  'timeout',
  'ai_unavailable',
];

/** `POST /api/engagement/items/{id}/angles`: up to three angles grounded in the owner's material. */
export function useAngles() {
  const client = useQueryClient();
  return useApiMutation('post', '/api/engagement/items/{item_id}/angles', {
    errorToast: false,
    onSuccess: (entry) => {
      rememberItem(client, entry);
    },
  });
}

/** `POST /api/engagement/items/{id}/lint`: the reply's check (no model call, nothing stored). */
export function useLint() {
  return useApiMutation('post', '/api/engagement/items/{item_id}/lint', { gcTime: 0, errorToast: false });
}

/** `POST /api/engagement/items/{id}/finalize`: keeps what the owner posted and deletes the item. */
export function useFinalize() {
  const client = useQueryClient();
  return useApiMutation('post', '/api/engagement/items/{item_id}/finalize', {
    gcTime: 0,
    successToast: 'Saved what you posted. Their text is deleted.',
    errorToast: 'Couldn’t save what you posted.',
    handles: ['etag_mismatch', 'expired', 'validation_failed', 'confidential_text'],
    invalidates: [engageKeys.records, engageKeys.watchlist, queryKeys.today],
    onSuccess: (_record, variables) => {
      forgetItem(client, variables.params.path.item_id);
    },
  });
}

/** `POST /api/engagement/items/{id}/dismiss`: deletes an item without answering it. */
export function useDismissItem() {
  const client = useQueryClient();
  return useApiMutation('post', '/api/engagement/items/{item_id}/dismiss', {
    successToast: 'Dismissed. Their text is deleted.',
    errorToast: 'Couldn’t dismiss it.',
    handles: ['expired'],
    onSuccess: (_data, variables) => {
      forgetItem(client, variables.params.path.item_id);
    },
  });
}

/**
 * `POST /api/engagement/extract` with the screenshot as the raw body (`Content-Type` image/png,
 * jpeg or webp). Nothing is stored on either side: the bytes live in this request only.
 */
export function useExtractScreenshot() {
  const { api } = useAppData();
  return useMutation({
    gcTime: 0,
    mutationFn: async (file: Blob): Promise<ExtractOut> => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      return apiRequest(api, 'post', '/api/engagement/extract', {
        body: bytes, // the raw image (the route reads the bytes, no multipart)
        bodySerializer: (body) => body,
        headers: { 'Content-Type': file.type },
      });
    },
  });
}

// ------------------------------------------------------------------------------------------------
// Watchlist
// ------------------------------------------------------------------------------------------------

function setWatch(client: QueryClient, entry: WatchEntryVersioned): void {
  client.setQueryData<InfiniteData<WatchPage>>(engageKeys.watchlist, (data) => upsertEntry(data, entry));
}

/** `POST /api/engagement/watchlist`. */
export function useAddVoice() {
  return useApiMutation('post', '/api/engagement/watchlist', {
    successToast: (entry) => `Added ${entry.item.name}.`,
    errorToast: 'Couldn’t add the voice.',
    handles: ['validation_failed'],
    invalidates: [engageKeys.watchlist],
  });
}

/** `PUT /api/engagement/watchlist/{id}` (If-Match): edit, pause or resume a voice. */
export function useUpdateVoice() {
  const client = useQueryClient();
  return useApiMutation('put', '/api/engagement/watchlist/{entry_id}', {
    errorToast: 'Couldn’t save the voice.',
    handles: ['validation_failed', 'etag_mismatch'],
    onSuccess: (entry) => {
      setWatch(client, entry);
    },
  });
}

/** `DELETE /api/engagement/watchlist/{id}` (If-Match). */
export function useRemoveVoice() {
  const client = useQueryClient();
  return useApiMutation('delete', '/api/engagement/watchlist/{entry_id}', {
    errorToast: 'Couldn’t remove the voice.',
    onSuccess: (_data, variables) => {
      client.setQueryData<InfiniteData<WatchPage>>(engageKeys.watchlist, (data) =>
        removeEntry<WatchEntryVersioned, WatchPage>(data, variables.params.path.entry_id),
      );
    },
    invalidates: [engageKeys.watchlist],
  });
}

/** `POST /api/engagement/watchlist/{id}/engaged`: one tap in the daily session. */
export function useMarkEngaged() {
  const client = useQueryClient();
  return useApiMutation('post', '/api/engagement/watchlist/{entry_id}/engaged', {
    errorToast: 'Couldn’t mark it.',
    onSuccess: (entry) => {
      setWatch(client, entry);
    },
  });
}

/** Refreshes the open list (after a 412 or a 410) and reports what went wrong if it fails. */
export async function reloadItems(client: QueryClient, key: QueryKey = engageKeys.items): Promise<void> {
  try {
    await client.refetchQueries({ queryKey: key });
  } catch (error) {
    toastError(error, { title: 'Couldn’t reload the replies.' });
  }
}
