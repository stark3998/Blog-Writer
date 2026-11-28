import type { QueryKey } from '@tanstack/react-query';

import { isProblem } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { useApiQuery } from '@/lib/query/api-query';
import { BACKUP_STATUS_ID, KB_SINGLETONS, LINKEDIN_CONNECTION_ID, queryKeys, type ListFilters } from '@/lib/query/keys';
import { shouldRetryQuery } from '@/lib/query/retry';

/**
 * The Settings area's query keys and the reads more than one section shares.
 *
 * Keys follow `lib/query/keys`: settings singletons are `kb.detail(<id>)`, so a job's
 * `entity.changed` refreshes them. Reads that are not documents sit under the prefix whose events
 * change them: the runner, the brake and the schedule items move as jobs run, so they live under
 * `['jobs', …]` and refresh with every job status event; static facts (the state machines, the
 * VAPID key) are fetched once.
 */

export type Schemas = components['schemas'];
export type SystemStatus = Schemas['SystemStatus'];
export type ServiceView = Schemas['ServiceView'];
export type ScheduleView = Schemas['ScheduleView'];
export type JobView = Schemas['JobView'];
export type Pillar = Schemas['Pillar-Output'];
export type FormatPack = Schemas['FormatPack'];

export const settingsKeys = {
  /** `GET /api/system/status`: runner, safe mode, brake and services change as jobs run. */
  systemStatus: queryKeys.jobs.systemStatus,
  /** `GET /api/schedules`: schedule items live with the jobs. */
  schedules: queryKeys.jobs.schedules,
  /** `GET /api/jobs?…`: a paged list. */
  jobs: (filters: ListFilters) => queryKeys.jobs.list(filters),
  /** `GET /api/meta/machines`: fixed for a build (`useMachines` in `@/lib/query/machines`). */
  machines: queryKeys.meta.machines,
  /** `GET /api/push/key`: the VAPID public key, fixed for a deployment. */
  pushKey: ['push', 'key'] as const,
  /** This browser's push state (permission, subscription): device facts, not server data. */
  pushDevice: ['push', 'device'] as const,
  /** `GET /api/kb/models`: routes and prices (read-only) with the preferences document. */
  models: queryKeys.kb.detail(KB_SINGLETONS.models),
  cadence: queryKeys.kb.detail(KB_SINGLETONS.cadence),
  blocklist: queryKeys.kb.detail(KB_SINGLETONS.blocklist),
  policy: queryKeys.kb.detail(KB_SINGLETONS.policy),
  /** `GET /api/kb/autonomy`: the settings document with today's auto-publish decision. */
  autonomy: queryKeys.kb.detail(KB_SINGLETONS.autonomy),
  pillars: queryKeys.kb.detail(KB_SINGLETONS.pillars),
  formats: queryKeys.kb.detail(KB_SINGLETONS.formats),
  /** `GET /api/kb/inputs`: computed from many documents; any settings change may move it. */
  ownerInputs: [...queryKeys.kb.lists(), 'owner-inputs'] as const,
  profileChecklist: queryKeys.kb.detail(KB_SINGLETONS.profileChecklist),
  /** `GET /api/linkedin/connection` (the contract's key for the connection). */
  linkedin: queryKeys.linkedin.detail(LINKEDIN_CONNECTION_ID),
  /** `GET /api/backups`: the backup status singleton with the recent dumps. */
  backups: queryKeys.backups.detail(BACKUP_STATUS_ID),
  sources: (filters: ListFilters) => queryKeys.sources.list(filters),
  /** `GET /api/evals/topics`: refreshed with the eval results (the eval job sets their briefs). */
  evalTopics: [...queryKeys.evalResults.lists(), 'topics'] as const,
  evalResults: (filters: ListFilters) => queryKeys.evalResults.list(filters),
} satisfies Record<string, QueryKey | ((filters: ListFilters) => QueryKey)>;

export { useSystemStatus } from '@/lib/query/system-status';

/** `GET /api/schedules` (soonest first). */
export function useSchedules() {
  return useApiQuery({ queryKey: settingsKeys.schedules, path: '/api/schedules' });
}

export { machineAllows, useMachines } from '@/lib/query/machines';

/** `GET /api/kb/pillars`: names for pillar ids. */
export function usePillars() {
  return useApiQuery({ queryKey: settingsKeys.pillars, path: '/api/kb/pillars' });
}

/** `GET /api/kb/formats`. */
export function useFormatPacks() {
  return useApiQuery({ queryKey: settingsKeys.formats, path: '/api/kb/formats' });
}

/** Pillar names by id, from the pillars document (an empty map while it loads). */
export function pillarNames(pillars: readonly Pillar[] | undefined): ReadonlyMap<string, string> {
  return new Map((pillars ?? []).map((pillar) => [pillar.id, pillar.name]));
}

/**
 * The default retry rule, except for a "not configured" answer (`push_unavailable`,
 * `storage_unavailable`): a 503 that says the feature is off is an answer, not an outage.
 */
export function retryUnless(code: string) {
  return (failureCount: number, error: unknown): boolean =>
    !isProblem(error, code) && shouldRetryQuery(failureCount, error);
}
