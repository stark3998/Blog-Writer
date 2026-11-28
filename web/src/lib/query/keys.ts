/**
 * Every query key in one place, so the event stream can invalidate exactly what an event changes.
 * Keys are prefixes of each other where invalidation should cascade: `jobs.all` covers every job
 * query.
 *
 * Server data follows contract sections 9 and 10: `[<plural>, 'list', filters]` for lists and
 * `[<plural>, 'detail', id]` for one document. A job's `entity.changed` event invalidates
 * `[<plural>, 'detail', id]` and `[<plural>, 'list']` (every list of that kind) plus `['today']`,
 * so keep sub-resources under their document's detail key (`[...articles.detail(id), 'versions']`)
 * and they refresh with it.
 */

/** The query-key prefix of each entity a job can report as changed (contract section 9). */
export const ENTITY_PREFIXES = {
  article: 'articles',
  post: 'posts',
  idea: 'ideas',
  research_run: 'researchRuns',
  brief: 'briefs',
  quick_search: 'quickSearches',
  story: 'stories',
  pov: 'povs',
  interview: 'interviews',
  voice_sample: 'voiceSamples',
  style_guide: 'styleGuides',
  voice_digest: 'voiceDigests',
  week_plan: 'plans',
  reminder: 'reminders',
  source: 'sources',
  engagement_item: 'engagementItems',
  experiment: 'experiments',
  insight_suggestion: 'suggestions',
  eval_result: 'evalResults',
  backup_status: 'backups',
  linkedin_connection: 'linkedin',
  settings: 'kb',
} as const;

/** `entity` of an `entity.changed` event (`domain.jobtypes.EntityName`). */
export type EntityName = keyof typeof ENTITY_PREFIXES;
export type EntityPrefix = (typeof ENTITY_PREFIXES)[EntityName];

export function isEntityName(value: unknown): value is EntityName {
  return typeof value === 'string' && Object.hasOwn(ENTITY_PREFIXES, value);
}

/** List filters as they go in a key: a plain object of JSON values (TanStack hashes it). */
export type ListFilters = Readonly<Record<string, string | number | boolean | null | undefined | readonly string[]>>;

export interface EntityKeys<P extends string> {
  /** Every query of this kind. */
  readonly all: readonly [P];
  /** Every list of this kind (what `entity.changed` invalidates). */
  readonly lists: () => readonly [P, 'list'];
  /** One list (an infinite query: lists are paged, contract 1.5). */
  readonly list: (filters?: ListFilters) => readonly [P, 'list', ListFilters];
  /** Every single document of this kind. */
  readonly details: () => readonly [P, 'detail'];
  /** One document; sub-resources extend it (`[...detail(id), 'versions']`). */
  readonly detail: (id: string) => readonly [P, 'detail', string];
}

/** The keys of one kind of document, under its prefix. */
export function entityKeys<P extends string>(prefix: P): EntityKeys<P> {
  return {
    all: [prefix],
    lists: () => [prefix, 'list'],
    list: (filters = {}) => [prefix, 'list', filters],
    details: () => [prefix, 'detail'],
    detail: (id) => [prefix, 'detail', id],
  };
}

/**
 * Ids of the settings singletons (`SINGLETON_ID` in `blogwriter.domain`). A singleton's key is
 * `[<prefix>, 'detail', <id>]` like any document, so `entity.changed` refreshes it:
 * `queryKeys.kb.detail(KB_SINGLETONS.pillars)` for `GET /api/kb/pillars`.
 */
export const KB_SINGLETONS = {
  pillars: 'pillars',
  formats: 'format-packs',
  lanes: 'lanes',
  brand: 'brand-kit',
  blocklist: 'blocklist',
  policy: 'employer-policy',
  cadence: 'cadence',
  models: 'model-preferences',
  autonomy: 'autonomy',
  voicePreferences: 'voice-preferences',
  standards: 'standards-catalog',
  profileChecklist: 'profile-checklist',
} as const;

/** The other singletons, each under its own entity's prefix. */
export const LINKEDIN_CONNECTION_ID = 'linkedin-connection';
export const BACKUP_STATUS_ID = 'backup-status';

export const queryKeys = {
  me: ['me'] as const,
  health: ['health'] as const,
  jobs: {
    all: ['jobs'] as const,
    detail: (jobId: string) => ['jobs', 'detail', jobId] as const,
    list: (filters: ListFilters = {}) => ['jobs', 'list', filters] as const,
    /** `GET /api/system/status`: the runner, safe mode, the brake and the services move as jobs run. */
    systemStatus: ['jobs', 'system-status'] as const,
    /** `GET /api/schedules`: schedule items live with the jobs. */
    schedules: ['jobs', 'schedules'] as const,
    /** `GET /api/analytics/spend?days=`: AI spend grows as jobs run. */
    spend: (days: number) => ['jobs', 'spend', { days }] as const,
  },
  /** `GET /api/today`: every `entity.changed` refreshes it. */
  today: ['today'] as const,
  /**
   * Fixed for a deployment, so outside the entity prefixes: no event refreshes them.
   * `meta.machines` is `GET /api/meta/machines` (`useMachines` in `./machines`).
   */
  meta: {
    machines: ['meta', 'machines'] as const,
  },

  articles: entityKeys(ENTITY_PREFIXES.article),
  posts: entityKeys(ENTITY_PREFIXES.post),
  ideas: entityKeys(ENTITY_PREFIXES.idea),
  researchRuns: entityKeys(ENTITY_PREFIXES.research_run),
  briefs: entityKeys(ENTITY_PREFIXES.brief),
  quickSearches: entityKeys(ENTITY_PREFIXES.quick_search),
  stories: entityKeys(ENTITY_PREFIXES.story),
  povs: entityKeys(ENTITY_PREFIXES.pov),
  interviews: entityKeys(ENTITY_PREFIXES.interview),
  voiceSamples: entityKeys(ENTITY_PREFIXES.voice_sample),
  styleGuides: entityKeys(ENTITY_PREFIXES.style_guide),
  voiceDigests: entityKeys(ENTITY_PREFIXES.voice_digest),
  plans: entityKeys(ENTITY_PREFIXES.week_plan),
  reminders: entityKeys(ENTITY_PREFIXES.reminder),
  sources: entityKeys(ENTITY_PREFIXES.source),
  engagementItems: entityKeys(ENTITY_PREFIXES.engagement_item),
  experiments: entityKeys(ENTITY_PREFIXES.experiment),
  suggestions: entityKeys(ENTITY_PREFIXES.insight_suggestion),
  evalResults: entityKeys(ENTITY_PREFIXES.eval_result),
  backups: entityKeys(ENTITY_PREFIXES.backup_status),
  linkedin: entityKeys(ENTITY_PREFIXES.linkedin_connection),
  /** Settings singletons (`/api/kb/*`): `kb.detail(KB_SINGLETONS.policy)`. */
  kb: entityKeys(ENTITY_PREFIXES.settings),
};
