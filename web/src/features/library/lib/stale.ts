import type { QueryClient, QueryKey } from '@tanstack/react-query';

import { isEtagMismatch } from '@/lib/api/envelope';

/**
 * A mutation's `onError` for list actions sent with a row's etag (review, exclude, delete): a
 * 412 means the row changed since the list loaded, so the list is reloaded and the next tap
 * carries the new etag. The error toast already said what happened; nothing is retried.
 */
export function refreshOnConflict(keys: readonly QueryKey[]) {
  return (error: Error, _variables: unknown, _result: unknown, context: { client: QueryClient }): void => {
    if (!isEtagMismatch(error)) return;
    for (const queryKey of keys) void context.client.invalidateQueries({ queryKey });
  };
}
