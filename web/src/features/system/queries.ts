import { queryOptions, useQuery } from '@tanstack/react-query';

import { unwrap, type ApiClient } from '@/lib/api/client';
import type { HealthResponse, MeResponse } from '@/lib/api/types';
import { useAppData } from '@/lib/app-data';
import { queryKeys } from '@/lib/query/keys';

/** Who is signed in and which build the server runs change rarely. */
const STALE_MS = 5 * 60_000;

export function meQuery(api: ApiClient) {
  return queryOptions({
    queryKey: queryKeys.me,
    queryFn: ({ signal }): Promise<MeResponse> => unwrap(api.GET('/api/me', { signal })),
    staleTime: STALE_MS,
  });
}

export function healthQuery(api: ApiClient) {
  return queryOptions({
    queryKey: queryKeys.health,
    queryFn: ({ signal }): Promise<HealthResponse> => unwrap(api.GET('/api/health', { signal })),
    staleTime: STALE_MS,
  });
}

export interface SystemQueryOptions {
  /** Refetch (when stale) as the owner comes back to the app. Today turns this on. */
  readonly refetchOnWindowFocus?: boolean;
}

/** `GET /api/me`: the signed-in owner. */
export function useMe({ refetchOnWindowFocus = false }: SystemQueryOptions = {}) {
  const { api } = useAppData();
  return useQuery({ ...meQuery(api), refetchOnWindowFocus });
}

/** `GET /api/health`: the server's build SHA. */
export function useHealth({ refetchOnWindowFocus = false }: SystemQueryOptions = {}) {
  const { api } = useAppData();
  return useQuery({ ...healthQuery(api), refetchOnWindowFocus });
}
