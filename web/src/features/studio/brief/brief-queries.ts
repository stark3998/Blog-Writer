import { useQueryClient } from '@tanstack/react-query';

import { useApiInfiniteQuery, useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

/**
 * The research queries and writes of the Brief tab, on the contract keys (`researchRuns`,
 * `briefs`, `quickSearches`), so the research job's `entity.changed` events refresh them.
 */

/** A piece's research runs, newest first (`GET /api/research/runs?articleId=`). */
export function useResearchRuns(articleId: string) {
  return useApiInfiniteQuery({
    queryKey: queryKeys.researchRuns.list({ articleId }),
    path: '/api/research/runs',
    init: { params: { query: { articleId } } },
    pageSize: 20,
  });
}

/** One brief (`GET /api/research/briefs/{id}`). */
export function useBrief(briefId: string | undefined) {
  return useApiQuery(
    {
      queryKey: queryKeys.briefs.detail(briefId ?? 'none'),
      path: '/api/research/briefs/{brief_id}',
      init: { params: { path: { brief_id: briefId ?? 'none' } } },
    },
    { enabled: briefId !== undefined },
  );
}

/** Which piece a quick search belongs to. */
export interface QuickScope {
  readonly articleId?: string;
  readonly postId?: string;
}

/** A piece's quick searches, newest first (`GET /api/research/quick?articleId=` or `?postId=`). */
export function useQuickSearches(scope: QuickScope) {
  return useApiInfiniteQuery({
    queryKey: queryKeys.quickSearches.list({ ...scope }),
    path: '/api/research/quick',
    init: { params: { query: { ...scope } } },
    pageSize: 20,
  });
}

/** One quick search (`GET /api/research/quick/{id}`). */
export function useQuickSearch(quickId: string | undefined) {
  return useApiQuery(
    {
      queryKey: queryKeys.quickSearches.detail(quickId ?? 'none'),
      path: '/api/research/quick/{quick_id}',
      init: { params: { path: { quick_id: quickId ?? 'none' } } },
    },
    { enabled: quickId !== undefined },
  );
}

/** Codes the run form explains next to its fields or in place (no toast). */
export const START_RUN_PROBLEMS = ['validation_failed', 'confidential_text', 'blocklist_not_configured'] as const;

/** `POST /api/research/runs`: 202 with the queued run and its job. */
export function useStartRun() {
  const queryClient = useQueryClient();
  return useApiMutation('post', '/api/research/runs', {
    successToast: 'Research started.',
    errorToast: 'Research didn’t start.',
    handles: START_RUN_PROBLEMS,
    invalidates: [queryKeys.researchRuns.lists()],
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.researchRuns.detail(data.run.item.id), data.run);
    },
  });
}

/** `POST /api/research/runs/{id}/cancel` with the run's etag. */
export function useCancelRun() {
  return useApiMutation('post', '/api/research/runs/{run_id}/cancel', {
    updates: (data) => queryKeys.researchRuns.detail(data.item.id),
    invalidates: [queryKeys.researchRuns.lists()],
    successToast: 'Research cancelled.',
    errorToast: 'Couldn’t cancel the research.',
    handles: ['etag_mismatch', 'invalid_transition'],
  });
}

/** Codes a brief edit shows itself: the conflict dialog, field messages. */
export const EDIT_BRIEF_PROBLEMS = ['etag_mismatch', 'validation_failed', 'confidential_text', 'unknown_finding'] as const;

/** `PUT /api/research/briefs/{id}`: the owner's edit; the answer (new etag) becomes the cache entry. */
export function useEditBrief(briefId: string) {
  return useApiMutation('put', '/api/research/briefs/{brief_id}', {
    updates: () => queryKeys.briefs.detail(briefId),
    errorToast: 'Couldn’t save the brief.',
    handles: EDIT_BRIEF_PROBLEMS,
  });
}

/** Codes the quick search shows in place. */
export const QUICK_SEARCH_PROBLEMS = [
  'validation_failed',
  'confidential_text',
  'blocklist_not_configured',
  'ai_unavailable',
  'timeout',
] as const;

/** `POST /api/research/quick`: synchronous (up to about 90 s), stored with its citations. */
export function useRunQuickSearch() {
  return useApiMutation('post', '/api/research/quick', {
    updates: (data) => queryKeys.quickSearches.detail(data.item.id),
    invalidates: [queryKeys.quickSearches.lists()],
    errorToast: 'The quick search didn’t finish.',
    handles: QUICK_SEARCH_PROBLEMS,
  });
}
