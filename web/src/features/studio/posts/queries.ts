import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { useMachines as useMachineTable } from '@/lib/query/machines';

import type { Machines } from './model/machines';

/**
 * The reads of the Posts and Go-live tabs. Keys follow contract section 9, so job events refresh
 * them: one post is `queryKeys.posts.detail(id)` (the same key the piece header and the other
 * Studio tabs use, so the cache is shared), its go-live view, kit and live lint sit under it.
 */

/** `GET /api/posts?articleId=`: the posts that come from one article (newest first). */
export function useArticlePosts(articleId: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useApiInfiniteQuery(
    {
      queryKey: queryKeys.posts.list({ articleId }),
      path: '/api/posts',
      init: { params: { query: { articleId } } },
      pageSize: 20,
    },
    { enabled },
  );
}

/** `GET /api/kb/brand`: the author line of the feed card. */
export function useBrand() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.brand), path: '/api/kb/brand' });
}

/** `GET /api/kb/cadence`: the planner's time zone, for slot inputs. */
export function useCadence() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.cadence), path: '/api/kb/cadence' });
}

/**
 * `GET /api/meta/machines`: which status changes exist, to enable the action buttons. The shared
 * query (one cache entry for every area); the contract's table stands in until it answers.
 */
export function useMachines(): Machines | undefined {
  return useMachineTable().data;
}

/** `value`, `delayMs` after it last changed. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(value);
    }, delayMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, delayMs]);
  return debounced;
}

/** How long the composer waits after a keystroke before it asks the server's lint. */
export const LINT_DEBOUNCE_MS = 400;

/**
 * The live composer lint (`POST /api/posts/{id}/lint`: deterministic, nothing stored) for the
 * text being typed, a moment after typing stops. The previous answer stays on screen while the
 * next one loads. `current` is false while the answer is for older text.
 */
export function useLiveLint(postId: string, displayText: string, { enabled = true }: { enabled?: boolean } = {}) {
  const { api } = useAppData();
  const settled = useDebouncedValue(displayText, LINT_DEBOUNCE_MS);
  const query = useQuery({
    queryKey: [...queryKeys.posts.detail(postId), 'lint', settled] as const,
    queryFn: ({ signal }) =>
      apiRequest(api, 'post', '/api/posts/{post_id}/lint', {
        params: { path: { post_id: postId } },
        body: { displayText: settled },
        signal,
      }),
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const current = settled === displayText && !query.isPlaceholderData && query.isSuccess;
  return { ...query, current };
}
