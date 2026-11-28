import type { components } from '@/lib/api/schema';

import { localToUtcMs, toTimestamp } from '../lib/time';

/**
 * Server-shaped documents for the calendar's tests, typed from `schema.ts`. Times follow the
 * owner's zone (New York): "now" is Friday 27 November 2026, 10:00 EST, so this week starts on
 * Monday 23 November and next week on Monday 30 November.
 */

type Schemas = components['schemas'];

export const NY = 'America/New_York';
/** Friday 27 November 2026, 10:00 in New York. */
export const TEST_NOW = '2026-11-27T15:00:00.000Z';
export const THIS_WEEK = '2026-11-23';
export const NEXT_WEEK = '2026-11-30';
export const STAMP = '2026-11-22T22:00:00.000000Z';

export function at(date: string, time: string, zone = NY): string {
  return toTimestamp(localToUtcMs(date, time, zone));
}

export function slotAt(
  date: string,
  time: string,
  kind: 'primary' | 'optional' | 'custom' = 'primary',
): Schemas['Slot'] {
  return { localDate: date, localTime: time, timezone: NY, utc: at(date, time), kind };
}

export function plannedSlot(overrides: Partial<Schemas['PlannedSlot']> & { id: string; date: string; time: string }) {
  const { id, date, time, ...rest } = overrides;
  const slot: Schemas['PlannedSlot'] = {
    id,
    slot: slotAt(date, time),
    postId: null,
    lane: 'native',
    pillar: 'identity',
    format: 'text',
    title: 'Working title',
    ...rest,
  };
  return slot;
}

export function weekPlan(overrides: Partial<Schemas['WeekPlan']> = {}): Schemas['WeekPlan'] {
  const weekStart = overrides.weekStart ?? NEXT_WEEK;
  return {
    id: `plan-${weekStart}`,
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    weekStart,
    timezone: NY,
    mode: 'normal',
    status: 'proposed',
    statusChangedAt: STAMP,
    flagship: {
      title: 'Okta to Entra: what 2,000 apps taught us',
      pillar: 'identity',
      format: 'case-study',
      ideaId: null,
      articleId: null,
      publishAt: at('2026-12-07', '09:00'),
      optional: false,
    },
    slots: [
      plannedSlot({
        id: 'tue',
        date: '2026-12-01',
        time: '10:00',
        lane: 'native',
        pillar: 'agents',
        format: 'carousel',
        title: 'MCP tool permissions in practice',
      }),
      plannedSlot({
        id: 'wed',
        date: '2026-12-02',
        time: '10:00',
        lane: 'quick_take',
        pillar: 'governance',
        format: 'text',
        title: 'EU AI Act guidance for agent builders',
        ideaId: 'idea-eu',
      }),
      plannedSlot({
        id: 'thu',
        date: '2026-12-03',
        time: '10:00',
        lane: 'native',
        pillar: 'identity',
        format: 'image_card',
        title: 'Conditional access checklist',
      }),
    ],
    pillarBalance: { agents: 1, identity: 1, governance: 1 },
    warnings: [],
    rationale: 'Identity leads this week: the migration story is ready and the bank has two takes on it.',
    approvedAt: null,
    scorecard: null,
    jobId: 'planweek-2026-11-30',
    ...overrides,
  };
}

export function versioned<T>(item: T, etag: string | null = '"etag-1"') {
  return { item, etag };
}

export function postSummary(
  overrides: Partial<Schemas['PostSummary']> & { id: string; date?: string; time?: string },
): Schemas['PostSummary'] {
  const { id, date, time, ...rest } = overrides;
  return {
    id,
    status: 'draft',
    statusChangedAt: STAMP,
    lane: 'native',
    pillar: 'agents',
    format: 'text',
    hook: '',
    slot: date && time ? slotAt(date, time) : null,
    goLiveMode: 'one_click',
    kitState: null,
    approvalCurrent: false,
    postedAt: null,
    reviewOnly: false,
    articleId: null,
    updatedAt: STAMP,
    ...rest,
  };
}

export function page<T>(items: readonly T[], nextCursor: string | null = null) {
  return { items: items.map((item, index) => ({ item, etag: `"etag-${String(index + 1)}"` })), nextCursor };
}

export function cadenceSettings(overrides: Partial<Schemas['CadenceSettings']> = {}): Schemas['CadenceSettings'] {
  return {
    id: 'cadence',
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    timezone: NY,
    planWeek: { day: 'sun', time: '18:00' },
    planLeadMinutes: 60,
    researchStart: { day: 'mon', time: '08:00' },
    interviewDue: { day: 'wed', time: '18:00' },
    draftDay: { day: 'thu', time: '09:00' },
    vetoNotice: { day: 'fri', time: '12:00' },
    articlePublish: { day: 'mon', time: '09:00' },
    minVetoLeadHours: 48,
    businessHoursStart: '09:00',
    businessHoursEnd: '17:00',
    postSlots: [
      { day: 'tue', time: '10:00', kind: 'primary', windowMinutes: 120 },
      { day: 'wed', time: '10:00', kind: 'primary', windowMinutes: 120 },
      { day: 'thu', time: '10:00', kind: 'primary', windowMinutes: 120 },
      { day: 'mon', time: '12:00', kind: 'optional', windowMinutes: 120 },
      { day: 'fri', time: '15:00', kind: 'optional', windowMinutes: 120 },
    ],
    postsPerWeek: { normal: 3, light: 2, away: 0, max: 5 },
    sameDayBlock: true,
    minGapHoursWarn: 24,
    missedSlotGraceMinutes: 120,
    stageLeadMinutes: 60,
    crawlIntervalHours: 6,
    backupTime: '03:30',
    defaultMode: 'normal',
    ...overrides,
  };
}

export function pillarSettings(): Schemas['PillarSettings'] {
  const pillar = (id: string, name: string) => ({
    id,
    name,
    topics: '',
    description: '',
    audience: '',
    vocabulary: [],
    renames: {},
    hashtags: [],
    researchDomains: [],
    targetShare: 0.33,
  });
  return {
    id: 'pillars',
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    pillars: [
      pillar('agents', 'AI agents & security'),
      pillar('identity', 'Identity modernization'),
      pillar('governance', 'AI governance'),
    ],
  };
}

export function laneSettings(): Schemas['LaneSettings'] {
  return {
    id: 'lanes',
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    derivativesMaxPerWeek: 2,
    nativeMinPerWeek: 1,
    nativeOtherPillar: true,
    onPillarMinShare: 0.8,
    hookVariants: 3,
  };
}

export function formatPacks(): Schemas['FormatPacks'] {
  const pack = (id: string, name: string) => ({
    id,
    name,
    minWords: 900,
    maxWords: 3000,
    mustContain: [],
    ownerInput: 'micro_interview' as const,
    minQuestions: 2,
    maxQuestions: 2,
    banned: [],
  });
  return {
    id: 'format-packs',
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    packs: [
      pack('case-study', 'Case study / how I did it'),
      pack('framework', 'Framework / checklist'),
      pack('deep-explainer', 'Deep explainer'),
      pack('news-analysis', 'News analysis'),
    ],
  };
}

export function idea(overrides: Partial<Schemas['Idea']> & { id: string }): Schemas['Idea'] {
  return {
    schemaVersion: 1,
    createdAt: STAMP,
    updatedAt: STAMP,
    status: 'new',
    statusChangedAt: STAMP,
    origin: 'feed',
    title: 'An idea',
    note: null,
    url: null,
    publisher: null,
    publishedAt: null,
    pillar: null,
    relevance: null,
    novelty: null,
    suggestedLane: null,
    suggestedFormat: null,
    angle: null,
    timeSensitive: false,
    expiresAt: null,
    snoozedUntil: null,
    triage: null,
    articleId: null,
    postId: null,
    ...overrides,
  };
}

export function articleSummary(
  overrides: Partial<Schemas['ArticleSummary']> & { id: string },
): Schemas['ArticleSummary'] {
  return {
    status: 'planned',
    statusChangedAt: STAMP,
    purpose: 'flagship',
    meta: { title: 'Okta to Entra: what 2,000 apps taught us', slug: null, pillar: 'identity', format: 'case-study' },
    version: 0,
    wordCount: 0,
    updatedAt: STAMP,
    updatedBy: 'owner',
    reviewOnly: false,
    gates: null,
    publish: { live: false, publishAt: null, url: null },
    ideaId: null,
    weekPlanId: 'plan-2026-11-30',
    ...overrides,
  };
}

/** `GET /api/meta/machines` for the machines the calendar reads (contract section 2). */
export const MACHINES = {
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
