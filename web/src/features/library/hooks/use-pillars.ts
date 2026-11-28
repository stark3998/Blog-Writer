import { useMemo } from 'react';

import { useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';

import type { Pillar } from '../lib/types';

/** `GET /api/kb/pillars`: the same query (and cache) the Pillars editor uses. */
export function usePillarsQuery() {
  return useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.pillars), path: '/api/kb/pillars' });
}

export interface PillarDirectory {
  /** The owner's pillars in their order (empty while loading). */
  readonly pillars: readonly Pillar[];
  /** A pillar's name, or its id when it is unknown (a pillar removed since). */
  readonly nameOf: (id: string) => string;
}

/** The pillars by id, for showing names where documents only store ids. */
export function usePillarDirectory(): PillarDirectory {
  const query = usePillarsQuery();
  const pillars = query.data?.item.pillars;
  return useMemo(() => {
    const list = pillars ?? [];
    const names = new Map(list.map((pillar) => [pillar.id, pillar.name]));
    return { pillars: list, nameOf: (id: string) => names.get(id) ?? id };
  }, [pillars]);
}
