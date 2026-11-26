import type { QueryClient, QueryKey } from '@tanstack/react-query';

import { queryKeys } from '@/lib/query/keys';

import type { StreamEvent } from './events';
import type { JobStore } from './job-store';

/**
 * The queries an event makes stale. A snapshot or a status change can change any job list or
 * job; any other durable event changes its job; deltas only change the live text (kept in the
 * job store) and `close` changes nothing.
 */
export function invalidationsFor(event: StreamEvent): readonly QueryKey[] {
  switch (event.type) {
    case 'snapshot':
      return [queryKeys.jobs.all];
    case 'job':
      return event.event.type === 'status' ? [queryKeys.jobs.all] : [queryKeys.jobs.detail(event.jobId)];
    case 'delta':
    case 'close':
      return [];
  }
}

function isPrefix(prefix: QueryKey, key: QueryKey): boolean {
  return (
    prefix.length <= key.length &&
    prefix.every((part, index) => JSON.stringify(part) === JSON.stringify(key[index]))
  );
}

/**
 * Routes stream events: into the job store first, then invalidates the related queries. A burst
 * of events (a snapshot, a replay) invalidates each key once, on the next microtask, and skips keys
 * that a pending prefix already covers.
 */
export function createEventRouter(jobs: JobStore, queryClient: QueryClient): (event: StreamEvent) => void {
  const pending: QueryKey[] = [];
  let scheduled = false;

  const flush = () => {
    scheduled = false;
    const keys = pending.splice(0).sort((a, b) => a.length - b.length);
    const kept: QueryKey[] = [];
    for (const key of keys) {
      if (!kept.some((prefix) => isPrefix(prefix, key))) kept.push(key);
    }
    for (const queryKey of kept) void queryClient.invalidateQueries({ queryKey });
  };

  return (event) => {
    jobs.apply(event);
    const keys = invalidationsFor(event);
    if (keys.length === 0) return;
    pending.push(...keys);
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(flush);
  };
}
