import type { components } from '@/lib/api/schema';

/**
 * Today's test documents, shaped like the server's (`components['schemas']`). Times are relative
 * to the moment a builder runs, so "due in 25 minutes" stays true whatever the clock says.
 */

type Schemas = components['schemas'];
export type Today = Schemas['Today'];
export type TodayItem = Schemas['TodayItem'];
export type TodayWeek = Schemas['TodayWeek'];
export type Scorecard = Schemas['Scorecard'];
export type SystemStatus = Schemas['SystemStatus'];
export type AutonomyView = Schemas['AutonomyView'];
export type WeekScorecard = Schemas['WeekScorecard'];
export type Post = Schemas['Post'];
export type PostEntry = Schemas['Versioned_Post_'];

const MINUTE = 60_000;

/** An ISO timestamp `minutes` from now (negative: in the past). */
export function inMinutes(minutes: number): string {
  return new Date(Date.now() + minutes * MINUTE).toISOString();
}

export function scorecard(overrides: Partial<Scorecard> = {}): Scorecard {
  return {
    postsPublished: 2,
    postsWithOwnerSignal: 1,
    replies: 4,
    substantiveComments: 3,
    onPillarShare: 1,
    computedAt: inMinutes(-5),
    ...overrides,
  };
}

export function todayWeek(overrides: Partial<TodayWeek> = {}): TodayWeek {
  return { weekStart: '2026-11-23', mode: 'normal', planStatus: 'approved', scorecard: scorecard(), ...overrides };
}

export function todayItem(overrides: Partial<TodayItem> & Pick<TodayItem, 'kind' | 'title' | 'href'>): TodayItem {
  return { id: `${overrides.kind}:${overrides.title.length.toString(16)}`, priority: 'normal', refs: {}, ...overrides };
}

/** A busy Monday: a post to publish, a plan to approve, an interview, a reply and a snapshot reminder. */
export function busyItems(): TodayItem[] {
  return [
    todayItem({
      id: 'publish_post:post-1',
      kind: 'publish_post',
      title: 'Post at 10:00 AM: tap to publish',
      href: '/studio/post-1/go-live?post=post-1',
      priority: 'urgent',
      dueAt: inMinutes(25),
      refs: { postId: 'post-1' },
    }),
    todayItem({
      id: 'approve_plan:plan-2026-11-30',
      kind: 'approve_plan',
      title: 'Approve next week’s plan',
      href: '/calendar/plan?week=2026-11-30',
      priority: 'high',
      dueAt: inMinutes(60 * 30),
      refs: { weekStart: '2026-11-30' },
    }),
    todayItem({
      id: 'answer_interview:intv-1',
      kind: 'answer_interview',
      title: 'Answer 2 quick questions',
      href: '/studio/art-1/interview',
      priority: 'high',
      dueAt: inMinutes(-90),
      refs: { interviewId: 'intv-1', articleId: 'art-1' },
    }),
    todayItem({
      id: 'reply:rem-reply-1',
      kind: 'reply',
      title: 'Live for 15 min: reply to comments on your post',
      href: '/today?engage=post-9',
      priority: 'high',
      dueAt: inMinutes(-2),
      refs: { reminderId: 'rem-reply-1', postId: 'post-9' },
    }),
    todayItem({
      id: 'snapshot:rem-snap-1',
      kind: 'snapshot',
      title: 'Log the 48-hour numbers for your post',
      href: '/today?snapshot=post-8',
      priority: 'normal',
      dueAt: inMinutes(-30),
      refs: { reminderId: 'rem-snap-1', postId: 'post-8' },
    }),
    todayItem({
      id: 'owner_input:blocklist',
      kind: 'owner_input',
      title: 'Save your blocklist',
      href: '/settings/policy',
      priority: 'low',
    }),
  ];
}

export function todayResponse({ items = busyItems(), week = todayWeek() }: { items?: TodayItem[]; week?: TodayWeek } = {}): Today {
  return { generatedAt: inMinutes(0), week, items };
}

export function systemStatus(overrides: Partial<SystemStatus> = {}): SystemStatus {
  const on = { state: 'on' as const };
  return {
    safeMode: false,
    boot: null,
    runner: { running: true, claimed: 0, schedulerRunning: true },
    schedules: { state: 'current', reason: null, seededAt: inMinutes(-60), retrying: false },
    brake: { spendTodayUsd: 1.2, thresholdUsd: 5, engaged: false },
    services: {
      ai: on,
      storage: on,
      render: on,
      fetcher: on,
      webiq: on,
      vault: on,
      push: on,
      portfolio: on,
      embeddings: on,
      linkedin: on,
    },
    expiry: { linkedinTokenDay: 12, patExpiresAt: null },
    buildSha: 'abc1234',
    ...overrides,
  };
}

export function autonomyView({ paused = false }: { paused?: boolean } = {}): AutonomyView {
  return {
    settings: {
      item: {
        id: 'autonomy',
        schemaVersion: 1,
        createdAt: inMinutes(-60 * 24 * 30),
        updatedAt: inMinutes(-60),
        autoPublishEnabled: false,
        paused,
        pausedAt: paused ? inMinutes(-60) : null,
      },
      etag: '"autonomy-1"',
    },
    decision: { allowed: false, reasons: ['auto_publish_off'] },
  };
}

export function scorecardHistory(): WeekScorecard[] {
  return [
    {
      weekStart: '2026-11-16',
      scorecard: scorecard({ postsPublished: 3, replies: 2, substantiveComments: 3, onPillarShare: 0.67 }),
    },
  ];
}

export function post(overrides: Partial<Post> = {}): Post {
  return {
    id: 'post-8',
    schemaVersion: 1,
    createdAt: inMinutes(-60 * 24 * 4),
    updatedAt: inMinutes(-60 * 47),
    status: 'posted',
    statusChangedAt: inMinutes(-60 * 47),
    lane: 'native',
    pillar: 'agents',
    format: 'text',
    displayText: 'Agents need identities, not shared keys.\n\nHere is what we changed.',
    payload: '',
    approvalCurrent: false,
    goLiveMode: 'one_click',
    timeSensitive: false,
    reviewOnly: false,
    postedAt: inMinutes(-60 * 47),
    snapshots: [],
    ...overrides,
  };
}

export function postEntry(overrides: Partial<Post> = {}, etag = '"post-8-v1"'): PostEntry {
  return { item: post(overrides), etag };
}
