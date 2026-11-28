import { QueryClient } from '@tanstack/react-query';

import { backoffDelay } from './backoff';
import { MAX_QUERY_RETRIES, shouldRetryQuery } from './retry';

export interface QueryClientOptions {
  readonly maxRetries?: number;
  /** The delay before the first retry; each later retry doubles it. */
  readonly retryBaseMs?: number;
  readonly retryMaxMs?: number;
  /** For tests: a fixed jitter source. */
  readonly random?: () => number;
}

/** Data younger than this is served from the cache without a refetch on mount. */
export const QUERY_STALE_TIME_MS = 30_000;

/**
 * The app's TanStack Query client.
 *
 * - Retries only transient failures (no response, 5xx, 408, 429), up to 3 times, with capped
 *   exponential backoff (1 s, 2 s, 4 s, at most 30 s, with jitter). Never 401, 403, 404 or other
 *   4xx: the session handling owns 401/403, and the same request gets the same answer.
 * - No refetch on window focus by default; queries that want it opt in (Today does, so the inbox is
 *   current when the owner comes back to the app).
 * - Mutations are never retried automatically.
 */
export function createQueryClient({
  maxRetries = MAX_QUERY_RETRIES,
  retryBaseMs = 1000,
  retryMaxMs = 30_000,
  random,
}: QueryClientOptions = {}): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => shouldRetryQuery(failureCount, error, maxRetries),
        retryDelay: (failureCount) => backoffDelay(failureCount, { baseMs: retryBaseMs, maxMs: retryMaxMs, random }),
        staleTime: QUERY_STALE_TIME_MS,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
