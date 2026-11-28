import { useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { useSystemStatus as useSharedSystemStatus } from '@/lib/query/system-status';

/**
 * Today's reads. They refetch whenever the owner comes back to the app (Phase 1 rule: Today's
 * queries opt into `refetchOnWindowFocus`), and every `entity.changed` event refreshes the
 * `['today']` prefix they share.
 */

/** Scorecards of the last complete weeks, newest first (`GET /api/plan/scorecard`). */
export const SCORECARD_WEEKS = 8;

export const todayKeys = {
  /** `GET /api/today`: the inbox and the week. */
  inbox: queryKeys.today,
  scorecards: (weeks: number) => [...queryKeys.today, 'scorecards', weeks] as const,
};

const TODAY_QUERY = { refetchOnWindowFocus: true } as const;

/** `GET /api/today`. */
export function useTodayInbox() {
  return useApiQuery({ queryKey: todayKeys.inbox, path: '/api/today' }, TODAY_QUERY);
}

/**
 * `GET /api/system/status`: safe mode, the spending brake and token expiries. The shared entry
 * (`queryKeys.jobs.systemStatus`), so a change made in Settings shows here at once.
 */
export function useSystemStatus() {
  return useSharedSystemStatus(TODAY_QUERY);
}

/** `GET /api/kb/autonomy` (shared with Settings > Blocklist & policy): the pause switch. */
export function useAutonomy() {
  return useApiQuery(
    { queryKey: queryKeys.kb.detail(KB_SINGLETONS.autonomy), path: '/api/kb/autonomy' },
    TODAY_QUERY,
  );
}

/** `GET /api/plan/scorecard?weeks=8`: last week's scorecard to compare this week with. */
export function useScorecards(weeks = SCORECARD_WEEKS) {
  return useApiQuery(
    {
      queryKey: todayKeys.scorecards(weeks),
      path: '/api/plan/scorecard',
      init: { params: { query: { weeks } } },
    },
    TODAY_QUERY,
  );
}
