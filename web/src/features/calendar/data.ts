import { useMemo } from 'react';

import type { components } from '@/lib/api/schema';
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { useMachines } from '@/lib/query/machines';

import { planIdFor } from './lib/plan';
import {
  articleFormats,
  cadenceView,
  lanesView,
  pillarViews,
  type CadenceView,
  type FormatOption,
  type LanesView,
  type PillarView,
} from './lib/settings';
import { addDays, localToUtcMs, toTimestamp } from './lib/time';

/**
 * The calendar's reads, on the shared keys (`queryKeys`), so the event stream refreshes them and
 * other areas share the cache: settings singletons under `kb`, week plans under `plans` (keyed by
 * the plan id `plan-<Monday>`, the id `entity.changed` names), posts, articles, ideas and jobs.
 */

type Schemas = components['schemas'];
export type IdeaStatus = Schemas['IdeaStatus'];
export type IdeaLane = Schemas['IdeaLane'];

// ------------------------------------------------------------------------------------------------
// Settings
// ------------------------------------------------------------------------------------------------

export interface CalendarSettings {
  readonly cadence: CadenceView;
  readonly lanes: LanesView;
  readonly pillars: readonly PillarView[];
  readonly formats: readonly FormatOption[];
  /** True until the cadence (the one setting every view needs) has an answer. */
  readonly pending: boolean;
  /** The settings that could not be loaded (the views use the defaults for them). */
  readonly failed: readonly string[];
  readonly retry: () => void;
}

/** The cadence, lanes, pillars and format packs, with defaults for anything not loaded. */
export function useCalendarSettings(): CalendarSettings {
  const cadence = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.cadence), path: '/api/kb/cadence' });
  const lanes = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.lanes), path: '/api/kb/lanes' });
  const pillars = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.pillars), path: '/api/kb/pillars' });
  const formats = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.formats), path: '/api/kb/formats' });
  const cadenceItem = cadence.data?.item;
  const lanesItem = lanes.data?.item;
  const pillarsItem = pillars.data?.item;
  const formatsItem = formats.data?.item;
  const failed = [
    cadence.isError ? 'slot settings' : undefined,
    lanes.isError ? 'lane rules' : undefined,
    pillars.isError ? 'pillars' : undefined,
    formats.isError ? 'format packs' : undefined,
  ].filter((name): name is string => name !== undefined);
  const { refetch: refetchCadence } = cadence;
  const { refetch: refetchLanes } = lanes;
  const { refetch: refetchPillars } = pillars;
  const { refetch: refetchFormats } = formats;
  return {
    cadence: useMemo(() => cadenceView(cadenceItem), [cadenceItem]),
    lanes: useMemo(() => lanesView(lanesItem), [lanesItem]),
    pillars: useMemo(() => pillarViews(pillarsItem), [pillarsItem]),
    formats: useMemo(() => articleFormats(formatsItem), [formatsItem]),
    pending: cadence.isPending,
    failed,
    retry: () => {
      if (cadence.isError) void refetchCadence();
      if (lanes.isError) void refetchLanes();
      if (pillars.isError) void refetchPillars();
      if (formats.isError) void refetchFormats();
    },
  };
}

// ------------------------------------------------------------------------------------------------
// State machines (`GET /api/meta/machines`): which buttons an item's status allows
// ------------------------------------------------------------------------------------------------

/** The contract's tables (section 2), used until the server's answer arrives. */
const FALLBACK_MACHINES: Readonly<Record<string, Readonly<Record<string, readonly string[]>>>> = {
  idea: {
    new: ['accepted', 'snoozed', 'used', 'dismissed', 'expired'],
    snoozed: ['new', 'dismissed', 'expired'],
    accepted: ['used', 'dismissed', 'new'],
    used: ['accepted'],
    dismissed: ['new'],
    expired: ['new'],
  },
  week_plan: {
    generating: ['proposed'],
    proposed: ['generating', 'approved'],
    approved: ['proposed', 'closed'],
    closed: [],
  },
};

export type CanTransition = (machine: string, from: string, to: string) => boolean;

/** `can(machine, from, to)`: true when the state machine has that edge. */
export function useTransitions(): CanTransition {
  const table = useMachines().data;
  return useMemo(
    () => (machine: string, from: string, to: string) => {
      const edges = table?.[machine] ?? FALLBACK_MACHINES[machine];
      return edges?.[from]?.includes(to) ?? false;
    },
    [table],
  );
}

// ------------------------------------------------------------------------------------------------
// Week plans, their posts and their flagship
// ------------------------------------------------------------------------------------------------

/** `GET /api/plan/weeks/{weekStart}` (404 while the week has no plan). */
export function useWeekPlan(weekStart: string, enabled = true) {
  return useApiQuery(
    {
      queryKey: queryKeys.plans.detail(planIdFor(weekStart)),
      path: '/api/plan/weeks/{week_start}',
      init: { params: { path: { week_start: weekStart } } },
    },
    { enabled },
  );
}

/** `GET /api/plan/weeks?from=`: the plans from the week of `from` on, oldest first. */
export function usePlansFrom(from: string, pageSize = 10) {
  return useApiInfiniteQuery({
    queryKey: queryKeys.plans.list({ from }),
    path: '/api/plan/weeks',
    init: { params: { query: { from } } },
    pageSize,
  });
}

/**
 * Every post with a slot in the week and a day either side (`GET /api/posts?from=&to=`): the
 * plan's posts and the ones outside it that hold a day, which the spacing rules count too.
 */
export function useWeekPosts(weekStart: string, zone: string, enabled = true) {
  const from = toTimestamp(localToUtcMs(addDays(weekStart, -1), '00:00', zone));
  const to = toTimestamp(localToUtcMs(addDays(weekStart, 8), '00:00', zone));
  return useApiInfiniteQuery(
    {
      queryKey: queryKeys.posts.list({ from, to }),
      path: '/api/posts',
      init: { params: { query: { from, to } } },
      pageSize: 100,
    },
    { enabled },
  );
}

/** The flagship articles a plan created (`GET /api/articles?weekPlanId=`). */
export function usePlanArticles(planId: string, enabled = true) {
  return useApiInfiniteQuery(
    {
      queryKey: queryKeys.articles.list({ weekPlanId: planId }),
      path: '/api/articles',
      init: { params: { query: { weekPlanId: planId } } },
      pageSize: 20,
    },
    { enabled },
  );
}

/**
 * One job's record (`GET /api/jobs/{id}`): its status when the event stream has not said yet.
 * `poll` asks again every 5 seconds (while the job runs), in case the stream is down.
 */
export function useJobRecord(jobId: string | null | undefined, poll = false) {
  return useApiQuery(
    {
      queryKey: queryKeys.jobs.detail(jobId ?? 'none'),
      path: '/api/jobs/{job_id}',
      init: { params: { path: { job_id: jobId ?? 'none' } } },
    },
    { enabled: Boolean(jobId), refetchInterval: poll ? 5_000 : false },
  );
}

// ------------------------------------------------------------------------------------------------
// Ideas
// ------------------------------------------------------------------------------------------------

export interface IdeaFilters {
  readonly status: readonly IdeaStatus[];
  readonly pillar?: string | undefined;
  readonly lane?: IdeaLane | undefined;
}

/** `GET /api/ideas` with the inbox's filters, newest first. */
export function useIdeas({ status, pillar, lane }: IdeaFilters) {
  return useApiInfiniteQuery({
    queryKey: queryKeys.ideas.list({ status, pillar: pillar ?? null, lane: lane ?? null }),
    path: '/api/ideas',
    init: {
      params: {
        query: { status: [...status], ...(pillar ? { pillar } : {}), ...(lane ? { lane } : {}) },
      },
    },
    pageSize: 20,
  });
}

/** `GET /api/ideas/{id}`: one idea a link names (a quick-take slot's idea). */
export function useIdea(ideaId: string | undefined) {
  return useApiQuery(
    {
      queryKey: queryKeys.ideas.detail(ideaId ?? 'none'),
      path: '/api/ideas/{idea_id}',
      init: { params: { path: { idea_id: ideaId ?? 'none' } } },
    },
    { enabled: Boolean(ideaId) },
  );
}
