import type { components } from '@/lib/api/schema';
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

/**
 * Insights' query keys and reads. Keys sit under the prefix whose events change them, so the
 * event stream keeps them fresh (`lib/query/keys`):
 *
 * - the rates overview reads the posts' snapshots: under `['posts', 'list']`, which every post
 *   change (a snapshot, an import, a post going live) refreshes;
 * - spend comes from the AI ledger, which grows as jobs run: under `['jobs']`;
 * - experiments and suggestions are their own entities (`experiments`, `suggestions`).
 */

type Schemas = components['schemas'];
export type InsightsOverview = Schemas['InsightsOverview'];
export type InsightsRow = Schemas['InsightsRow'];
export type MetricRates = Schemas['MetricRates'];
export type InsightDimension = Schemas['InsightDimension'];
export type SpendView = Schemas['SpendView'];
export type SpendDay = Schemas['SpendDay'];
export type Experiment = Schemas['Experiment'];
export type ExperimentStatus = Schemas['ExperimentStatus'];
export type InsightSuggestion = Schemas['InsightSuggestion'];
export type PostSummary = Schemas['PostSummary'];
export type Post = Schemas['Post'];
export type MetricsSnapshot = Schemas['MetricsSnapshot'];
export type AnalyticsImport = Schemas['AnalyticsImport'];

export const insightsKeys = {
  overview: (weeks: number) => [...queryKeys.posts.lists(), 'insights-overview', { weeks }] as const,
  spend: (days: number) => queryKeys.jobs.spend(days),
  experiments: () => queryKeys.experiments.list({}),
  suggestions: (status: string) => queryKeys.suggestions.list({ status }),
  /** Posts that are live on LinkedIn, for the snapshot form and the experiment arms. */
  postedPosts: () => queryKeys.posts.list({ status: 'posted' }),
  imports: () => [...queryKeys.posts.lists(), 'insights-imports'] as const,
};

/** `GET /api/analytics/overview?weeks=`: every dimension at once (the page filters one). */
export function useOverview(weeks: number) {
  return useApiQuery(
    {
      queryKey: insightsKeys.overview(weeks),
      path: '/api/analytics/overview',
      init: { params: { query: { weeks } } },
    },
    { placeholderData: (previous) => previous },
  );
}

/** `GET /api/analytics/spend?days=`. */
export function useSpend(days: number) {
  return useApiQuery(
    { queryKey: insightsKeys.spend(days), path: '/api/analytics/spend', init: { params: { query: { days } } } },
    { placeholderData: (previous) => previous },
  );
}

/** `GET /api/posts?status=posted`: the newest posts live on LinkedIn (one page of 20). */
export function usePostedPosts() {
  return useApiInfiniteQuery({
    queryKey: insightsKeys.postedPosts(),
    path: '/api/posts',
    init: { params: { query: { status: ['posted'] } } },
    pageSize: 20,
  });
}
