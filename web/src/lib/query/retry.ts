import { ApiError } from '@/lib/api/errors';

/** Retries after the first failure, so at most four attempts in all. */
export const MAX_QUERY_RETRIES = 3;

/**
 * Whether a failed query is worth another attempt. Only transient failures are: no response,
 * a 5xx, a 408 or a 429. Never 401/403 (the session handling takes over), 404 or any other 4xx
 * (the same request gets the same answer), and never errors that are not `ApiError`s (bugs, such
 * as a response that does not parse).
 */
export function isRetryableError(error: unknown): boolean {
  return error instanceof ApiError && error.transient;
}

/**
 * TanStack Query's `retry` option. `failureCount` is the number of failures before this one
 * (0 on the first), as TanStack passes it.
 */
export function shouldRetryQuery(failureCount: number, error: unknown, maxRetries = MAX_QUERY_RETRIES): boolean {
  return failureCount < maxRetries && isRetryableError(error);
}
