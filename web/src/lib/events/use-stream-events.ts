import { useEffect, useEffectEvent } from 'react';

import { useAppData } from '@/lib/app-data';
import type { EntityName } from '@/lib/query/keys';

import { entityChangeOf, type EntityChange } from './entities';
import type { StreamEvent } from './events';

/**
 * Calls `listener` with every stream event while the component is mounted, after the job store
 * and the query cache have it (so a refetch the event caused is already under way).
 *
 * This does not open the stream: it is open while something retains it (a `JobTimeline`, or
 * `useLiveEvents(true)` from `features/jobs/use-job`).
 */
export function useStreamEvents(listener: (event: StreamEvent) => void): void {
  const { feed } = useAppData();
  const onEvent = useEffectEvent(listener);
  useEffect(
    () =>
      feed.subscribe((event) => {
        onEvent(event);
      }),
    [feed],
  );
}

export interface EntityFilter {
  readonly entity: EntityName;
  /** Only this document; every document of the kind when left out. */
  readonly id?: string;
}

/**
 * Calls `listener` when a job reports that a document changed (`entity.changed`). The queries
 * are already invalidated by then; use this for what invalidation cannot do, such as telling the
 * owner "The pipeline updated this draft" or reloading an editor that is not driven by a query.
 */
export function useEntityChanged(filter: EntityFilter, listener: (change: EntityChange) => void): void {
  const { entity, id } = filter;
  useStreamEvents((event) => {
    const change = entityChangeOf(event);
    if (change?.entity !== entity) return;
    if (id !== undefined && change.id !== id) return;
    listener(change);
  });
}
